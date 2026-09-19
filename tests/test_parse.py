"""Offline and mocked-online parser contracts grounded in the real vocabulary.

Utterances and invalid provider replies are test inputs. Known identifiers and
descriptions come from the actual Phase 3 data; no alarm records are generated.
The socket layer is blocked throughout, including tests of online dispatch.
"""

from copy import deepcopy
from datetime import datetime, timezone
import json
import socket
from types import SimpleNamespace

import pandas as pd
import pytest

from earshot.config import CONFIG, PROJECT_ROOT
from earshot.detector import create_detector
from earshot import parse as parser
from earshot.policy import PolicyLayer


FIXTURES = json.loads(
    (PROJECT_ROOT / "demo" / "utterances" / "utterances.json").read_text(encoding="utf-8")
)["fixtures"]


@pytest.fixture(scope="module")
def vocabulary():
    path = PROJECT_ROOT / "demo" / "vocabulary.json"
    if not path.is_file():
        pytest.skip("Parser regressions require the real-source demo/vocabulary.json")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def offline_and_network_blocked(monkeypatch):
    monkeypatch.setenv("EARSHOT_OFFLINE", "1")
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    network_attempts = []
    sdk_attempts = []

    def deny_network(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError("Parser tests must never access the network")

    def deny_unmocked_sdk(*args, **kwargs):
        sdk_attempts.append(True)
        raise AssertionError("An online parser test must install an explicit fake SDK")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(parser, "OpenAI", deny_unmocked_sdk, raising=False)
    yield
    # These assertions also catch attempts swallowed by a parser fallback.
    assert not network_attempts, "A caught connection failure still counts as a network attempt"
    assert not sdk_attempts, "Offline/missing-credential parsing must not construct a provider client"


def _assert_expected(rule, expected):
    if expected is None:
        assert rule is None
        return
    assert rule is not None
    assert rule.scope == expected["scope"]
    assert rule.action == expected["action"]
    assert rule.confidence == 0.6
    assert rule.reversible is True
    assert rule.pattern["kind"] == "code_match"
    assert rule.pattern["conditions"] == []


@pytest.mark.parametrize("fixture", FIXTURES, ids=[row["id"] for row in FIXTURES])
def test_ten_real_vocabulary_fixtures_parse_offline(fixture, vocabulary):
    rule = parser.parse_utterance(fixture["text"], vocabulary)

    _assert_expected(rule, fixture["expected"])
    if rule is not None:
        assert rule.utterance == fixture["text"]
        assert rule.pattern["window_s"] == (60 if rule.action == "collapse" else 0)


def test_fixture_identifiers_are_grounded_in_supplied_vocabulary(vocabulary):
    assert len(FIXTURES) == 10
    assert len({row["id"] for row in FIXTURES}) == 10
    assert vocabulary["turbine_aliases"]["t04"] == "2304513"
    known_codes = {row["alarm_code"]: row["description"] for row in vocabulary["codes"]}
    for fixture in FIXTURES:
        expected = fixture["expected"]
        if expected is not None:
            assert expected["scope"]["alarm_code"] in known_codes
            assert known_codes[expected["scope"]["alarm_code"]] != "(undocumented)"
            assert expected["scope"]["turbine_id"] is None or expected["scope"]["turbine_id"] in vocabulary["turbines"]


@pytest.mark.parametrize("alias", ["T04", "turbine 4", "turbine four", "number four"])
def test_all_required_turbine_aliases_use_metadata_identity(alias, vocabulary):
    rule = parser.parse_utterance(f"ignore the cable untwist alarm on {alias}", vocabulary)

    assert rule is not None
    assert rule.scope == {"turbine_id": "2304513", "alarm_code": 10105, "signal": None}


@pytest.mark.parametrize(
    "text",
    [
        "ignore low wind on turbine 99",
        "ignore cable untwist on turbine999",
        "ignore cable untwist on turbine twenty three",
        "ignore cable untwist on T04X",
        "ignore cable untwist on turbine4a",
        "ignore low wind on turbine four and turbine two",
        "ignore cable untwist on turbine four and twenty one",
        "ignore cable untwist on turbine 4, 5",
        "ignore cable untwist but not on turbine four",
        "ignore cable untwist on turbine four at night",
        "ignore generator cut-in and low wind on turbine four",
        "ignore alarm 20 low wind on turbine four",
        "ignore code 20 and 25",
        "low wind on turbine four",
        "generator cut-in",
        "ignore everything",
        "never suppress",
    ],
)
def test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules(text, vocabulary):
    assert parser.parse_utterance(text, vocabulary) is None


def test_unknown_explicit_alarm_code_is_not_replaced_by_keyword_guess(vocabulary):
    unknown = max(row["alarm_code"] for row in vocabulary["codes"]) + 1
    text = f"ignore alarm {unknown} low wind on turbine four"

    assert parser.parse_utterance(text, vocabulary) is None


@pytest.mark.parametrize("intent", [
    "don't ignore", "never suppress", "do not ever ignore",
    "never ever suppress", "not ignore",
])
def test_negative_suppression_intent_becomes_escalation(intent, vocabulary):
    rule = parser.parse_utterance(f"{intent} low wind on turbine four", vocabulary)

    assert rule is not None and rule.action == "escalate"
    assert rule.scope == {"turbine_id": "2304513", "alarm_code": 1005, "signal": None}


def test_do_not_alarm_is_a_positive_suppression_request(vocabulary):
    rule = parser.parse_utterance("do not alarm about low wind on turbine four", vocabulary)

    assert rule is not None and rule.action == "suppress"
    assert rule.scope == {"turbine_id": "2304513", "alarm_code": 1005, "signal": None}


@pytest.mark.parametrize("text,action", [
    ("ignore the cable untwist alarm on T04", "suppress"),
    ("suppress the cable untwist alarm on T04", "suppress"),
    ("mute the cable untwist alarm on T04", "suppress"),
    ("stop telling me about the cable untwist alarm on T04", "suppress"),
    ("don't alarm about cable untwist on T04", "suppress"),
    ("the cable untwist alarm on T04, that's normal", "suppress"),
    ("the cable untwist alarm on T04, that's routine", "suppress"),
    ("group the cable untwist alarm on T04", "collapse"),
    ("collapse the cable untwist alarm on T04", "collapse"),
    ("one line for the cable untwist alarm on T04", "collapse"),
    ("just tell me once about the cable untwist alarm on T04", "collapse"),
    ("always tell me about the cable untwist alarm on T04", "escalate"),
    ("never hide the cable untwist alarm on T04", "escalate"),
    ("escalate the cable untwist alarm on T04", "escalate"),
])
def test_all_required_intent_phrases_resolve_real_alarm_scope(text, action, vocabulary):
    rule = parser.parse_utterance(text, vocabulary)

    assert rule is not None and rule.action == action
    assert rule.scope == {"turbine_id": "2304513", "alarm_code": 10105, "signal": None}
    assert rule.confidence == 0.6


@pytest.mark.parametrize(
    "text", [None, True, 42, [], {}, "", "   ", "x" * 4001],
    ids=["none", "boolean", "integer", "list", "mapping", "empty", "blank", "too-long"],
)
def test_invalid_text_returns_none_without_network(text, vocabulary):
    assert parser.parse_utterance(text, vocabulary) is None


@pytest.mark.parametrize("context", [None, [], {}, {"turbines": [], "codes": []}])
def test_missing_or_invalid_context_returns_none_without_network(context):
    assert parser.parse_utterance(FIXTURES[0]["text"], context) is None


def test_each_parse_has_fresh_provenance_without_mutating_context(vocabulary):
    context = deepcopy(vocabulary)
    before_context = deepcopy(context)
    before_time = datetime.now(timezone.utc)

    first = parser.parse_utterance(FIXTURES[0]["text"], context)
    second = parser.parse_utterance(FIXTURES[0]["text"], context)

    after_time = datetime.now(timezone.utc)
    assert first is not None and second is not None
    assert first.rule_id != second.rule_id
    assert context == before_context
    for rule in (first, second):
        stamp = datetime.fromisoformat(rule.taught_at.replace("Z", "+00:00"))
        assert stamp.tzinfo is not None
        assert before_time <= stamp <= after_time
        assert rule.taught_by


def _online_environment(monkeypatch):
    monkeypatch.setenv("EARSHOT_OFFLINE", "0")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "parser-test-placeholder")
    monkeypatch.setenv("LLM_MODEL", "parser-test-model")


def _provider_rule(text, *, scope=None, action="suppress"):
    return {
        "rule_id": "provider-proposed-rule",
        "utterance": text,
        "scope": scope or {"turbine_id": "2304513", "alarm_code": 10105, "signal": None},
        "pattern": {"kind": "code_match", "window_s": 0, "conditions": []},
        "action": action,
        "confidence": 0.9,
        "taught_by": "mock-provider",
        "taught_at": datetime.now(timezone.utc).isoformat(),
        "reversible": True,
    }


def _mock_provider(monkeypatch, replies, *, metadata=None):
    pending = list(replies)
    calls = SimpleNamespace(constructors=[], requests=[], closes=0)

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            calls.closes += 1

        def create(self, **kwargs):
            calls.requests.append(deepcopy(kwargs))
            assert pending, "Parser exceeded its bounded provider attempts"
            reply = pending.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return SimpleNamespace(**(metadata or {}), choices=[SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=reply, refusal=None),
            )])

    def factory(**kwargs):
        calls.constructors.append(kwargs)
        return FakeClient()

    monkeypatch.setattr(parser, "OpenAI", factory)
    return calls


def test_online_valid_result_uses_bounded_sdk_options(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    text = FIXTURES[0]["text"]
    calls = _mock_provider(monkeypatch, [json.dumps(_provider_rule(text))])

    rule = parser.parse_utterance(text, vocabulary)

    assert rule is not None and rule.scope == FIXTURES[0]["expected"]["scope"]
    assert rule.action == "suppress" and rule.confidence >= 0.85
    assert rule.rule_id != "provider-proposed-rule"
    assert rule.taught_by == "operator"
    assert len(calls.requests) == 1 and calls.closes == 1
    options = calls.constructors[0]
    assert options["max_retries"] == 0 and options["timeout"] == 8
    assert options["base_url"] == "https://example.invalid/v1"
    assert options["api_key"] == "parser-test-placeholder"
    request = calls.requests[0]
    assert request["temperature"] == 0 and request["model"] == "parser-test-model"
    assert request["max_tokens"] == 512
    assert "2304513" in json.dumps(request["messages"])
    assert "10105" in json.dumps(request["messages"])


def test_invalid_json_retries_once_with_validation_feedback(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    text = FIXTURES[0]["text"]
    calls = _mock_provider(monkeypatch, ["not JSON", json.dumps(_provider_rule(text))])

    rule = parser.parse_utterance(text, vocabulary)

    assert rule is not None and rule.confidence >= 0.85
    assert len(calls.requests) == 2 and calls.closes == 1
    assert len(calls.requests[1]["messages"]) > len(calls.requests[0]["messages"])


def test_two_invalid_responses_fall_back_to_offline(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    text = FIXTURES[0]["text"]
    calls = _mock_provider(monkeypatch, ["not JSON", "still not JSON"])

    rule = parser.parse_utterance(text, vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])
    assert len(calls.requests) == 2 and calls.closes == 1


def test_transport_failure_falls_back_immediately_without_retry(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    calls = _mock_provider(monkeypatch, [OSError("Simulated provider transport failure")])

    rule = parser.parse_utterance(FIXTURES[0]["text"], vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])
    assert len(calls.requests) == 1 and calls.closes == 1


def test_missing_credentials_use_offline_without_constructing_sdk(monkeypatch, vocabulary):
    monkeypatch.setenv("EARSHOT_OFFLINE", "0")

    rule = parser.parse_utterance(FIXTURES[0]["text"], vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])


@pytest.mark.parametrize("offline_switch", ["environment", "application"])
def test_offline_switch_prevents_sdk_even_with_credentials(monkeypatch, vocabulary, offline_switch):
    _online_environment(monkeypatch)
    if offline_switch == "environment":
        monkeypatch.setenv("EARSHOT_OFFLINE", "1")
    else:
        monkeypatch.setattr(parser, "OFFLINE_MODE", True)

    rule = parser.parse_utterance(FIXTURES[0]["text"], vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])


@pytest.mark.parametrize("text", [
    "low wind on turbine four",
    "what does cable untwist on turbine four mean",
    "what is the weather like",
])
def test_question_or_missing_intent_does_not_call_provider(monkeypatch, vocabulary, text):
    _online_environment(monkeypatch)
    calls = _mock_provider(monkeypatch, [])

    assert parser.parse_utterance(text, vocabulary) is None
    assert not calls.constructors and not calls.requests


@pytest.mark.parametrize("wrong_scope", [
    {"turbine_id": "2304510", "alarm_code": 10105, "signal": None},
    {"turbine_id": "2304513", "alarm_code": 20, "signal": None},
    {"turbine_id": None, "alarm_code": 10105, "signal": None},
])
def test_online_result_cannot_change_recognized_scope(monkeypatch, vocabulary, wrong_scope):
    _online_environment(monkeypatch)
    text = FIXTURES[0]["text"]
    response = json.dumps(_provider_rule(text, scope=wrong_scope))
    calls = _mock_provider(monkeypatch, [response, response])

    rule = parser.parse_utterance(text, vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])
    assert len(calls.requests) == 2


def test_online_result_cannot_reverse_negative_suppression_intent(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    fixture = next(row for row in FIXTURES if row["id"] == "dont_ignore_cut_in")
    response = json.dumps(_provider_rule(fixture["text"], scope=fixture["expected"]["scope"], action="suppress"))
    calls = _mock_provider(monkeypatch, [response, response])

    rule = parser.parse_utterance(fixture["text"], vocabulary)

    _assert_expected(rule, fixture["expected"])
    assert len(calls.requests) == 2


def test_online_result_rejects_a_code_outside_controlled_vocabulary(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    text = FIXTURES[0]["text"]
    unknown = max(row["alarm_code"] for row in vocabulary["codes"]) + 1
    response = json.dumps(_provider_rule(text, scope={
        "turbine_id": "2304513", "alarm_code": unknown, "signal": None,
    }))
    calls = _mock_provider(monkeypatch, [response, response])

    rule = parser.parse_utterance(text, vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])
    assert len(calls.requests) == 2


def test_online_result_rejects_a_turbine_outside_controlled_vocabulary(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    text = FIXTURES[0]["text"]
    unknown = str(max(int(value) for value in vocabulary["turbines"]) + 1)
    response = json.dumps(_provider_rule(text, scope={
        "turbine_id": unknown, "alarm_code": 10105, "signal": None,
    }))
    calls = _mock_provider(monkeypatch, [response, response])

    rule = parser.parse_utterance(text, vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])
    assert len(calls.requests) == 2


def test_offline_parse_teach_held_out_scope_and_undo_with_real_events(tmp_path, vocabulary):
    path = CONFIG.data.processed_dir / "alarms.parquet"
    if not path.is_file():
        pytest.skip("Parser-policy integration requires real processed alarms.parquet")
    alarms = pd.read_parquet(path)
    rule = parser.parse_utterance(FIXTURES[0]["text"], vocabulary)
    assert rule is not None
    scope = rule.scope
    matches = alarms.turbine_id.eq(scope["turbine_id"]) & alarms.alarm_code.eq(scope["alarm_code"])
    positions = matches.to_numpy().nonzero()[0]
    assert len(positions) > 1, "Integration needs real training and held-out scope matches"
    end = max(2000, int(positions[0]) + 1)
    recent = alarms.iloc[end - 2000:end]
    assert len(recent) == 2000
    expected_suppressed = int(matches.iloc[end - 2000:end].sum())
    assert expected_suppressed > 0
    later = alarms.iloc[end:]
    held_out = later.loc[later.turbine_id.eq(scope["turbine_id"]) & later.alarm_code.eq(scope["alarm_code"])]
    other_asset = later.loc[later.turbine_id.ne(scope["turbine_id"]) & later.alarm_code.eq(scope["alarm_code"])]
    assert not held_out.empty and not other_asset.empty
    policy = PolicyLayer(create_detector(), buffer_size=2000, rules_path=tmp_path / "rules.jsonl")
    for event in recent.to_dict("records"):
        policy.score(event)
    assert all(verdict.show for verdict in policy.verdicts)
    weights_before = policy.classifier.weights.copy()
    intercept_before = policy.classifier.intercept
    version_before = policy.model_version

    learned = policy.learn(rule)

    assert learned["newly_suppressed_count"] == expected_suppressed
    assert learned["examples_learned"] == 2000
    assert policy.model_version == version_before + 1
    assert policy.classifier.weights != weights_before
    probes = sorted([held_out.iloc[0].to_dict(), other_asset.iloc[0].to_dict()], key=lambda row: row["ts"])
    for event in probes:
        verdict = policy.score(event)
        if event["turbine_id"] == scope["turbine_id"]:
            assert not verdict.show and verdict.suppressed_by == rule.rule_id
        else:
            assert verdict.show and verdict.suppressed_by is None

    assert policy.undo(rule.rule_id) is True
    assert policy.model_version == version_before + 2
    assert not policy.active_rules
    assert all(verdict.show for verdict in policy.verdicts)
    assert policy.classifier.weights == weights_before
    assert policy.classifier.intercept == intercept_before
    assert policy.score(held_out.iloc[0].to_dict()).show


def test_offline_receipt_identifies_actual_local_execution(vocabulary):
    rule, evidence = parser.parse_utterance_with_evidence(FIXTURES[0]["text"], vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])
    assert evidence["source"] == "local" and evidence["fallback_reason"] == "offline"
    assert evidence["provider_attempted"] is False and evidence["attempts"] == 0
    assert all(evidence[field] is None for field in ("provider", "endpoint_host", "model", "requested_model", "request_id", "completion_id"))


@pytest.mark.parametrize("text,reason", [
    (None, "invalid_input"),
    ("what does cable untwist mean", "missing_intent"),
    ("ignore cable untwist on turbine999", "ambiguous_reference"),
    ("ignore cable untwist on turbine four at night", "unsupported_restriction"),
])
def test_unparsed_receipt_reports_guard_reason_without_provider(monkeypatch, vocabulary, text, reason):
    _online_environment(monkeypatch)
    rule, evidence = parser.parse_utterance_with_evidence(text, vocabulary)

    assert rule is None and evidence["source"] == "none"
    assert evidence["fallback_reason"] == reason
    assert evidence["provider_attempted"] is False and evidence["attempts"] == 0


def test_invalid_vocabulary_has_a_sanitized_receipt():
    rule, evidence = parser.parse_utterance_with_evidence(FIXTURES[0]["text"], None)
    assert rule is None and evidence["source"] == "none"
    assert evidence["fallback_reason"] == "invalid_vocabulary"


def test_missing_credentials_receipt_does_not_claim_provider_attempt(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    monkeypatch.delenv("LLM_API_KEY")
    rule, evidence = parser.parse_utterance_with_evidence(FIXTURES[0]["text"], vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])
    assert evidence["source"] == "local" and evidence["fallback_reason"] == "missing_credentials"
    assert not evidence["provider_attempted"] and evidence["request_id"] is None


def test_provider_receipt_uses_returned_model_and_ids_not_configured_guess(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
    text = FIXTURES[0]["text"]
    calls = _mock_provider(monkeypatch, [json.dumps(_provider_rule(text))], metadata={
        "model": "Qwen/actual-model-revision", "_request_id": "request-123", "id": "chatcmpl-456",
    })

    rule, evidence = parser.parse_utterance_with_evidence(text, vocabulary)

    assert rule is not None and evidence["source"] == "provider"
    assert evidence["provider"] == "Nebius" and evidence["endpoint_host"] == "api.tokenfactory.nebius.com"
    assert evidence["requested_model"] == "parser-test-model"
    assert evidence["model"] == "Qwen/actual-model-revision"
    assert evidence["request_id"] == "request-123" and evidence["completion_id"] == "chatcmpl-456"
    assert evidence["provider_attempted"] is True and evidence["attempts"] == len(calls.requests) == 1
    assert evidence["fallback_reason"] is None


def test_provider_response_without_metadata_is_not_misrepresented(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    text = FIXTURES[0]["text"]
    _mock_provider(monkeypatch, [json.dumps(_provider_rule(text))])

    rule, evidence = parser.parse_utterance_with_evidence(text, vocabulary)

    assert rule is not None and evidence["source"] == "provider"
    assert evidence["model"] is None and evidence["request_id"] is None
    assert evidence["requested_model"] == "parser-test-model"


def test_invalid_provider_responses_are_local_fallback_with_attempt_receipts(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    calls = _mock_provider(monkeypatch, ["provider body must stay private", "still invalid"], metadata={
        "model": "actual-model", "_request_id": "rejected-request", "id": "rejected-completion",
    })

    rule, evidence = parser.parse_utterance_with_evidence(FIXTURES[0]["text"], vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])
    assert evidence["source"] == "local" and evidence["fallback_reason"] == "provider_invalid_response"
    assert evidence["attempts"] == len(calls.requests) == 2
    assert evidence["request_id"] == "rejected-request"
    assert "provider body" not in json.dumps(evidence)


@pytest.mark.parametrize("status,reason", [(401, "provider_auth_error"), (429, "provider_rate_limited"), (503, "provider_http_error")])
def test_provider_http_failure_receipts_are_stable_and_sanitized(monkeypatch, vocabulary, status, reason):
    _online_environment(monkeypatch)

    class ProviderFailure(Exception):
        status_code = status
        request_id = "failed-provider-request"

    calls = _mock_provider(monkeypatch, [ProviderFailure("parser-test-placeholder and private response body")])
    rule, evidence = parser.parse_utterance_with_evidence(FIXTURES[0]["text"], vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])
    assert evidence["source"] == "local" and evidence["fallback_reason"] == reason
    assert evidence["http_status"] == status and evidence["request_id"] == "failed-provider-request"
    assert evidence["attempts"] == len(calls.requests) == 1
    serialized = json.dumps(evidence)
    assert "parser-test-placeholder" not in serialized and "private response" not in serialized


def test_transport_timeout_receipt_preserves_local_fallback(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    _mock_provider(monkeypatch, [TimeoutError("provider secret")])
    rule, evidence = parser.parse_utterance_with_evidence(FIXTURES[0]["text"], vocabulary)
    _assert_expected(rule, FIXTURES[0]["expected"])
    assert evidence["source"] == "local" and evidence["fallback_reason"] == "provider_timeout"
    assert "provider secret" not in json.dumps(evidence)


def test_receipts_strip_url_secrets_and_reflected_invalid_identifiers(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://person:embedded-secret@api.tokenfactory.nebius.com/private/path?key=query-secret#fragment")
    text = FIXTURES[0]["text"]
    _mock_provider(monkeypatch, [json.dumps(_provider_rule(text))], metadata={
        "model": "parser-test-placeholder", "_request_id": "parser-test-placeholder", "id": "bad\nidentifier",
    })

    rule, evidence = parser.parse_utterance_with_evidence(text, vocabulary)

    assert rule is not None and evidence["endpoint_host"] == "api.tokenfactory.nebius.com"
    assert evidence["model"] is None and evidence["request_id"] is None and evidence["completion_id"] is None
    serialized = json.dumps(evidence)
    assert all(fragment not in serialized for fragment in ("embedded-secret", "query-secret", "private/path", "parser-test-placeholder"))


def test_provider_and_offline_receipts_do_not_share_mutable_state(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    text = FIXTURES[0]["text"]
    _mock_provider(monkeypatch, [json.dumps(_provider_rule(text))], metadata={"_request_id": "first-request", "model": "actual-model"})
    _, first = parser.parse_utterance_with_evidence(text, vocabulary)
    first_snapshot = deepcopy(first)
    monkeypatch.setenv("EARSHOT_OFFLINE", "1")

    _, second = parser.parse_utterance_with_evidence(text, vocabulary)

    assert first == first_snapshot and first is not second
    assert first["source"] == "provider" and first["request_id"] == "first-request"
    assert second["source"] == "local" and second["request_id"] is None and second["attempts"] == 0


def test_switch_after_provider_response_records_discard_and_local_execution(monkeypatch, vocabulary):
    _online_environment(monkeypatch)
    text = FIXTURES[0]["text"]
    original_accept = parser._accept_online

    def switch_after_validation(*args, **kwargs):
        rule = original_accept(*args, **kwargs)
        monkeypatch.setattr(parser, "OFFLINE_MODE", True)
        return rule

    monkeypatch.setattr(parser, "_accept_online", switch_after_validation)
    _mock_provider(monkeypatch, [json.dumps(_provider_rule(text))], metadata={"_request_id": "discarded-request"})
    rule, evidence = parser.parse_utterance_with_evidence(text, vocabulary)

    _assert_expected(rule, FIXTURES[0]["expected"])
    assert evidence["source"] == "local" and evidence["fallback_reason"] == "offline_changed"
    assert evidence["provider_attempted"] is True and evidence["request_id"] == "discarded-request"
