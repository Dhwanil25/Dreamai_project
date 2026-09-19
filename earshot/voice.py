"""Voice I/O contracts; importing this module creates no provider clients."""


class OfflineError(RuntimeError):
    """A requested voice-provider operation is unavailable in offline mode."""


def transcribe(audio_bytes: bytes) -> str:
    """Return recognized operator text from encoded audio bytes.

    The future implementation defines supported audio formats and checks
    runtime offline controls before any optional provider I/O. It does not
    interpret the transcript as a rule or update policy. This scaffold always
    raises without recording, storing, sending or decoding audio.
    """
    raise NotImplementedError


def speak(text: str) -> bytes:
    """Return encoded speech audio for a confirmation string.

    The future implementation honors offline controls before optional
    provider I/O and may read a local confirmation cache. It does not play
    audio or change policy. This scaffold always raises without network,
    filesystem or audio-device access.
    """
    raise NotImplementedError
