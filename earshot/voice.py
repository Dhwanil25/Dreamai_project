"""Optional ElevenLabs voice calls plus independent local WAV confirmations.

Provider APIs follow the official REST contracts:
https://elevenlabs.io/docs/api-reference/speech-to-text/convert
https://elevenlabs.io/docs/api-reference/text-to-speech/convert

No client or network activity occurs on import. Provider operations require an
explicit online state and a key. Cached confirmations need neither. The local
asset builder may use an installed operating-system speech engine to generate
WAV files at cache_path(text); those recordings must be labelled synthetic.
"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
from time import monotonic
import wave

import requests

from earshot.config import CONFIG, PROJECT_ROOT
from earshot.rules import SuppressionRule


OFFLINE_MODE = False
CACHE_DIR = PROJECT_ROOT / "demo/utterances/cached"
REQUEST_TIMEOUT_S = 10
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 1000
MAX_TRANSCRIPT_CHARS = 4000
MAX_JSON_BYTES = 128 * 1024
API_BASE = "https://api.elevenlabs.io/v1"
NO_RULE_CONFIRMATION = "I didn't catch a rule in that. No learning was applied."
_MIME_EXTENSIONS = {
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a",
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/flac": "flac",
}


class VoiceError(RuntimeError):
    """A safe, user-facing voice failure with no provider response or secrets."""


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
        raise OfflineError("Offline mode blocks voice-provider calls; use local speech or scripted audio")


def _key() -> str:
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise VoiceUnavailableError("ElevenLabs is not configured; use a local voice fallback")
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


def _request(url: str, *, limit: int, expected_type: str, **kwargs) -> bytes:
    """Bound response reads and sanitize all transport errors.

    Requests' 10-second timeout bounds connect/read inactivity. A monotonic
    deadline also rejects responses/chunks received after ten elapsed seconds;
    the transport timeout remains the bound for an in-progress blocking read.
    No automatic retries or redirects can initiate another provider request.
    """
    response = None
    started = monotonic()
    try:
        _require_online()  # Check again immediately before the outbound call.
        response = requests.post(url, timeout=REQUEST_TIMEOUT_S, allow_redirects=False,
                                 stream=True, **kwargs)
        if response.status_code != 200:
            raise VoiceProviderError(f"Voice provider returned HTTP {response.status_code}")
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
    except VoiceError:
        raise
    except requests.Timeout:
        raise VoiceProviderError("Voice provider timed out; use a local voice fallback") from None
    except Exception:
        raise VoiceProviderError("Voice provider request failed; use a local voice fallback") from None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def transcribe(audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
    """Transcribe a bounded microphone upload; never persist its bytes locally."""
    _require_online()
    if not isinstance(audio_bytes, bytes) or not 0 < len(audio_bytes) <= MAX_AUDIO_BYTES:
        raise VoiceInputError(f"Audio must contain 1 to {MAX_AUDIO_BYTES} bytes")
    if not isinstance(mime_type, str):
        raise VoiceInputError("Audio MIME type is not supported")
    mime = mime_type.split(";", 1)[0].strip().lower()
    if mime not in _MIME_EXTENSIONS:
        raise VoiceInputError("Audio MIME type is not supported")
    body = _request(
        f"{API_BASE}/speech-to-text", limit=MAX_JSON_BYTES, expected_type="application/json",
        headers={"xi-api-key": _key()},
        data={"model_id": _setting("ELEVENLABS_STT_MODEL", "scribe_v2"),
              "language_code": "en", "tag_audio_events": "false",
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
    return text.strip()


def speak(text: str) -> bytes:
    """Request MP3 speech online; callers check cached_audio first for local WAV."""
    _require_online()
    text = _text(text)
    voice_id = _setting("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
    audio = _request(
        f"{API_BASE}/text-to-speech/{voice_id}", limit=MAX_AUDIO_BYTES, expected_type="audio/mpeg",
        headers={"xi-api-key": _key(), "Accept": "audio/mpeg"},
        params={"output_format": "mp3_44100_128"},
        json={"text": text, "model_id": _setting("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")},
    )
    if len(audio) < 3 or not (audio.startswith(b"ID3") or (audio[0] == 255 and audio[1] & 224 == 224)):
        raise VoiceProviderError("Voice provider returned invalid MP3 audio")
    return audio


def cache_path(text: str) -> Path:
    """Return the exact local WAV filename for stripped confirmation text."""
    return CACHE_DIR / (sha256(_text(text).encode("utf-8")).hexdigest() + ".wav")


def cached_audio(text: str) -> bytes | None:
    """Read bounded, complete PCM WAV locally; a missing/invalid cache is a miss."""
    path = cache_path(text)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_AUDIO_BYTES:
            return None
        with path.open("rb") as handle:
            audio = handle.read(MAX_AUDIO_BYTES + 1)
        if (len(audio) > MAX_AUDIO_BYTES or len(audio) < 44 or audio[:4] != b"RIFF"
                or audio[8:12] != b"WAVE" or int.from_bytes(audio[4:8], "little") + 8 != len(audio)):
            return None
        with wave.open(BytesIO(audio), "rb") as wav:
            if (wav.getcomptype() != "NONE" or wav.getnchannels() not in (1, 2)
                    or not 8000 <= wav.getframerate() <= 96000 or wav.getsampwidth() not in (1, 2, 3, 4)
                    or wav.getnframes() <= 0):
                return None
            expected = wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
            if len(wav.readframes(wav.getnframes())) != expected:
                return None
        return audio
    except (OSError, EOFError, ValueError, wave.Error):
        return None


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
