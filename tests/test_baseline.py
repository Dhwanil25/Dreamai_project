"""Baseline regressions from the unchanged, supplied January–March 2025 data.

No event records are invented. Invalid-input checks derive empty, one-row or
malformed views of real records. Prior-output sentinels are only file state,
never a substitute dataset. Source-dependent checks skip if local data is absent.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import socket

import pandas as pd
import pytest

from earshot.config import CONFIG
from scripts import compute_baseline as baseline


SOURCE_ALARM_SHA256 = "9b99821be20e5758765e206107c0bd0e4a4225d79ac860908937320fc192220e"
METADATA_NAME = "Hill_of_Towie_turbine_metadata.csv"


def _sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@pytest.fixture(scope="module")
def real_inputs():
    alarm_path = CONFIG.data.processed_dir / "alarms.parquet"
    metadata_path = CONFIG.data.raw_dir / METADATA_NAME
    if not alarm_path.is_file() or not metadata_path.is_file():
        pytest.skip("Real baseline regression requires the supplied alarm Parquet and metadata")
    alarms = pd.read_parquet(alarm_path)
    metadata = pd.read_csv(metadata_path)
    provenance = {
        "alarm_path": "data/processed/alarms.parquet",
        "metadata_path": f"data/raw/{METADATA_NAME}",
        "alarms_sha256": _sha256(alarm_path),
        "metadata_sha256": _sha256(metadata_path),
    }
    return alarms, metadata, provenance, alarm_path, metadata_path


@pytest.fixture(scope="module")
def real_stats(real_inputs):
    alarms, metadata, provenance, _, _ = real_inputs
    original_alarms = alarms.copy(deep=True)
    original_metadata = metadata.copy(deep=True)
    stats = baseline.calculate_baseline(alarms, metadata, provenance)
    pd.testing.assert_frame_equal(alarms, original_alarms)
    pd.testing.assert_frame_equal(metadata, original_metadata)
    return stats


def _reject_network(*args, **kwargs):
    raise AssertionError("Baseline computation must not attempt network access")


def _local_output_paths(tmp_path, monkeypatch):
    project = tmp_path / "project"
    processed = project / "data" / "processed"
    processed.mkdir(parents=True)
    (project / "demo").mkdir()
    monkeypatch.setattr(baseline, "PROJECT_ROOT", project)
    monkeypatch.setattr(baseline.CONFIG.data, "processed_dir", processed)
    return processed, processed / "baseline_report.md", project / "demo" / "baseline_stats.json"


def _write_previous_outputs(report_path, json_path):
    previous = {
        report_path: b"# Previous completed baseline report\n",
        json_path: b'{"previous_completed_artifact":true}\n',
    }
    for path, contents in previous.items():
        path.write_bytes(contents)
    return previous


def _copy_real_inputs(real_inputs, processed, monkeypatch):
    _, _, _, source_path, metadata_path = real_inputs
    raw_dir = processed.parent / "raw"
    raw_dir.mkdir()
    copied_source = processed / "alarms.parquet"
    copied_metadata = raw_dir / METADATA_NAME
    shutil.copy2(source_path, copied_source)
    shutil.copy2(metadata_path, copied_metadata)
    monkeypatch.setattr(baseline.CONFIG.data, "raw_dir", raw_dir)
    return copied_source, copied_metadata


def _assert_outputs_unchanged(previous):
    for path, contents in previous.items():
        assert path.read_bytes() == contents


def test_baseline_source_receipt_and_record_counts_match_real_artifact(real_inputs, real_stats):
    alarms, metadata, provenance, alarm_path, metadata_path = real_inputs

    assert provenance["alarms_sha256"] == SOURCE_ALARM_SHA256
    assert len(alarms) == 68_891
    assert real_stats["source"] == provenance
    assert real_stats["counts"] == {
        "record_count": 68_891,
        "turbine_count": 21,
        "observed_turbine_count": 21,
        "station_count": 22,
        "known_turbine_records": 68_889,
        "unknown_station_records": 2,
        "unknown_station_ids": ["91"],
        "duplicate_event_records": 348,
    }
    assert metadata["Station ID"].nunique() == 21
    assert _sha256(alarm_path) == provenance["alarms_sha256"]
    assert _sha256(metadata_path) == provenance["metadata_sha256"]


def test_rate_denominators_use_observed_span_and_known_turbines(real_inputs, real_stats):
    alarms, metadata, _, _, _ = real_inputs
    hours = (alarms.ts.max() - alarms.ts.min()).total_seconds() / 3600
    known_ids = set(metadata["Station ID"].astype(str))
    known_records = alarms.turbine_id.isin(known_ids)
    rates = real_stats["rates"]

    assert hours == pytest.approx(2_157.315)
    assert real_stats["observed_period"]["hours"] == pytest.approx(hours)
    assert rates["site"] == pytest.approx(31.933676815856746)
    assert rates["site"] == pytest.approx(len(alarms) / hours)
    assert rates["per_turbine"] == pytest.approx(1.5206071303581403)
    assert rates["per_turbine"] == pytest.approx(known_records.sum() / hours / len(metadata))
    assert rates["reference_per_hour"] == 12.0
    assert rates["site_vs_reference"] == pytest.approx(rates["site"] / 12)
    assert rates["per_turbine_vs_reference"] == pytest.approx(rates["per_turbine"] / 12)
    assert len(rates["by_turbine"]) == 21
    assert {row["turbine_id"] for row in rates["by_turbine"]} == known_ids
    assert sum(row["count"] for row in rates["by_turbine"]) == 68_889
    for row in rates["by_turbine"]:
        assert row["alarms_per_hour"] == pytest.approx(row["count"] / hours)


def test_pareto_top_codes_and_stopping_counts_reconcile(real_inputs, real_stats):
    alarms, _, _, _, _ = real_inputs
    assert real_stats["pareto"] == {
        "top10_share_pct": pytest.approx(92.02217996545266),
        "codes_to_reach_80pct": 2,
        "distinct_codes": 192,
    }
    rows = real_stats["top_codes"]
    assert len(rows) == 20
    assert sum(row["count"] for row in rows[:10]) == 63_395
    assert [row["count"] for row in rows] == sorted(
        (row["count"] for row in rows), reverse=True
    )
    for row in rows:
        assert row["share_pct"] == pytest.approx(100 * row["count"] / len(alarms))
    splits = list(real_stats["stopping_split"].values())
    assert sorted(row["count"] for row in splits) == [639, 12_557, 55_695]
    assert sum(row["count"] for row in splits) == len(alarms)
    assert sum(row["share_pct"] for row in splits) == pytest.approx(100.0)
    for row in splits:
        assert row["share_pct"] == pytest.approx(100 * row["count"] / len(alarms))


def test_real_burst_bins_and_duplicate_sensitivity_are_explicit(real_stats):
    floods = real_stats["floods"]
    assert floods["window_seconds"] == 600
    assert floods["threshold"] == 10
    assert floods["window_count"] == 2_263
    assert floods["total_observed_bins"] == 12_944
    assert floods["flood_bin_share_pct"] == pytest.approx(17.48300370828183)
    assert len(floods["windows"]) == floods["window_count"]
    assert len(floods["runs"]) == floods["run_count"]
    assert floods["worst_window"]["count"] == 567
    assert pd.Timestamp(floods["worst_window"]["start"]) == pd.Timestamp("2025-03-31 07:30:00")
    assert floods["longest_run"]["bin_count"] == 50
    assert floods["longest_run"]["duration_minutes"] == 500
    assert sum(row["count"] for row in floods["windows"]) == 50_849

    sensitivity = real_stats["duplicate_sensitivity"]
    assert sensitivity["removed_records"] == 348
    assert sensitivity["record_count"] == 68_543
    assert sensitivity["alarms_per_hour"] == pytest.approx(68_543 / 2_157.315)
    assert isinstance(sensitivity["cluster_memberships_changed"], bool)


def test_full_baseline_json_is_deterministic_finite_and_complete(
    real_inputs, real_stats
):
    alarms, metadata, provenance, _, _ = real_inputs
    repeated = baseline.calculate_baseline(alarms, metadata, provenance)
    encoded = json.dumps(real_stats, allow_nan=False, sort_keys=True)
    assert encoded == json.dumps(repeated, allow_nan=False, sort_keys=True)
    decoded = json.loads(encoded)
    assert {
        "schema_version", "source", "observed_period", "counts", "rates", "pareto",
        "top_codes", "clusters", "floods", "stopping_split", "duplicate_sensitivity",
        "sanity_checks", "caveats", "slide_numbers",
    }.issubset(decoded)
    assert decoded["clusters"]["group_count"] == len(decoded["clusters"]["groups"]) > 0
    assert decoded["sanity_checks"]["status"] == "passed"
    assert 3 <= len(decoded["slide_numbers"]) <= 5
    assert decoded["caveats"]


@pytest.mark.parametrize("selection", ["empty", "zero_span"])
def test_unmeasurable_real_slices_are_rejected(real_inputs, selection):
    alarms, metadata, provenance, _, _ = real_inputs
    frame = alarms.iloc[:0].copy() if selection == "empty" else alarms.iloc[:1].copy()
    with pytest.raises(ValueError):
        baseline.calculate_baseline(frame, metadata, provenance)


def test_single_code_real_slice_triggers_publication_sanity_guard(real_inputs):
    alarms, metadata, provenance, _, _ = real_inputs
    code = alarms.alarm_code.mode().iloc[0]
    frame = alarms.loc[alarms.alarm_code.eq(code)].copy()
    hours = (frame.ts.max() - frame.ts.min()).total_seconds() / 3600

    assert hours > 0
    assert frame.alarm_code.nunique() == 1
    assert len(frame) / hours <= 10_000
    with pytest.raises(ValueError, match="Suspicious"):
        baseline.calculate_baseline(frame, metadata, provenance)


def test_dense_real_two_instant_window_triggers_publication_sanity_guard(real_inputs):
    alarms, metadata, provenance, _, _ = real_inputs
    counts = alarms.groupby("ts", sort=True).size()
    durations = counts.index.to_series().diff().dt.total_seconds()
    rates = (counts + counts.shift(1)) * 3600 / durations
    frame = None

    for end in rates.index[rates.gt(10_000)]:
        start = counts.index[counts.index.get_loc(end) - 1]
        candidate = alarms.loc[alarms.ts.between(start, end)].copy()
        if candidate.alarm_code.value_counts(normalize=True).max() <= 0.95:
            frame = candidate
            break

    assert frame is not None, "The supplied source must contain a dense, mixed-code window"
    assert frame.ts.nunique() == 2
    hours = (frame.ts.max() - frame.ts.min()).total_seconds() / 3600
    assert hours > 0 and len(frame) / hours > 10_000
    assert frame.alarm_code.value_counts(normalize=True).max() <= 0.95
    with pytest.raises(ValueError, match="Suspicious"):
        baseline.calculate_baseline(frame, metadata, provenance)


def test_missing_alarm_input_preserves_previous_artifacts(tmp_path, monkeypatch):
    _, report_path, json_path = _local_output_paths(tmp_path, monkeypatch)
    previous = _write_previous_outputs(report_path, json_path)

    with pytest.raises(FileNotFoundError):
        baseline.build_baseline()

    _assert_outputs_unchanged(previous)


def test_failure_before_publication_preserves_both_outputs(
    real_inputs, real_stats, tmp_path, monkeypatch
):
    processed, report_path, json_path = _local_output_paths(tmp_path, monkeypatch)
    _copy_real_inputs(real_inputs, processed, monkeypatch)
    previous = _write_previous_outputs(report_path, json_path)

    # The replacement is the already computed real result, not fabricated input.
    monkeypatch.setattr(baseline, "calculate_baseline", lambda *args: deepcopy(real_stats))

    def fail_render(stats):
        raise OSError("Report generation failed before publication")

    monkeypatch.setattr(baseline, "render_report", fail_render)
    with pytest.raises(OSError, match="before publication"):
        baseline.build_baseline()

    _assert_outputs_unchanged(previous)


def test_nonfinite_artifact_is_rejected_before_overwriting_outputs(
    real_stats, tmp_path, monkeypatch
):
    _, report_path, json_path = _local_output_paths(tmp_path, monkeypatch)
    previous = _write_previous_outputs(report_path, json_path)
    invalid_stats = deepcopy(real_stats)
    invalid_stats["rates"]["site"] = float("nan")

    with pytest.raises(ValueError):
        baseline._publish_outputs("# Candidate report\n", invalid_stats, report_path, json_path)

    _assert_outputs_unchanged(previous)


def test_second_staging_failure_preserves_both_artifacts_and_cleans_first_stage(
    real_stats, tmp_path, monkeypatch
):
    processed, report_path, json_path = _local_output_paths(tmp_path, monkeypatch)
    previous = _write_previous_outputs(report_path, json_path)
    create_temporary = baseline.NamedTemporaryFile
    attempts = []

    def fail_second_stage(*args, **kwargs):
        attempts.append(kwargs["dir"])
        if len(attempts) == 2:
            raise OSError("Second artifact staging failed")
        return create_temporary(*args, **kwargs)

    monkeypatch.setattr(baseline, "NamedTemporaryFile", fail_second_stage)
    with pytest.raises(OSError, match="Second artifact staging failed"):
        baseline._publish_outputs("# Complete candidate report\n", real_stats, report_path, json_path)

    assert len(attempts) == 2
    _assert_outputs_unchanged(previous)
    assert {path.name for path in processed.iterdir()} == {"baseline_report.md"}
    assert {path.name for path in json_path.parent.iterdir()} == {"baseline_stats.json"}


def test_build_writes_reconciled_artifacts_offline_and_preserves_inputs(
    real_inputs, tmp_path, monkeypatch
):
    _, _, provenance, source_path, metadata_path = real_inputs
    processed, report_path, json_path = _local_output_paths(tmp_path, monkeypatch)
    copied_source, copied_metadata = _copy_real_inputs(real_inputs, processed, monkeypatch)
    monkeypatch.setattr(socket, "create_connection", _reject_network)
    monkeypatch.setattr(socket.socket, "connect", _reject_network)

    stats = baseline.build_baseline()

    encoded = json_path.read_text(encoding="utf-8")
    assert json.loads(encoded) == json.loads(json.dumps(stats, allow_nan=False))
    report = report_path.read_text(encoding="utf-8")
    assert report == baseline.render_report(stats)
    assert "SLIDE NUMBERS" in report
    assert "operator" in report.lower()
    assert stats["counts"]["record_count"] == 68_891
    assert stats["source"]["alarms_sha256"] == provenance["alarms_sha256"]
    assert _sha256(copied_source) == provenance["alarms_sha256"]
    assert _sha256(source_path) == provenance["alarms_sha256"]
    assert _sha256(metadata_path) == provenance["metadata_sha256"]
    assert _sha256(copied_metadata) == provenance["metadata_sha256"]
    assert {path.name for path in processed.iterdir()} == {"alarms.parquet", "baseline_report.md"}
    assert {path.name for path in json_path.parent.iterdir()} == {"baseline_stats.json"}
