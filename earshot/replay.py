"""Replay interface contracts; no data is loaded by this scaffold."""

from collections import deque
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any


class Replayer:
    """A seekable, pausable event stream with a shared recent-event buffer."""

    def __init__(
        self,
        alarms_path: str | Path | None = None,
        scada_path: str | Path | None = None,
        *,
        speed: float | None = None,
        buffer_size: int | None = None,
    ) -> None:
        """Initialize replay from local processed dataset paths.

        Omitted arguments will use project configuration. The future
        implementation reads alarm data and optional SCADA, orders events by
        timestamp, and initializes playback state and a bounded buffer. Return
        None; this stub raises without loading data or allocating state.
        """
        raise NotImplementedError

    def stream(self) -> AsyncIterator[dict[str, Any]]:
        """Return an asynchronous iterator over timestamp-ordered events.

        No arguments are required. Each yielded event will contain ts,
        turbine_id, alarm_code, description, stopping and a signals mapping.
        Iteration will honor speed and pause state, advance the playhead and
        append emitted events to the shared buffer. The scaffold raises before
        returning an iterator; event generation belongs to a later phase.
        """
        raise NotImplementedError

    def seek(self, ts: str | datetime) -> None:
        """Move playback to the supplied parseable timestamp.

        Return None. The future implementation updates the playhead and resets
        stale replay-buffer state so examples reflect the new position; it
        does not change source data. This stub has no side effects.
        """
        raise NotImplementedError

    def pause(self) -> None:
        """Pause emission without modifying source records.

        Takes no arguments and returns None. The future implementation changes
        playback state only; this stub has no side effects.
        """
        raise NotImplementedError

    def resume(self) -> None:
        """Resume emission at the current playhead.

        Takes no arguments and returns None. The future implementation changes
        playback state only; this stub has no side effects.
        """
        raise NotImplementedError

    @property
    def buffer(self) -> deque[dict[str, Any]]:
        """Expose the bounded recent-event deque for policy re-scoring.

        Takes no arguments and returns the live shared buffer, not a copy.
        Reading the property will not emit events or mutate the buffer. This
        stub raises because replay storage has not been implemented.
        """
        raise NotImplementedError
