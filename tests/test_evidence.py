"""Source receipts use real records; corruptions are rejected in isolated copies."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from zipfile import ZipFile

import pandas as pd
import pyarrow.dataset as ds
import pytest

from earshot.config import CONFIG, PROJECT_ROOT
from earshot.evidence import build_source_evidence
import scripts.audit_source_evidence as audit


@pytest.fixture(scope='module')
def real_sources():
    paths = [CONFIG.data.raw_dir / '2025.zip', CONFIG.data.processed_dir / 'alarms.parquet',
             CONFIG.data.processed_dir / 'scada.parquet']
    if any(not path.exists() for path in paths):
        pytest.skip('Original supplied ZIP and real processed data are required')
    return {
        'raw_dir': CONFIG.data.raw_dir,
        'processed_dir': CONFIG.data.processed_dir,
        'scenario_path': PROJECT_ROOT / 'demo/scenario.json',
        'baseline_path': PROJECT_ROOT / 'demo/baseline_stats.json',
    }


@pytest.fixture(scope='module')
def verified(real_sources):
    return build_source_evidence(**real_sources)


def _copy_json(path, target):
    data = json.loads(path.read_text())
    target.write_text(json.dumps(data))
    return data


def test_full_alarm_source_and_independent_baseline_reconcile(verified):
    assert verified['status'] == 'verified'
    assert verified['source_kind'] == 'recorded_dataset_replay'
    assert verified['live_industrial_connection'] is False
    alarms = verified['alarm_verification']
    assert alarms['all_processed_rows_match_original_zip']
    assert alarms['description_and_stopping_lookup_matches']
    assert alarms['record_count'] == sum(row['rows'] for row in alarms['raw_members']) == 68891
    assert alarms['duplicates_retained'] == 348
    baseline = verified['baseline_recomputed']
    assert baseline['known_turbines'] == 21
    assert baseline['site_alarms_per_hour'] == pytest.approx(31.933676815856746)
    assert baseline['top10_code_share_pct'] == pytest.approx(92.02217996545266)
    assert verified['selected_window']['alarm_records'] == 276
    assert all(len(row['sha256']) == 64 for row in verified['files'].values())


def test_alarm_examples_trace_original_fields_and_metadata(verified):
    examples = verified['alarm_verification']['sample_rows']
    assert [row['purpose'] for row in examples] == [
        'prewarm_first', 'prewarm_last', 'recurrence', 'untouched_stopping', 'later_recurrence']
    for example in examples:
        original = example['original']
        source, event = original['raw_fields'], example['processed']
        assert original['member'] == 'tblAlarmLog_2025_02.csv'
        assert original['csv_data_row_1_based'] > 0
        assert pd.Timestamp(source['TimeOn']).isoformat() == event['ts']
        assert source['StationNr'] == event['turbine_id']
        assert int(source['Alarmcode']) == event['alarm_code']
        assert example['matches_original'] and example['original_occurrences'] >= 1
    assert examples[2]['turbine_label'] == 'T04'
    assert examples[3]['turbine_label'] == 'T21'
    assert examples[3]['processed']['stopping'] == 1


def test_scada_snapshot_cells_match_raw_with_nulls_preserved(verified):
    scada = verified['scada_verification']
    assert scada['parquet_reading_count'] == 50327215
    assert scada['parquet_row_groups'] == 53
    snapshot = scada['sample_snapshot']
    assert snapshot['ts'] == '2025-02-21T20:20:00'
    assert snapshot['turbine_id'] == '2304513'
    assert snapshot['verified_reading_count'] == 185
    assert snapshot['null_reading_count'] == 5
    assert sum(item['verified_signal_count'] for item in snapshot['raw_sources']) == 185
    assert all(item['csv_data_row_1_based'] == 63044 for item in snapshot['raw_sources'])
    assert snapshot['preview']['wtc_ActPower_endvalue'] is None
    assert snapshot['preview']['wtc_A1ExtTmp_min'] == 15.0
    assert 'No complete raw-quarter SCADA reconciliation is claimed' in scada['validation_scope']


def test_deterministic_compact_and_offline_without_private_utterances(real_sources, verified, tmp_path, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError('Source audit must not access the network')
    monkeypatch.setattr(socket.socket, 'connect', forbid)
    monkeypatch.setattr(socket, 'create_connection', forbid)
    scenario_path = tmp_path / 'scenario.json'
    scenario = _copy_json(real_sources['scenario_path'], scenario_path)
    scenario['beats'][1]['utterance'] = 'PRIVATE_OPERATOR_TEXT_MUST_NOT_BE_PUBLISHED'
    scenario_path.write_text(json.dumps(scenario))
    receipt = build_source_evidence(**{**real_sources, 'scenario_path': scenario_path})
    assert receipt == verified
    serialized = json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False)
    assert len(serialized.encode()) < 20000
    assert 'PRIVATE_OPERATOR_TEXT' not in serialized
    assert json.loads(serialized) == receipt


@pytest.mark.parametrize('change', ['count', 'rate', 'pareto', 'hash'])
def test_rejects_wrong_published_baseline(real_sources, tmp_path, change):
    path = tmp_path / 'baseline.json'
    baseline = _copy_json(real_sources['baseline_path'], path)
    if change == 'count':
        baseline['counts']['record_count'] -= 1
    elif change == 'rate':
        baseline['rates']['site'] *= 2
    elif change == 'pareto':
        baseline['pareto']['top10_share_pct'] /= 2
    else:
        baseline['source']['alarms_sha256'] = '0' * 64
    path.write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match='baseline|Baseline'):
        build_source_evidence(**{**real_sources, 'baseline_path': path})


def test_rejects_scenario_observation_mismatch(real_sources, tmp_path):
    path = tmp_path / 'scenario.json'
    scenario = _copy_json(real_sources['scenario_path'], path)
    scenario['beats'][2]['untaught_stopping_event']['stopping'] = 0
    path.write_text(json.dumps(scenario))
    with pytest.raises(ValueError, match='Scenario event mismatch'):
        build_source_evidence(**{**real_sources, 'scenario_path': path})


def test_rejects_original_zip_row_omission(real_sources, tmp_path):
    raw = tmp_path / 'raw'
    raw.mkdir()
    for name in ['Hill_of_Towie_alarms_description.csv', 'Hill_of_Towie_turbine_metadata.csv']:
        (raw / name).symlink_to(real_sources['raw_dir'] / name)
    with ZipFile(real_sources['raw_dir'] / '2025.zip') as source, ZipFile(raw / '2025.zip', 'w') as destination:
        for month in CONFIG.data.months:
            name = f'tblAlarmLog_2025_{month:02d}.csv'
            payload = source.read(name)
            if month == 1:
                lines = payload.splitlines(keepends=True)
                payload = b''.join([lines[0], *lines[2:]])
            destination.writestr(name, payload)
    with pytest.raises(ValueError, match='do not match original ZIP'):
        build_source_evidence(**{**real_sources, 'raw_dir': raw})


def test_rejects_missing_real_scada_cell(real_sources, tmp_path):
    processed = tmp_path / 'processed'
    processed.mkdir()
    (processed / 'alarms.parquet').symlink_to(real_sources['processed_dir'] / 'alarms.parquet')
    dataset = ds.dataset(real_sources['processed_dir'] / 'scada.parquet', format='parquet')
    predicate = ((ds.field('ts') == pd.Timestamp('2025-02-21T20:20:00').to_pydatetime())
                 & (ds.field('turbine_id') == '2304513'))
    snapshot = dataset.to_table(filter=predicate).to_pandas()
    snapshot.iloc[1:].to_parquet(processed / 'scada.parquet', index=False)
    with pytest.raises(ValueError, match='SCADA snapshot differs'):
        build_source_evidence(**{**real_sources, 'processed_dir': processed})


def test_audit_publication_preserves_previous_receipt_on_failure(verified, tmp_path, monkeypatch):
    destination = tmp_path / 'source_evidence.json'
    prior = b'previous verified evidence\n'
    destination.write_bytes(prior)
    monkeypatch.setattr(audit, 'CONFIG', SimpleNamespace(data=SimpleNamespace(processed_dir=tmp_path)))
    monkeypatch.setattr(audit, 'build_source_evidence', lambda: deepcopy(verified))
    def failed(_):
        raise OSError('Storage sync failed')
    monkeypatch.setattr(audit.os, 'fsync', failed)
    with pytest.raises(OSError, match='Storage sync failed'):
        audit.publish_evidence()
    assert destination.read_bytes() == prior
    assert not list(tmp_path.glob('.source-evidence-*'))


def test_missing_required_sources_are_clear(real_sources, tmp_path):
    with pytest.raises(FileNotFoundError, match='2025.zip'):
        build_source_evidence(**{**real_sources, 'raw_dir': tmp_path})
