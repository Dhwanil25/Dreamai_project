"""Policy regressions using actual records from the processed alarm dataset.

No sensor observations, timestamps, alarm codes or stopping labels are made up.
Explicit critical/severity annotations are test controls on unchanged source
events. Operator rules are test inputs, and every journal lives under tmp_path.
"""

from datetime import datetime, timezone
import json
from time import perf_counter

import pandas as pd
import pyarrow.parquet as pq
import pytest

from earshot.config import CONFIG
from earshot.detector import ZScoreDetector
from earshot.policy import PolicyLayer
from earshot.rules import SuppressionRule


@pytest.fixture(scope="module")
def alarms():
    path = CONFIG.data.processed_dir / "alarms.parquet"
    if not path.is_file():
        pytest.skip("Real-source policy regressions require processed alarms.parquet")
    frame = pd.read_parquet(path)
    assert len(frame) > 2000, "Policy timing requires 2000 actual buffered events and held-out events"
    return frame


@pytest.fixture(scope="module")
def cases(alarms):
    """Choose a frequent real scope and later matching/nonmatching records."""
    recent = alarms.head(2000)
    populations = recent.groupby(["turbine_id", "alarm_code"]).size().sort_values(ascending=False)
    station, code = populations.index[0]
    later = alarms.iloc[2000:]
    matching = later.loc[later.turbine_id.eq(station) & later.alarm_code.eq(code)]
    other_asset = later.loc[later.turbine_id.ne(station) & later.alarm_code.eq(code)]
    other_scope = populations.index[1]
    assert len(matching) >= 3 and not other_asset.empty
    return {
        "events": recent.to_dict("records"),
        "station": str(station),
        "code": int(code),
        "matching_count": int(populations.iloc[0]),
        "matching": matching.head(3).to_dict("records"),
        "other_asset": other_asset.iloc[0].to_dict(),
        "second_scope": {"turbine_id": str(other_scope[0]), "alarm_code": int(other_scope[1]), "signal": None},
    }


@pytest.fixture(scope="module")
def scada_events():
    path = CONFIG.data.processed_dir / "scada.parquet"
    if not path.is_file():
        pytest.skip("Numeric policy regression requires processed scada.parquet")
    batch = next(pq.ParquetFile(path).iter_batches(batch_size=64))
    records = batch.to_pandas().to_dict("records")
    assert records and all(pd.notna(row["value"]) for row in records)
    return records


def _rule(cases, rule_id="scope-rule", *, action="suppress", kind="code_match", scope=None, reversible=True, window_s=0):
    return SuppressionRule(
        rule_id=rule_id,
        utterance="Operator correction used by the real-source policy regression",
        scope=scope if scope is not None else {
            "turbine_id": cases["station"], "alarm_code": cases["code"], "signal": None,
        },
        pattern={
            "kind": kind, "window_s": window_s,
            "conditions": [{"field": "alarm_code", "op": "eq", "value": cases["code"]}]
            if kind == "learned" else [],
        },
        action=action,
        confidence=0.9,
        taught_by="policy-regression",
        taught_at=datetime.now(timezone.utc).isoformat(),
        reversible=reversible,
    )


def _policy(tmp_path, *, name="rules.jsonl", buffer_size=2000, threshold=None, detector=None):
    return PolicyLayer(
        detector if detector is not None else ZScoreDetector(),
        buffer_size=buffer_size,
        rules_path=tmp_path / name,
        nuisance_threshold=threshold,
    )


def _populate(policy, events):
    for event in events:
        policy.score(event)


def _model_state(policy):
    return {
        "weights": policy.classifier.weights,
        "intercept": policy.classifier.intercept,
        "iterations": policy.classifier.optimizer.n_iterations,
    }


def _assert_model_state(policy, expected):
    assert policy.classifier.weights == pytest.approx(expected["weights"], rel=1e-12, abs=1e-12)
    assert policy.classifier.intercept == pytest.approx(expected["intercept"], rel=1e-12, abs=1e-12)
    assert policy.classifier.optimizer.n_iterations == expected["iterations"]


def test_teaching_scoped_rule_suppresses_only_its_asset_and_code(tmp_path, cases):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    assert all(verdict.show for verdict in policy.verdicts)

    result = policy.learn(_rule(cases))

    assert result["rule_id"] == "scope-rule"
    assert result["model_version"] == policy.model_version == 2
    assert result["newly_suppressed_count"] == cases["matching_count"]
    probes = sorted([cases["matching"][0], cases["other_asset"]], key=lambda row: row["ts"])
    for event in probes:
        verdict = policy.score(event)
        if event["turbine_id"] == cases["station"]:
            assert not verdict.show and verdict.suppressed_by == "scope-rule"
        else:
            assert verdict.show and verdict.suppressed_by is None


def test_learn_updates_real_classifier_and_finishes_below_500ms(tmp_path, cases, record_property):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    before = _model_state(policy)
    rule = _rule(cases)

    started = perf_counter()
    result = policy.learn(rule)
    elapsed_ms = (perf_counter() - started) * 1000

    record_property("learn_ms_for_2000_real_events", elapsed_ms)
    assert len(policy.buffer) == 2000
    assert 0 < cases["matching_count"] < 2000
    assert result["examples_learned"] == 2000
    assert policy.classifier.weights != before["weights"]
    assert policy.classifier.optimizer.n_iterations == 2000
    assert (tmp_path / "rules.jsonl").stat().st_size > 0
    assert elapsed_ms < 500, f"Full learn/persist/rescore took {elapsed_ms:.3f} ms"


def test_undo_restores_visibility_and_removes_classifier_training(tmp_path, cases):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    initial = _model_state(policy)
    policy.learn(_rule(cases))
    assert policy.model_version == 2
    assert any(not verdict.show for verdict in policy.verdicts)

    assert policy.undo("scope-rule") is True

    assert policy.model_version == 3
    assert not policy.active_rules
    assert all(verdict.show for verdict in policy.verdicts)
    assert policy.score(cases["matching"][0]).show
    _assert_model_state(policy, initial)


def test_undo_latest_rule_preserves_earlier_model_exactly(tmp_path, cases):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    policy.learn(_rule(cases, "first"))
    first_state = _model_state(policy)
    policy.learn(_rule(cases, "second", scope=cases["second_scope"]))
    assert policy.model_version == 3
    assert policy.classifier.weights != first_state["weights"]

    assert policy.undo("second") is True

    assert policy.model_version == 4
    assert [rule.rule_id for rule in policy.active_rules] == ["first"]
    _assert_model_state(policy, first_state)


def test_later_rule_does_not_carry_earlier_positive_labels_through_undo(tmp_path, cases):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    policy.learn(_rule(cases, "first"))

    second = policy.learn(_rule(cases, "second", scope=cases["second_scope"]))

    # Disjoint scopes: first-rule positives are omitted from the second batch,
    # rather than receiving contradictory labels or acquiring a second owner.
    assert second["examples_learned"] == 2000 - cases["matching_count"]
    assert policy.undo("first")
    assert policy.model_version == 4
    assert policy.classifier.optimizer.n_iterations == second["examples_learned"]
    assert policy.score(cases["matching"][0]).show
    assert [rule.rule_id for rule in policy.active_rules] == ["second"]


def test_journal_reload_reconstructs_rules_learning_and_undo(tmp_path, cases):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    policy.learn(_rule(cases, "first"))
    policy.learn(_rule(cases, "second", scope=cases["second_scope"]))
    learned = _model_state(policy)

    restarted = _policy(tmp_path)

    assert restarted.model_version == policy.model_version == 3
    assert {rule.rule_id for rule in restarted.active_rules} == {"first", "second"}
    _assert_model_state(restarted, learned)
    assert not restarted.score(cases["matching"][0]).show
    assert restarted.undo("second")
    remaining = _model_state(restarted)

    restarted_again = _policy(tmp_path)

    assert restarted_again.model_version == 4
    assert [rule.rule_id for rule in restarted_again.active_rules] == ["first"]
    _assert_model_state(restarted_again, remaining)


def test_uncommitted_learning_has_no_effect_in_memory_or_after_restart(tmp_path, cases, monkeypatch):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    initial = _model_state(policy)
    append = policy._append

    def unavailable_commit(record):
        if record["op"] == "commit":
            raise OSError("Simulated commit storage failure")
        append(record)

    with monkeypatch.context() as patch:
        patch.setattr(policy, "_append", unavailable_commit)
        with pytest.raises(OSError, match="commit storage failure"):
            policy.learn(_rule(cases))

    assert policy.model_version == 1
    assert not policy.active_rules
    assert all(verdict.show for verdict in policy.verdicts)
    _assert_model_state(policy, initial)
    restarted = _policy(tmp_path)
    assert restarted.model_version == 1 and not restarted.active_rules
    _assert_model_state(restarted, initial)
    assert restarted.score(cases["matching"][0]).show


def test_unknown_and_nonreversible_undo_do_not_change_state(tmp_path, cases):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    policy.learn(_rule(cases, reversible=False))
    prior_version = policy.model_version
    prior_bytes = (tmp_path / "rules.jsonl").read_bytes()
    prior_model = _model_state(policy)

    assert policy.undo("does-not-exist") is False
    assert policy.undo("scope-rule") is False

    assert policy.model_version == prior_version
    assert (tmp_path / "rules.jsonl").read_bytes() == prior_bytes
    _assert_model_state(policy, prior_model)


def test_rule_and_buffer_snapshots_cannot_rewrite_live_policy(tmp_path, cases):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    policy.learn(_rule(cases))
    rule_snapshot = policy.active_rules[0]
    event_snapshot = policy.buffer[0]
    original_station = policy.buffer[0]["turbine_id"]

    rule_snapshot.scope["turbine_id"] = cases["other_asset"]["turbine_id"]
    event_snapshot["turbine_id"] = cases["other_asset"]["turbine_id"]

    assert policy.active_rules[0].scope["turbine_id"] == cases["station"]
    assert policy.buffer[0]["turbine_id"] == original_station
    assert not policy.score(cases["matching"][0]).show


def test_all_none_scope_documents_broad_wildcard_behavior(tmp_path, cases):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    policy.learn(_rule(cases, scope={"turbine_id": None, "alarm_code": None, "signal": None}))

    assert all(not verdict.show for verdict in policy.verdicts)
    assert not policy.score(cases["other_asset"]).show
    assert policy.stats()["suppression_rate"] == 1.0


@pytest.mark.parametrize("annotation", [{"critical": True}, {"severity": "critical"}, {"severity": "emergency"}])
def test_explicit_critical_events_survive_matching_suppression(tmp_path, cases, annotation):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    policy.learn(_rule(cases))
    event = {**cases["matching"][0], **annotation}

    verdict = policy.score(event)

    assert verdict.show and verdict.suppressed_by is None


def test_stopping_label_is_not_automatically_a_critical_override(tmp_path, alarms, cases):
    event = alarms.loc[alarms.stopping.eq(1)].iloc[0].to_dict()
    policy = _policy(tmp_path)
    policy.score(event)
    policy.learn(_rule(cases, scope={
        "turbine_id": event["turbine_id"], "alarm_code": event["alarm_code"], "signal": None,
    }))

    assert not policy.verdicts[0].show


def test_classifier_only_rule_learns_and_never_crosses_asset_scope(tmp_path, cases):
    threshold = 0.6
    policy = _policy(tmp_path, threshold=threshold)
    _populate(policy, cases["events"])
    policy.learn(_rule(cases, kind="learned"))
    probes = sorted([cases["matching"][0], cases["other_asset"]], key=lambda row: row["ts"])

    for event in probes:
        verdict = policy.score(event)
        if event["turbine_id"] == cases["station"]:
            assert not verdict.show, "The trained classifier must contribute actual suppression"
            assert verdict.confidence >= threshold
        else:
            assert verdict.show and verdict.suppressed_by is None


def test_rare_real_scope_learns_above_configured_probability(tmp_path, cases, alarms):
    recent = alarms.head(2000)
    counts = recent.groupby(["turbine_id", "alarm_code"]).size()
    eligible = counts[counts.eq(17)]
    selected = None
    for station, code in eligible.index:
        later = alarms.iloc[2000:]
        later = later.loc[later.turbine_id.eq(station) & later.alarm_code.eq(code)]
        if not later.empty:
            selected = str(station), int(code), later.iloc[0].to_dict()
            break
    assert selected is not None, "The real buffer must contain the discovered 17-record scope"
    station, code, held_out = selected
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])

    result = policy.learn(_rule({**cases, "station": station, "code": code}, kind="learned"))
    verdict = policy.score(held_out)

    assert result["examples_learned"] == 2000
    assert not verdict.show
    assert verdict.suppressed_by == "classifier:scope-rule"
    assert verdict.confidence >= CONFIG.policy.nuisance_probability


@pytest.mark.parametrize("bad_weight", [0, -1, float("inf"), "0.5", True])
def test_reload_rejects_invalid_persisted_training_weights(tmp_path, cases, bad_weight):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    policy.learn(_rule(cases))
    journal = tmp_path / "rules.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    commit = next(row for row in rows if row["op"] == "commit")
    commit["examples"][0]["weight"] = bad_weight
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(ValueError):
        _policy(tmp_path)


@pytest.mark.parametrize(
    ("setting", "bad_value"),
    [
        ("nuisance_threshold", 0.1),
        ("nuisance_threshold", 1.1),
        ("learning_rate", 0.0),
        ("learning_rate", -0.1),
        ("learning_rate", float("inf")),
        ("collapse_window_s", 0),
    ],
)
def test_reload_validates_persisted_settings_before_rebuilding_model(tmp_path, cases, setting, bad_value):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    policy.learn(_rule(cases))
    journal = tmp_path / "rules.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    next(row for row in rows if row["op"] == "commit")[setting] = bad_value
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(ValueError):
        _policy(tmp_path)


def test_escalation_overrides_broad_suppression_and_undo_restores_it(tmp_path, cases):
    policy = _policy(tmp_path)
    _populate(policy, cases["events"])
    policy.learn(_rule(cases, "broad", scope={"turbine_id": None, "alarm_code": None, "signal": None}))
    policy.learn(_rule(cases, "escalation", action="escalate"))

    for event, verdict in zip(policy.buffer, policy.verdicts):
        assert verdict.show == (event["turbine_id"] == cases["station"] and event["alarm_code"] == cases["code"])
    assert policy.undo("escalation")
    assert all(not verdict.show for verdict in policy.verdicts)


def test_collapse_keeps_first_real_event_and_reopens_after_window(tmp_path, alarms, cases):
    chosen = None
    for (station, code), group in alarms.groupby(["turbine_id", "alarm_code"], sort=False):
        group = group.sort_values("ts").reset_index(drop=True)
        repeats = group.index[group.ts.diff().dt.total_seconds().between(0, 60)]
        for index in repeats:
            first, second = group.iloc[index - 1], group.iloc[index]
            later = group.loc[group.ts.gt(first.ts + pd.Timedelta(seconds=60))]
            if not later.empty:
                chosen = first.to_dict(), second.to_dict(), later.iloc[0].to_dict()
                break
        if chosen:
            break
    assert chosen is not None, "The real log must provide a repeated scope inside a 60-second window"
    first, second, later = chosen
    policy = _policy(tmp_path)
    assert policy.score(first).show
    policy.learn(_rule(cases, action="collapse", window_s=60, scope={
        "turbine_id": first["turbine_id"], "alarm_code": first["alarm_code"], "signal": None,
    }))

    assert policy.verdicts[0].show
    assert not policy.score(second).show
    assert policy.score(later).show


def test_rescore_is_idempotent_and_does_not_update_detector(tmp_path, cases, scada_events):
    class ObservedDetector:
        """Count updates while delegating real numeric behavior to ZScoreDetector."""

        updates = 0

        def __init__(self):
            self.detector = ZScoreDetector()

        def score(self, features):
            return self.detector.score(features)

        def update(self, features):
            # Policy clones detectors per asset; count calls across those real
            # clones so this cannot pass vacuously on alarm-only observations.
            type(self).updates += 1
            self.detector.update(features)

    detector = ObservedDetector()
    policy = _policy(tmp_path, detector=detector)
    _populate(policy, scada_events)
    updates = ObservedDetector.updates
    assert updates == len(scada_events)
    original_anomalies = [verdict.anomaly_score for verdict in policy.verdicts]
    policy.learn(_rule(cases, scope={
        "turbine_id": scada_events[0]["turbine_id"],
        "alarm_code": None,
        "signal": scada_events[0]["signal"],
    }))
    after_learn = policy.stats()
    assert ObservedDetector.updates == updates

    assert policy.rescore_buffer() == 0
    assert policy.rescore_buffer() == 0

    assert ObservedDetector.updates == updates
    assert policy.stats() == after_learn
    assert [verdict.anomaly_score for verdict in policy.verdicts] == original_anomalies
    assert len(policy.buffer) == len(policy.verdicts) == len(scada_events)


def test_bounded_buffer_rescore_preserves_evicted_cumulative_counts(tmp_path, cases):
    policy = _policy(tmp_path, buffer_size=5)
    _populate(policy, cases["events"][:12])
    assert len(policy.buffer) == 5
    assert policy.stats()["alarms_shown"] == 12
    policy.learn(_rule(cases, scope={"turbine_id": None, "alarm_code": None, "signal": None}))

    assert policy.stats()["alarms_shown"] == 7
    assert policy.stats()["alarms_suppressed"] == 5
    assert policy.stats()["suppression_rate"] == pytest.approx(5 / 12)
    assert policy.undo("scope-rule")
    assert policy.stats()["alarms_shown"] == 12
    assert policy.stats()["alarms_suppressed"] == 0
