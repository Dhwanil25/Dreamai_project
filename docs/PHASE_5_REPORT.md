# Phase 5 — local detection and teaching engine

Phase 5 implements the core teach, suppress, persist and undo loop. A correction for station `2304521`, code `25`, changed a buffer of 2,000 real alarm records from **2,000 shown / 0 suppressed** to **1,983 shown / 17 suppressed**. Learning took **33.016 ms**, including durable local journal writes, classifier training and rescoring. The same code remained visible on station `2304511`. All **215 tests passed**, including **31 policy tests**.

This is a verification of correction mechanics on replayed source events. The prompt's sample correction is a test instruction, not a ground-truth nuisance label. Neither its 0.85% suppression fraction nor classifier confidence establishes false-alarm accuracy. No source records or sensor values were synthesized for these checks.

## Implemented behavior

- `earshot/rules.py`: exactly the nine specified fields, strict validation, deterministic JSON serialization, None wildcards and AND conditions. Missing/nonfinite condition values do not match. Conditions use literal field lookups and supported comparisons, with no expression execution.
- `earshot/detector.py`: rolling population Z-scores and seeded River Half-Space Trees with MinMaxScaler and explicit [0, 1] bounds. Scoring does not learn; update fits once. Numeric features remain opaque to the detector. `create_detector()` selects the configured method. Policy clones detectors per asset so physical baselines do not mix.
- `earshot/policy.py`: bounded replay observations, rules, logistic classifier, model revisions, six statistics, rescoring and undo. Teaching uses real matching examples and recent counter-examples, preserving arrival order and balancing class weights. Existing positives belonging to another correction are excluded from the new rule's negative set and are not copied into its batch.
- `config.yaml`: policy probability 0.9, SGD learning rate 0.1 and default collapse window 60 seconds. The detector remains Z-score with a 288-observation window.
- Backend and static page: Phase 5 library status. Live teaching, rules, statistics and undo APIs remain intentional HTTP 501 responses until Phase 7. Voice, parsing and automatic replay remain pending.

Code-match and conditions rules suppress deterministically. A `learned` pattern additionally requires classifier probability to reach the configured threshold, inside all of that rule's scope and conditions. This bounded classifier path prevents a correction for one asset from silently suppressing another asset's events. A dedicated test verifies that the rare 17-positive / 2,000-record case learns above 0.9; this path changes visibility because of actual classifier learning.

Collapse keeps the first matching event per rule and asset, hides repeats inside its window, and reopens at the window boundary. The rule's nonzero `window_s` overrides the default. An escalation or an explicit `critical=True` / critical or emergency severity takes precedence over suppression. The source `stopping` label alone does not establish critical severity. A wildcard rule suppresses broadly except for these explicit protections.

## Local persistence and undo

The default ledger is `data/processed/rules.jsonl`, which remains Git-ignored. Each operation is an append-only JSON object with `schema_version: 1`:

1. `learn` records the exact validated rule before training.
2. `commit` records its ID, examples, labels, weights, model revision and training settings after training succeeds.
3. `undo` revokes a reversible rule and records the next revision.

A complete learn record without a commit has no effect after restart. Invalid or truncated JSON fails with an explicit error rather than silently discarding history. In-process write failures attempt to remove their partial append. This is a single-writer file journal, without a multiprocess lock or automatic repair of externally damaged files.

Restart reconstructs the active classifier by replaying saved batches in order. Undo rebuilds it from the remaining active batches, removing the revoked correction's batch as well as its deterministic rule. Committed IDs cannot be reused, including after undo. Rule and event snapshots prevent caller mutations from rewriting accepted state. Each successful learn and undo increments the version; missing or irreversible undo does not.

The validation below created the local default ledger, demonstrated restoration, then revoked the sample correction. The retained audit history is at **version 3 with zero active rules**. Use a new rule ID for a new correction. Repeatable README examples and unit tests use temporary ledgers.

Detector history, replay buffers and visibility counters are not persisted in this phase. Rescoring changes only buffered verdicts and never fits the anomaly detector a second time. Evicted records retain their last visibility contribution to lifetime counters. `alarms_per_hour_current` counts shown buffered events in the trailing hour ending at the latest event timestamp; it can undercount when the buffer does not contain that whole hour. It does not extrapolate short intervals.

Alarm-only events receive an anomaly score of zero because they have no numeric sensor measurement. SCADA values can be supplied through numeric signals/features or a scalar signal/value pair. HST establishes its feature schema on the first nonmissing update; later new feature names require a fresh detector. Z-scores and HST scores have different scales and are not incident probabilities.

## Required before/after validation

The plan's first 2,000 Parquet records and sample `r1` rule were used without changing the source data. Additional checks verified exact classifier reconstruction, cross-turbine scope, undo and restart after undo. Outbound socket connections were blocked throughout scoring, teaching, persistence and restoration. This verifies offline execution of this local engine; it is not a completed voice or browser demo.

```text
before: {'alarms_shown': 2000, 'alarms_suppressed': 0, 'suppression_rate': 0.0, 'model_version': 1, 'active_rules': 0, 'alarms_per_hour_current': 25.0}
learn result: {'rule_id': 'r1', 'examples_learned': 2000, 'model_version': 2, 'newly_suppressed_count': 17}
learn ms: 33.016
after: {'alarms_shown': 1983, 'alarms_suppressed': 17, 'suppression_rate': 0.0085, 'model_version': 2, 'active_rules': 1, 'alarms_per_hour_current': 25.0}
rule scope: {'turbine_id': '2304521', 'alarm_code': 25, 'signal': None}
classifier nonzero weights: 269
restart restores exact classifier and version: True
matching event suppressed: True
same code on different turbine remains visible: True 2304511
after undo: {'alarms_shown': 2000, 'alarms_suppressed': 0, 'suppression_rate': 0.0, 'model_version': 3, 'active_rules': 0, 'alarms_per_hour_current': 25.0}
undo removes classifier weights: True
restart after undo: {'model_version': 3, 'active_rules': 0, 'empty_classifier': True}
outbound network connections blocked throughout: PASS
demo correction revoked; local journal retained: data/processed/rules.jsonl
```

The learning measurement is one run on this checkout's macOS arm64 / Python 3.13.7 environment, not a latency guarantee across hardware or larger ledgers. All 2,000 examples were trained and 269 classifier feature weights became nonzero. Buffered visibility and classifier weights were restored by undo.

## Full required policy pytest output

```text
============================= test session starts ==============================
platform darwin -- Python 3.13.7, pytest-9.1.1, pluggy-1.6.0 -- /Users/dhwanil/Desktop/DOM/Dreamai_Project/venv/bin/python
cachedir: .pytest_cache
rootdir: /Users/dhwanil/Desktop/DOM/Dreamai_Project
configfile: pytest.ini
plugins: anyio-4.15.1
collecting ... collected 31 items

tests/test_policy.py::test_teaching_scoped_rule_suppresses_only_its_asset_and_code PASSED [  3%]
tests/test_policy.py::test_learn_updates_real_classifier_and_finishes_below_500ms PASSED [  6%]
tests/test_policy.py::test_undo_restores_visibility_and_removes_classifier_training PASSED [  9%]
tests/test_policy.py::test_undo_latest_rule_preserves_earlier_model_exactly PASSED [ 12%]
tests/test_policy.py::test_later_rule_does_not_carry_earlier_positive_labels_through_undo PASSED [ 16%]
tests/test_policy.py::test_journal_reload_reconstructs_rules_learning_and_undo PASSED [ 19%]
tests/test_policy.py::test_uncommitted_learning_has_no_effect_in_memory_or_after_restart PASSED [ 22%]
tests/test_policy.py::test_unknown_and_nonreversible_undo_do_not_change_state PASSED [ 25%]
tests/test_policy.py::test_rule_and_buffer_snapshots_cannot_rewrite_live_policy PASSED [ 29%]
tests/test_policy.py::test_all_none_scope_documents_broad_wildcard_behavior PASSED [ 32%]
tests/test_policy.py::test_explicit_critical_events_survive_matching_suppression[annotation0] PASSED [ 35%]
tests/test_policy.py::test_explicit_critical_events_survive_matching_suppression[annotation1] PASSED [ 38%]
tests/test_policy.py::test_explicit_critical_events_survive_matching_suppression[annotation2] PASSED [ 41%]
tests/test_policy.py::test_stopping_label_is_not_automatically_a_critical_override PASSED [ 45%]
tests/test_policy.py::test_classifier_only_rule_learns_and_never_crosses_asset_scope PASSED [ 48%]
tests/test_policy.py::test_rare_real_scope_learns_above_configured_probability PASSED [ 51%]
tests/test_policy.py::test_reload_rejects_invalid_persisted_training_weights[0] PASSED [ 54%]
tests/test_policy.py::test_reload_rejects_invalid_persisted_training_weights[-1] PASSED [ 58%]
tests/test_policy.py::test_reload_rejects_invalid_persisted_training_weights[inf] PASSED [ 61%]
tests/test_policy.py::test_reload_rejects_invalid_persisted_training_weights[0.5] PASSED [ 64%]
tests/test_policy.py::test_reload_rejects_invalid_persisted_training_weights[True] PASSED [ 67%]
tests/test_policy.py::test_reload_validates_persisted_settings_before_rebuilding_model[nuisance_threshold-0.1] PASSED [ 70%]
tests/test_policy.py::test_reload_validates_persisted_settings_before_rebuilding_model[nuisance_threshold-1.1] PASSED [ 74%]
tests/test_policy.py::test_reload_validates_persisted_settings_before_rebuilding_model[learning_rate-0.0] PASSED [ 77%]
tests/test_policy.py::test_reload_validates_persisted_settings_before_rebuilding_model[learning_rate--0.1] PASSED [ 80%]
tests/test_policy.py::test_reload_validates_persisted_settings_before_rebuilding_model[learning_rate-inf] PASSED [ 83%]
tests/test_policy.py::test_reload_validates_persisted_settings_before_rebuilding_model[collapse_window_s-0] PASSED [ 87%]
tests/test_policy.py::test_escalation_overrides_broad_suppression_and_undo_restores_it PASSED [ 90%]
tests/test_policy.py::test_collapse_keeps_first_real_event_and_reopens_after_window PASSED [ 93%]
tests/test_policy.py::test_rescore_is_idempotent_and_does_not_update_detector PASSED [ 96%]
tests/test_policy.py::test_bounded_buffer_rescore_preserves_evicted_cumulative_counts PASSED [100%]

============================== 31 passed in 1.64s ==============================
```

## Full regression output

```text
........................................................................ [ 33%]
........................................................................ [ 66%]
.......................................................................  [100%]
=============================== warnings summary ===============================
venv/lib/python3.13/site-packages/starlette/testclient.py:53
  /Users/dhwanil/Desktop/DOM/Dreamai_Project/venv/lib/python3.13/site-packages/starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
215 passed, 1 warning in 5.37s
```

The existing Starlette/AnyIO deprecation warning remains. No tests failed or skipped with the supplied local data. Data-dependent tests explicitly skip in checkouts without the required Parquet evidence.

After restarting the local backend, `/`, `/health` and `/openapi.json` returned HTTP 200. Health reported Phase 5 with detection and teaching library capabilities available, and replay/voice unavailable. `/stats` and `/teach` returned their expected HTTP 501 responses. The README's temporary-ledger example also ran successfully, and the original alarm Parquet checksum was unchanged.

## Reproduction and phase boundary

Run from the repository root:

```bash
./venv/bin/pytest tests/test_policy.py -v
./venv/bin/pytest -q
```

The [README teaching example](../README.md#verify-the-local-teaching-engine) prints before, learn, after and undo statistics using a temporary ledger. The source alarm SHA-256 remains `9b99821be20e5758765e206107c0bd0e4a4225d79ac860908937320fc192220e`. The raw archives and processed Parquet were not rebuilt or modified.

Phase 6 has not begun. Natural-language parsing is next; live API integration, replay and voice follow their later execution-plan phases.
