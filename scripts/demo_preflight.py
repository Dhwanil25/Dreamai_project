"""Validate local demo prerequisites and wait for this launcher's server only."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earshot.config import CONFIG, PROJECT_ROOT
import pandas as pd
import pyarrow.parquet as pq


def _run(script: str) -> None:
    print(f'Preparing local inputs: {script}', flush=True)
    result = subprocess.run([sys.executable, str(PROJECT_ROOT / 'scripts' / script)],
                            cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-3000:]
        raise RuntimeError(f'{script} failed: {detail}')


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate(*, check_port: bool = True) -> dict:
    if check_port:
        with socket.socket() as listener:
            # Match Uvicorn's reuse behavior so recently closed connections do
            # not make an otherwise free port fail immediately after restart.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(('127.0.0.1', 8000))
            except OSError as error:
                raise RuntimeError('Port 8000 is unavailable. Stop the server you own, then retry; this launcher never kills other processes.') from error
    paths = {name: CONFIG.data.processed_dir / f'{name}.parquet' for name in ('alarms', 'scada')}
    if any(not path.is_file() for path in paths.values()):
        print('Processed Parquet is missing. Rebuilding from supplied raw data; first ingestion can exceed 60 seconds.', flush=True)
        _run('explore_schema.py')
        _run('build_dataset.py')
    required = {'alarms': {'ts', 'turbine_id', 'alarm_code', 'description', 'stopping'},
                'scada': {'ts', 'turbine_id', 'signal', 'value'}}
    counts = {}
    for name, path in paths.items():
        try:
            parquet = pq.ParquetFile(path)
        except Exception as error:
            raise RuntimeError(f'{path.name} is unreadable. Rebuild the local dataset before launching.') from error
        if parquet.metadata.num_rows == 0:
            raise ValueError(f'{path.name} is empty. Rebuild the local dataset before launching.')
        if not required[name].issubset(parquet.schema_arrow.names):
            raise ValueError(f'{path.name} has the wrong schema. Rebuild the local dataset before launching.')
        counts[name] = parquet.metadata.num_rows
    alarms_hash = _digest(paths['alarms'])
    metadata_hash = _digest(CONFIG.data.raw_dir / 'Hill_of_Towie_turbine_metadata.csv')
    descriptions_hash = _digest(CONFIG.data.raw_dir / 'Hill_of_Towie_alarms_description.csv')
    baseline_path = PROJECT_ROOT / 'demo' / 'baseline_stats.json'
    baseline = _json(baseline_path) if baseline_path.is_file() else {}
    if (baseline.get('source', {}).get('alarms_sha256') != alarms_hash
            or baseline.get('source', {}).get('metadata_sha256') != metadata_hash):
        _run('compute_baseline.py')
    vocabulary_path = PROJECT_ROOT / 'demo' / 'vocabulary.json'
    vocabulary = _json(vocabulary_path) if vocabulary_path.is_file() else {}
    receipt = vocabulary.get('source', {})
    if (receipt.get('alarms_sha256') != alarms_hash or receipt.get('metadata_sha256') != metadata_hash
            or receipt.get('descriptions_sha256') != descriptions_hash):
        _run('build_vocabulary.py')
    scenario = _json(PROJECT_ROOT / 'demo' / 'scenario.json')
    if scenario.get('source', {}).get('alarms_sha256') != alarms_hash:
        raise ValueError('Demo scenario does not match this alarm dataset; choose and verify new real demo timestamps.')
    alarms = pd.read_parquet(paths['alarms'])
    beats = {beat['id']: beat for beat in scenario['beats']}
    start = pd.Timestamp(scenario['replay']['seek_ts'])
    end = pd.Timestamp(scenario['replay']['warm_until_ts'])
    window = alarms[(alarms.ts > start) & (alarms.ts <= end)]
    if len(window) != beats['pain']['trailing_window']['alarm_records']:
        raise ValueError('The scenario alarm-rate evidence no longer matches the source.')
    for event in (beats['proof']['suppressed_event'], beats['proof']['untaught_stopping_event'],
                  beats['unplug']['later_matching_event']):
        match = alarms[alarms.ts.eq(pd.Timestamp(event['ts']))
                       & alarms.turbine_id.eq(event['turbine_id']) & alarms.alarm_code.eq(event['alarm_code'])]
        if len(match) != 1 or int(match.iloc[0].stopping) != event['stopping']:
            raise ValueError('A configured demo proof event no longer matches the source.')
    result = {'rows': counts, 'warm_alarm_records': len(window),
              'warm_until': end.isoformat(), 'source_kind': 'recorded_dataset_replay'}
    print('Demo preflight passed: ' + json.dumps(result), flush=True)
    return result


def wait_for_server(pid: int, timeout: float) -> dict:
    start = time.monotonic()
    latest = 'Server has not answered yet'
    while time.monotonic() - start < timeout:
        try:
            os.kill(pid, 0)
        except ProcessLookupError as error:
            raise RuntimeError('The owned server exited before becoming ready; inspect its output above.') from error
        try:
            with urlopen('http://127.0.0.1:8000/health', timeout=1) as response:
                health = json.load(response)
            if health.get('ok') and health.get('replay_position'):
                elapsed = time.monotonic() - start
                launched = float(os.environ.get('EARSHOT_LAUNCH_STARTED', time.time() - elapsed))
                total = time.time() - launched
                receipt = {'total_cold_start_seconds': total, 'server_wait_seconds': elapsed,
                           'replay_position': health['replay_position'], 'health_ok': True,
                           'timing_scope': 'After virtualenv activation, including preflight, server initialization and real-data buffer prewarm'}
                receipt_path = CONFIG.data.processed_dir / 'phase10_cold_start.json'
                receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
                print(f'Demo-ready: {total:.3f}s total cold start ({elapsed:.3f}s server wait): http://127.0.0.1:8000/', flush=True)
                print('Replay is prewarmed and paused. Open the console; use Resume after teaching. Ctrl+C stops only this launcher\'s server.', flush=True)
                return health
            latest = health.get('message') or health.get('error') or str(health)
            if health.get('status') in ('unavailable', 'degraded'):
                raise RuntimeError(f'Server started but local replay is unavailable: {latest}')
        except (URLError, HTTPError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f'Demo was not ready within {timeout:g}s: {latest}')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait-pid', type=int)
    parser.add_argument('--timeout', type=float, default=55)
    parser.add_argument('--no-port-check', action='store_true', help='Validate files only; useful for a running server')
    args = parser.parse_args()
    try:
        if args.wait_pid is not None:
            wait_for_server(args.wait_pid, args.timeout)
        else:
            validate(check_port=not args.no_port_check)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f'Demo cannot start: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
