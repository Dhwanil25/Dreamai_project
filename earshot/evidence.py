"""Auditable local proof connecting displayed observations to supplied sources.

All selected alarm records are compared with original ZIP CSV rows. SCADA uses
one bounded real snapshot cross-checked against the two original wide tables;
its quarter-wide count comes from Parquet metadata, not a fabricated stream.
No network, credentials, operator recordings, or learned state are read.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from earshot.config import CONFIG, PROJECT_ROOT

_CHUNK_ROWS = 4096
_DESCRIPTION_FILE = 'Hill_of_Towie_alarms_description.csv'
_METADATA_FILE = 'Hill_of_Towie_turbine_metadata.csv'


def _digest(path: Path) -> str:
    result = sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def _path(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else path.name


def _iso(value) -> str:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError('Evidence contains a missing timestamp')
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert('UTC').tz_localize(None)
    return timestamp.isoformat()


def _key(timestamp, station, code) -> tuple[str, str, int]:
    return _iso(timestamp), str(station), int(code)


def _event(row: pd.Series | dict) -> dict:
    return {'ts': _iso(row['ts']), 'turbine_id': str(row['turbine_id']),
            'alarm_code': int(row['alarm_code']), 'description': str(row['description']),
            'stopping': int(row['stopping'])}


def _integers(values: pd.Series, label: str) -> pd.Series:
    numeric = pd.to_numeric(values, errors='raise')
    if numeric.isna().any() or (numeric % 1 != 0).any():
        raise ValueError(f'{label} must contain non-null integer identifiers')
    return numeric.astype('int64')


def _member(zipped: ZipFile, table: str, month: int) -> str:
    filename = f'{table}_{CONFIG.data.year}_{month:02d}.csv'
    matches = [name for name in zipped.namelist() if Path(name).name == filename]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one source member {filename}')
    return matches[0]


def _raw_alarms(zipped: ZipFile, wanted: set[tuple]) -> tuple[Counter, dict, list[dict]]:
    counts, matches, receipts = Counter(), {}, []
    for month in sorted(CONFIG.data.months):
        member = _member(zipped, CONFIG.schema.alarm_table_prefix, month)
        digest = sha256()
        with zipped.open(member) as handle:
            for data in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(data)
        offset = 0
        with zipped.open(member) as handle:
            for chunk in pd.read_csv(handle, encoding='utf-8-sig', dtype=str,
                                     keep_default_na=False, chunksize=_CHUNK_ROWS):
                required = ['TimeOn', 'TimeOff', 'StationNr', 'Alarmcode']
                if not set(required).issubset(chunk.columns):
                    raise ValueError(f'{member} is missing original alarm columns')
                times = pd.to_datetime(chunk.TimeOn, utc=True, format='ISO8601', errors='raise').dt.tz_localize(None)
                if times.isna().any():
                    raise ValueError(f'{member} contains missing alarm timestamps')
                stations = _integers(chunk.StationNr, 'StationNr').astype(str)
                codes = _integers(chunk.Alarmcode, 'Alarmcode')
                for number, (timestamp, station, code) in enumerate(zip(times, stations, codes)):
                    key = _key(timestamp, station, code)
                    counts[key] += 1
                    if key in wanted and key not in matches:
                        matches[key] = {'member': member, 'csv_data_row_1_based': offset + number + 1,
                                        'raw_fields': {name: str(chunk.iloc[number][name]) for name in required}}
                offset += len(chunk)
        receipts.append({'member': member, 'rows': offset, 'sha256': digest.hexdigest()})
    return counts, matches, receipts


def _number(value) -> float | None:
    if pd.isna(value):
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('A source SCADA measurement is nonfinite')
    return result


def _snapshot(zipped: ZipFile, scada_path: Path, timestamp: pd.Timestamp, station: str) -> dict:
    dataset = ds.dataset(scada_path, format='parquet')
    predicate = (ds.field('ts') == timestamp.to_pydatetime()) & (ds.field('turbine_id') == station)
    batches, count = [], 0
    for batch in dataset.scanner(columns=['ts', 'turbine_id', 'signal', 'value'], filter=predicate,
                                 batch_size=_CHUNK_ROWS, batch_readahead=1, fragment_readahead=1,
                                 use_threads=False).to_batches():
        count += batch.num_rows
        if count > 10000:
            raise ValueError('Selected source snapshot exceeds the bounded evidence limit')
        if batch.num_rows:
            batches.append(batch.to_pandas())
    if not batches:
        raise ValueError('The selected SCADA snapshot is missing')
    frame = pd.concat(batches, ignore_index=True)
    if frame.signal.duplicated().any():
        raise ValueError('Selected SCADA snapshot has duplicate signal identities')
    processed = {str(row.signal): _number(row.value) for row in frame.itertuples(index=False)}
    raw_values, members = {}, []
    for table in ('tblSCTurTemp', 'tblSCTurGrid'):
        member = _member(zipped, table, timestamp.month)
        with zipped.open(member) as handle:
            header = pd.read_csv(BytesIO(handle.readline()), encoding='utf-8-sig', nrows=0).columns.tolist()
        signal_names = [name for name in header if name not in
                        {CONFIG.schema.scada_time_col, CONFIG.schema.scada_turbine_col,
                         *CONFIG.schema.scada_exclude_signals}]
        offset, matched = 0, []
        with zipped.open(member) as handle:
            for chunk in pd.read_csv(handle, encoding='utf-8-sig', chunksize=_CHUNK_ROWS):
                times = pd.to_datetime(chunk[CONFIG.schema.scada_time_col], utc=True,
                                       format='ISO8601', errors='raise').dt.tz_localize(None)
                stations = _integers(chunk[CONFIG.schema.scada_turbine_col], 'StationId').astype(str)
                mask = times.eq(timestamp) & stations.eq(station)
                for index in range(len(chunk)):
                    if bool(mask.iloc[index]):
                        matched.append((offset + index + 1, chunk.iloc[index]))
                offset += len(chunk)
        if len(matched) != 1:
            raise ValueError(f'{member}: expected exactly one original snapshot row; found {len(matched)}')
        line, row = matched[0]
        for signal in signal_names:
            if signal in raw_values:
                raise ValueError('Original SCADA tables have overlapping signal names')
            raw_values[signal] = _number(row[signal])
        members.append({'member': member, 'csv_data_row_1_based': line,
                        'source_timestamp': str(row[CONFIG.schema.scada_time_col]),
                        'source_station_id': int(row[CONFIG.schema.scada_turbine_col]),
                        'verified_signal_count': len(signal_names)})
    if processed != raw_values:
        changed = sorted(name for name in set(processed) | set(raw_values)
                         if name not in processed or name not in raw_values or processed[name] != raw_values[name])
        raise ValueError(f'SCADA snapshot differs from original source cells: {changed[:5]}')
    encoded = json.dumps(dict(sorted(processed.items())), sort_keys=True, separators=(',', ':'), allow_nan=False)
    names = sorted(processed)
    preview_names = sorted(set(names[:6] + names[-6:] + [name for name in names if processed[name] is None][:2]))
    return {'ts': timestamp.isoformat(), 'turbine_id': station,
            'verified_reading_count': len(processed),
            'null_reading_count': sum(value is None for value in processed.values()),
            'canonical_signals_sha256': sha256(encoded.encode()).hexdigest(),
            'preview': {name: processed[name] for name in preview_names},
            'preview_note': 'A bounded selection of actual verified signal cells; null remains null. All snapshot signals were compared.',
            'raw_sources': members, 'all_snapshot_cells_match_raw': True}


def build_source_evidence(*, raw_dir: Path | None = None, processed_dir: Path | None = None,
                          scenario_path: Path | None = None, baseline_path: Path | None = None) -> dict:
    """Build a deterministic receipt from local sources; raise on disagreement.

    Optional path overrides support isolated verification of real-data copies.
    This function never publishes, changes data, loads keys, or applies rules.
    The SCADA snapshot proof is a sample, not a full raw-quarter reconciliation.
    """
    raw_dir = Path(raw_dir) if raw_dir is not None else CONFIG.data.raw_dir
    processed_dir = Path(processed_dir) if processed_dir is not None else CONFIG.data.processed_dir
    scenario_path = Path(scenario_path) if scenario_path is not None else PROJECT_ROOT / 'demo/scenario.json'
    baseline_path = Path(baseline_path) if baseline_path is not None else PROJECT_ROOT / 'demo/baseline_stats.json'
    paths = {'raw_archive': raw_dir / f'{CONFIG.data.year}.zip',
             'alarms_parquet': processed_dir / 'alarms.parquet',
             'scada_parquet': processed_dir / 'scada.parquet',
             'alarm_descriptions': raw_dir / _DESCRIPTION_FILE,
             'turbine_metadata': raw_dir / _METADATA_FILE}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(f'Source evidence requires {path.name}')
    scenario = json.loads(scenario_path.read_text(encoding='utf-8'))
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    alarms = pd.read_parquet(paths['alarms_parquet'])
    required = {'ts', 'turbine_id', 'alarm_code', 'description', 'stopping'}
    if alarms.empty or not required.issubset(alarms.columns):
        raise ValueError('Source evidence requires nonempty alarm data with its original schema')
    _integers(alarms.alarm_code, 'Processed alarm codes')
    _integers(alarms.stopping, 'Processed stopping values')
    if (alarms.turbine_id.isna().any()
            or not alarms.turbine_id.map(lambda value: isinstance(value, str) and bool(value.strip())).all()):
        raise ValueError('Processed station identities must be nonblank strings')
    alarms = alarms.sort_values(['ts', 'turbine_id', 'alarm_code'], kind='stable')
    metadata = pd.read_csv(paths['turbine_metadata'], encoding='utf-8-sig')
    ids = _integers(metadata['Station ID'], 'Station ID').astype(str)
    if ids.duplicated().any():
        raise ValueError('Metadata station IDs are not unique')
    labels = dict(zip(ids, metadata['Turbine Name'].astype(str)))
    descriptions = pd.read_csv(paths['alarm_descriptions'], encoding='utf-8-sig')
    if descriptions['Alarm Code'].duplicated().any():
        raise ValueError('Description lookup codes are not unique')
    lookup = {int(row['Alarm Code']): (str(row['Description']).strip(), int(row['Stopping']))
              for _, row in descriptions.iterrows()}
    for row in alarms.itertuples(index=False):
        expected = lookup.get(int(row.alarm_code), ('(undocumented)', -1))
        if (row.description, int(row.stopping)) != expected:
            raise ValueError('Processed alarm description/stopping differs from the original lookup')
    alarm_hash = _digest(paths['alarms_parquet'])
    metadata_hash = _digest(paths['turbine_metadata'])
    if baseline.get('source', {}).get('alarms_sha256') != alarm_hash or scenario.get('source', {}).get('alarms_sha256') != alarm_hash:
        raise ValueError('Baseline or scenario alarm source hash does not match the current Parquet')
    if baseline.get('source', {}).get('metadata_sha256') != metadata_hash:
        raise ValueError('Baseline metadata source hash does not match the current CSV')
    start, end = alarms.ts.min(), alarms.ts.max()
    hours = (end - start).total_seconds() / 3600
    if hours <= 0:
        raise ValueError('Alarm evidence needs a positive observation duration')
    rate = len(alarms) / hours
    top10 = float(alarms.alarm_code.value_counts().head(10).sum() / len(alarms) * 100)
    if (baseline['counts']['record_count'] != len(alarms)
            or baseline['counts']['turbine_count'] != len(ids)
            or not math.isclose(float(baseline['rates']['site']), rate, rel_tol=1e-12)
            or not math.isclose(float(baseline['pareto']['top10_share_pct']), top10, rel_tol=1e-12)):
        raise ValueError('Published baseline differs from independently recomputed source metrics')
    beats = {beat['id']: beat for beat in scenario['beats']}
    window_start = pd.Timestamp(scenario['replay']['seek_ts'])
    window_end = pd.Timestamp(scenario['replay']['warm_until_ts'])
    window = alarms[(alarms.ts > window_start) & (alarms.ts <= window_end)]
    if window.empty or len(window) != beats['pain']['trailing_window']['alarm_records']:
        raise ValueError('Selected replay-hour count differs from original alarm records')
    targets = [('prewarm_first', _event(window.iloc[0])), ('prewarm_last', _event(window.iloc[-1]))]
    for name, event in [('recurrence', beats['proof']['suppressed_event']),
                        ('untouched_stopping', beats['proof']['untaught_stopping_event']),
                        ('later_recurrence', beats['unplug']['later_matching_event'])]:
        matches = alarms[alarms.ts.eq(pd.Timestamp(event['ts'])) & alarms.turbine_id.eq(event['turbine_id'])
                         & alarms.alarm_code.eq(event['alarm_code'])]
        if len(matches) != 1 or _event(matches.iloc[0]) != event:
            raise ValueError(f'Scenario event mismatch: {name}')
        targets.append((name, _event(matches.iloc[0])))
    wanted = {_key(event['ts'], event['turbine_id'], event['alarm_code']) for _, event in targets}
    processed_keys = Counter(_key(row.ts, row.turbine_id, row.alarm_code) for row in alarms.itertuples(index=False))
    with ZipFile(paths['raw_archive']) as zipped:
        raw_keys, raw_matches, members = _raw_alarms(zipped, wanted)
        if raw_keys != processed_keys:
            raise ValueError('Processed alarm records do not match original ZIP records and multiplicities')
        snapshot = _snapshot(zipped, paths['scada_parquet'], window_end.floor('10min'),
                             str(window.iloc[-1].turbine_id))
    evidence_rows = []
    for name, event in targets:
        key = _key(event['ts'], event['turbine_id'], event['alarm_code'])
        evidence_rows.append({'purpose': name, 'processed': event, 'turbine_label': labels.get(event['turbine_id']),
                              'original': raw_matches[key], 'original_occurrences': raw_keys[key],
                              'matches_original': True})
    receipts = {name: {'path': _path(path), 'bytes': path.stat().st_size,
                       'sha256': alarm_hash if name == 'alarms_parquet' else _digest(path)}
                for name, path in paths.items()}
    scada_metadata = pq.ParquetFile(paths['scada_parquet']).metadata
    result = {
        'schema_version': 1, 'status': 'verified', 'source_kind': 'recorded_dataset_replay',
        'live_industrial_connection': False, 'synthetic_sensor_or_alarm_records': False,
        'dataset': {'name': 'Hill of Towie wind farm open dataset',
                    'attribution': 'RES on behalf of TRIG; curators Alex Clerc and Elizabeth Lingkan',
                    'url': 'https://zenodo.org/records/22662930', 'license': 'CC-BY-4.0',
                    'selected_year': CONFIG.data.year, 'selected_months': sorted(CONFIG.data.months)},
        'files': receipts,
        'alarm_verification': {'all_processed_rows_match_original_zip': True,
                               'record_count': len(alarms), 'raw_members': members,
                               'duplicates_retained': int(alarms.duplicated(['ts', 'turbine_id', 'alarm_code']).sum()),
                               'description_and_stopping_lookup_matches': True, 'sample_rows': evidence_rows},
        'baseline_recomputed': {'first_alarm': _iso(start), 'last_alarm': _iso(end),
                                'observed_hours': hours, 'alarm_records': len(alarms),
                                'known_turbines': len(ids), 'site_alarms_per_hour': rate,
                                'top10_code_share_pct': top10, 'matches_published_baseline': True},
        'selected_window': {'start_exclusive': _iso(window_start), 'end_inclusive': _iso(window_end),
                            'alarm_records': len(window), 'alarms_per_hour': len(window) / ((window_end-window_start).total_seconds()/3600)},
        'scada_verification': {'parquet_reading_count': scada_metadata.num_rows,
                               'parquet_row_groups': scada_metadata.num_row_groups,
                               'sample_snapshot': snapshot,
                               'validation_scope': 'Quarter count from Parquet metadata; selected snapshot cells independently compared with original CSVs. No complete raw-quarter SCADA reconciliation is claimed.'},
        'interpretation': [
            'These are recorded dataset observations, not a live connection to a wind farm.',
            'No operator utterances, credentials, recordings, learned rules or model weights are included in this evidence.',
            'No sensor or alarm values are generated or imputed. Null source measurements remain null.',
            'Operator corrections are labels supplied during use; the source does not identify ground-truth nuisance alarms or detector accuracy.',
            'Stopping is supplied lookup metadata, not a verified dangerous-incident label.',
            'Ten-minute SCADA timestamps are UTC per dataset release; alarm-log timezone remains an unverified naive UTC assumption.',
        ],
    }
    if len(json.dumps(result, sort_keys=True, allow_nan=False).encode()) > 20000:
        raise ValueError('Source evidence exceeded its compact publication limit')
    return result
