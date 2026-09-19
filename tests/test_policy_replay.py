"""Replay-policy integration using unchanged records from the real Parquet files.

Sensor samples come from one bounded SCADA batch; no measurements, timestamps,
station IDs or alarm codes are fabricated. Journals stay under pytest tmp_path.
"""

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json

import pandas as pd
import pyarrow.parquet as pq
import pytest

from earshot.config import CONFIG
from earshot.detector import ZScoreDetector
from earshot.policy import PolicyLayer
from earshot.rules import SuppressionRule


@pytest.fixture(scope="module")
def real_sources():
    alarm_path = CONFIG.data.processed_dir / "alarms.parquet"
    scada_path = CONFIG.data.processed_dir / "scada.parquet"
    if not alarm_path.is_file() or not scada_path.is_file():
        pytest.skip("Replay-policy regressions require the real processed alarm and SCADA files")
    alarms = pd.read_parquet(alarm_path)
    sensors = next(pq.ParquetFile(scada_path).iter_batches(batch_size=10000)).to_pandas()
    sensors = sensors.loc[sensors.turbine_id.eq(sensors.turbine_id.iloc[0])].reset_index(drop=True)
    assert not alarms.empty and len(sensors) > 3
    assert sensors.signal.nunique() == 1
    assert sensors.value.notna().all()
    return alarms, sensors


@pytest.fixture(scope="module")
def sensor_transition(real_sources):
    _, sensors = real_sources
    for index in range(2, len(sensors)):
        first, second, changed = sensors.value.iloc[index - 2:index + 1]
        if first == second and changed != second:
            return sensors.iloc[index - 2:index + 1].to_dict("records")
    pytest.fail("Real SCADA slice must contain a constant pair followed by a changed value")


def _policy(tmp_path, buffer_size=128):
    return PolicyLayer(ZScoreDetector(), buffer_size, rules_path=tmp_path / "rules.jsonl")


def _rule(event, *, action="suppress", identifier="replay-correction"):
    return SuppressionRule(
        rule_id=identifier,
        utterance="Operator correction for the real replay regression",
        scope={"turbine_id": event["turbine_id"], "alarm_code": int(event["alarm_code"]), "signal": None},
        pattern={"kind": "code_match", "window_s": 60 if action == "collapse" else 0, "conditions": []},
        action=action,
        confidence=0.9,
        taught_by="replay-regression",
        taught_at=datetime.now(timezone.utc).isoformat(),
        reversible=True,
    )


def test_sensor_observations_update_baseline_without_incrementing_alarm_counts(tmp_path, sensor_transition):
    policy = _policy(tmp_path)

    verdicts = [policy.score(event) for event in sensor_transition]

    assert [verdict.anomaly_score for verdict in verdicts[:2]] == [0.0, 0.0]
    assert verdicts[2].anomaly_score > 0
    assert len(policy.buffer) == len(policy.verdicts) == 3
    stats = policy.stats()
    assert stats["alarms_shown"] == stats["alarms_suppressed"] == 0
    assert stats["suppression_rate"] == stats["alarms_per_hour_current"] == 0


def test_sensor_clock_expires_old_alarms_without_counting_sensor_samples(tmp_path, real_sources):
    alarms, sensors = real_sources
    first, second = alarms.iloc[:2].to_dict("records")
    before = sensors.loc[sensors.ts.le(first["ts"])].iloc[-1].to_dict()
    after = sensors.loc[sensors.ts.gt(first["ts"] + pd.Timedelta(hours=1))].iloc[0].to_dict()
    assert before["ts"] < first["ts"] < after["ts"] < second["ts"]
    policy = _policy(tmp_path)

    policy.score(before)
    policy.score(first)
    assert policy.stats()["alarms_per_hour_current"] == 1.0
    policy.score(after)

    assert policy.stats()["alarms_per_hour_current"] == 0.0
    assert policy.stats()["alarms_shown"] == 1
    assert policy.stats()["alarms_suppressed"] == 0
    policy.score(second)
    assert policy.stats()["alarms_shown"] == 2
    assert policy.stats()["alarms_per_hour_current"] == 1.0


def test_alarm_rule_omits_sensor_samples_from_training_and_rescore_counts(tmp_path, real_sources):
    alarms, sensors = real_sources
    selected_alarms = alarms.iloc[:3].to_dict("records")
    selected_sensors = sensors.loc[sensors.ts.le(selected_alarms[-1]["ts"])].to_dict("records")
    events = sorted(selected_alarms + selected_sensors, key=lambda event: event["ts"])
    assert len(selected_sensors) > len(selected_alarms)
    policy = _policy(tmp_path)
    for event in events:
        policy.score(event)

    result = policy.learn(_rule(selected_alarms[0]))

    assert len(policy.buffer) == len(events)
    assert result["examples_learned"] == len(selected_alarms)
    assert result["newly_suppressed_count"] == 1
    assert policy.classifier.optimizer.n_iterations == len(selected_alarms)
    assert policy.stats()["alarms_shown"] == 2
    assert policy.stats()["alarms_suppressed"] == 1
    assert policy.stats()["suppression_rate"] == pytest.approx(1 / 3)
    commit = json.loads((tmp_path / "rules.jsonl").read_text().splitlines()[-1])
    assert sorted(example["label"] for example in commit["examples"]) == [0, 0, 1]
    assert all(any(key.startswith("code=") for key in sample["features"]) for sample in commit["examples"])
    assert policy.rescore_buffer() == 0
    assert policy.undo(result["rule_id"])
    assert policy.stats()["alarms_shown"] == 3
    assert policy.stats()["alarms_suppressed"] == 0


def test_shared_deque_is_live_but_caller_edits_do_not_rewrite_scored_events(tmp_path, real_sources):
    alarms, _ = real_sources
    original, other = alarms.iloc[:2].to_dict("records")
    policy = _policy(tmp_path, buffer_size=2)
    shared = deque(maxlen=2)
    policy.bind_replay_buffer(shared)
    assert policy.buffer is shared
    shared.append(deepcopy(original))
    policy.score(shared[-1])

    # Replace the public record with another unchanged source record. The
    # accepted private snapshot must retain its original correction scope.
    shared[0].clear()
    shared[0].update(other)
    result = policy.learn(_rule(original))

    assert policy.buffer is shared
    assert shared[0] == other
    assert result["newly_suppressed_count"] == 1
    assert not policy.verdicts[0].show
    assert policy.stats()["alarms_suppressed"] == 1
    assert policy.rescore_buffer() == 0


@pytest.mark.parametrize("buffer", [deque(), deque(maxlen=1), []])
def test_binding_rejects_unbounded_or_mismatched_buffers(tmp_path, buffer):
    policy = _policy(tmp_path, buffer_size=2)

    with pytest.raises(ValueError, match="same bounded size"):
        policy.bind_replay_buffer(buffer)


def test_stream_reset_retains_corrections_classifier_and_journal(tmp_path, real_sources):
    alarms, _ = real_sources
    original = alarms.iloc[0].to_dict()
    recent = alarms.head(100).to_dict("records")
    held_out = alarms.loc[
        alarms.turbine_id.eq(original["turbine_id"])
        & alarms.alarm_code.eq(original["alarm_code"])
        & alarms.ts.gt(recent[-1]["ts"])
    ].iloc[0].to_dict()
    other = alarms.loc[
        alarms.turbine_id.ne(original["turbine_id"])
        & alarms.alarm_code.eq(original["alarm_code"])
        & alarms.ts.ge(held_out["ts"])
    ].iloc[0].to_dict()
    policy = _policy(tmp_path)
    shared = deque(maxlen=128)
    policy.bind_replay_buffer(shared)
    for event in recent:
        shared.append(event)
        policy.score(event)
    policy.learn(_rule(original))
    version = policy.model_version
    weights = deepcopy(policy.classifier.weights)
    intercept = policy.classifier.intercept
    iterations = policy.classifier.optimizer.n_iterations
    serialized_rules = [rule.to_json() for rule in policy.active_rules]
    journal = (tmp_path / "rules.jsonl").read_bytes()

    # Replayer.seek clears its own shared input deque before policy reset.
    shared.clear()
    policy.reset_stream()

    assert policy.buffer is shared and not shared
    assert not policy.verdicts
    assert policy.model_version == version
    assert [rule.to_json() for rule in policy.active_rules] == serialized_rules
    assert policy.classifier.weights == weights
    assert policy.classifier.intercept == intercept
    assert policy.classifier.optimizer.n_iterations == iterations
    assert (tmp_path / "rules.jsonl").read_bytes() == journal
    assert policy.stats()["alarms_shown"] == policy.stats()["alarms_suppressed"] == 0
    assert policy.stats()["alarms_per_hour_current"] == 0.0
    assert policy.rescore_buffer() == 0

    for event in (held_out, other):
        shared.append(event)
        verdict = policy.score(event)
        assert verdict.show == (event["turbine_id"] != original["turbine_id"])
    assert policy.stats()["alarms_shown"] == policy.stats()["alarms_suppressed"] == 1


def test_stream_reset_clears_numeric_detector_history(tmp_path, sensor_transition):
    policy = _policy(tmp_path)
    for event in sensor_transition[:2]:
        policy.score(event)

    policy.reset_stream()

    assert policy.score(sensor_transition[2]).anomaly_score == 0.0
    assert len(policy.verdicts) == 1
    assert policy.stats()["alarms_shown"] == policy.stats()["alarms_suppressed"] == 0


def test_stream_reset_clears_collapse_representative(tmp_path, real_sources):
    alarms, _ = real_sources
    chosen = None
    for _, group in alarms.groupby(["turbine_id", "alarm_code"], sort=False):
        ordered = group.sort_values("ts").reset_index(drop=True)
        repeats = ordered.ts.diff().dt.total_seconds().between(0, 59.999999)
        if repeats.any():
            index = repeats[repeats].index[0]
            chosen = ordered.iloc[index - 1:index + 1].to_dict("records")
            break
    assert chosen is not None, "The real alarm log must contain a repeated scope inside 60 seconds"
    first, second = chosen
    policy = _policy(tmp_path)
    policy.learn(_rule(first, action="collapse"))
    assert policy.score(first).show
    assert not policy.score(second).show
    version = policy.model_version

    policy.reset_stream()

    assert policy.model_version == version
    assert policy.score(second).show
    assert policy.stats()["alarms_shown"] == 1
    assert policy.stats()["alarms_suppressed"] == 0
