"""Bounded local Parquet replay, preserving every alarm and sensor value.

The source SCADA file is signal-major, not chronological. Filter bounded time
slices before sorting, group simultaneous readings into snapshots, and merge
with alarms. Parquet work runs in a worker thread during asynchronous replay.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
import heapq
from itertools import islice
import math
from numbers import Integral, Real
from pathlib import Path
import time
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from earshot.config import CONFIG

_DAY_NS = 86_400_000_000_000
_MAX_SLICE_ROWS = 1_000_000
_SCAN_BATCH_ROWS = 65_536
_FETCH_EVENTS = 128
_ALARM_COLUMNS = ["ts", "turbine_id", "alarm_code", "description", "stopping"]
_SCADA_COLUMNS = ["ts", "turbine_id", "signal", "value"]


def _timestamp(value: str | datetime | pd.Timestamp) -> pd.Timestamp:
    """Use the project's naive UTC assumption without changing source dates."""
    if not isinstance(value, (str, datetime, pd.Timestamp)):
        raise ValueError("Replay timestamp must be an ISO string or datetime")
    try:
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            raise ValueError
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC").tz_localize(None)
        return timestamp.as_unit("ns")
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Replay timestamp must be a valid finite timestamp") from error


class _Source:
    """A read-only source with bounded scans and metadata time bounds."""

    def __init__(self, path: Path, columns: list[str]) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"Replay source is missing: {path.name}")
        self.path, self.columns = path, columns
        self.dataset = ds.dataset(path, format="parquet")
        if not set(columns).issubset(self.dataset.schema.names):
            raise ValueError(f"Replay {path.name} requires columns {columns}")
        timestamp_type = self.dataset.schema.field("ts").type
        if not pa.types.is_timestamp(timestamp_type) or timestamp_type.tz is not None:
            raise ValueError("Replay sources require naive timestamp columns")
        parquet = pq.ParquetFile(path)
        self.rows = parquet.metadata.num_rows
        minima, maxima = [], []
        index = parquet.schema_arrow.get_field_index("ts")
        complete = True
        for group_number in range(parquet.metadata.num_row_groups):
            stats = parquet.metadata.row_group(group_number).column(index).statistics
            if stats is None or not stats.has_min_max:
                complete = False
                break
            if stats.null_count:
                raise ValueError(f"Replay {path.name} contains missing timestamps")
            minima.append(pd.Timestamp(stats.min).value)
            maxima.append(pd.Timestamp(stats.max).value)
        if not complete:
            minima, maxima = [], []
            for batch in parquet.iter_batches(batch_size=_SCAN_BATCH_ROWS, columns=["ts"]):
                values = batch.column(0)
                if values.null_count:
                    raise ValueError(f"Replay {path.name} contains missing timestamps")
                bounds = pc.min_max(values).as_py()
                if bounds["min"] is not None:
                    minima.append(pd.Timestamp(bounds["min"]).value)
                    maxima.append(pd.Timestamp(bounds["max"]).value)
        self.start = min(minima) if minima else None
        self.end = max(maxima) if maxima else None

    def slice(self, start: int, end: int) -> pa.Table | None:
        """Return one bounded slice; None requests a smaller time interval."""
        time_type = self.dataset.schema.field("ts").type
        predicate = ((ds.field("ts") >= pa.scalar(pd.Timestamp(start), type=time_type))
                     & (ds.field("ts") < pa.scalar(pd.Timestamp(end), type=time_type)))
        scanner = self.dataset.scanner(
            columns=self.columns, filter=predicate, batch_size=_SCAN_BATCH_ROWS,
            batch_readahead=1, fragment_readahead=1, use_threads=False,
        )
        batches, count = [], 0
        for batch in scanner.to_batches():
            count += batch.num_rows
            if count > _MAX_SLICE_ROWS:
                return None
            if batch.num_rows:
                batches.append(batch)
        schema = pa.schema([self.dataset.schema.field(name) for name in self.columns])
        return pa.Table.from_batches(batches, schema=schema)


def _alarm_events(table: pa.Table) -> Iterator[dict[str, Any]]:
    frame = table.to_pandas().sort_values(["ts", "turbine_id", "alarm_code"], kind="stable")
    for row in frame.itertuples(index=False):
        if pd.isna(row.turbine_id) or pd.isna(row.alarm_code) or pd.isna(row.stopping):
            raise ValueError("Replay alarm identities and stopping values cannot be missing")
        yield {"ts": pd.Timestamp(row.ts).isoformat(), "turbine_id": str(row.turbine_id),
               "alarm_code": int(row.alarm_code), "description": str(row.description),
               "stopping": int(row.stopping), "signals": {}}


def _sensor_events(table: pa.Table) -> Iterator[dict[str, Any]]:
    frame = table.to_pandas().sort_values(["ts", "turbine_id", "signal"], kind="stable")
    if frame[["turbine_id", "signal"]].isna().any().any():
        raise ValueError("Replay sensor identities cannot be missing")
    for (timestamp, turbine), rows in frame.groupby(["ts", "turbine_id"], sort=False, observed=True):
        # Repeated measurements cannot fit in one mapping. Extra snapshots
        # preserve each occurrence rather than overwriting a source value.
        layers: list[dict[str, float | None]] = [{}]
        occurrences: dict[str, int] = {}
        for signal, raw in zip(rows.signal, rows.value):
            signal = str(signal)
            if not signal.strip():
                raise ValueError("Replay signal names must be nonblank")
            value = None if pd.isna(raw) else float(raw)
            if value is not None and not math.isfinite(value):
                raise ValueError("Replay measurements must be finite or missing")
            occurrence = occurrences.get(signal, 0)
            occurrences[signal] = occurrence + 1
            if occurrence == len(layers):
                layers.append({})
            layers[occurrence][signal] = value
        for signals in layers:
            yield {"ts": pd.Timestamp(timestamp).isoformat(), "turbine_id": str(turbine),
                   "alarm_code": None, "description": None, "stopping": None,
                   "signals": signals}


def _next_batch(iterator: Iterator[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(islice(iterator, _FETCH_EVENTS))


class Replayer:
    """One persistent chronological cursor, with one active stream consumer.

    Construction reads schemas and timestamp metadata; call it in a worker
    thread if metadata may be absent. SCADA is optional when the default file
    is absent; an explicitly supplied missing path is an error. Set
    include_scada=False for an explicit alarm-only replay. Sensor snapshots
    carry null alarm fields and preserve missing readings as None. They must
    not be counted as alarms by consumers. No measurement is carried forward.
    """

    def __init__(self, alarms_path: str | Path | None = None,
                 scada_path: str | Path | None = None, *, speed: float | None = None,
                 buffer_size: int | None = None, include_scada: bool = True) -> None:
        if not isinstance(include_scada, bool):
            raise ValueError("include_scada must be a boolean")
        size = CONFIG.replay.buffer_events if buffer_size is None else buffer_size
        if isinstance(size, bool) or not isinstance(size, Integral) or size <= 0:
            raise ValueError("buffer_size must be a positive integer")
        self._changed = asyncio.Event()
        self._speed = self._checked_speed(CONFIG.replay.speed if speed is None else speed)
        self._buffer: deque[dict[str, Any]] = deque(maxlen=int(size))
        self._alarms = _Source(Path(alarms_path) if alarms_path is not None
                               else CONFIG.data.processed_dir / "alarms.parquet", _ALARM_COLUMNS)
        if not self._alarms.rows:
            raise ValueError("Alarm replay source must contain at least one event")
        selected_scada = Path(scada_path) if scada_path is not None else CONFIG.data.processed_dir / "scada.parquet"
        self._scada = (_Source(selected_scada, _SCADA_COLUMNS)
                       if include_scada and (scada_path is not None or selected_scada.is_file()) else None)
        sources = [self._alarms] + ([self._scada] if self._scada is not None else [])
        starts = [source.start for source in sources if source.start is not None]
        ends = [source.end for source in sources if source.end is not None]
        self._start, self._end = min(starts), max(ends)
        self.source_counts = {"alarms": self._alarms.rows,
                              "scada_readings": self._scada.rows if self._scada else 0}
        self._position: str | None = None
        self._paused = self._exhausted = self._streaming = False
        self._generation = 0
        self._iterator = self._events(self._start)
        self._ready: deque[dict[str, Any]] = deque()
        self._fetch: tuple[int, asyncio.Task] | None = None
        self._pending: dict[str, Any] | None = None
        self._clock_sim: int | None = None
        self._clock_wall: float | None = None

    @staticmethod
    def _checked_speed(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value <= 0:
            raise ValueError("Replay speed must be finite and positive")
        return float(value)

    def _signal_change(self) -> None:
        self._changed.set()

    def _advance_clock(self) -> None:
        if self._clock_sim is not None and self._clock_wall is not None and not self._paused:
            now = time.monotonic()
            self._clock_sim += int((now - self._clock_wall) * self._speed * 1_000_000_000)
            self._clock_wall = now

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        selected = self._checked_speed(value)
        self._advance_clock()
        self._speed = selected
        self._signal_change()

    @property
    def buffer(self) -> deque[dict[str, Any]]:
        return self._buffer

    @property
    def position(self) -> str | None:
        return self._position

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    @property
    def generation(self) -> int:
        return self._generation

    def pause(self) -> None:
        if not self._paused:
            self._advance_clock()
            self._paused = True
            self._signal_change()

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            if self._clock_sim is not None:
                self._clock_wall = time.monotonic()
            self._signal_change()

    def seek(self, ts: str | datetime) -> None:
        target = _timestamp(ts)
        self._generation += 1
        self._iterator = self._events(target.value)
        self._ready.clear()
        self._pending = None
        self._buffer.clear()
        self._position = target.isoformat()
        self._exhausted = False
        self._clock_sim = self._clock_wall = None
        self._signal_change()

    def _window_events(self, start: int, end: int) -> Iterator[dict[str, Any]]:
        alarms = self._alarms.slice(start, end)
        scada = self._scada.slice(start, end) if self._scada is not None else None
        if alarms is None or (self._scada is not None and scada is None):
            # Partition time, never rows, so snapshot groups cannot be split
            # accidentally at an arbitrary batch boundary.
            del alarms, scada
            if end - start <= 1:
                raise ValueError("A single timestamp exceeds the bounded replay slice limit")
            middle = start + (end - start) // 2
            yield from self._window_events(start, middle)
            yield from self._window_events(middle, end)
            return
        sensors = _sensor_events(scada) if scada is not None else iter(())
        yield from heapq.merge(sensors, _alarm_events(alarms),
                              key=lambda event: (event["ts"], event["alarm_code"] is not None))

    def _events(self, start: int) -> Iterator[dict[str, Any]]:
        cursor = max(start, self._start)
        while cursor <= self._end:
            end = min((cursor // _DAY_NS + 1) * _DAY_NS, self._end + 1)
            yield from self._window_events(cursor, end)
            cursor = end

    async def _event(self) -> dict[str, Any] | None:
        while not self._ready:
            generation = self._generation
            if self._fetch is None or self._fetch[0] != generation:
                self._fetch = (generation, asyncio.create_task(asyncio.to_thread(_next_batch, self._iterator)))
            current = self._fetch
            # Cancellation must not discard a batch already being read by a
            # worker. Another stream call resumes the same in-flight fetch.
            batch = await asyncio.shield(current[1])
            if current == self._fetch:
                self._fetch = None
            if generation != self._generation:
                continue
            if not batch:
                return None
            self._ready.extend(batch)
        return self._ready.popleft()

    async def aclose(self) -> None:
        """Drain any in-flight read after the consuming task is cancelled.

        Worker threads only prepare private source batches, never change the
        exposed buffer or position. Closing the async stream first releases
        its single-consumer guard; awaiting this method drains outstanding I/O.
        """
        if self._fetch is not None:
            await asyncio.shield(self._fetch[1])

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        if self._streaming:
            raise RuntimeError("Replay supports only one active stream consumer")
        self._streaming = True
        try:
            while True:
                self._changed.clear()
                if self._paused:
                    await self._changed.wait()
                    continue
                generation = self._generation
                if self._pending is None:
                    self._pending = await self._event()
                    if generation != self._generation:
                        continue
                    if self._pending is None:
                        self._exhausted = True
                        return
                if self._paused:
                    continue
                event_time = _timestamp(self._pending["ts"]).value
                if self._clock_sim is None:
                    self._clock_sim, self._clock_wall = event_time, time.monotonic()
                simulated_now = self._clock_sim + int((time.monotonic() - self._clock_wall) * self._speed * 1_000_000_000)
                delay = (event_time - simulated_now) / (self._speed * 1_000_000_000)
                if delay > 0:
                    try:
                        await asyncio.wait_for(self._changed.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    continue
                event, self._pending = self._pending, None
                self._position = event["ts"]
                self._buffer.append(event)
                yield event
        finally:
            self._streaming = False
