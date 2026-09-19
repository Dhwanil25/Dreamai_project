# EARSHOT

**The industrial AI you teach by talking to it — and it never phones home.**

EARSHOT is a local industrial monitoring project for a hackathon demo. Its planned sensor replay, anomaly detection, and operator feedback loop will let a site retain its own operational knowledge.

## Current status

Phase 2 is complete: the virtual environment and pinned dependencies are installed, project-relative configuration works, and the package and FastAPI application import successfully. The backend serves the local scaffold page and a health response. Unfinished business endpoints return structured HTTP 501 responses; ingestion, anomaly detection, teaching, speech and replay are not implemented yet. Configuration and backend regression tests verify this boundary. The two policy/parser test files remain placeholders, not passing business tests.

Phase 1's reference inventory is available locally at `reference/REUSE_NOTES.md`; reference clones remain Git-ignored. The dataset placement gate is complete in the current workspace; Phase 3 ingestion has not started.

This repository is both the workspace and project root. Execution-plan paths under `~/earshot-hackathon/earshot/` resolve here, and the plan's workspace-level `reference/` directory and `preflight_report.md` also live here. Preserve the existing Git repository and origin when scaffolding later phases.

## Setup and scaffold checks

Validated with Python 3.13.7 on macOS arm64. Run from the repository root:

```bash
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt -c requirements.lock
mkdir -p data/raw data/processed
cp .env.example .env
./venv/bin/python -m pip check
./venv/bin/pytest -q
```

`requirements.txt` pins direct dependencies; `requirements.lock` records the resolved dependency versions from this environment and acts as installation constraints. Other Python versions/platforms have not been validated. Pytest searches only this project's `tests/`, excluding the reference repositories.

The config loader finds `config.yaml` relative to its module, resolves data paths against the project root, and reads the root `.env` without replacing existing environment variables. Schema column names stay unset until Phase 3. The selected year is 2025, months January–March.

`.env.example` uses `EARSHOT_OFFLINE=1`, preserving the local-only requirement rather than the original plan's online default. Keys are optional and unused by this scaffold. The example retains the plan's future provider settings; their availability has not been tested. Runtime offline enforcement and local speech still need implementation.

Start the scaffold process:

```bash
./venv/bin/uvicorn earshot.server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/` to see the scaffold page. `/health` returns HTTP 200 with `status: "scaffold"` and explicit unavailable feature flags; `ok: true` means the server is responding, not that the monitoring pipeline is ready. `/openapi.json` exposes the route contracts. Interactive API documentation is disabled so the scaffold does not load CDN assets.

`/stats`, `/rules`, `/teach`, `/undo/{rule_id}` and `/killswitch` return HTTP 501 with a structured `not_implemented` explanation until their implementation phase. `/stream` sends an explanatory error message and closes normally. These responses do not train a model, suppress alarms or change offline controls.

If a server was running before a code update, stop it with Ctrl+C and rerun the startup command. The original Phase 2 route stubs raised unhandled `NotImplementedError`, causing HTTP 500 even on the home page and health endpoint; the scaffold now handles these cases explicitly.

## First demo

1. Load the supplied dataset locally and show its actual sensor signals.
2. Replay a recorded interval and surface candidate anomalies.
3. Select one nuisance event and record an operator's explanation by voice, with a text input available during development.
4. Preview the specific local correction, then apply it to the site's knowledge store.
5. Replay a later, matching event and show the effect of the correction.
6. Show a different anomaly still being raised.
7. Restart the app and repeat the correction test with Wi-Fi disabled to demonstrate persistence and offline operation.

The demo should distinguish measured results, human labels, and any synthetic scenarios. It must not report anomaly accuracy or false-alarm reduction unless the dataset supports those measurements.

## Build boundaries

- Sensor records, audio, transcripts, learned state, and audit events stay on the demo computer.
- All application assets and any required model weights must be installed before the offline demonstration.
- Speech transcription must run locally. A cloud-backed browser speech service would not satisfy the product requirement.
- The first correction mechanism must be described honestly: a stored rule, a learned local classifier, and a retrained detector are different implementations.
- A correction targets a specific operating pattern and site. It does not permanently disable an entire sensor or silently remove the original event.
- Corrections can be inspected and revoked. Explicit critical-limit events remain visible in the prototype.
- This prototype replays data and advises an operator; it does not control industrial equipment.

## Dataset intake

All eight supplied files are now copied into this workspace's `data/raw/`: the 2024, 2025 and 2026 year ZIPs, the optional ShutdownDuration ZIP, and the four companion CSVs. Copies were SHA-256 verified, original Downloads files were preserved, and archives remain compressed. The ignored `data/dataset_placement_manifest.json` records this local placement. No dataset has been ingested; `data/processed/` is still empty.

When setting up another checkout, place `2025.zip` in its `data/raw/` without unzipping it. The other supplied archives are optional for that configured year. Include these four companion files with their original names:

- `Hill_of_Towie_alarms_description.csv`
- `Hill_of_Towie_tables_description.csv`
- `Hill_of_Towie_turbine_fields_description.csv`
- `Hill_of_Towie_turbine_metadata.csv`

The archive and metadata remain local and Git-ignored. The original plan's `~/earshot-hackathon/earshot/data/raw/` path maps to this repository's `data/raw/`.

Useful context includes timestamp format, sensor units, asset or site identifiers, operating modes, and the meaning of any event labels. Actual columns and coverage will be discovered during Phase 3.

Before choosing a detector, inspect sampling intervals, missing values, repeated records, sensor ranges, operating cycles, and label coverage. Keep a later replay interval separate when evaluating whether a correction generalizes.

## Project documents

- [Phase 2 scaffold validation](docs/PHASE_2_REPORT.md)
- [Phase 0 preflight report](preflight_report.md)
- [Original elevator pitch](docs/PITCH.md)
- [Implementation and presentation plan](docs/DEMO_PLAN.md)
