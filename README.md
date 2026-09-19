# EARSHOT

EARSHOT replays industrial sensor and alarm data on a site's own computer and learns scoped, reversible corrections from an operator's words. Its detector, teaching engine, saved knowledge, and console work locally; optional online speech and language services are disabled by default.

## Run the demo

With the supplied dataset already processed in this checkout:

```bash
./scripts/run_demo.sh
```

Open **http://127.0.0.1:8000**. The launcher warms a real recorded hour and pauses at the presentation's starting point. Start with **“ignore generator cut-in on turbine four”**, watch the model revision and struck-out matching events, then resume playback to see a later recurrence. Undo is available beside every correction. The offline switch stays on by default; it does not turn off the computer's Wi-Fi.

Follow [the 90-second demo script](demo/DEMO_SCRIPT.md), [the chosen source timestamps](demo/scenario.json), and [the submission draft](demo/SUBMISSION.md). The console labels scripted recording shortcuts explicitly. They apply the recorded transcript through the real teaching engine; they are not live speech recognition.

Stop the launcher with **Ctrl+C**. It owns and stops only the server it started. Use one Uvicorn worker: one process owns the replay clock, model, and append-only correction journal. Port 8000 already in use produces a clear message; stop the previous EARSHOT terminal rather than starting another writer.

## What the data actually shows

The January–March 2025 selection contains **68,891 alarm records**, **50,327,215 numeric SCADA readings**, and **21 known turbines**. Logged alarms average **31.933677 per hour across the site**; the top ten codes account for **92.022180%** of records. The demonstration's selected trailing hour contains **276 logged alarms**, including four generator cut-in records for turbine four.

Source: Alex Clerc and Elizabeth Lingkan, RES on behalf of TRIG, [Hill of Towie wind farm open dataset, Zenodo record 22662930](https://zenodo.org/records/22662930), version 2.1.0, [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). EARSHOT selects three months, reshapes numeric measurements, joins supplied descriptions, and preserves source alarm records. These transformations are local; the raw archives are unchanged. The record confirms UTC/end-of-period timestamps for ten-minute SCADA; the alarm-log timezone is not independently established.

These figures measure event logs, not verified nuisance labels, incident accuracy, or operator staffing. Two records belong to unmapped station `91` and remain in site totals. Most observed alarm codes lack supplied descriptions and are displayed as undocumented. The chart's 12/hour line is an approximate operator-console workload reference, not a universal safety limit. See [baseline evidence](docs/PHASE_4_REPORT.md) and [machine-readable measurements](demo/baseline_stats.json).

## Setup from a fresh checkout

Validated on macOS arm64 with Python 3.13.7. Run in the repository root:

```bash
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt -c requirements.lock
cp .env.example .env
mkdir -p data/raw data/processed
```

Place the supplied ZIPs and metadata under `data/raw/` as documented in [dataset placement](docs/DATASET_PLACEMENT.md). The selected build needs `2025.zip`, `Hill_of_Towie_alarms_description.csv`, and `Hill_of_Towie_turbine_metadata.csv`; retain the field/table descriptions alongside them. Configuration selects January–March 2025.

```bash
./venv/bin/python scripts/explore_schema.py
./venv/bin/python scripts/build_dataset.py
./venv/bin/python scripts/compute_baseline.py
./venv/bin/python scripts/build_vocabulary.py
./scripts/run_demo.sh
```

The launcher checks processed inputs and rebuilds missing datasets using these commands. **The sub-minute launch target assumes dependencies and processed data already exist**; initial installation and processing tens of millions of measurements take longer. Raw and processed data, secrets, site journals, logs, and reference checkouts remain Git-ignored. The WAV demonstration assets are checked in, so ordinary use does not require macOS speech tools.

For ordinary chronological replay from the beginning, without demo prewarming:

```bash
./venv/bin/uvicorn earshot.server:app --host 127.0.0.1 --port 8000
```

## How teaching works

```text
Local ZIPs + metadata -> Parquet -> chronological replay -> per-asset detector
                                           |                     |
                                           +----> local policy <-+
                                                      |
Operator text / verified local speech -----------------+
        |                                             |
        +-> validated scoped rule -> labeled buffer -> River classifier
                                      |               |
                               JSONL journal <--------+ -> revision + rescore
                                                      |
                                               local console / undo

Optional online mode: microphone -> ElevenLabs; text -> Nebius/Qwen-compatible API
Offline switch: block those application provider calls; local teaching continues
```

A correction becomes a validated rule with an asset, alarm code, action, and audit identity. The policy labels matching buffered alarms and counter-examples, updates a local River logistic classifier, writes the exact training batch to disk, increments the revision, and rescores recent observations. Simple alarm-code corrections take effect through their explicit rule immediately. This is not retraining a large language model. Numeric sensor observations separately update the rolling anomaly detector.

Undo revokes the rule and reconstructs the classifier from remaining training batches. A restart restores accepted rules and training; detector history and stream counters start fresh. Seek clears recent observations while retaining teaching. Explicit critical/emergency events and escalation rules stay visible; the dataset's `stopping` flag alone does not prove an incident is dangerous. The prototype advises on recorded data and does not operate equipment.

The live ring holds 2,000 merged observations, including sensor snapshots. The displayed trailing-hour count is limited to that ring and is not extrapolated from a shorter interval. Suppressing the chosen four T04 events lowers the selected hour from 276 to 272; it does not make the whole site's alarm flood disappear.

## Voice and offline behavior

- **Optional live provider:** set `ELEVENLABS_API_KEY` in `.env`, restart, and explicitly turn offline mode off. Hold the talk button (or Space outside text fields) and release to submit one clip. Audio goes to ElevenLabs; the resulting transcript uses the same local teaching endpoint logic. Live provider quality is unverified without credentials.
- **On-device browser speech:** offered only if the browser supports `processLocally` and reports an installed language pack. EARSHOT never silently substitutes cloud browser recognition in offline mode. Permission and browser support vary. [Browser contract](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/processLocally).
- **Scripted fallback:** buttons/keys 1–5 play local synthetic recordings and apply their disclosed transcripts. Five spoken confirmations are cached as WAV files. These work without credentials or network and are visibly labeled.
- **Text:** always available. Unsupported or ambiguous instructions do not change the model.

Online parsing optionally uses `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`; `.env.example` includes a Nebius/Qwen example. Online mode can send text/vocabulary to that provider and voice/audio to ElevenLabs. Detector state, source events, and classifier weights stay local. The default `EARSHOT_OFFLINE=1` blocks application provider calls, including before retries. A switch cannot recall a request already sent; late provider results are rejected after switching offline. Cached confirmations are served locally first.

Provider failures have bounded timeouts and clear fallbacks. Browser microphone permissions, local recognition availability, and room-volume checks require a person at the machine. This prototype's fully local live voice experience depends on verified browser speech support; the recordings demonstrate teaching mechanics without claiming that transcription occurred.

## Inspect and validate

```bash
./venv/bin/pytest -q
./venv/bin/python -m pip check
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/rules
```

`/health`, `/stats`, `/console`, `/rules`, and `/openapi.json` expose local state and contracts. `/stream` publishes events, teaching, undo, link state, and statistics. `/teach`, `/listen`, `/undo/{rule_id}`, `/killswitch`, and `/replay` accept local actions. Replay controls pause/resume or seek to a source timestamp. Missing inputs leave the page and health endpoint available; unavailable operations return structured errors.

Meaningful tests cover real-data ingestion/reconciliation, scoped learning, undo/persistence, provider-free operation, concurrent clients, offline races, cached voice, and scripted teaching. For optional browser regression, install the development tool with `./venv/bin/python -m pip install playwright`, then run `./venv/bin/python scripts/verify_browser.py` against the prewarmed demo. It uses installed Chrome, temporarily teaches and undoes its own corrections, and saves screenshots locally. Playwright is not an application dependency. Final measured results and local evidence paths are in [the remaining-phases report](docs/PHASES_8_10_REPORT.md).

## PRIOR ART VS BUILT TODAY

The local [Phase 1 reference inventory](reference/REUSE_NOTES.md) reviewed `Dhwanil25/autonomous-ml-pipeline` at `ef26b071abd73979daff1bd7a7242afeded8c934` and `RAIN-Lab-AI/DOM_OS_Simulator` at `618295eaa460f3b21dd0e68cc464a92b65762b5a`. That inventory and both private clones are intentionally ignored by Git.

**Functions copied from either repository: none.** The inventory's COPY recommendations were candidates, not completed extraction. EARSHOT's cadence handling, replay timing, inline chart, and other implementation use fresh code; no reference module is imported or required at runtime. Concepts such as per-asset scoping, chronological observations, local model revisions, and an inspectable console informed the design.

Written in this project's implementation phases: the Hill of Towie adapter and measured baseline, bounded replay, rolling detectors, validated rules, local correction journal and River training, conservative parser, FastAPI/WebSocket server, operator console, optional voice adapters, local demonstration audio, and rehearsal/validation scripts. Third-party dependencies remain their respective upstream projects. See [the implementation plan](docs/PHASES_8_10_PLAN.md) and the individual phase reports under `docs/`.
