"""Voice boundaries with mocked providers, local WAVs and real rule vocabulary.

Silence is generated solely as a unit-test audio container; it is not a source
sensor observation or a demo recording. No test contacts a speech provider.
"""

from hashlib import sha256
from io import BytesIO
import json
import socket
import wave

import pytest
import requests

from earshot.config import PROJECT_ROOT
from earshot.parse import parse_utterance
from earshot import voice


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setenv("EARSHOT_OFFLINE", "1")
    monkeypatch.setattr(voice, "OFFLINE_MODE", False)
    for name in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "ELEVENLABS_STT_MODEL", "ELEVENLABS_TTS_MODEL"):
        monkeypatch.delenv(name, raising=False)
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Voice tests forbid real provider requests")

    monkeypatch.setattr(voice.requests, "post", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    yield
    assert not attempts, "Even a swallowed outbound failure is a test failure"


@pytest.fixture
def wav_bytes():
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(3200))
    return buffer.getvalue()


@pytest.fixture(scope="module")
def vocabulary():
    path = PROJECT_ROOT / "demo/vocabulary.json"
    if not path.is_file():
        pytest.skip("Confirmation scope tests require the real source vocabulary")
    return json.loads(path.read_text())


class Response:
    def __init__(self, body, content_type="application/json", *, status=200, chunks=None, length=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self.chunks = [body] if chunks is None else chunks
        self.closed = False
        self.reads = 0

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


def online(monkeypatch):
    monkeypatch.setenv("EARSHOT_OFFLINE", "0")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "unit-test-key-never-send")


def provider(monkeypatch, response):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(voice.requests, "post", post)
    return calls


@pytest.mark.parametrize("switch", ["default", "environment", "application", "invalid-environment"])
@pytest.mark.parametrize("operation", ["transcribe", "speak"])
def test_offline_gate_prevents_all_provider_calls(monkeypatch, wav_bytes, switch, operation):
    online(monkeypatch)
    if switch == "default":
        monkeypatch.delenv("EARSHOT_OFFLINE")
    elif switch == "environment":
        monkeypatch.setenv("EARSHOT_OFFLINE", "1")
    elif switch == "application":
        monkeypatch.setattr(voice, "OFFLINE_MODE", True)
    else:
        monkeypatch.setenv("EARSHOT_OFFLINE", "misspelled")

    with pytest.raises(voice.OfflineError):
        getattr(voice, operation)(wav_bytes if operation == "transcribe" else "Got it.")


@pytest.mark.parametrize("operation", ["transcribe", "speak"])
def test_missing_credentials_are_typed_and_never_attempt_requests(monkeypatch, wav_bytes, operation):
    monkeypatch.setenv("EARSHOT_OFFLINE", "0")
    assert voice.provider_available() is False
    with pytest.raises(voice.VoiceUnavailableError):
        getattr(voice, operation)(wav_bytes if operation == "transcribe" else "Got it.")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "  present-for-presence-check  ")
    assert voice.provider_available() is True


def test_transcription_uses_bounded_official_multipart_contract(monkeypatch, wav_bytes):
    online(monkeypatch)
    response = Response(json.dumps({"text": "  ignore cable untwist on turbine four  "}).encode())
    calls = provider(monkeypatch, response)

    assert voice.transcribe(wav_bytes, "audio/wav;codec=pcm") == "ignore cable untwist on turbine four"

    assert len(calls) == 1 and response.closed
    url, options = calls[0]
    assert url == "https://api.elevenlabs.io/v1/speech-to-text"
    assert options["files"]["file"] == ("operator-audio.wav", wav_bytes, "audio/wav")
    assert options["data"]["model_id"] == "scribe_v2"
    assert options["data"]["webhook"] == "false"
    assert options["data"]["tag_audio_events"] == "false"
    assert options["timeout"] == 10
    assert options["stream"] is True and options["allow_redirects"] is False
    assert options["headers"]["xi-api-key"] == "unit-test-key-never-send"


def test_speech_returns_provider_mp3_with_bounded_json_contract(monkeypatch):
    online(monkeypatch)
    mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
    response = Response(mp3, "audio/mpeg")
    calls = provider(monkeypatch, response)

    assert voice.speak("  Got it.  ") == mp3

    assert len(calls) == 1 and response.closed
    url, options = calls[0]
    assert url == "https://api.elevenlabs.io/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb"
    assert options["json"] == {"text": "Got it.", "model_id": "eleven_flash_v2_5"}
    assert options["params"] == {"output_format": "mp3_44100_128"}
    assert options["timeout"] == 10 and options["allow_redirects"] is False


@pytest.mark.parametrize("bad", [b"", "not bytes", None])
def test_invalid_audio_never_reaches_provider(monkeypatch, bad):
    online(monkeypatch)
    with pytest.raises(voice.VoiceInputError):
        voice.transcribe(bad)


def test_audio_size_and_mime_limits_precede_network(monkeypatch, wav_bytes):
    online(monkeypatch)
    with pytest.raises(voice.VoiceInputError):
        voice.transcribe(wav_bytes, "text/html")
    monkeypatch.setattr(voice, "MAX_AUDIO_BYTES", len(wav_bytes) - 1)
    with pytest.raises(voice.VoiceInputError):
        voice.transcribe(wav_bytes)


@pytest.mark.parametrize("bad", [None, "", "  ", "x" * 1001])
def test_invalid_speech_text_never_reaches_provider(monkeypatch, bad):
    online(monkeypatch)
    with pytest.raises(voice.VoiceInputError):
        voice.speak(bad)


def test_provider_voice_identifier_cannot_change_request_path(monkeypatch):
    online(monkeypatch)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "../../elsewhere?secret=value")
    with pytest.raises(voice.VoiceUnavailableError):
        voice.speak("Got it.")


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_http_errors_are_sanitized_without_retry_or_redirect(monkeypatch, wav_bytes, status):
    online(monkeypatch)
    response = Response(b"provider secret or private transcript", "text/plain", status=status)
    calls = provider(monkeypatch, response)

    with pytest.raises(voice.VoiceProviderError) as error:
        voice.transcribe(wav_bytes)

    assert str(status) in str(error.value)
    assert "secret" not in str(error.value) and "transcript" not in str(error.value)
    assert len(calls) == 1 and response.closed and response.reads == 0


@pytest.mark.parametrize("failure", [requests.Timeout("unit-test-key-never-send"), requests.ConnectionError("private transcript")])
def test_transport_failures_expose_only_safe_typed_messages(monkeypatch, wav_bytes, failure):
    online(monkeypatch)
    calls = provider(monkeypatch, failure)
    with pytest.raises(voice.VoiceProviderError) as error:
        voice.transcribe(wav_bytes)
    assert "unit-test-key" not in str(error.value) and "private transcript" not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"[]", b"{}", b'{"text": 42}', b'{"text": "  "}', json.dumps({"text": "x" * 4001}).encode()])
def test_malformed_or_empty_transcripts_are_rejected(monkeypatch, wav_bytes, body):
    online(monkeypatch)
    response = Response(body)
    provider(monkeypatch, response)
    with pytest.raises(voice.VoiceProviderError):
        voice.transcribe(wav_bytes)
    assert response.closed


def test_wrong_audio_content_or_container_is_rejected(monkeypatch):
    online(monkeypatch)
    response = Response(b"<html>not audio</html>", "audio/mpeg")
    provider(monkeypatch, response)
    with pytest.raises(voice.VoiceProviderError, match="MP3"):
        voice.speak("Got it.")
    assert response.closed
    response = Response(b"ID3audio", "text/html")
    provider(monkeypatch, response)
    with pytest.raises(voice.VoiceProviderError, match="content type"):
        voice.speak("Got it.")
    assert response.closed and response.reads == 0


@pytest.mark.parametrize("declared", [True, False])
def test_provider_responses_are_size_limited_before_accumulating(monkeypatch, wav_bytes, declared):
    online(monkeypatch)
    monkeypatch.setattr(voice, "MAX_JSON_BYTES", 10)
    response = Response(b"x" * 11, length=11 if declared else None)
    provider(monkeypatch, response)
    with pytest.raises(voice.VoiceProviderError, match="size limit"):
        voice.transcribe(wav_bytes)
    assert response.closed
    assert response.reads == (0 if declared else 1)


def test_elapsed_response_deadline_is_checked_without_real_waits(monkeypatch, wav_bytes):
    online(monkeypatch)
    ticks = iter([0.0, 11.0])
    monkeypatch.setattr(voice, "monotonic", lambda: next(ticks))
    response = Response(b'{"text":"ignore cable untwist"}')
    provider(monkeypatch, response)
    with pytest.raises(voice.VoiceProviderError, match="time limit"):
        voice.transcribe(wav_bytes)
    assert response.closed


@pytest.mark.parametrize("operation", ["transcribe", "speak"])
def test_offline_gate_rechecks_after_request_preparation(monkeypatch, wav_bytes, operation):
    online(monkeypatch)

    def switch_during_configuration():
        monkeypatch.setenv("EARSHOT_OFFLINE", "1")
        return "unit-test-key-never-send"

    monkeypatch.setattr(voice, "_key", switch_during_configuration)
    with pytest.raises(voice.OfflineError):
        getattr(voice, operation)(wav_bytes if operation == "transcribe" else "Got it.")


def test_switch_during_inflight_response_discards_result_and_closes(monkeypatch, wav_bytes):
    online(monkeypatch)
    response = Response(b'{"text":"ignore cable untwist"}')
    calls = []

    def post(*args, **kwargs):
        calls.append(True)
        monkeypatch.setattr(voice, "OFFLINE_MODE", True)
        return response

    monkeypatch.setattr(voice.requests, "post", post)
    with pytest.raises(voice.OfflineError):
        voice.transcribe(wav_bytes)
    assert len(calls) == 1 and response.closed


@pytest.mark.parametrize("text,action,target", [
    ("ignore cable untwist on turbine four", "Suppressing", "turbine 4"),
    ("when it is icing give me one line per turbine not six", "at most once per 60 seconds", "all turbines"),
    ("always tell me about high wind on number four", "Always showing", "turbine 4"),
])
def test_confirmation_describes_real_action_alarm_and_scope(vocabulary, text, action, target):
    rule = parse_utterance(text, vocabulary)
    assert rule is not None
    description = next(row["description"] for row in vocabulary["codes"] if row["alarm_code"] == rule.scope["alarm_code"])

    result = voice.confirmation(rule, vocabulary)

    assert action in result and description in result and target in result
    assert result.endswith("Tell me if I’m wrong.")
    assert voice.confirmation(rule.model_dump(), vocabulary) == result


def test_confirmation_does_not_claim_learning_for_no_rule_or_unknown_scope(vocabulary):
    assert "No learning was applied" in voice.confirmation(None, vocabulary)
    rule = parse_utterance("ignore cable untwist on turbine four", vocabulary)
    unknown = str(max(map(int, vocabulary["turbines"])) + 1)
    rule = rule.model_copy(update={"scope": {**rule.scope, "turbine_id": unknown}})
    with pytest.raises(voice.VoiceInputError):
        voice.confirmation(rule, vocabulary)


@pytest.mark.parametrize("returned_model", [None, "actual-model-id"])
def test_transcription_receipt_measures_returned_content_without_copying_it(monkeypatch, wav_bytes, returned_model):
    online(monkeypatch)
    monkeypatch.setenv("ELEVENLABS_STT_MODEL", "requested-model-id")
    text = "ignore cable untwist on turbine four"
    payload = {"text": "  " + text + "  "}
    if returned_model:
        payload["model_id"] = returned_model
    response = Response(json.dumps(payload).encode())
    response.headers["request-id"] = "stt-request-123"
    calls = provider(monkeypatch, response)

    result, receipt = voice.transcribe_with_evidence(wav_bytes)

    assert result == text and len(calls) == 1 and response.closed
    assert receipt["provider"] == "elevenlabs"
    assert receipt["operation"] == "speech_to_text"
    assert receipt["requested_model"] == "requested-model-id"
    assert receipt["response_model"] == returned_model
    assert receipt["http_status"] == 200 and receipt["request_id"] == "stt-request-123"
    assert receipt["attempted"] is True and receipt["success"] is True
    assert receipt["input_bytes"] == len(wav_bytes)
    assert receipt["input_sha256"] == sha256(wav_bytes).hexdigest()
    assert receipt["output_bytes"] == len(text.encode())
    assert receipt["output_sha256"] == sha256(text.encode()).hexdigest()
    assert 0 <= receipt["request_elapsed_ms"] <= receipt["elapsed_ms"] + 0.001
    assert receipt["error_code"] is None and receipt["required_permission"] is None
    serialized = json.dumps(receipt)
    assert text not in serialized and "unit-test-key-never-send" not in serialized


def test_speech_receipt_records_actual_bytes_and_does_not_infer_response_model(monkeypatch):
    online(monkeypatch)
    mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
    response = Response(mp3, "audio/mpeg")
    response.headers["x-request-id"] = "tts-request-456"
    calls = provider(monkeypatch, response)

    result, receipt = voice.speak_with_evidence("  Got it.  ")

    assert result == mp3 and len(calls) == 1 and response.closed
    assert receipt["operation"] == "text_to_speech"
    assert receipt["requested_model"] == "eleven_flash_v2_5"
    assert receipt["response_model"] is None
    assert receipt["http_status"] == 200 and receipt["request_id"] == "tts-request-456"
    assert receipt["success"] is True and receipt["attempted"] is True
    assert receipt["input_bytes"] == len(b"Got it.")
    assert receipt["input_sha256"] == sha256(b"Got it.").hexdigest()
    assert receipt["output_bytes"] == len(mp3)
    assert receipt["output_sha256"] == sha256(mp3).hexdigest()
    assert "Got it." not in json.dumps(receipt)


@pytest.mark.parametrize("operation", ["transcribe_with_evidence", "speak_with_evidence"])
def test_offline_receipt_explicitly_reports_zero_provider_requests(wav_bytes, operation):
    argument = wav_bytes if operation.startswith("transcribe") else "Got it."
    with pytest.raises(voice.OfflineError) as error:
        getattr(voice, operation)(argument)
    receipt = error.value.evidence
    assert receipt["attempted"] is False and receipt["success"] is False
    assert receipt["http_status"] is None and receipt["request_id"] is None
    assert receipt["request_elapsed_ms"] is None
    assert receipt["output_bytes"] is None and receipt["output_sha256"] is None
    assert receipt["elapsed_ms"] >= 0


@pytest.mark.parametrize("permission,operation", [
    ("speech_to_text", "transcribe_with_evidence"),
    ("text_to_speech", "speak_with_evidence"),
])
def test_missing_permissions_are_actionable_without_exposing_provider_body(monkeypatch, wav_bytes, permission, operation):
    online(monkeypatch)
    body = {"detail": {"status": "missing_permissions", "message":
        f"The API key you used is missing the permission {permission} to execute this operation. "
        "unit-test-key-never-send private transcript"}}
    response = Response(json.dumps(body).encode(), status=401)
    response.headers["xi-request-id"] = "denied-123"
    calls = provider(monkeypatch, response)
    argument = wav_bytes if operation.startswith("transcribe") else "Got it."

    with pytest.raises(voice.VoiceProviderError) as error:
        getattr(voice, operation)(argument)

    assert f"Enable the {permission} permission" in str(error.value)
    assert "ElevenLabs API-key settings" in str(error.value)
    receipt = error.value.evidence
    assert receipt["error_code"] == "missing_permissions"
    assert receipt["required_permission"] == permission
    assert receipt["http_status"] == 401 and receipt["request_id"] == "denied-123"
    assert receipt["attempted"] is True and receipt["success"] is False
    assert receipt["output_bytes"] is None and receipt["output_sha256"] is None
    assert len(calls) == 1 and response.closed and response.reads == 1
    exposed = str(error.value) + json.dumps(receipt)
    assert "unit-test-key-never-send" not in exposed and "private transcript" not in exposed


@pytest.mark.parametrize("body", [
    b"not JSON", b"[]",
    b'{"detail":{"status":"private-error-secret","message":"speech_to_text private transcript"}}',
    b'{"detail":{"status":"missing_permissions","message":"private transcript"}}',
])
def test_unknown_error_details_never_become_public_metadata(monkeypatch, wav_bytes, body):
    online(monkeypatch)
    response = Response(body, status=401)
    provider(monkeypatch, response)
    with pytest.raises(voice.VoiceProviderError) as error:
        voice.transcribe_with_evidence(wav_bytes)
    exposed = str(error.value) + json.dumps(error.value.evidence)
    assert "private" not in exposed and "secret" not in exposed
    assert error.value.evidence["required_permission"] is None
    assert error.value.evidence["error_code"] in (None, "missing_permissions")
    assert response.closed


@pytest.mark.parametrize("declared,reads", [(True, 0), (False, 1)])
def test_http_error_json_has_a_separate_small_read_bound(monkeypatch, wav_bytes, declared, reads):
    online(monkeypatch)
    oversized = b"x" * (voice.MAX_ERROR_JSON_BYTES + 1)
    response = Response(oversized, status=401, length=len(oversized) if declared else None,
                        chunks=[oversized, b"must not read this next chunk"])
    provider(monkeypatch, response)
    with pytest.raises(voice.VoiceProviderError) as error:
        voice.transcribe_with_evidence(wav_bytes)
    assert response.reads == reads and response.closed
    assert error.value.evidence["error_code"] is None
    assert error.value.evidence["http_status"] == 401


@pytest.mark.parametrize("identifier", ["unit-test-key-never-send", "prefix-unit-test-key-never-send", "bad\nheader"])
def test_reflected_secret_or_invalid_provider_identifiers_are_redacted(monkeypatch, wav_bytes, identifier):
    online(monkeypatch)
    response = Response(json.dumps({"text": "ignore cable untwist", "model_id": identifier}).encode())
    response.headers["request-id"] = identifier
    provider(monkeypatch, response)
    _, receipt = voice.transcribe_with_evidence(wav_bytes)
    assert receipt["request_id"] is None and receipt["response_model"] is None
    assert "unit-test-key-never-send" not in json.dumps(receipt)


def test_accidentally_misconfigured_model_does_not_copy_key_into_receipt(monkeypatch, wav_bytes):
    online(monkeypatch)
    monkeypatch.setenv("ELEVENLABS_STT_MODEL", "unit-test-key-never-send")
    provider(monkeypatch, Response(b'{"text":"ignore cable untwist"}'))
    _, receipt = voice.transcribe_with_evidence(wav_bytes)
    assert receipt["requested_model"] is None
    assert "unit-test-key-never-send" not in json.dumps(receipt)


def test_transport_failure_receipt_records_attempt_without_inventing_http_status(monkeypatch, wav_bytes):
    online(monkeypatch)
    calls = provider(monkeypatch, requests.Timeout("private provider exception"))
    with pytest.raises(voice.VoiceProviderError) as error:
        voice.transcribe_with_evidence(wav_bytes)
    receipt = error.value.evidence
    assert len(calls) == 1 and receipt["attempted"] is True
    assert receipt["http_status"] is None and receipt["request_id"] is None
    assert receipt["success"] is False and receipt["output_sha256"] is None
    assert receipt["request_elapsed_ms"] >= 0 and receipt["elapsed_ms"] >= 0
    assert "private" not in str(error.value) + json.dumps(receipt)


def test_invalid_success_payload_preserves_actual_status_but_does_not_claim_success(monkeypatch, wav_bytes):
    online(monkeypatch)
    provider(monkeypatch, Response(b'{"text":""}'))
    with pytest.raises(voice.VoiceProviderError) as error:
        voice.transcribe_with_evidence(wav_bytes)
    assert error.value.evidence["http_status"] == 200
    assert error.value.evidence["success"] is False
    assert error.value.evidence["output_sha256"] is None


def test_non_utf8_speech_text_is_a_typed_input_error_without_network(monkeypatch):
    online(monkeypatch)
    with pytest.raises(voice.VoiceInputError) as error:
        voice.speak_with_evidence("\ud800")
    assert error.value.evidence["attempted"] is False


def test_non_utf8_transcript_is_a_typed_provider_error(monkeypatch, wav_bytes):
    online(monkeypatch)
    provider(monkeypatch, Response(b'{"text":"\\ud800"}'))
    with pytest.raises(voice.VoiceProviderError) as error:
        voice.transcribe_with_evidence(wav_bytes)
    assert error.value.evidence["http_status"] == 200
    assert error.value.evidence["success"] is False
