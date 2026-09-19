# Phase 6 — text to validated correction

> Historical phase document. Current provider results, source verification and removal of prerecorded input/cache paths are documented in [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md). Earlier measurements describe the earlier build.

Phase 6 implements `parse_utterance(text, context)`, the real-source vocabulary builder and ten fixture utterances. The parser returns a proposed `SuppressionRule` or `None`. It never calls `learn`, writes a correction ledger or changes event visibility by itself.

All **314 tests passed**, including **83 parser tests** and **16 vocabulary tests**. The text-to-learning demonstration used 2,000 actual records, updated the classifier in **16.565 ms**, hid a later matching event, preserved the same code on another turbine, reconstructed the model after restart and restored visibility on undo. These are correction-mechanics checks, not false-alarm accuracy measurements.

## How teaching works

1. An operator supplies an instruction such as “ignore the cable untwist alarm on turbine four.”
2. The parser resolves the instruction using actual metadata: T04 is station `2304513`; the observed cable-untwist description belongs to code `10105`. It proposes a scoped rule with suppress intent.
3. Calling `policy.learn(rule)` persists the correction, labels matching buffered records as operator-provided nuisance examples and other eligible records as counter-examples, then updates the local River logistic classifier. The model revision increments.
4. Rescoring changes buffered visibility, and future matching events use the updated policy. Code-match rules apply directly; classifier weights also change. The Phase 5 `learned` pattern is the separate path where classifier probability determines suppression inside an authorized scope. Phase 6 proposes ordinary code-match rules.
5. Undo removes the rule and rebuilds the classifier from remaining training batches, restoring buffered visibility.

This is supervised feedback to a local classifier, not fine-tuning a general-purpose language model. The operator's test instruction does not prove the underlying alarm is harmless. Parsing and applying a correction are distinct operations; voice and the live browser/API loop follow in later phases.

## Controlled vocabulary and fixtures

`demo/vocabulary.json` contains **22 observed station IDs**, **192 observed codes**, **7 documented descriptions**, and **174 aliases** derived from the real metadata. The 21 named turbines retain their actual identities. Unmapped station `91` remains a raw identifier with no invented turbine number. Code `10105` is chosen from its actual description; the parser never hardcodes that ID as the meaning of untwisting.

The builder reads `alarms.parquet`, the supplied alarm-description CSV and turbine metadata. It rejects conflicting descriptions, ambiguous aliases and missing inputs, then atomically publishes deterministic JSON with source SHA-256 receipts. Rebuilding produced byte-identical output. All three source checksums still match; raw files and Parquet were not modified.

These are the real descriptions used across the ten fixtures:

| Code | Exact source description |
|---:|---|
| 20 | Large generator Cut-in |
| 25 | Fast cut-out of generator |
| 1005 | Availability - low wind |
| 3130 | Pitch lubrication |
| 8000 | Windspeed too high to operate |
| 8230 | Ice detection: Low torque |
| 10105 | Stopped, untwisting cables |

The other 185 observed codes retain `(undocumented)` and are not keyword matched. They can still be addressed explicitly by an observed alarm-code number. The required generator-winding-temperature phrase has no corresponding documented alarm here and returns `None`; a SCADA temperature channel does not establish an alarm-code mapping.

The icing instruction resolves only to `8230`, the sole documented ice code in this vocabulary. It collapses repeats of that code per turbine within the configured 60 seconds; it does not invent the six-code family implied by the example's wording. Fixtures are operator test utterances, not extra source events or verified nuisance labels.

## Parsing behavior and online boundary

The offline parser supports the required T04 / turbine 4 / turbine four / number four references, the specified keyword families and intent phrases. Normalized keywords are matched against real descriptions. One named asset remains one asset; omitting the asset yields a None wildcard. Different assets/codes, unknown references, conflicting intent, exclusions and unsupported time/sensor conditions return no proposal rather than silently widening the supported scope.

“Don't ignore,” “never suppress,” and the tested emphatic variants produce escalation when a real alarm can be resolved. Residual unsupported negation returns no rule. Unparseable questions and missing correction intent also return `None`. Proposed rules use fresh local IDs, UTC timestamps and confidence **0.6 offline** or **at least 0.85 online**. These confidence constants are not calibrated correctness probabilities.

With `EARSHOT_OFFLINE=1` or the module's forced-offline flag, no provider client is constructed. Otherwise all three configured `LLM_*` settings are needed for the optional online path. The installed OpenAI SDK uses the specified compatible base URL, an 8-second request timeout, no SDK retries and temperature zero. Invalid returned content gets one application retry with validation feedback; transport failure falls back immediately. Two attempts can take longer than eight seconds in total.

The system prompt supplies the exact nine-field contract, supported nested scope/pattern schema, and controlled vocabulary. Returned rules are checked for strict JSON/schema validity, observed IDs, recognized scope and intent, confidence, original utterance and reversibility. Locally generated provenance replaces provider-generated identifiers. JSON mode alone does not enforce the application schema. [Official OpenAI structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs)

Online parsing can resolve description paraphrases, but still requires a locally recognized correction action and preserves any recognized asset/code restriction. The supported output is one alarm-class rule with no invented sensor predicates. Ordinary parsing, context and provider errors return `None` or an offline result rather than escaping to the caller.

**Online path untested - no credentials.** No `.env` file or LLM credentials were present. The three optional live-provider fixtures were therefore not run. The request contract, validation retry, transport fallback, unknown IDs, changed scope, reversed negation and offline switches were tested with local mocks and sockets blocked. Successful mock tests are not a claim of live endpoint compatibility.

Optional online parsing sends the utterance and controlled vocabulary to the configured endpoint. Keep `EARSHOT_OFFLINE=1` for the never-phones-home demonstration. Teaching and classifier reconstruction remain local; the parser does not send raw event buffers or model weights.

## Required five-sentence output

```text
vocabulary rebuild byte-identical: True
'ignore the cable untwist alarm on turbine four' -> {"action":"suppress","confidence":0.6,"pattern":{"conditions":[],"kind":"code_match","window_s":0},"reversible":true,"rule_id":"41323655-e7d0-482c-b8b8-28f0b7d5ee00","scope":{"alarm_code":10105,"signal":null,"turbine_id":"2304513"},"taught_at":"2026-09-19T20:32:01.279886+00:00","taught_by":"operator","utterance":"ignore the cable untwist alarm on turbine four"}
'generator cut-in is just the wind changing, stop showing me those' -> {"action":"suppress","confidence":0.6,"pattern":{"conditions":[],"kind":"code_match","window_s":0},"reversible":true,"rule_id":"5111a931-9561-42c0-92eb-e7899156f5c5","scope":{"alarm_code":20,"signal":null,"turbine_id":null},"taught_at":"2026-09-19T20:32:01.280384+00:00","taught_by":"operator","utterance":"generator cut-in is just the wind changing, stop showing me those"}
'when it is icing give me one line per turbine not six' -> {"action":"collapse","confidence":0.6,"pattern":{"conditions":[],"kind":"code_match","window_s":60},"reversible":true,"rule_id":"099c6764-20c3-4ca8-9d55-5bae701f9c2b","scope":{"alarm_code":8230,"signal":null,"turbine_id":null},"taught_at":"2026-09-19T20:32:01.280815+00:00","taught_by":"operator","utterance":"when it is icing give me one line per turbine not six"}
'never suppress the generator winding temperature alarms' -> None
'what is the weather like' -> None
["2304510", "2304511", "2304512", "2304513", "2304514"]
192 codes in vocabulary
negation did not produce a suppress rule: PASS
online path untested - no credentials
local sockets blocked throughout: PASS
```

The required negation sentence did not create a suppress rule. A separate fixture for “don't ignore generator cut-in on turbine four” returns escalation for real code `20` on station `2304513`.

## Full text-to-teaching demonstration

The default untwist/T04 event first occurs on January 28, outside the first 2,000 rows. The demo therefore selects a contiguous real 2,000-record interval ending at that event. It checks a later matching record on February 26 and a later same-code event on another turbine, using a temporary ledger. The site's existing correction history stays unchanged.

```text
Operator: ignore the cable untwist alarm on turbine four
Proposed rule: {"action":"suppress","confidence":0.6,"pattern":{"conditions":[],"kind":"code_match","window_s":0},"reversible":true,"rule_id":"5ee87980-f318-4311-b3b1-216f70ab6d1b","scope":{"alarm_code":10105,"signal":null,"turbine_id":"2304513"},"taught_at":"2026-09-19T20:32:01.735521+00:00","taught_by":"operator","utterance":"ignore the cable untwist alarm on turbine four"}
Real buffer: 2000 events, 2025-01-26 03:21:58 to 2025-01-28 05:40:22
Before: {"active_rules": 0, "alarms_per_hour_current": 66.0, "alarms_shown": 2000, "alarms_suppressed": 0, "model_version": 1, "suppression_rate": 0.0}
Learn: {"examples_learned": 2000, "model_version": 2, "newly_suppressed_count": 1, "rule_id": "5ee87980-f318-4311-b3b1-216f70ab6d1b"}
Learn milliseconds: 16.565
After: {"active_rules": 1, "alarms_per_hour_current": 65.0, "alarms_shown": 1999, "alarms_suppressed": 1, "model_version": 2, "suppression_rate": 0.0005}
Nonzero classifier weights: 233
Restart preserves classifier: True
Later real matching event: 2025-02-26 16:53:11 show = False
Same code on another turbine: 2304523 show = True
Undo: {"active_rules": 0, "alarms_per_hour_current": 66.0, "alarms_shown": 2000, "alarms_suppressed": 0, "model_version": 3, "suppression_rate": 0.0}
Temporary demonstration complete; the site's saved corrections were unchanged.
```

The measured duration includes learning, local journal writes and rescoring on this machine. It is one execution, not a hardware-independent latency guarantee. The held-out event follows the same explicit rule; it is not evidence of learned generalization to a new physical pattern.

## Full parser pytest output

```text
============================= test session starts ==============================
platform darwin -- Python 3.13.7, pytest-9.1.1, pluggy-1.6.0 -- /Users/dhwanil/Desktop/DOM/Dreamai_Project/venv/bin/python
cachedir: .pytest_cache
rootdir: /Users/dhwanil/Desktop/DOM/Dreamai_Project
configfile: pytest.ini
plugins: anyio-4.15.1
collecting ... collected 83 items

tests/test_parse.py::test_ten_real_vocabulary_fixtures_parse_offline[untwist_turbine_four] PASSED [  1%]
tests/test_parse.py::test_ten_real_vocabulary_fixtures_parse_offline[generator_cut_in_all_turbines] PASSED [  2%]
tests/test_parse.py::test_ten_real_vocabulary_fixtures_parse_offline[icing_collapse] PASSED [  3%]
tests/test_parse.py::test_ten_real_vocabulary_fixtures_parse_offline[unmapped_temperature_negation] PASSED [  4%]
tests/test_parse.py::test_ten_real_vocabulary_fixtures_parse_offline[unparseable_weather] PASSED [  6%]
tests/test_parse.py::test_ten_real_vocabulary_fixtures_parse_offline[low_wind_t04] PASSED [  7%]
tests/test_parse.py::test_ten_real_vocabulary_fixtures_parse_offline[pitch_turbine_4] PASSED [  8%]
tests/test_parse.py::test_ten_real_vocabulary_fixtures_parse_offline[high_wind_number_four] PASSED [  9%]
tests/test_parse.py::test_ten_real_vocabulary_fixtures_parse_offline[dont_ignore_cut_in] PASSED [ 10%]
tests/test_parse.py::test_ten_real_vocabulary_fixtures_parse_offline[generator_cut_out_t04] PASSED [ 12%]
tests/test_parse.py::test_fixture_identifiers_are_grounded_in_supplied_vocabulary PASSED [ 13%]
tests/test_parse.py::test_all_required_turbine_aliases_use_metadata_identity[T04] PASSED [ 14%]
tests/test_parse.py::test_all_required_turbine_aliases_use_metadata_identity[turbine 4] PASSED [ 15%]
tests/test_parse.py::test_all_required_turbine_aliases_use_metadata_identity[turbine four] PASSED [ 16%]
tests/test_parse.py::test_all_required_turbine_aliases_use_metadata_identity[number four] PASSED [ 18%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore low wind on turbine 99] PASSED [ 19%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore cable untwist on turbine999] PASSED [ 20%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore cable untwist on turbine twenty three] PASSED [ 21%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore cable untwist on T04X] PASSED [ 22%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore cable untwist on turbine4a] PASSED [ 24%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore low wind on turbine four and turbine two] PASSED [ 25%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore cable untwist on turbine four and twenty one] PASSED [ 26%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore cable untwist on turbine 4, 5] PASSED [ 27%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore cable untwist but not on turbine four] PASSED [ 28%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore cable untwist on turbine four at night] PASSED [ 30%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore generator cut-in and low wind on turbine four] PASSED [ 31%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore alarm 20 low wind on turbine four] PASSED [ 32%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore code 20 and 25] PASSED [ 33%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[low wind on turbine four] PASSED [ 34%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[generator cut-in] PASSED [ 36%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[ignore everything] PASSED [ 37%]
tests/test_parse.py::test_unknown_ambiguous_or_incomplete_requests_do_not_create_rules[never suppress] PASSED [ 38%]
tests/test_parse.py::test_unknown_explicit_alarm_code_is_not_replaced_by_keyword_guess PASSED [ 39%]
tests/test_parse.py::test_negative_suppression_intent_becomes_escalation[don't ignore] PASSED [ 40%]
tests/test_parse.py::test_negative_suppression_intent_becomes_escalation[never suppress] PASSED [ 42%]
tests/test_parse.py::test_negative_suppression_intent_becomes_escalation[do not ever ignore] PASSED [ 43%]
tests/test_parse.py::test_negative_suppression_intent_becomes_escalation[never ever suppress] PASSED [ 44%]
tests/test_parse.py::test_negative_suppression_intent_becomes_escalation[not ignore] PASSED [ 45%]
tests/test_parse.py::test_do_not_alarm_is_a_positive_suppression_request PASSED [ 46%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[ignore the cable untwist alarm on T04-suppress] PASSED [ 48%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[suppress the cable untwist alarm on T04-suppress] PASSED [ 49%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[mute the cable untwist alarm on T04-suppress] PASSED [ 50%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[stop telling me about the cable untwist alarm on T04-suppress] PASSED [ 51%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[don't alarm about cable untwist on T04-suppress] PASSED [ 53%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[the cable untwist alarm on T04, that's normal-suppress] PASSED [ 54%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[the cable untwist alarm on T04, that's routine-suppress] PASSED [ 55%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[group the cable untwist alarm on T04-collapse] PASSED [ 56%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[collapse the cable untwist alarm on T04-collapse] PASSED [ 57%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[one line for the cable untwist alarm on T04-collapse] PASSED [ 59%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[just tell me once about the cable untwist alarm on T04-collapse] PASSED [ 60%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[always tell me about the cable untwist alarm on T04-escalate] PASSED [ 61%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[never hide the cable untwist alarm on T04-escalate] PASSED [ 62%]
tests/test_parse.py::test_all_required_intent_phrases_resolve_real_alarm_scope[escalate the cable untwist alarm on T04-escalate] PASSED [ 63%]
tests/test_parse.py::test_invalid_text_returns_none_without_network[none] PASSED [ 65%]
tests/test_parse.py::test_invalid_text_returns_none_without_network[boolean] PASSED [ 66%]
tests/test_parse.py::test_invalid_text_returns_none_without_network[integer] PASSED [ 67%]
tests/test_parse.py::test_invalid_text_returns_none_without_network[list] PASSED [ 68%]
tests/test_parse.py::test_invalid_text_returns_none_without_network[mapping] PASSED [ 69%]
tests/test_parse.py::test_invalid_text_returns_none_without_network[empty] PASSED [ 71%]
tests/test_parse.py::test_invalid_text_returns_none_without_network[blank] PASSED [ 72%]
tests/test_parse.py::test_invalid_text_returns_none_without_network[too-long] PASSED [ 73%]
tests/test_parse.py::test_missing_or_invalid_context_returns_none_without_network[None] PASSED [ 74%]
tests/test_parse.py::test_missing_or_invalid_context_returns_none_without_network[context1] PASSED [ 75%]
tests/test_parse.py::test_missing_or_invalid_context_returns_none_without_network[context2] PASSED [ 77%]
tests/test_parse.py::test_missing_or_invalid_context_returns_none_without_network[context3] PASSED [ 78%]
tests/test_parse.py::test_each_parse_has_fresh_provenance_without_mutating_context PASSED [ 79%]
tests/test_parse.py::test_online_valid_result_uses_bounded_sdk_options PASSED [ 80%]
tests/test_parse.py::test_invalid_json_retries_once_with_validation_feedback PASSED [ 81%]
tests/test_parse.py::test_two_invalid_responses_fall_back_to_offline PASSED [ 83%]
tests/test_parse.py::test_transport_failure_falls_back_immediately_without_retry PASSED [ 84%]
tests/test_parse.py::test_missing_credentials_use_offline_without_constructing_sdk PASSED [ 85%]
tests/test_parse.py::test_offline_switch_prevents_sdk_even_with_credentials[environment] PASSED [ 86%]
tests/test_parse.py::test_offline_switch_prevents_sdk_even_with_credentials[application] PASSED [ 87%]
tests/test_parse.py::test_question_or_missing_intent_does_not_call_provider[low wind on turbine four] PASSED [ 89%]
tests/test_parse.py::test_question_or_missing_intent_does_not_call_provider[what does cable untwist on turbine four mean] PASSED [ 90%]
tests/test_parse.py::test_question_or_missing_intent_does_not_call_provider[what is the weather like] PASSED [ 91%]
tests/test_parse.py::test_online_result_cannot_change_recognized_scope[wrong_scope0] PASSED [ 92%]
tests/test_parse.py::test_online_result_cannot_change_recognized_scope[wrong_scope1] PASSED [ 93%]
tests/test_parse.py::test_online_result_cannot_change_recognized_scope[wrong_scope2] PASSED [ 95%]
tests/test_parse.py::test_online_result_cannot_reverse_negative_suppression_intent PASSED [ 96%]
tests/test_parse.py::test_online_result_rejects_a_code_outside_controlled_vocabulary PASSED [ 97%]
tests/test_parse.py::test_online_result_rejects_a_turbine_outside_controlled_vocabulary PASSED [ 98%]
tests/test_parse.py::test_offline_parse_teach_held_out_scope_and_undo_with_real_events PASSED [100%]

============================== 83 passed in 0.87s ==============================
```

## Full regression output

```text
........................................................................ [ 22%]
........................................................................ [ 45%]
........................................................................ [ 68%]
........................................................................ [ 91%]
..........................                                               [100%]
=============================== warnings summary ===============================
venv/lib/python3.13/site-packages/starlette/testclient.py:53
  /Users/dhwanil/Desktop/DOM/Dreamai_Project/venv/lib/python3.13/site-packages/starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
314 passed, 1 warning in 5.85s
```

No tests failed or skipped with the supplied local data. The existing Starlette/AnyIO deprecation warning remains. Data-dependent integration checks skip explicitly if their real local Parquet is unavailable. The checked-in vocabulary permits parser unit tests without the raw archive.

After restart, the backend returned HTTP 200 at `/`, `/health` and `/openapi.json`. Health reports Phase 6, with parsing/detection/teaching library capabilities and replay/voice unavailable. `/stats` and `/teach` still return intentional HTTP 501 responses until Phase 7.

## Reproduce

```bash
./venv/bin/python scripts/build_vocabulary.py
EARSHOT_OFFLINE=1 ./venv/bin/pytest tests/test_parse.py -v
EARSHOT_OFFLINE=1 ./venv/bin/python scripts/demo_teaching.py
./venv/bin/pytest -q
```

Run from the repository root. The vocabulary builder needs the actual local Parquet and companion CSVs; it has no fallback dataset. The teaching demo accepts `--text`, uses a temporary ledger and leaves the site's saved corrections untouched.

Phase 7 has not begun. There is no live teaching endpoint, replay stream or microphone integration in this phase.
