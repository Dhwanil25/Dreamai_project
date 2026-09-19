# Phase 7 — replay and live API

Phase 7 connects the real dataset, parser, local policy, HTTP handlers and WebSockets in one process. **364 tests passed**, including **61 targeted replay/server/integration checks**. The live ten-second verification received **213 events at 21.298909 events/second**, with the replay clock configured to **600×**. Both connected clients received teaching and offline-switch updates. No request in the live validation returned an HTTP error.

The test server was shut down cleanly after verification. Its two demonstration rules were undone; the previous active-rule set and offline setting were restored. The local audit ledger remains at **model version 7 with zero active rules**. Phase 8 has not begun.

## Replay and source preservation

The SCADA Parquet is signal-major, so reading its rows directly would move backward in time. `Replayer` uses bounded filtered time slices, sorts each slice, groups readings at the same timestamp and turbine, then merges them with the alarm stream. It starts with day-sized slices and subdivides any slice exceeding one million rows. Disk/Parquet work runs in worker threads; there is no full 50-million-row DataFrame or generated fallback dataset.

Full-quarter reconciliation yielded:

| Source or output | Exact count |
|---|---:|
| Alarm records, duplicates preserved | 68,891 |
| Sensor readings, including missing values | 50,327,215 |
| Grouped sensor snapshots | 272,039 |
| Total chronological events | 340,930 |

The reconciliation completed in **34.104 seconds** using accelerated scheduling, with a peak counting-process RSS of **548,470,784 bytes (about 523 MiB)**. This benchmark measured replay/counting alone, not model scoring or WebSockets. The first actual event was available in **0.168 seconds**. Source files were not changed.

Each event has `ts`, `turbine_id`, `alarm_code`, `description`, `stopping` and `signals`. Sensor snapshots carry null alarm fields and actual named measurements; missing values remain null. Source alarm events retain their alarm metadata and have empty signals. No sensor value is carried forward or attached to an unrelated alarm. At equal timestamps, sensors precede alarms. Repeated readings for one signal use extra same-time snapshots rather than being overwritten.

The asynchronous cursor supports pause/resume, validated speed changes, seek, exhaustion and a single active consumer. Seek interrupts pending waits and invalidates stale worker results. Cancellation does not drop an in-flight batch. Runtime shutdown waits for worker reads to finish. At 600×, a simulated day takes approximately 144 wall seconds; instantaneous event rate depends on how many real events occur in that interval.

## Policy integration and live statistics

Replay and policy expose the same bounded 2,000-observation deque. Policy retains private validated scoring snapshots for rescoring, so edits to the public replay deque cannot rewrite accepted verdicts. Each yielded event is scored exactly once. A changed replay generation clears detector histories, buffered verdicts, counters and collapse representatives while preserving rules, classifier weights and model revision.

Sensor observations update numeric baselines but do not increment `alarms_shown` or `alarms_suppressed`. Code-scoped teaching omits sensor-only rows instead of calling them negative alarm labels. The last 2,000 merged observations may contain fewer than 2,000 alarm examples. The trailing alarm rate counts visible buffered alarm records in the preceding simulated hour, anchored to the latest observation so quiet alarm periods still age correctly. It remains bounded by available buffer coverage.

The HTTP `/stats` response includes the complete Phase 4 baseline artifact plus live policy/replay statistics. WebSocket statistics contain dynamic values every two seconds without repeatedly sending the 772 KB static baseline. Sensor anomaly scores are observations; the implementation does not invent source alarm codes or claim a calibrated incident probability.

## HTTP and WebSocket contract

| Interface | Behavior |
|---|---|
| `GET /` | Local status page; operator console is Phase 8 |
| `GET /health` | Readiness, phase, replay position, model revision and online-mode flag |
| `GET /stats` | Policy statistics, replay timing/state and baseline figures |
| `GET /rules` | Active validated rules |
| `POST /teach` | Parse text, apply local learning, rescore and broadcast `taught` |
| `POST /undo/{rule_id}` | Revoke/rebuild/rescore and broadcast `undo` |
| `POST /killswitch` | Change environment and parser/voice flags; broadcast `link` |
| `WS /stream` | Shared events/verdicts plus statistics, taught, undo and link notifications |

Teaching returns the rule, version before/after, training-example count and newly suppressed count. Unparseable text returns HTTP 200 with `{"parsed":false,"message":"no rule found in that"}` and no mutation. Invalid request bodies return structured 422 errors. Missing initialization inputs or failed operations return structured 503 errors. Undo of a missing/already-revoked rule returns structured 404. Health remains readable if initialization fails; a failed replay reports degraded status instead of pretending to continue.

One lifespan owns the runtime, producer and statistics task. One failing/slow WebSocket client is removed independently; its send has a one-second timeout. Per-client sends are serialized, and score/learn/undo operations use a shared policy lock. Blocking learning and parsing run off the event loop. Worker cancellation is drained before releasing mutation locks, including repeated cancellation. Initialization occurs in lifespan and cleanup cancels/awaits owned tasks and closes replay resources. [FastAPI lifespan guidance](https://fastapi.tiangolo.com/advanced/events/), [WebSocket guidance](https://fastapi.tiangolo.com/advanced/websockets/)

Run **one Uvicorn worker**. This is a local prototype with an in-process connection set and a single-writer site journal, not a distributed service. Data loading does not happen at module import. Interactive CDN-backed API docs remain disabled; `/openapi.json` is local.

## Five streamed events

These are the first five event frames captured by the live validation client. Each contained all 185 measurements; the table includes one measurement and the anomaly score for inspection. Full unabridged frames, both teaching responses, notifications and HTTP evidence remain locally in `data/processed/phase7_api_validation.json` and are Git-ignored.

| Timestamp | Turbine ID | Alarm code | Signals | Example actual reading | Anomaly score | Show |
|---|---|---|---:|---|---:|---|
| 2025-01-01T12:20:00 | 2304510 | null (sensor) | 185 | wtc_A1ExtTmp_min = 10.0 | 2.064151 | True |
| 2025-01-01T12:20:00 | 2304511 | null (sensor) | 185 | wtc_A1ExtTmp_min = 11.0 | 4.515796 | True |
| 2025-01-01T12:20:00 | 2304512 | null (sensor) | 185 | wtc_A1ExtTmp_min = 11.0 | 2.017195 | True |
| 2025-01-01T12:20:00 | 2304513 | null (sensor) | 185 | wtc_A1ExtTmp_min = 10.0 | 2.334635 | True |
| 2025-01-01T12:20:00 | 2304514 | null (sensor) | 185 | wtc_A1ExtTmp_min = 11.0 | 3.296654 | True |

These frames are sensor observations, not five newly raised alarms. The numeric baseline had already been learning while replay ran before the clients connected. The dataset timezone remains the unverified naive-UTC assumption documented in Phase 3.

## Teaching before and after the offline switch

The first teaching response was:

```json
{
  "parsed": true,
  "rule": {
    "rule_id": "5f08c321-455f-451f-81e3-a58e4729c26d",
    "utterance": "ignore the cable untwist alarm on turbine four",
    "scope": {
      "turbine_id": "2304513",
      "alarm_code": 10105,
      "signal": null
    },
    "pattern": {
      "kind": "code_match",
      "window_s": 0,
      "conditions": []
    },
    "action": "suppress",
    "confidence": 0.6,
    "taught_by": "operator",
    "taught_at": "2026-09-19T20:48:20.573610+00:00",
    "reversible": true
  },
  "model_version_before": 3,
  "rule_id": "5f08c321-455f-451f-81e3-a58e4729c26d",
  "examples_learned": 80,
  "model_version": 4,
  "newly_suppressed_count": 0
}
```

This advanced **version 3 → 4**, trained on 80 actual buffered alarm examples and suppressed **zero** buffered events. No cable-untwist/T04 event was present yet in this early-January replay. The rule is active for future matching events; zero immediate matches is not a learning/API failure. Separate real-data integration tests preload a matching interval and verify later matching suppression and another turbine's visibility.

After `POST /killswitch` with `{"on":true}`, both clients received `{"type":"link","online":false,"offline":true}`. The subsequent teaching response was:

```json
{
  "parsed": true,
  "rule": {
    "rule_id": "c3f11a9e-4436-4901-b498-4075c3bb0bff",
    "utterance": "generator cut-in is routine, suppress it",
    "scope": {
      "turbine_id": null,
      "alarm_code": 20,
      "signal": null
    },
    "pattern": {
      "kind": "code_match",
      "window_s": 0,
      "conditions": []
    },
    "action": "suppress",
    "confidence": 0.6,
    "taught_by": "operator",
    "taught_at": "2026-09-19T20:48:20.636542+00:00",
    "reversible": true
  },
  "model_version_before": 4,
  "rule_id": "c3f11a9e-4436-4901-b498-4075c3bb0bff",
  "examples_learned": 80,
  "model_version": 5,
  "newly_suppressed_count": 17
}
```

This advanced **version 4 → 5**, used offline confidence **0.6**, and newly suppressed **17** buffered code-20 alarms. A further actual code-20 event arrived and was also suppressed. At the end of observation, the policy reported **65 shown / 18 suppressed**, two active rules, version 5 and one visible alarm in the buffered trailing simulated hour. This measures the applied test correction, not false-alarm accuracy.

The offline switch sets `EARSHOT_OFFLINE`, `parse.OFFLINE_MODE` and `voice.OFFLINE_MODE` before yielding control. Online parsing results are rechecked after waiting for the policy lock, so a switch during that wait cannot apply the stale online proposal. Voice has no implemented provider operations in this phase; its existing entry points now consult the flags.

The switch is an application outbound-call gate, not a hardware Wi-Fi control. It cannot retract an HTTP request already sent to a provider. No live LLM credentials were available; the first teaching request also fell back locally. Tests with configured fake credentials block SDK construction/network access after the switch. The live run verifies local teaching under the enforced flags, not live cloud-provider compatibility.

## Measured stream and endpoint results

```json
{
  "wall_seconds": 10.000512375001563,
  "events_received": 213,
  "events_per_second": 21.2989086971623,
  "event_kinds": {
    "alarm": 3,
    "sensor": 210
  },
  "stats_messages_received": 5,
  "configured_replay_speed": 600
}
```

The ten-second session included 210 sensor snapshots and three actual alarm events, plus five statistics messages. Both clients received the same teaching IDs and offline link update. The counter measures frames received by one client over actual elapsed wall time, including the HTTP teaching actions; it is not the standalone accelerated replay benchmark.

**Live HTTP errors: none.** Invalid-input, missing-data, failed-journal and missing-rule errors were separately exercised in tests and returned the intended structured 4xx/503 responses, with no HTTP 500. Journal-commit failure left classifier weights, rules, revisions and visibility unchanged. Unparseable input returned the expected nonmutating HTTP 200 response.

Cleanup undid only the two verification rules, advancing revisions **5 → 6 → 7**, and restored the original offline setting and active-rule list. Uvicorn logged application shutdown complete; port 8000 was verified closed. The raw event files, metadata, vocabulary and baseline remain unchanged.

## Full targeted test output

```text
============================= test session starts ==============================
platform darwin -- Python 3.13.7, pytest-9.1.1, pluggy-1.6.0 -- /Users/dhwanil/Desktop/DOM/Dreamai_Project/venv/bin/python
cachedir: .pytest_cache
rootdir: /Users/dhwanil/Desktop/DOM/Dreamai_Project
configfile: pytest.ini
plugins: anyio-4.15.1
collecting ... collected 61 items

tests/test_replay.py::test_real_sources_merge_chronologically_without_losing_rows PASSED [  1%]
tests/test_replay.py::test_full_real_alarm_count_and_duplicate_events_preserved PASSED [  3%]
tests/test_replay.py::test_actual_first_day_groups_185_signals_without_skipping_measurements PASSED [  4%]
tests/test_replay.py::test_recursive_bounded_slices_preserve_all_values PASSED [  6%]
tests/test_replay.py::test_consumer_restart_resumes_same_cursor PASSED   [  8%]
tests/test_replay.py::test_single_consumer_guard PASSED                  [  9%]
tests/test_replay.py::test_pause_seek_resume_interrupts_old_wait PASSED  [ 11%]
tests/test_replay.py::test_speed_change_interrupts_wait PASSED           [ 13%]
tests/test_replay.py::test_wall_clock_delay_is_simulated_delta_divided_by_speed PASSED [ 14%]
tests/test_replay.py::test_cancel_pending_wait_does_not_skip_event PASSED [ 16%]
tests/test_replay.py::test_cancel_inflight_worker_preserves_batch_and_shared_buffer PASSED [ 18%]
tests/test_replay.py::test_seek_during_worker_read_discards_stale_generation PASSED [ 19%]
tests/test_replay.py::test_seek_after_exhaustion_and_aware_timestamp PASSED [ 21%]
tests/test_replay.py::test_invalid_configuration[kwargs0] PASSED         [ 22%]
tests/test_replay.py::test_invalid_configuration[kwargs1] PASSED         [ 24%]
tests/test_replay.py::test_invalid_configuration[kwargs2] PASSED         [ 26%]
tests/test_replay.py::test_invalid_configuration[kwargs3] PASSED         [ 27%]
tests/test_replay.py::test_invalid_configuration[kwargs4] PASSED         [ 29%]
tests/test_replay.py::test_invalid_configuration[kwargs5] PASSED         [ 31%]
tests/test_replay.py::test_invalid_configuration[kwargs6] PASSED         [ 32%]
tests/test_replay.py::test_invalid_configuration[kwargs7] PASSED         [ 34%]
tests/test_replay.py::test_invalid_configuration[kwargs8] PASSED         [ 36%]
tests/test_replay.py::test_invalid_seek_is_atomic PASSED                 [ 37%]
tests/test_replay.py::test_optional_scada_absent_but_explicit_missing_is_error PASSED [ 39%]
tests/test_replay.py::test_empty_and_missing_schema_rejected PASSED      [ 40%]
tests/test_replay.py::test_missing_metadata_statistics_fallback PASSED   [ 42%]
tests/test_server.py::test_root_serves_local_ui_independently_of_launch_directory PASSED [ 44%]
tests/test_server.py::test_health_and_stats_report_live_policy_and_exact_local_baseline PASSED [ 45%]
tests/test_server.py::test_stream_broadcasts_same_real_event_to_two_clients PASSED [ 47%]
tests/test_server.py::test_periodic_stats_continue_without_new_source_events PASSED [ 49%]
tests/test_server.py::test_teach_rescores_real_buffer_and_broadcasts_to_both_clients PASSED [ 50%]
tests/test_server.py::test_taught_scope_hides_later_matching_event_but_preserves_other_turbine PASSED [ 52%]
tests/test_server.py::test_unparseable_teaching_returns_200_without_mutation PASSED [ 54%]
tests/test_server.py::test_undo_restores_visibility_model_and_broadcasts PASSED [ 55%]
tests/test_server.py::test_killswitch_blocks_provider_and_teaching_still_works PASSED [ 57%]
tests/test_server.py::test_malformed_request_bodies_return_structured_422[/teach-payload0] PASSED [ 59%]
tests/test_server.py::test_malformed_request_bodies_return_structured_422[/teach-payload1] PASSED [ 60%]
tests/test_server.py::test_malformed_request_bodies_return_structured_422[/teach-payload2] PASSED [ 62%]
tests/test_server.py::test_malformed_request_bodies_return_structured_422[/teach-payload3] PASSED [ 63%]
tests/test_server.py::test_malformed_request_bodies_return_structured_422[/killswitch-payload4] PASSED [ 65%]
tests/test_server.py::test_malformed_request_bodies_return_structured_422[/killswitch-payload5] PASSED [ 67%]
tests/test_server.py::test_malformed_request_bodies_return_structured_422[/killswitch-payload6] PASSED [ 68%]
tests/test_server.py::test_invalid_json_returns_structured_422 PASSED    [ 70%]
tests/test_server.py::test_journal_commit_failure_returns_503_without_model_change PASSED [ 72%]
tests/test_server.py::test_one_failed_socket_does_not_break_broadcast_to_healthy_clients PASSED [ 73%]
tests/test_server.py::test_missing_dataset_reports_unavailable_and_closes_socket_cleanly PASSED [ 75%]
tests/test_server.py::test_lifespan_shutdown_stops_replay_and_all_background_tasks PASSED [ 77%]
tests/test_server.py::test_openapi_contract_available_without_cdn_documentation PASSED [ 78%]
tests/test_server.py::test_killswitch_reparses_online_proposal_after_waiting_for_policy_lock PASSED [ 80%]
tests/test_server.py::test_cancelled_worker_drains_before_releasing_policy_lock[1] PASSED [ 81%]
tests/test_server.py::test_cancelled_worker_drains_before_releasing_policy_lock[2] PASSED [ 83%]
tests/test_policy_replay.py::test_sensor_observations_update_baseline_without_incrementing_alarm_counts PASSED [ 85%]
tests/test_policy_replay.py::test_sensor_clock_expires_old_alarms_without_counting_sensor_samples PASSED [ 86%]
tests/test_policy_replay.py::test_alarm_rule_omits_sensor_samples_from_training_and_rescore_counts PASSED [ 88%]
tests/test_policy_replay.py::test_shared_deque_is_live_but_caller_edits_do_not_rewrite_scored_events PASSED [ 90%]
tests/test_policy_replay.py::test_binding_rejects_unbounded_or_mismatched_buffers[buffer0] PASSED [ 91%]
tests/test_policy_replay.py::test_binding_rejects_unbounded_or_mismatched_buffers[buffer1] PASSED [ 93%]
tests/test_policy_replay.py::test_binding_rejects_unbounded_or_mismatched_buffers[buffer2] PASSED [ 95%]
tests/test_policy_replay.py::test_stream_reset_retains_corrections_classifier_and_journal PASSED [ 96%]
tests/test_policy_replay.py::test_stream_reset_clears_numeric_detector_history PASSED [ 98%]
tests/test_policy_replay.py::test_stream_reset_clears_collapse_representative PASSED [100%]

=============================== warnings summary ===============================
venv/lib/python3.13/site-packages/starlette/testclient.py:53
  /Users/dhwanil/Desktop/DOM/Dreamai_Project/venv/lib/python3.13/site-packages/starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 61 passed, 1 warning in 3.43s =========================
```

## Full regression output

```text
........................................................................ [ 19%]
........................................................................ [ 39%]
........................................................................ [ 59%]
........................................................................ [ 79%]
........................................................................ [ 98%]
....                                                                     [100%]
=============================== warnings summary ===============================
venv/lib/python3.13/site-packages/starlette/testclient.py:53
  /Users/dhwanil/Desktop/DOM/Dreamai_Project/venv/lib/python3.13/site-packages/starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
364 passed, 1 warning in 8.52s
```

No tests failed or skipped with the supplied data. The existing Starlette/AnyIO deprecation warning remains. Replay checks cover chronological order, exact alarm counts and first-day reading counts, bounded slicing, pause/resume/speed/seek, metadata fallback, missing/null data, cancellation and cursor preservation. Server tests cover dual-client broadcasts, transactional learning/undo, the offline race, failure isolation and clean shutdown.

## Reproduce

From the repository root, start the server:

```bash
./venv/bin/uvicorn earshot.server:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
./venv/bin/python scripts/verify_live_api.py
./venv/bin/pytest -q
```

The verification script applies two demo rules and then undoes them, restoring the previous offline setting. Its operations remain in the local append-only journal. Use Ctrl+C to stop the server cleanly. The home page is a status page; the interactive operator console and voice follow in later phases.
