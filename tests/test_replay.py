"""Replay validation against unchanged real source rows, never invented events."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest

from earshot.config import CONFIG
import earshot.replay as module
from earshot.replay import Replayer


def _hash(path):
    digest = sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture(scope='module')
def real_rows():
    alarm_path = CONFIG.data.processed_dir / 'alarms.parquet'
    sensor_path = CONFIG.data.processed_dir / 'scada.parquet'
    if not alarm_path.exists() or not sensor_path.exists():
        pytest.skip('Real processed dataset is required')
    hashes = [_hash(alarm_path), _hash(sensor_path)]
    alarms = pd.read_parquet(alarm_path)
    simultaneous = alarms[alarms.ts.dt.minute.mod(10).eq(0) & alarms.ts.dt.second.eq(0)].head(1)
    duplicate = alarms[alarms.duplicated(['ts', 'turbine_id', 'alarm_code'], keep=False)].head(6)
    selected = pd.concat([alarms.head(12), simultaneous, duplicate]).sort_values('ts', kind='stable')
    source = ds.dataset(sensor_path, format='parquet')
    predicate = ((ds.field('ts') < datetime(2025, 1, 1, 0, 21))
                 | (ds.field('ts') == simultaneous.iloc[0].ts.to_pydatetime()))
    sensors = source.to_table(filter=predicate).to_pandas()
    yield {'alarms': selected, 'sensors': sensors, 'all_alarms': alarms,
           'alarm_path': alarm_path, 'sensor_path': sensor_path}
    assert [_hash(alarm_path), _hash(sensor_path)] == hashes


@pytest.fixture
def sources(real_rows, tmp_path):
    alarms = tmp_path / 'alarms.parquet'
    scada = tmp_path / 'scada.parquet'
    # Reordering real rows makes chronological sorting necessary. Small row
    # groups also exercise snapshots that cross physical storage boundaries.
    real_rows['alarms'].iloc[::-1].to_parquet(alarms, index=False, row_group_size=3)
    real_rows['sensors'].iloc[::-1].to_parquet(scada, index=False, row_group_size=1000)
    return alarms, scada


async def _collect(replayer):
    try:
        return [event async for event in replayer.stream()]
    finally:
        await replayer.aclose()


def _alarm_tuple(event):
    return (pd.Timestamp(event['ts']), event['turbine_id'], event['alarm_code'],
            event['description'], event['stopping'])


def _sensor_rows(events):
    return sorted((pd.Timestamp(event['ts']), event['turbine_id'], signal, value)
                  for event in events if event['alarm_code'] is None
                  for signal, value in event['signals'].items())


def test_real_sources_merge_chronologically_without_losing_rows(real_rows, sources):
    replay = Replayer(*sources, speed=1e12, buffer_size=3)
    events = asyncio.run(_collect(replay))
    assert [event['ts'] for event in events] == sorted(event['ts'] for event in events)
    alarm_events = [event for event in events if event['alarm_code'] is not None]
    expected_alarms = Counter(tuple(row) for row in real_rows['alarms'].itertuples(index=False, name=None))
    assert Counter(map(_alarm_tuple, alarm_events)) == expected_alarms
    sensors = real_rows['sensors']
    expected_sensors = sorted((row.ts, str(row.turbine_id), str(row.signal),
                               None if pd.isna(row.value) else float(row.value))
                              for row in sensors.itertuples(index=False))
    assert _sensor_rows(events) == expected_sensors
    assert all(set(event) == {'ts', 'turbine_id', 'alarm_code', 'description', 'stopping', 'signals'}
               for event in events)
    assert all(not event['signals'] for event in alarm_events)
    assert any(value is None for event in events for value in event['signals'].values())
    json.dumps(events, allow_nan=False)
    assert replay.buffer is replay.buffer
    assert list(replay.buffer) == events[-3:]
    assert replay.buffer[-1] is events[-1]
    assert replay.exhausted and replay.position == events[-1]['ts']
    assert replay.source_counts == {'alarms': len(real_rows['alarms']), 'scada_readings': len(sensors)}
    same = real_rows['alarms'].loc[real_rows['alarms'].ts.dt.minute.mod(10).eq(0)
                                 & real_rows['alarms'].ts.dt.second.eq(0)].iloc[0].ts.isoformat()
    simultaneous = [event for event in events if event['ts'] == same]
    assert simultaneous[0]['alarm_code'] is None
    assert simultaneous[-1]['alarm_code'] is not None


def test_full_real_alarm_count_and_duplicate_events_preserved(real_rows):
    replay = Replayer(real_rows['alarm_path'], include_scada=False, speed=1e12)
    events = asyncio.run(_collect(replay))
    assert len(events) == len(real_rows['all_alarms']) == 68891
    assert Counter(map(_alarm_tuple, events)) == Counter(
        tuple(row) for row in real_rows['all_alarms'].itertuples(index=False, name=None))
    assert replay.source_counts['scada_readings'] == 0
    assert len(replay.buffer) == CONFIG.replay.buffer_events


def test_actual_first_day_groups_185_signals_without_skipping_measurements(real_rows):
    replay = Replayer(real_rows['alarm_path'], real_rows['sensor_path'], speed=1e12)
    start = pd.Timestamp('2025-01-01').value
    events = list(replay._window_events(start, start + module._DAY_NS))
    sensors = [event for event in events if event['alarm_code'] is None]
    source = ds.dataset(real_rows['sensor_path'], format='parquet')
    expected = source.count_rows(filter=ds.field('ts') < datetime(2025, 1, 2))
    assert sum(len(event['signals']) for event in sensors) == expected == 559440
    assert len(sensors) == 3024
    assert all(len(event['signals']) == 185 for event in sensors)
    assert all(events[index]['ts'] <= events[index + 1]['ts'] for index in range(len(events) - 1))


def test_recursive_bounded_slices_preserve_all_values(real_rows, sources, monkeypatch):
    monkeypatch.setattr(module, '_MAX_SLICE_ROWS', 4000)
    replay = Replayer(*sources, speed=1e12)
    events = asyncio.run(_collect(replay))
    assert sum(len(event['signals']) for event in events) == len(real_rows['sensors'])
    assert sum(event['alarm_code'] is not None for event in events) == len(real_rows['alarms'])


def test_consumer_restart_resumes_same_cursor(sources):
    async def run():
        replay = Replayer(*sources, include_scada=False, speed=1e12)
        stream = replay.stream()
        first = await anext(stream)
        await stream.aclose()
        second_stream = replay.stream()
        second = await anext(second_stream)
        await second_stream.aclose()
        await replay.aclose()
        assert first['ts'] < second['ts']
        assert list(replay.buffer) == [first, second]
    asyncio.run(run())


def test_single_consumer_guard(sources):
    async def run():
        replay = Replayer(*sources, include_scada=False, speed=1e12)
        stream = replay.stream()
        await anext(stream)
        other = replay.stream()
        with pytest.raises(RuntimeError, match='one active'):
            await anext(other)
        await stream.aclose()
        await replay.aclose()
    asyncio.run(run())


def test_pause_seek_resume_interrupts_old_wait(sources, real_rows):
    async def run():
        replay = Replayer(*sources, include_scada=False, speed=1)
        stream = replay.stream()
        await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.01)
        replay.pause()
        assert replay.paused
        target = real_rows['alarms'].iloc[-1].ts
        replay.seek(target.isoformat())
        assert replay.generation == 1 and not replay.buffer
        await asyncio.sleep(0.02)
        assert not pending.done()
        replay.resume()
        event = await asyncio.wait_for(pending, timeout=1)
        assert event['ts'] == target.isoformat()
        assert replay.position == event['ts'] and list(replay.buffer) == [event]
        await stream.aclose()
        await replay.aclose()
    asyncio.run(run())


def test_speed_change_interrupts_wait(sources):
    async def run():
        replay = Replayer(*sources, include_scada=False, speed=1)
        stream = replay.stream()
        first = await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.01)
        assert not pending.done()
        replay.speed = 1e12
        second = await asyncio.wait_for(pending, timeout=1)
        assert second['ts'] > first['ts']
        await stream.aclose()
        await replay.aclose()
    asyncio.run(run())


def test_wall_clock_delay_is_simulated_delta_divided_by_speed(sources, monkeypatch):
    clock = [1_000_000.0]
    waits = []
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    async def elapsed(awaitable, timeout):
        awaitable.close()
        waits.append(timeout)
        clock[0] += timeout + 1e-6
        raise TimeoutError
    monkeypatch.setattr(module.asyncio, 'wait_for', elapsed)
    async def run():
        replay = Replayer(*sources, include_scada=False, speed=600)
        stream = replay.stream()
        first, second = await anext(stream), await anext(stream)
        expected = (pd.Timestamp(second['ts']) - pd.Timestamp(first['ts'])).total_seconds() / 600
        assert waits == pytest.approx([expected], abs=1e-6)
        await stream.aclose()
        await replay.aclose()
    asyncio.run(run())


def test_cancel_pending_wait_does_not_skip_event(sources, real_rows):
    async def run():
        replay = Replayer(*sources, include_scada=False, speed=1)
        stream = replay.stream()
        await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.01)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await stream.aclose()
        replay.speed = 1e12
        resumed = replay.stream()
        second = await anext(resumed)
        assert second['ts'] == real_rows['alarms'].iloc[1].ts.isoformat()
        await resumed.aclose()
        await replay.aclose()
    asyncio.run(run())


def test_cancel_inflight_worker_preserves_batch_and_shared_buffer(sources, monkeypatch, real_rows):
    entered, release = threading.Event(), threading.Event()
    original = module._next_batch
    def delayed(iterator):
        entered.set()
        assert release.wait(3)
        return original(iterator)
    monkeypatch.setattr(module, '_next_batch', delayed)
    async def run():
        replay = Replayer(*sources, include_scada=False, speed=1e12)
        stream = replay.stream()
        pending = asyncio.create_task(anext(stream))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert replay.position is None and not replay.buffer
        release.set()
        await replay.aclose()
        assert replay.position is None and not replay.buffer
        resumed = replay.stream()
        first = await asyncio.wait_for(anext(resumed), timeout=1)
        assert first['ts'] == real_rows['alarms'].iloc[0].ts.isoformat()
        await resumed.aclose()
    asyncio.run(run())


def test_seek_during_worker_read_discards_stale_generation(sources, monkeypatch, real_rows):
    entered, release = threading.Event(), threading.Event()
    original = module._next_batch
    calls = [0]
    def delayed_once(iterator):
        calls[0] += 1
        if calls[0] == 1:
            entered.set()
            assert release.wait(3)
        return original(iterator)
    monkeypatch.setattr(module, '_next_batch', delayed_once)
    async def run():
        replay = Replayer(*sources, include_scada=False, speed=1e12)
        stream = replay.stream()
        pending = asyncio.create_task(anext(stream))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        target = real_rows['alarms'].iloc[-1].ts.isoformat()
        replay.seek(target)
        release.set()
        event = await asyncio.wait_for(pending, timeout=1)
        assert event['ts'] == target
        assert list(replay.buffer) == [event] and replay.generation == 1
        await stream.aclose()
        await replay.aclose()
    asyncio.run(run())


def test_seek_after_exhaustion_and_aware_timestamp(sources, real_rows):
    replay = Replayer(*sources, include_scada=False, speed=1e12)
    asyncio.run(_collect(replay))
    target = real_rows['alarms'].iloc[-1].ts
    replay.seek(target.tz_localize('UTC').isoformat())
    assert not replay.exhausted and not replay.buffer
    events = asyncio.run(_collect(replay))
    assert events and all(event['ts'] == target.isoformat() for event in events)
    replay.seek((target + pd.Timedelta(days=1)).isoformat())
    assert asyncio.run(_collect(replay)) == []
    assert replay.exhausted


@pytest.mark.parametrize('kwargs', [
    {'speed': 0}, {'speed': -1}, {'speed': True}, {'speed': float('nan')},
    {'speed': float('inf')}, {'buffer_size': 0}, {'buffer_size': True},
    {'buffer_size': 1.5}, {'include_scada': 1},
])
def test_invalid_configuration(sources, kwargs):
    with pytest.raises(ValueError):
        Replayer(*sources, **kwargs)


def test_invalid_seek_is_atomic(sources):
    replay = Replayer(*sources, include_scada=False)
    for value in ['NaT', 'not-a-date', 123]:
        with pytest.raises(ValueError):
            replay.seek(value)
    assert replay.generation == 0 and replay.position is None


def test_optional_scada_absent_but_explicit_missing_is_error(sources, tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'CONFIG', SimpleNamespace(
        data=SimpleNamespace(processed_dir=tmp_path / 'missing'), replay=CONFIG.replay))
    replay = Replayer(sources[0])
    assert replay.source_counts['scada_readings'] == 0
    with pytest.raises(FileNotFoundError):
        Replayer(sources[0], tmp_path / 'absent.parquet')
    with pytest.raises(FileNotFoundError):
        Replayer(tmp_path / 'absent.parquet', include_scada=False)


def test_empty_and_missing_schema_rejected(real_rows, tmp_path):
    empty = tmp_path / 'empty.parquet'
    real_rows['alarms'].head(0).to_parquet(empty, index=False)
    with pytest.raises(ValueError, match='at least one'):
        Replayer(empty, include_scada=False)
    malformed = tmp_path / 'malformed.parquet'
    real_rows['alarms'].drop(columns=['alarm_code']).to_parquet(malformed, index=False)
    with pytest.raises(ValueError, match='requires columns'):
        Replayer(malformed, include_scada=False)


def test_missing_metadata_statistics_fallback(real_rows, tmp_path):
    path = tmp_path / 'without_stats.parquet'
    pq.write_table(pa.Table.from_pandas(real_rows['alarms'], preserve_index=False), path, write_statistics=False)
    replay = Replayer(path, include_scada=False, speed=1e12)
    assert len(asyncio.run(_collect(replay))) == len(real_rows['alarms'])
