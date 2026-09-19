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
    response = Response(b"provider secret or private transcript", status=status)
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


def test_cached_wav_works_offline_without_key_or_provider(monkeypatch, tmp_path, wav_bytes):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    text = "Got it. Tell me if I’m wrong."
    path = voice.cache_path(text)
    assert path.name == sha256(text.encode()).hexdigest() + ".wav"
    assert voice.cache_path("  " + text + "  ") == path
    assert voice.cached_audio(text) is None
    path.write_bytes(wav_bytes)

    assert voice.cached_audio(text) == wav_bytes
    assert voice.offline_enabled() and not voice.provider_available()
    with pytest.raises(voice.OfflineError):
        voice.speak(text)


@pytest.mark.parametrize("damage", ["not-wav", "truncated", "size", "symlink"])
def test_invalid_cached_audio_is_a_local_cache_miss(monkeypatch, tmp_path, wav_bytes, damage):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path / "cached")
    path = voice.cache_path("Got it.")
    path.parent.mkdir()
    if damage == "not-wav":
        path.write_bytes(b"not a WAV file")
    elif damage == "truncated":
        path.write_bytes(wav_bytes[:-100])
    elif damage == "size":
        path.write_bytes(wav_bytes)
        monkeypatch.setattr(voice, "MAX_AUDIO_BYTES", len(wav_bytes) - 1)
    else:
        target = tmp_path / "outside-cache.wav"
        target.write_bytes(wav_bytes)
        path.symlink_to(target)
    assert voice.cached_audio("Got it.") is None


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
