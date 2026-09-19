# Phases 8–10 — console, voice and repeatable demo

The remaining phases implement the operator console, optional voice adapters with local fallbacks, and a one-command presentation using genuine source events. The source execution-plan file remains untouched. The requested plan is recorded in [PHASES_8_10_PLAN.md](PHASES_8_10_PLAN.md).

## Measured rehearsal

A cold application launch with already-installed dependencies and processed data reached a healthy, prewarmed, paused state in **1.710 seconds**, including preflight, server initialization and prewarming after virtualenv activation. This is a local measurement with existing files and OS caches, not an initial-install or uncached-disk guarantee. The receipt is `data/processed/phase10_cold_start.json`.

The complete automated API rehearsal took **19.958 seconds**. The first correction took **6.735 ms** end to end, trained on **276 genuine alarm examples**, advanced the model revision, and changed **four buffered matching events** to suppressed. The later matching event was suppressed; a different source stopping event remained visible. Teaching after the offline switch succeeded, its later matching event was suppressed, and the locally cached spoken confirmation returned **167,876 bytes**. The script undid only its own two rules and preserved the audit history; no active rehearsal rules remained.

Run `./venv/bin/python scripts/verify_demo.py` against a freshly started, prewarmed launcher with no active demo rules. Detailed responses and original source events are retained locally in `data/processed/phase10_rehearsal.json`. Unit tests use isolated temporary journals.

## Four real-data beats

| Beat | Recorded timestamp | Evidence and purpose |
| --- | --- | --- |
| Pain | 2025-02-21 20:25:06 | The preceding hour contains 276 alarm records and 126 sensor snapshots; four alarms are T04/code20. |
| Teach | 2025-02-21 20:25:06, paused | “ignore generator cut-in on turbine four” creates a reversible suppress rule scoped to station 2304513/code20. |
| Proof | 2025-02-21 20:27:36 | T04/code20 recurs about 10 seconds after Resume at 15×. T21/code3130 “Pitch lubrication,” with stopping=1, remains visible at 20:30:02, about 19.7 seconds after Resume. |
| Unplug | 2025-02-21 20:30:02 | Engage offline mode and teach “ignore fast cut-out of generator on turbine four.” Its code25 recurrence at 20:45:29 is verified after an explicit recovery seek. |

Exact source rows, expected scopes, timestamps, the alarm-file checksum, and interpretation limits are checked into [scenario.json](../demo/scenario.json). A recovery seek intentionally changes replay timing; the normal script uses Resume from the prewarmed pause. The complete presenter script, all four exact lines/actions, and recovery lines are in [DEMO_SCRIPT.md](../demo/DEMO_SCRIPT.md).

## Phase 8 console

The console is a single HTML file containing its own CSS, SVG and vanilla JavaScript. It uses local HTTP/WebSocket endpoints, displays actual alarm descriptions, caps the alarm DOM at 200 rows, and obtains authoritative rescored decisions from `/console`. Rule IDs and model revisions remain visible; teaching and undo refresh existing decisions. Stable event IDs and replay generations support reconnect/seek reconciliation.

The chart qualifies 12/hour as a workload reference. A code-specific correction does not imply the site-wide flood disappeared, nor does a source stopping flag prove a hazardous incident. The footer reports the measured baseline and dataset provenance.

## Phase 9 voice

`voice.py` implements ElevenLabs STT/TTS through the documented REST endpoints, typed offline/provider/input failures, bounded inputs and streamed responses, ten-second connect/read inactivity timeouts, an elapsed-response deadline, no redirects, and no automatic retries. A blocking network read is not promised to stop at exactly ten wall-clock seconds. Provider results are discarded when offline mode changes in flight.

**ElevenLabs credentials were absent; live provider STT/TTS were not exercised.** Fifty mocked-provider/cache tests cover request shape, timeouts, invalid content, missing credentials, offline zero-call behavior, and cache integrity. The optional Nebius/Qwen parser remains unvalidated against a live account; its local fallback and mocked contract are tested.

The browser prefers configured online recording, then verified on-device recognition where supported, then explicitly labeled scripted fixtures/text. Offline browser recognition requires `processLocally` plus an installed local language pack; cloud browser recognition cannot silently bypass the switch. Missing browser support is disclosed. Cached WAV confirmation works without speech services. The five input WAVs and five spoken confirmations contain non-silent locally synthesized speech, labeled as synthetic; they are not presented as human recordings or live transcription.

| Key | Scripted transcript |
| --- | --- |
| 1 | ignore generator cut-in on turbine four |
| 2 | ignore the cable untwist alarm on turbine four |
| 3 | ignore fast cut-out of generator on turbine four |
| 4 | always tell me about high wind on number four |
| 5 | ignore generator cut-in on turbine seven |

[The manifest](../demo/utterances/manifest.json) records transcripts, source scopes, hashes, durations, synthetic origin, and matching cached confirmations. A modified/missing fixture is rejected before teaching until its manifest is regenerated. Plain text and microphone transcripts use the same accepted-rule/learning pipeline.

## Resilience fixes and limits

- Fast prewarming initially advanced the simulated clock beyond the last emitted event. `pause(at_position=True)` now anchors it precisely; a real-data regression verifies the next observation waits at the configured 15× speed. The rehearsal verifies the later recurrence arrives after roughly10 seconds.
- Statistics previously could be broadcast after a newer teach/seek frame. Snapshot and broadcast now share the runtime lock, preserving frame order; statistics also expose replay generation and sequence.
- Console snapshots copy aligned scored observations rather than pairing a changing source ring with old verdicts. Buffered source rows keep stable IDs across teach/undo.
- Replay seeks discard stale-generation observations and restart a normally exhausted producer. Seek clears recent history while retaining site corrections.
- Audio uploads and provider responses are size-bounded; errors are sanitized into local structured responses. Cached audio is checked as valid, bounded PCM WAV. A confirmation is built before committing teaching so a read-back validation error cannot create a hidden successful mutation.
- The launcher checks missing/empty/unreadable Parquet, schema/source receipts, actual scenario records, audio hashes, and port ownership. Missing processed data triggers local rebuilding; that first build can exceed 60 seconds. Failures print actionable messages without an HTTP traceback.
- The browser layout no longer overflows narrow screens. Offline controls and the newest correction remain visible on the projector; completed local recognition waits for push-to-talk release before teaching.
- A failed producer now rejects replay controls with a restart instruction instead of reporting a successful seek without a live stream.
- Slow or disconnected WebSocket peers are isolated. Missing provider credentials leave text/scripted teaching available. The launcher stops only its own child PID and never deletes or resets the site's correction journal.

The offline switch blocks application provider calls; it does not disable the operating system's networking or recall an already-sent request. No production incident-detection accuracy or operator-safety certification is claimed. The model demonstrates local scoped corrections and classifier updates, not large-language-model retraining.

## Final validation

**438 tests passed** in 10.22 seconds, with one pre-existing Starlette/AnyIO deprecation warning and no skipped tests on this checkout. `pip check`, Python syntax checks, inline JavaScript syntax checks, and `git diff --check` pass. Full pytest output is saved locally as `data/processed/phases8_10_pytest.txt`.

Chrome 153 browser validation passed: real teaching, authoritative strike-through, version-badge animation, offline teaching, explicit scripted audio/transcript teaching, UI Undo, and recovery after a forced WebSocket disconnect. A mocked browser-recognition contract test confirms early recognition completion does not teach until the operator releases Space; this is not a live microphone-quality test. There were **zero uncaught page errors and zero external page requests**. The final single-file console is approximately **55.6 KB**, with no framework, build step, or remote assets.

Visual inspection covered desktop, a **1366×768** projector viewport, and a **390-pixel** mobile viewport. The offline control remains in the header, the newest rule and core teaching controls fit the projector viewport, and mobile has no horizontal overflow. The rate headline remains at least 64 pixels. The ledger was moved below the feed and secondary scripted controls are expandable to keep the main demonstration visible. Four screenshots and structured browser evidence are saved locally under `data/processed/phase8_console_*.png` and `phase8_browser_validation.json`.

Rehearsal and browser tests revoked only their own corrections. The local audit journal retains its history; the final verification found zero active rules. Validation servers were stopped using only their recorded process IDs. Run `./scripts/run_demo.sh` to open a fresh paused demonstration.

## Human presentation checks

Open http://127.0.0.1:8000 and confirm alarms stream after Resume, the counter updates, the version badge animates on teaching, the rule card appears, and the offline indicator changes. Check projector legibility at the actual presentation resolution.

Grant microphone permission and test push-to-talk at realistic room volume. The committed scripted voices already work; re-record them in your own voice if desired and regenerate the manifest without overwriting your recordings. Engage the offline switch and verify your selected input mode remains available; use text or a visibly labeled fixture when local recognition is unavailable.

Record a backup video while the demo works, rehearse the 90-second script three times, submit before the deadline, and disable notifications before presenting. Those room/device/presentation actions cannot be completed by automated API tests.
