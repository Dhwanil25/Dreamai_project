# EARSHOT

**The industrial AI you teach by talking to it — and it never phones home.**

EARSHOT is a local industrial monitoring project for a hackathon demo. Its planned sensor replay, anomaly detection, and operator feedback loop will let a site retain its own operational knowledge.

## Current status

Phase 4 adds a reproducible alarm baseline to the real-data ingestion pipeline. January–March 2025 contains **68,891 alarm rows**, averaging **31.933677 logged records/hour site-wide**. The top ten codes account for **92.022180%** of records. These are event-log measurements; the dataset does not establish false-alarm rates or operator workload. See [the Phase 4 report](docs/PHASE_4_REPORT.md) for methodology and verification.

Phase 3 also produced **50,327,215 numeric SCADA readings**. All 21 known turbines are present; two alarms have the additional unmapped station ID `91` and remain in site-wide totals. The turbine-rate denominator uses the 21 metadata turbines and excludes those two records. See [the Phase 3 report](docs/PHASE_3_REPORT.md) for ingestion details.

The backend serves the local scaffold page and a health response. Unfinished business endpoints return structured HTTP 501 responses; anomaly detection, teaching, speech and replay remain unimplemented. Configuration, ingestion, baseline and backend regression tests verify this boundary. The two policy/parser test files remain placeholders, not passing business tests.

Phase 1's reference inventory is available locally at `reference/REUSE_NOTES.md`; reference clones remain Git-ignored. Raw data, processed datasets and their detailed local reports are also Git-ignored.

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

The config loader finds `config.yaml` relative to its module, resolves data paths against the project root, and reads the root `.env` without replacing existing environment variables. Schema mappings now contain the column names discovered from the real files. The selected year is 2025, months January–March.

`.env.example` uses `EARSHOT_OFFLINE=1`, preserving the local-only requirement rather than the original plan's online default. Keys are optional and unused by this scaffold. The example retains the plan's future provider settings; their availability has not been tested. Runtime offline enforcement and local speech still need implementation.

Start the scaffold process:

```bash
./venv/bin/uvicorn earshot.server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/` to see the scaffold page. `/health` returns HTTP 200 with `status: "scaffold"`, `phase: 4` and `ingestion: true`; this flag describes the available local ingestion code, not a live feed or automatic dataset check. `ok: true` means the server is responding. `/openapi.json` exposes the route contracts. Interactive API documentation is disabled so the scaffold does not load CDN assets.

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

All eight supplied files are copied into this workspace's `data/raw/`: the 2024, 2025 and 2026 year ZIPs, the optional ShutdownDuration ZIP, and the four companion CSVs. Copies were SHA-256 verified, original Downloads files were preserved, and archives remain compressed. The ignored `data/dataset_placement_manifest.json` records this local placement. Phase 3 processes only January–March of `2025.zip`; the remaining archives are retained for later selection.

When setting up another checkout, place `2025.zip` in its `data/raw/` without unzipping it. The other supplied archives are optional for that configured year. Include these four companion files with their original names:

- `Hill_of_Towie_alarms_description.csv`
- `Hill_of_Towie_tables_description.csv`
- `Hill_of_Towie_turbine_fields_description.csv`
- `Hill_of_Towie_turbine_metadata.csv`

The archive and metadata remain local and Git-ignored. The original plan's `~/earshot-hackathon/earshot/data/raw/` path maps to this repository's `data/raw/`.

## Build and inspect the local dataset

With the supplied files in place, run from the repository root. No API keys or internet connection are needed:

```bash
./venv/bin/python scripts/explore_schema.py
./venv/bin/python scripts/build_dataset.py
./venv/bin/pytest -q
```

Discovery prints exact headers, types, null counts, full sample rows and metadata. Building reads ZIP members in memory without extracting them, writes `data/processed/alarms.parquet` and `scada.parquet`, and appends coverage to `data/processed/schema_report.md`. Machine-readable counts are saved in `data/processed/ingestion_report.json`. Rerunning the build replaces its outputs and coverage section. Missing required inputs cause a clear error; no substitute data is generated.

Inspect results without rebuilding:

```bash
./venv/bin/python - <<'PY'
import pandas as pd
from earshot.config import CONFIG
for name in ('alarms', 'scada'):
    frame = pd.read_parquet(CONFIG.data.processed_dir / f'{name}.parquet')
    print(name, 'rows:', len(frame), 'columns:', list(frame.columns))
    print(frame.head().to_string(index=False))
    del frame
PY
cat data/processed/schema_report.md
```

Alarm columns are `ts`, `turbine_id`, `alarm_code`, `description`, `stopping`. SCADA columns are `ts`, `turbine_id`, `signal`, `value`; string-valued categories keep the larger table compact. Only temperature and turbine-grid tables are ingested. The categorical grid status field is explicitly excluded; all numeric fields and their nulls remain. Monthly endpoint overlaps are resolved by keeping timestamps inside each source month.

Unknown codes keep `(undocumented)` descriptions and `stopping=-1`; missing alarm endings do not establish stopping status. Raw alarm duplicates are retained and reported. Timestamps have no source timezone declaration: the output's naive UTC interpretation is an explicit, unverified assumption. Station IDs retain their source values; turbine display names come from the metadata mapping. Documentation and stopping classifications do not identify false alarms.

Before choosing a detector, inspect sampling intervals, missing values, repeated records, sensor ranges, operating cycles, and label coverage. Keep a later replay interval separate when evaluating whether a correction generalizes.

## Compute and inspect the baseline

After Phase 3 has produced the alarm Parquet, run:

```bash
./venv/bin/python scripts/compute_baseline.py
cat data/processed/baseline_report.md
./venv/bin/python -m json.tool demo/baseline_stats.json
./venv/bin/pytest -q
```

The command prints the complete report and writes both artifacts. The versioned JSON includes source checksums, rates, Pareto, top codes, every discovered group and its pairwise support counts, every qualifying flood bin, consecutive runs, stopping classifications and duplicate sensitivity. Values retain computed precision in JSON; the report formats them for reading. Missing, invalid or suspicious inputs stop publication without inventing substitute records. Runs need no API keys or network access.

Floods use fixed, clock-aligned, half-open ten-minute bins containing more than ten records; consecutive flagged bins are reported as runs. Co-occurrence requires another code on the same station within inclusive ±60 seconds, with at least 80% support in **both** directions. Maximal cliques keep every pair in a group above the threshold. All qualifying groups remain visible; a member with fewer than 20 observations flags limited evidence. Co-occurrence does not establish a common cause or permission to suppress an event.

The **12/hour operator-console reference** is an approximate average workload benchmark, not a universal safety limit. The site log's ratio to that reference is descriptive because annunciation, routing and staffing are unknown. Per-turbine rates are asset diagnostics. [ISA background](https://www.isa.org/intech-home/2016/may-june/features/getting-the-most-from-your-safety-alarms)

Only aggregate `demo/baseline_stats.json` is tracked for later UI use. Raw records and the detailed local report remain Git-ignored. The `/stats` API and dashboard integration are still scheduled for later phases.

## Project documents

- [Phase 4 baseline validation](docs/PHASE_4_REPORT.md)
- [Phase 3 ingestion validation](docs/PHASE_3_REPORT.md)
- [Phase 2 scaffold validation](docs/PHASE_2_REPORT.md)
- [Phase 0 preflight report](preflight_report.md)
- [Original elevator pitch](docs/PITCH.md)
- [Implementation and presentation plan](docs/DEMO_PLAN.md)
