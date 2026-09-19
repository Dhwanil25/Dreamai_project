# EARSHOT

EARSHOT replays real industrial sensor and alarm records on a site's own computer and learns scoped, reversible corrections from an operator's words. The console exposes the source records, actual local classifier state, and provider receipts so a displayed change can be checked against what the system did.

## Run and inspect the evidence

With dependencies and the supplied dataset already processed:

```bash
./venv/bin/python scripts/audit_source_evidence.py
./scripts/run_demo.sh
```

Open **http://127.0.0.1:8000/**. The launcher prewarms a real recorded hour and pauses at **2025-02-21 20:25:06**. Enter your own instruction by text or an available live microphone path. For a reproducible example, type **“ignore generator cut-in on turbine four”**, inspect its rule and learning receipt, then resume to the next recorded occurrence. No correction is applied just by starting the app or navigating the recording.

This is **recorded wind-farm data**, not a live connection to industrial equipment. The website serves no prerecorded operator input, fixed-transcript teaching shortcuts, or synthetic sensor/alarm rows. Spoken confirmations, when available, are labeled computer-generated output from the current instruction.

Open **[/evidence](http://127.0.0.1:8000/evidence)** to inspect three distinct kinds of proof:

- **Source audit:** raw ZIP, Parquet and metadata hashes; all **68,891** alarm timestamp/station/code records and duplicate counts compared with the original CSVs; description/stopping joins checked; five exact source-row traces; and **185 SCADA cells** compared with the original temperature/grid rows. The full **50,327,215** SCADA row count comes from Parquet metadata; the audit does not claim to compare every SCADA cell with the raw archive.
- **Current local learning:** classifier type, actual weights/intercept SHA-256, nonzero weight count, training-example count, active rules and model revision. Teaching receipts record before/after state, the actual accepted scope, examples learned and visibility changes. A revision badge alone is not the proof of learning.
- **Provider results:** actual parser source, request identifiers and recorded provider outcomes. A configured key is not evidence that an API call succeeded.

The server audits the actual source files at startup and attaches `verified_at` to that result. Each evidence request checks source-file size, modification time and identity; a detected change invalidates the result and requires a restart to verify again. It does not promote an old `source_evidence.json` file to current proof. The standalone audit command writes a separate receipt for independent reruns. Live learning state comes from the running policy; provider validation records describe the calls that produced them. See [current live verification](docs/LIVE_VERIFICATION.md), [the presentation guide](demo/DEMO_SCRIPT.md), and [the submission](demo/SUBMISSION.md).

Stop the launcher with **Ctrl+C**. It owns only its own server process. Use one Uvicorn worker: one process owns replay, learning and the correction journal. If port 8000 is occupied, stop the previous server you own before launching another writer.

## Measured dataset

January–March 2025 contains **68,891 alarm records**, **50,327,215 numeric SCADA readings**, and **21 known turbines**. Logged alarms average **31.933677/hour site-wide**; the top ten codes account for **92.022180%** of records. The selected presentation hour contains **276 logged alarms**, including four generator cut-in records on turbine four.

Source: Alex Clerc and Elizabeth Lingkan, RES on behalf of TRIG, [Hill of Towie wind farm open dataset, Zenodo record 22662930](https://zenodo.org/records/22662930), version 2.1.0, [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). EARSHOT selects three months, reshapes numeric measurements and joins supplied descriptions. Raw archives remain unchanged. The release confirms UTC/end-of-period timestamps for ten-minute SCADA; the alarm-log timezone is not independently established.

These are event-log measurements, not verified false-alarm labels or incident-detection accuracy. Two records belong to unmapped station `91` and remain in site totals. Undocumented codes stay visible as undocumented. The 12/hour chart line is an approximate operator-console workload reference, not a universal safety limit. See [baseline methodology](docs/PHASE_4_REPORT.md) and [calculated measurements](demo/baseline_stats.json).

## Setup

Validated on macOS arm64 with Python 3.13.7. From the repository root:

```bash
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt -c requirements.lock
# For a new checkout only; preserve an existing local .env:
test -f .env || cp .env.example .env
mkdir -p data/raw data/processed
```

Place the supplied files under `data/raw/` as described in [dataset placement](docs/DATASET_PLACEMENT.md). The selected build uses `2025.zip` and the companion CSVs with their original names. Configuration selects January–March 2025.

```bash
./venv/bin/python scripts/explore_schema.py
./venv/bin/python scripts/build_dataset.py
./venv/bin/python scripts/compute_baseline.py
./venv/bin/python scripts/build_vocabulary.py
./venv/bin/python scripts/audit_source_evidence.py
./scripts/run_demo.sh
```

The launcher validates processed inputs and rebuilds missing Parquet from the supplied raw files. The under-60-second launch target assumes dependencies and preprocessing already exist. Initial ingestion is separate setup work. Raw/processed data, credentials, site journals and reference checkouts remain Git-ignored.

Configuration honors an explicit process environment first, then the local `.env`. An unconfigured checkout and `.env.example` default to `EARSHOT_OFFLINE=1`; the current locally configured `EARSHOT_OFFLINE=0` enables provider attempts. To force local-only operation for a launch:

```bash
EARSHOT_OFFLINE=1 ./scripts/run_demo.sh
```

For ordinary chronological replay from the beginning, without presentation prewarming:

```bash
./venv/bin/uvicorn earshot.server:app --host 127.0.0.1 --port 8000
```

## What changes when you teach

```text
Supplied ZIPs + metadata -> Parquet -> chronological replay -> local detectors
           |                                  |
           +-> source audit                   +-> observation buffer
                                                      |
Actual text / live transcript -> validated scope -> rule + River classifier
                                                      |
                                   local journal -> rescore -> console + undo
                                                      |
                                    /evidence: state hashes and operation receipts

Online parsing: Nebius / Qwen        Optional cloud voice: ElevenLabs
Offline mode: block provider calls; local parsing and learning continue
```

The policy labels matching buffered alarms and counter-examples, trains a local River logistic classifier, records the training batch, increments the revision and rescores recent observations. A simple `code_match` correction changes visibility through its explicit scoped rule immediately; the classifier also updates from those examples. **The LLM is not fine-tuned**, and immediate suppression is not presented as proof that the classifier independently inferred the policy.

Undo revokes the rule and reconstructs the classifier from remaining batches. Restart restores accepted corrections and training; detector histories and stream counters start fresh. Seek clears recent observations while retaining teaching. Numeric sensor snapshots separately update the anomaly detector. Explicit critical/emergency events and escalation rules stay visible; the source's stopping flag alone does not prove a dangerous incident. The prototype does not control equipment.

The ring holds 2,000 merged observations. Its trailing-hour count is buffer-limited and is not extrapolated from a shorter interval. With no prior relevant correction, teaching the selected T04 example changes four buffered records and lowers this hour from **276 to 272**, while other scopes remain visible.

## Live provider status and voice

| Component | Verified result |
| --- | --- |
| **Nebius** | A real parse succeeded in **3.652 seconds**, using `https://api.tokenfactory.nebius.com/v1` and `Qwen/Qwen3-30B-A3B-Instruct-2507`. Provider request ID: `3516f66876d5626ed670c551157be88b`. |
| **ElevenLabs speech-to-text** | The website’s `/listen` endpoint returned **HTTP 200** for a real human reference WAV in **831.529 ms** end-to-end. The transcript was “I have that curiosity beside me at this moment.” It returned `parsed: false`, correctly leaving learning unchanged because the speech was not an industrial instruction. |
| **ElevenLabs text-to-speech** | The website’s `/speak` endpoint returned **HTTP 200** in **780.125 ms**, producing a **79,038-byte MP3**. Provider request ID: `AShN6GPZSPhe1VkahNjB`. |
| **Local text teaching** | Available without provider credentials. Unsupported or ambiguous input returns no rule. |

The earlier ElevenLabs HTTP 401 permission failures are historical; both permissions have now been corrected and both website endpoints have succeeded. The local receipt is `data/processed/website_voice_verification.json`; recorded provider outcomes are also exposed through `/evidence`. See [live verification](docs/LIVE_VERIFICATION.md) for their limits. This reference-audio check does not establish that an industrial command spoken into the user's microphone has completed teaching: browser microphone permission, device capture and that full interaction still need a human check.

Hold the microphone button, or Space outside an input, and release to submit an actual clip. When online and correctly authorized, that audio is sent to ElevenLabs. Browser recognition is offered only when the browser verifies on-device support and an installed language pack. Otherwise use actual typed text; the app does not substitute a fixed transcript. On-device spoken readback requires an installed local synthesis voice and is explicitly computer-generated confirmation.

Online mode can send audio to ElevenLabs and utterance/vocabulary text to Nebius. Source event data, detector state and local classifier learning remain on this computer. The offline switch prevents new application provider calls, including retries, while local text teaching continues. It does not turn off Wi-Fi or recall requests already sent; late provider results are checked before use. Microphone permissions, installed language packs and room-volume performance still need a human check.

## Validate

```bash
EARSHOT_OFFLINE=1 LLM_API_KEY= ELEVENLABS_API_KEY= ./venv/bin/pytest -q
./venv/bin/python -m pip check
./venv/bin/python scripts/audit_source_evidence.py
curl -s http://127.0.0.1:8000/evidence
```

`/health`, `/stats`, `/console`, `/rules`, `/evidence` and `/openapi.json` expose runtime state and contracts. `/stream` publishes events and state changes. `/teach`, `/listen`, `/undo/{rule_id}`, `/killswitch` and `/replay` accept operator actions; `/speak` requests fresh provider readback. Missing data and unavailable providers produce explicit errors rather than fabricated results.

Tests cover source reconciliation, local learning state, scoped rules, undo/persistence, offline behavior, provider outcomes, real-data replay and concurrent clients. The earlier phase reports are historical milestones; [LIVE_VERIFICATION.md](docs/LIVE_VERIFICATION.md) is the current account of provider validation and the removal of prerecorded-input paths.

## PRIOR ART VS BUILT TODAY

The local [Phase 1 inventory](reference/REUSE_NOTES.md) reviewed `Dhwanil25/autonomous-ml-pipeline` at `ef26b071abd73979daff1bd7a7242afeded8c934` and `RAIN-Lab-AI/DOM_OS_Simulator` at `618295eaa460f3b21dd0e68cc464a92b65762b5a`. Both private clones and the inventory are intentionally Git-ignored.

**Functions copied from either repository: none.** The COPY recommendations were candidates, not completed extraction. EARSHOT uses fresh implementation code; no reference module is imported at runtime. Per-asset scoping, chronological observations, local model revisions and inspectable decisions informed its design.

Built in this project: the dataset adapter and baseline, bounded replay, detectors, validated rules, correction journal and River training, local/provider parser, FastAPI/WebSocket server, operator console, live voice adapters, source audit and operation evidence. Third-party dependencies remain their respective upstream projects. Historical phase plans and reports under `docs/` record how the implementation developed.
