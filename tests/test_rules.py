"""Rule contracts checked against unchanged real alarm and SCADA observations.

Correction thresholds are rule metadata, not invented monitored data. Invalid
event checks derive malformed views of the supplied observations.
"""

from copy import deepcopy
import json
import math

import pandas as pd
import pyarrow.parquet as parquet
from pydantic import ValidationError
import pytest

from earshot.config import CONFIG
from earshot.rules import SuppressionRule


@pytest.fixture(scope="module")
def real_alarms():
    path = CONFIG.data.processed_dir / "alarms.parquet"
    if not path.is_file():
        pytest.skip("Rules regression requires the supplied processed alarm records")
    frame = pd.read_parquet(path)
    first = frame.iloc[0]
    other_turbine = frame.loc[
        frame.alarm_code.eq(first.alarm_code) & frame.turbine_id.ne(first.turbine_id)
    ].iloc[0]
    other_code = frame.loc[
        frame.turbine_id.eq(first.turbine_id) & frame.alarm_code.ne(first.alarm_code)
    ].iloc[0]
    return [row.to_dict() for row in (first, other_turbine, other_code)]


@pytest.fixture(scope="module")
def real_scada():
    path = CONFIG.data.processed_dir / "scada.parquet"
    if not path.is_file():
        pytest.skip("Rules regression requires the supplied processed SCADA records")
    # One real batch, not the complete 50-million-row artifact.
    with parquet.ParquetFile(path) as source:
        batch = next(source.iter_batches(batch_size=256, columns=["ts", "turbine_id", "signal", "value"]))
    frame = batch.to_pandas()
    row = frame.loc[frame.value.notna()].iloc[0].to_dict()
    assert math.isfinite(row["value"])
    return row


@pytest.fixture
def valid_payload(real_alarms):
    event = real_alarms[0]
    return {
        "rule_id": "source-rule",
        "utterance": "Use this observed event to check the scoped correction contract.",
        "scope": {"turbine_id": event["turbine_id"], "alarm_code": int(event["alarm_code"]), "signal": None},
        "pattern": {"kind": "code_match", "window_s": 0, "conditions": []},
        "action": "suppress",
        "confidence": 0.9,
        "taught_by": "regression-test",
        "taught_at": "2026-09-20T10:00:00Z",
        "reversible": True,
    }


def _scada_rule(payload, row, conditions, *, kind="conditions"):
    payload = deepcopy(payload)
    payload["scope"] = {"turbine_id": row["turbine_id"], "alarm_code": None, "signal": row["signal"]}
    payload["pattern"] = {"kind": kind, "window_s": 0, "conditions": conditions}
    return SuppressionRule(**payload)


def test_exact_prompt_fields_roundtrip_without_input_mutation(valid_payload):
    original = deepcopy(valid_payload)
    rule = SuppressionRule(**valid_payload)
    encoded = rule.to_json()
    assert set(json.loads(encoded)) == {
        "rule_id", "utterance", "scope", "pattern", "action", "confidence", "taught_by", "taught_at", "reversible",
    }
    assert SuppressionRule.from_json(encoded).model_dump() == rule.model_dump()
    assert SuppressionRule.from_json(encoded).to_json() == encoded
    assert valid_payload == original


def test_scope_ands_constraints_without_mutating_real_events(valid_payload, real_alarms):
    rule = SuppressionRule(**valid_payload)
    original = deepcopy(real_alarms)
    assert rule.matches_scope(real_alarms[0])
    assert rule.matches(real_alarms[0])
    assert not rule.matches(real_alarms[1])
    assert not rule.matches(real_alarms[2])
    missing = {key: value for key, value in real_alarms[0].items() if key != "turbine_id"}
    assert not rule.matches(missing)
    assert real_alarms == original


def test_none_scope_is_broad_without_interpreting_window(valid_payload, real_alarms):
    valid_payload["scope"] = {"turbine_id": None, "alarm_code": None, "signal": None}
    valid_payload["pattern"]["window_s"] = 60
    rule = SuppressionRule(**valid_payload)
    assert all(rule.matches(event) for event in real_alarms)


@pytest.mark.parametrize("namespace", ["direct", "signals", "features"])
def test_signal_scope_accepts_actual_named_signals(valid_payload, real_scada, namespace):
    rule = _scada_rule(valid_payload, real_scada, [], kind="code_match")
    if namespace == "direct":
        event = deepcopy(real_scada)
    else:
        event = {
            "ts": real_scada["ts"], "turbine_id": real_scada["turbine_id"],
            namespace: {real_scada["signal"]: real_scada["value"]},
        }
    assert rule.matches_scope(event)
    assert rule.matches(event)
    stripped = {key: value for key, value in event.items() if key in {"ts", "turbine_id"}}
    assert not rule.matches_scope(stripped)


@pytest.mark.parametrize(
    ("operation", "matches"),
    [("eq", True), ("ne", False), ("lt", False), ("lte", True), ("gt", False), ("gte", True)],
)
def test_conditions_use_real_numeric_boundaries(valid_payload, real_scada, operation, matches):
    rule = _scada_rule(valid_payload, real_scada, [
        {"field": "value", "op": operation, "value": real_scada["value"]},
    ])
    assert rule.matches_scope(real_scada)
    assert rule.matches(real_scada) is matches


@pytest.mark.parametrize("namespace", ["signals", "features"])
@pytest.mark.parametrize("explicit", [False, True])
def test_condition_namespaces_read_real_values(valid_payload, real_scada, namespace, explicit):
    name = real_scada["signal"]
    field = f"{namespace}.{name}" if explicit else name
    rule = _scada_rule(valid_payload, real_scada, [
        {"field": field, "op": "eq", "value": real_scada["value"]},
    ])
    event = {
        "ts": real_scada["ts"], "turbine_id": real_scada["turbine_id"],
        namespace: {name: real_scada["value"]},
    }
    assert rule.matches(event)


def test_all_pattern_kinds_apply_every_predicate(valid_payload, real_scada):
    conditions = [
        {"field": "value", "op": "gte", "value": real_scada["value"]},
        {"field": "value", "op": "lt", "value": real_scada["value"]},
    ]
    for kind in ("code_match", "conditions", "learned"):
        rule = _scada_rule(valid_payload, real_scada, conditions, kind=kind)
        assert rule.matches_scope(real_scada)
        assert not rule.matches(real_scada)
    assert _scada_rule(valid_payload, real_scada, conditions[:1], kind="learned").matches(real_scada)


@pytest.mark.parametrize("bad_value", [None, float("nan"), float("inf"), [], {}, True])
def test_invalid_measurements_never_match_numeric_conditions(valid_payload, real_scada, bad_value):
    rule = _scada_rule(valid_payload, real_scada, [
        {"field": "value", "op": "gte", "value": real_scada["value"]},
    ])
    assert not rule.matches({**real_scada, "value": bad_value})


def test_missing_field_does_not_satisfy_not_equal(valid_payload, real_scada):
    rule = _scada_rule(valid_payload, real_scada, [
        {"field": "value", "op": "ne", "value": real_scada["value"]},
    ])
    invalid = {key: value for key, value in real_scada.items() if key != "value"}
    assert not rule.matches(invalid)
    invalid["value"] = float("nan")
    assert not rule.matches(invalid)


@pytest.mark.parametrize(
    ("key", "value"),
    [("confidence", float("nan")), ("confidence", float("inf")), ("confidence", -0.1),
     ("confidence", 1.1), ("confidence", "0.9"), ("confidence", True),
     ("action", "silence_forever"), ("reversible", "true"), ("reversible", 1),
     ("rule_id", " "), ("utterance", "\t"), ("taught_by", ""),
     ("taught_at", "2026-09-20T10:00:00"), ("taught_at", "2026-02-30T10:00:00Z"),
     ("taught_at", "yesterday"), ("unexpected", "extra")],
)
def test_invalid_top_level_fields_are_rejected(valid_payload, key, value):
    valid_payload[key] = value
    with pytest.raises(ValidationError):
        SuppressionRule(**valid_payload)


@pytest.mark.parametrize("change", ["missing_key", "unknown_key", "boolean_code", "string_code", "blank_signal"])
def test_scope_schema_is_explicit_and_strict(valid_payload, change):
    scope = valid_payload["scope"]
    if change == "missing_key":
        scope.pop("alarm_code")
    elif change == "unknown_key":
        scope["station"] = scope["turbine_id"]
    elif change == "boolean_code":
        scope["alarm_code"] = True
    elif change == "string_code":
        scope["alarm_code"] = str(scope["alarm_code"])
    else:
        scope["signal"] = " "
    with pytest.raises(ValidationError):
        SuppressionRule(**valid_payload)


@pytest.mark.parametrize(
    "pattern",
    [
        {"kind": "morning_startup", "window_s": 0, "conditions": []},
        {"kind": "conditions", "window_s": 0, "conditions": []},
        {"kind": "learned", "window_s": 0, "conditions": []},
        {"kind": "code_match", "window_s": -1, "conditions": []},
        {"kind": "code_match", "window_s": True, "conditions": []},
        {"kind": "code_match", "window_s": 1.5, "conditions": []},
        {"kind": "code_match", "window_s": 0},
        {"kind": "code_match", "window_s": 0, "conditions": [], "temporal_guess": True},
    ],
)
def test_unsupported_patterns_cannot_silently_broaden(valid_payload, pattern):
    valid_payload["pattern"] = pattern
    with pytest.raises(ValidationError):
        SuppressionRule(**valid_payload)


@pytest.mark.parametrize(
    "condition",
    [
        {"field": "value", "op": "contains", "value": 1},
        {"field": "value", "op": "lt", "value": "1"},
        {"field": "value", "op": "gte", "value": True},
        {"field": "value", "op": "eq", "value": float("nan")},
        {"field": "value", "op": "eq", "value": float("inf")},
        {"field": "value", "op": "eq", "value": [1]},
        {"field": "value", "op": "eq"},
        {"field": "value", "op": "eq", "value": 1, "fallback": True},
        {"field": "event.value", "op": "eq", "value": 1},
        {"field": "__import__('os').system('false')", "op": "eq", "value": 1},
    ],
)
def test_invalid_predicates_are_rejected_without_evaluation(valid_payload, condition):
    valid_payload["pattern"]["conditions"] = [condition]
    with pytest.raises(ValidationError):
        SuppressionRule(**valid_payload)


def test_json_rejects_duplicate_keys_nonfinite_constants_and_nonobjects(valid_payload):
    encoded = SuppressionRule(**valid_payload).to_json()
    duplicate = '{"rule_id":"duplicate",' + encoded[1:]
    nonfinite = encoded.replace('"confidence":0.9', '"confidence":NaN')
    assert nonfinite != encoded
    for payload in (duplicate, nonfinite, "[]", "null"):
        with pytest.raises(ValueError):
            SuppressionRule.from_json(payload)


def test_invalid_nested_mutation_is_revalidated(valid_payload, real_alarms):
    rule = SuppressionRule(**valid_payload)
    rule.pattern["kind"] = "unsupported_temporal_pattern"
    with pytest.raises(ValidationError):
        rule.matches(real_alarms[0])
    with pytest.raises(ValidationError):
        rule.to_json()

