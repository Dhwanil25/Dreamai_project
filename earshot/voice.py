"""Optional ElevenLabs voice calls with sanitized per-request evidence.

Provider APIs follow the official REST contracts:
https://elevenlabs.io/docs/api-reference/speech-to-text/convert
https://elevenlabs.io/docs/api-reference/text-to-speech/convert

No client or network activity occurs on import. Provider operations require an
explicit online state and a key. Text and verified on-device speech are the
runtime fallbacks. Provider audio is generated for each request and is never
substituted with prerecorded content.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
import re
from time import monotonic, perf_counter

import requests

from earshot.config import CONFIG
from earshot.rules import SuppressionRule


OFFLINE_MODE = False
REQUEST_TIMEOUT_S = 10
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 1000
MAX_TRANSCRIPT_CHARS = 4000
MAX_JSON_BYTES = 128 * 1024
MAX_ERROR_JSON_BYTES = 4096
API_BASE = "https://api.elevenlabs.io/v1"
NO_RULE_CONFIRMATION = "I didn't catch a rule in that. No learning was applied."
_MIME_EXTENSIONS = {
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a",
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/flac": "flac",
}


class VoiceError(RuntimeError):
    """A safe, user-facing voice failure with no provider response or secrets."""

    def __init__(self, message: str, *, evidence: dict | None = None) -> None:
        super().__init__(message)
        self.evidence = dict(evidence or {})


class OfflineError(VoiceError):
    """A requested provider operation is blocked by the local offline gate."""


class VoiceUnavailableError(VoiceError):
    """Optional provider credentials/configuration are unavailable."""


class VoiceInputError(VoiceError):
    """Audio or text is outside the local request contract."""


class VoiceProviderError(VoiceError):
    """The provider failed, timed out or returned unusable bounded content."""


def offline_enabled() -> bool:
    """Missing configuration defaults to local-only operation."""
    return OFFLINE_MODE or os.getenv("EARSHOT_OFFLINE", "1") != "0"


def provider_available() -> bool:
    """Report key presence only; this does not probe or validate the account."""
    return bool(os.getenv("ELEVENLABS_API_KEY", "").strip())


def _require_online() -> None:
    if offline_enabled():
        raise OfflineError("Offline mode blocks voice-provider calls; use text or verified on-device speech")


def _key() -> str:
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise VoiceUnavailableError("ElevenLabs is not configured; use text or verified on-device speech")
    return key


def _setting(name: str, default: str) -> str:
    value = os.getenv(name, "").strip() or default
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise VoiceUnavailableError("Voice provider configuration is invalid")
    return value


def _text(text: str) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise VoiceInputError(f"Speech text must contain 1 to {MAX_TEXT_CHARS} characters")
    return text.strip()


def _receipt(operation: str) -> dict:
    """Create an empty operation receipt; no content or credentials are stored."""
    return {"provider": "elevenlabs", "operation": operation,
            "requested_model": None, "response_model": None,
            "attempted": False, "success": False, "http_status": None,
            "request_id": None, "elapsed_ms": 0.0, "request_elapsed_ms": None,
            "input_bytes": None, "input_sha256": None,
            "output_bytes": None, "output_sha256": None,
            "error_code": None, "required_permission": None}


def _safe_identifier(value: object) -> str | None:
    """Accept bounded provider identifiers, excluding the configured secret."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value):
        return None
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    return None if key and key in value else value


def _error_metadata(response, evidence: dict, started: float) -> None:
    """Inspect at most 4 KiB of JSON and copy only known error/permission codes.

    Provider messages are used solely to recognize two literal permissions;
    neither the original body nor any arbitrary message is returned or logged.
    Oversized, non-JSON, malformed or slow error content supplies no details.
    """
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            if not 0 <= int(declared) <= MAX_ERROR_JSON_BYTES:
                return
        except (TypeError, ValueError):
            return
    body = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=MAX_ERROR_JSON_BYTES):
            _require_online()
            if monotonic() - started > REQUEST_TIMEOUT_S:
                return
            if len(body) + len(chunk) > MAX_ERROR_JSON_BYTES:
                return
            body.extend(chunk)
        payload = json.loads(body)
    except VoiceError:
        raise
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    detail = payload.get("detail", payload)
    if not isinstance(detail, dict):
        return
    code = detail.get("status", detail.get("code"))
    allowed = {"missing_permissions", "invalid_api_key", "quota_exceeded",
               "rate_limit_exceeded", "voice_not_found", "model_not_found",
               "validation_error", "too_many_concurrent_requests",
               "missing_authorization_header", "blocked_ip"}
    if isinstance(code, str) and code in allowed:
        evidence["error_code"] = code
    message = detail.get("message")
    if code == "missing_permissions" and isinstance(message, str):
        permissions = {permission for permission in ("speech_to_text", "text_to_speech")
                       if re.search(r"\b" + permission + r"\b", message)}
        if len(permissions) == 1:
            evidence["required_permission"] = permissions.pop()


def _http_error(status: int, evidence: dict) -> VoiceProviderError:
    message = f"Voice provider returned HTTP {status}"
    if evidence.get("error_code") == "missing_permissions":
        permission = evidence.get("required_permission")
        action = f"Enable the {permission} permission" if permission else "Enable the required voice permission"
        message += f". {action} in the ElevenLabs API-key settings, then try again. Text teaching remains available."
    return VoiceProviderError(message, evidence=evidence)


def _request(url: str, *, limit: int, expected_type: str,
             evidence: dict | None = None, **kwargs) -> bytes:
    """Bound response reads and record only sanitized actual transport metadata.

    Ten seconds bounds connect/read inactivity; the monotonic deadline also
    rejects late chunks. Redirects and SDK retries are disabled. The optional
    receipt is modified in place; requests never copy secrets or body contents
    into it. All typed failures carry a snapshot in ``error.evidence``.
    """
    evidence = evidence if evidence is not None else {}
    response = None
    started = monotonic()
    request_started = perf_counter()
    failure = None
    try:
        _require_online()
        evidence["attempted"] = True
        response = requests.post(url, timeout=REQUEST_TIMEOUT_S, allow_redirects=False,
                                 stream=True, **kwargs)
        evidence["http_status"] = int(response.status_code)
        for name in ("request-id", "x-request-id", "xi-request-id", "Request-Id", "X-Request-Id"):
            identifier = _safe_identifier(response.headers.get(name))
            if identifier:
                evidence["request_id"] = identifier
                break
        if response.status_code != 200:
            _error_metadata(response, evidence, started)
            raise _http_error(response.status_code, evidence)
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != expected_type:
            raise VoiceProviderError("Voice provider returned an unexpected content type")
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                length = int(declared)
            except (TypeError, ValueError):
                raise VoiceProviderError("Voice provider returned an invalid content length") from None
            if length < 0 or length > limit:
                raise VoiceProviderError("Voice provider response exceeds the local size limit")
        body = bytearray()
        for chunk in response.iter_content(chunk_size=16 * 1024):
            _require_online()
            if monotonic() - started > REQUEST_TIMEOUT_S:
                raise VoiceProviderError("Voice provider response exceeded the time limit")
            if not chunk:
                continue
            if len(body) + len(chunk) > limit:
                raise VoiceProviderError("Voice provider response exceeds the local size limit")
            body.extend(chunk)
        _require_online()
        if monotonic() - started > REQUEST_TIMEOUT_S:
            raise VoiceProviderError("Voice provider response exceeded the time limit")
        if not body:
            raise VoiceProviderError("Voice provider returned an empty response")
        return bytes(body)
    except VoiceError as error:
        failure = error
        raise
    except requests.Timeout:
        failure = VoiceProviderError("Voice provider timed out; use text or verified on-device speech")
        raise failure from None
    except Exception:
        failure = VoiceProviderError("Voice provider request failed; use text or verified on-device speech")
        raise failure from None
    finally:
        evidence["request_elapsed_ms"] = round((perf_counter() - request_started) * 1000, 3)
        if failure is not None:
            failure.evidence = dict(evidence)
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def transcribe_with_evidence(audio_bytes: bytes, mime_type: str = "audio/wav") -> tuple[str, dict]:
    """Return genuine provider transcription plus a content-free request receipt.

    Input bytes are bounded and never persisted. Receipt hashes describe the
    exact uploaded audio and the returned stripped transcript's UTF-8 bytes.
    Requested and actually returned model identifiers remain separate. Typed
    failures include the partial receipt and never trigger an offline substitute.
    """
    evidence = _receipt("speech_to_text")
    started = perf_counter()
    try:
        _require_online()
        if not isinstance(audio_bytes, bytes) or not 0 < len(audio_bytes) <= MAX_AUDIO_BYTES:
            raise VoiceInputError(f"Audio must contain 1 to {MAX_AUDIO_BYTES} bytes")
        evidence.update(input_bytes=len(audio_bytes), input_sha256=sha256(audio_bytes).hexdigest())
        if not isinstance(mime_type, str):
            raise VoiceInputError("Audio MIME type is not supported")
        mime = mime_type.split(";", 1)[0].strip().lower()
        if mime not in _MIME_EXTENSIONS:
            raise VoiceInputError("Audio MIME type is not supported")
        model = _setting("ELEVENLABS_STT_MODEL", "scribe_v2")
        evidence["requested_model"] = _safe_identifier(model)
        body = _request(
            f"{API_BASE}/speech-to-text", limit=MAX_JSON_BYTES, expected_type="application/json",
            evidence=evidence, headers={"xi-api-key": _key()},
            data={"model_id": model, "language_code": "en", "tag_audio_events": "false",
                  "timestamps_granularity": "none", "diarize": "false", "webhook": "false"},
            files={"file": (f"operator-audio.{_MIME_EXTENSIONS[mime]}", audio_bytes, mime)},
        )
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise VoiceProviderError("Voice provider returned an invalid transcript") from None
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TRANSCRIPT_CHARS:
            raise VoiceProviderError("Voice provider returned an empty or invalid transcript")
        evidence["response_model"] = _safe_identifier(payload.get("model_id"))
        result = text.strip()
        try:
            encoded = result.encode("utf-8")
        except UnicodeEncodeError:
            raise VoiceProviderError("Voice provider returned an invalid transcript") from None
        evidence.update(output_bytes=len(encoded), output_sha256=sha256(encoded).hexdigest(), success=True)
        evidence["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
        return result, dict(evidence)
    except VoiceError as error:
        evidence["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
        error.evidence = dict(evidence)
        raise


def transcribe(audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
    """Return genuine transcription, preserving the original string interface."""
    return transcribe_with_evidence(audio_bytes, mime_type)[0]


def speak_with_evidence(text: str) -> tuple[bytes, dict]:
    """Generate provider MP3 plus a receipt; never read prerecorded audio.

    Input byte count/hash describe the normalized text's UTF-8 representation;
    output count/hash describe the returned MP3. The configured model is only
    a requested model because this binary response supplies no model identity.
    """
    evidence = _receipt("text_to_speech")
    started = perf_counter()
    try:
        _require_online()
        text = _text(text)
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError:
            raise VoiceInputError("Speech text must contain valid UTF-8 characters") from None
        evidence.update(input_bytes=len(encoded), input_sha256=sha256(encoded).hexdigest())
        voice_id = _setting("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
        model = _setting("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
        evidence["requested_model"] = _safe_identifier(model)
        audio = _request(
            f"{API_BASE}/text-to-speech/{voice_id}", limit=MAX_AUDIO_BYTES, expected_type="audio/mpeg",
            evidence=evidence, headers={"xi-api-key": _key(), "Accept": "audio/mpeg"},
            params={"output_format": "mp3_44100_128"}, json={"text": text, "model_id": model},
        )
        if len(audio) < 3 or not (audio.startswith(b"ID3") or (audio[0] == 255 and audio[1] & 224 == 224)):
            raise VoiceProviderError("Voice provider returned invalid MP3 audio")
        evidence.update(output_bytes=len(audio), output_sha256=sha256(audio).hexdigest(), success=True)
        evidence["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
        return audio, dict(evidence)
    except VoiceError as error:
        evidence["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
        error.evidence = dict(evidence)
        raise


def speak(text: str) -> bytes:
    """Return freshly generated provider MP3, preserving the bytes interface."""
    return speak_with_evidence(text)[0]


def confirmation(rule: SuppressionRule | dict | None, vocabulary: dict) -> str:
    """Describe the accepted action and actual source scope for local read-back."""
    if rule is None:
        return NO_RULE_CONFIRMATION
    try:
        accepted = SuppressionRule.model_validate(rule)
        descriptions = {item["alarm_code"]: item["description"] for item in vocabulary["codes"]}
        code = accepted.scope["alarm_code"]
        if code not in descriptions:
            raise ValueError("Unknown source alarm")
        description = descriptions[code]
        if description == "(undocumented)":
            description = f"alarm {code}"
        station = accepted.scope["turbine_id"]
        if station is None:
            target = "all turbines"
        else:
            if station not in vocabulary["turbines"]:
                raise ValueError("Unknown source turbine")
            label = vocabulary.get("turbine_labels", {}).get(station, "")
            target = f"turbine {int(label[1:])}" if re.fullmatch(r"T\d+", label) else f"station {station}"
        if accepted.action == "suppress":
            sentence = f"Suppressing {description} on {target}."
        elif accepted.action == "collapse":
            window = accepted.pattern["window_s"] or CONFIG.policy.collapse_window_s
            sentence = f"Showing {description} at most once per {window} seconds on {target}."
        else:
            sentence = f"Always showing {description} on {target}."
        return _text(f"Got it. {sentence} Tell me if I’m wrong.")
    except (KeyError, TypeError, ValueError):
        raise VoiceInputError("Cannot describe a rule outside the supplied source vocabulary") from None
