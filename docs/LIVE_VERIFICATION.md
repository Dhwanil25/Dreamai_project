# EARSHOT live verification

Checked on September 19, 2026 against the supplied Hill of Towie files and the configured provider accounts. The console is available at <http://127.0.0.1:8000/>; inspect current source, learning and operation evidence at <http://127.0.0.1:8000/evidence>.

## Observed provider results

| Check | Actual result | Evidence |
| --- | --- | --- |
| Nebius parsing | Successful provider response in 3.652 seconds; correct T04/code20 suppress proposal | `data/processed/nebius_live_parse.json` |
| ElevenLabs transcription | HTTP 401, `missing_permissions`, missing `speech_to_text` | `data/processed/elevenlabs_live_stt.json` |
| ElevenLabs synthesis | HTTP 401, `missing_permissions`, missing `text_to_speech` | `data/processed/elevenlabs_live_tts.json` |

Nebius returned model `Qwen/Qwen3-30B-A3B-Instruct-2507`, request ID `3516f66876d5626ed670c551157be88b`, and completion ID `chatcmpl-bf697822-7e0e-4cff-89e0-3b811109b92d` through `https://api.tokenfactory.nebius.com/v1`. This first check exercised parsing only; it did not change the site's rules. Successful parser receipts leave HTTP status null when the SDK does not expose that status; no status is invented.

The ElevenLabs key needs both permissions enabled before successful provider voice can be demonstrated. These failures are not a claim that the key is invalid. No transcript or successful audio output was substituted for the rejected calls. Transcription used an actual public human recording from the [official PyTorch speech-recognition tutorial](https://docs.pytorch.org/audio/stable/tutorials/speech_recognition_pipeline_tutorial.html), not synthesized operator input: `Lab41-SRI-VOiCES-src-sp0307-ch127535-sg0042.wav`, 108,844 bytes, SHA-256 `c65fcd726d6b08c82c1e5dc7558f863cd8d483e3ed2f4a7bcf271dc1865ada14`.

Provider keys are read only on the server from the ignored local `.env`. They are not embedded in the page, receipts, or committed files. Online mode sends utterance/vocabulary text to Nebius and microphone audio to ElevenLabs. Local-only mode blocks new provider calls; it does not disconnect the operating system from the network.

Both speech paths were also called through the running website's actual `/listen` and `/speak` endpoints. Each returned an actionable application HTTP 503 with the provider's HTTP 401 `missing_permissions` receipt, and neither changed learning state. The receipt is `data/processed/website_voice_verification.json`; request identifiers remain null because these provider responses supplied none.

## Live application rehearsal

`scripts/verify_live_proof.py` passed against the running HTTP/WebSocket application in **24.644 seconds**. Its complete local receipt is `data/processed/live_verification.json`.

| Observation | Actual result |
| --- | --- |
| Online correction | Nebius request `c8ed0cf9c041ac44ac73729d794cde16`, 4,548.580 ms end to end |
| Accepted scope | Turbine `2304513`, alarm code `20`, suppress |
| Local learning | 276 actual alarm examples; nonzero classifier weights increased from 0 to 122 |
| Buffered decisions | Four matching records became suppressed |
| Future matching source event | `2025-02-21T20:27:36`, T04/code20, suppressed by the accepted rule |
| Untaught source event | `2025-02-21T20:30:02`, T21/code3130, stopping=1, remained visible |
| Offline correction | Local parser, no provider attempt; later T04/code25 recurrence suppressed |
| Offline provider synthesis | Explicitly blocked with application HTTP 503 |
| Undo | Both test rules revoked; original classifier hash restored exactly; no active rules left |

The actual classifier fingerprint changed from `72e25a1ba031b6128acbe2ad6da448535eddbaa91bbeb9d7c23414998be2aca7` to `f0bc897f41d3be761619635899183dc60b1e74fbf085997d03c2170348cc14c9` after the first correction, and returned to the first value after undo. The receipt preserves both full learning states and the actual provider metadata. Model revisions remain monotonic after undo; revisions are not used as substitutes for weight comparisons.

The script restores the prior online/paused settings and only undoes rule IDs it created. Recorded time advances during the test; it does not restore the earlier replay buffer. An initial verification attempt could not connect from its execution environment and applied no changes; its failure is preserved separately at `data/processed/live_verification_initial_connection_failure.json`. The successful run above connected from the root environment.

## Source proof

The audit compares **all 68,891 processed alarm records**, including duplicate multiplicities, with January–March 2025 CSV records inside the original ZIP. It checks description/stopping joins against the supplied lookup, preserves 348 duplicate records, traces five selected events to exact CSV data-row numbers, and independently recomputes **31.933676815856746 alarms/hour** and **92.02217996545266%** top-ten-code share.

The sensor audit independently compares **185 cells**, including five nulls, for turbine `2304513` at `2025-02-21T20:20:00` against original temperature and grid CSV data row 63,044. All sampled cells match. The **50,327,215** quarter-wide SCADA reading count comes from Parquet metadata; this audit does **not** reconcile every raw-quarter SCADA cell.

| File | SHA-256 |
| --- | --- |
| `data/raw/2025.zip` | `cd439de7bf8e297dd8422d5c93eda04785c56e3861be8d2a79f403ac39b873ee` |
| `data/processed/alarms.parquet` | `9b99821be20e5758765e206107c0bd0e4a4225d79ac860908937320fc192220e` |
| `data/processed/scada.parquet` | `221b574af4db864cc50373fafac8292595ad56e29ddcaf8d11707d5c453bc464` |

The server reruns reconciliation at startup. `/evidence` includes the verification time and invalidates source proof if the audited files' size, modification time or identity changes. The standalone command publishes a separate local receipt:

```bash
./venv/bin/python scripts/audit_source_evidence.py
curl -s http://127.0.0.1:8000/evidence
```

These are inspectable local receipts, not signed third-party attestations. The dataset does not supply ground-truth nuisance labels or prove incident-detection accuracy. It is a recorded-data replay, not a live industrial connection.

## What changed in the website

- Removed all five prerecorded input WAVs, five cached confirmations, the audio manifest/generator, scripted teaching routes, and keys 1–5. No fixed transcript is served as microphone recognition.
- Text and microphone paths use actual submitted input. Every parsed result identifies provider or local parsing, actual returned model/request IDs when available, and fallback reasons.
- Teaching receipts include actual classifier weights/intercept hashes before and after, nonzero weight counts, training examples, accepted scope, and observed visibility changes. Undo records its resulting state.
- Immediate code-based suppression uses an explicit scoped rule. The local River classifier also learns from buffered examples; the language model is not fine-tuned.
- Provider readbacks are generated for the current text. Permission failures remain visible with their receipts. Any available on-device readback is labeled computer-generated output.
- Replay counters, alarm decisions, chart points and ledger entries derive from the running backend. Historical baseline figures derive from the audited source files.

Browser permissions, local speech-pack availability, and a human speaking into the presentation microphone remain device-specific checks. Automated browser API mocks test interface behavior only and must not be counted as successful live speech recognition.

## Validation and reproduction

**512 tests passed** in 17.07 seconds, with one existing Starlette/AnyIO deprecation warning. The run used blank provider-key environment variables and offline mode; these tests are separate from the live calls recorded above. Dependency consistency, inline JavaScript syntax, and Git whitespace checks passed. Full output is in `data/processed/live_pytest.txt`.

Chrome 153 verification passed actual replay advancement, source-derived typed teaching, retroactive suppression, local parsing receipts, UI undo, WebSocket reconnection, removed-route HTTP 404s and inactive numeric shortcuts. It found zero uncaught page errors, zero external browser requests, and no mobile horizontal overflow. Provider-parse display, early speech-recognition completion and permission-error rendering also passed explicitly labeled browser API mocks; those mocks are not live speech proof.

The browser receipt is `data/processed/phase8_browser_validation.json`; current desktop, projector and mobile screenshots use the `data/processed/phase8_console_*.png` paths. Visual checks covered 1366×768 and a 390-pixel mobile viewport. Browser verification undoes only its own corrections and restores the earlier online/paused settings.

The audit journal now preserves valid receipts around damaged lines, reports incomplete history in `/evidence.operation_history_integrity`, and rolls back interrupted appends. Invalid Unicode is rejected before learning. If online parsing completes just as offline mode engages, the final local parse and the earlier provider attempt are both retained in `parse_attempts`.

```bash
EARSHOT_OFFLINE=1 LLM_API_KEY= ELEVENLABS_API_KEY= ./venv/bin/pytest -q
./venv/bin/python scripts/audit_source_evidence.py
# Against a fresh prewarmed server, with configured Nebius access:
./venv/bin/python scripts/verify_live_proof.py
# Against a running server; uses actual local teaching plus labeled browser mocks:
./venv/bin/python scripts/verify_browser.py
```

Run the live rehearsal before browser verification if using both: the live rehearsal needs the original prewarmed timestamp. Add `--voice` only after correcting the ElevenLabs permissions to retest provider speech. Site rules and the complete operation history remain local and Git-ignored; the repository contains this measured summary and the reproducible verification code.
