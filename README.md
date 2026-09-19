# EARSHOT

**The industrial AI you teach by talking to it — and it never phones home.**

EARSHOT is a local industrial monitoring project for a hackathon demo. Its text parser, anomaly detectors and teaching engine run locally; sensor replay and voice integration are still upcoming.

## Current status

Phase 6 turns operator text into a validated correction proposal using the site's real vocabulary. It recognizes turbine aliases, supported alarm descriptions and suppress/collapse/escalate instructions; unknown, ambiguous or unsupported requests return no rule. Offline parsing needs no keys or network. An optional compatible online parser has bounded retries and falls back locally. See [the Phase 6 report](docs/PHASE_6_REPORT.md) for validation and the text-to-learning demonstration.

Phase 5 implements validated correction rules, Z-score and Half-Space Trees detectors, and a local policy engine with a River logistic classifier. Teaching updates classifier weights, rescoring changes buffered visibility, and undo removes the correction's training batch. Rules and training batches persist locally. On 2,000 real alarm records, a turbine-specific correction suppressed **17 matching events in 33.016 ms** while keeping the same code visible on other turbines. Its **215 tests passed**. See [the Phase 5 report](docs/PHASE_5_REPORT.md) for complete validation output and limitations.

Phase 4 provides the reproducible alarm baseline. January–March 2025 contains **68,891 alarm rows**, averaging **31.933677 logged records/hour site-wide**. The top ten codes account for **92.022180%** of records. These are event-log measurements; the dataset does not establish false-alarm rates or operator workload. See [the Phase 4 report](docs/PHASE_4_REPORT.md) for methodology and verification.

Phase 3 also produced **50,327,215 numeric SCADA readings**. All 21 known turbines are present; two alarms have the additional unmapped station ID `91` and remain in site-wide totals. The turbine-rate denominator uses the 21 metadata turbines and excludes those two records. See [the Phase 3 report](docs/PHASE_3_REPORT.md) for ingestion details.

The backend serves the local scaffold page and a health response. Text parsing, detection and teaching are available through the Python library and tests; live business endpoints return structured HTTP 501 responses until Phase 7. Speech and replay remain unimplemented.

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

`.env.example` uses `EARSHOT_OFFLINE=1`. The parser enforces this before constructing a provider client and before each call; local speech still needs implementation. With offline mode off and all three `LLM_*` settings populated, the optional parser sends the utterance and controlled vocabulary to the configured endpoint. Policy learning, event data and model weights remain local. Keep offline mode on for the never-phones-home demonstration. No LLM credentials were available for live provider validation; its request, validation and fallback paths were tested with local mocks.

Start the scaffold process:

```bash
./venv/bin/uvicorn earshot.server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/` to see the scaffold page. `/health` returns HTTP 200 with `status: "scaffold"`, `phase: 6` and ingestion, parsing, detection and teaching flags set to true. These flags describe available Python libraries, not live endpoints or automatic dataset checks. `ok: true` means the server is responding. `/openapi.json` exposes the route contracts. Interactive API documentation is disabled so the scaffold does not load CDN assets.

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

## Teach with text and see what changes

Run this from the repository root:

```bash
EARSHOT_OFFLINE=1 ./venv/bin/python scripts/demo_teaching.py
```

It parses “ignore the cable untwist alarm on turbine four,” displays the proposed rule, and applies it to 2,000 real events in a temporary local ledger. The output shows before/after counts, changed classifier weights, a later matching event, the same code on another turbine, restart and undo. It leaves your saved site corrections unchanged. Try another supported sentence with `--text "group pitch lubrication on turbine 4"`.

Teaching is a sequence:

1. `parse_utterance(text, context)` resolves words against real metadata. Here, “turbine four” becomes station `2304513` and “cable untwist” becomes code `10105`. Parsing only proposes a rule.
2. `policy.learn(rule)` saves the correction, labels matching buffered events as nuisance examples and other eligible events as counter-examples, then updates the local River logistic classifier. The model version increments.
3. The policy rescans its buffer and applies the correction to future matching events. Ordinary code rules take effect immediately; the classifier also changes its weights. A `learned` pattern uses classifier probability within its authorized scope.
4. `policy.undo(rule.rule_id)` removes the correction, rebuilds the classifier from remaining training batches and restores buffered visibility.

This is supervised feedback from the operator's instruction. It does not prove an event is a false alarm or train a general-purpose language model. Voice recording and the browser's teach button are later phases.

The cached vocabulary contains 22 observed station IDs, 192 observed codes, seven documented alarm descriptions and metadata-derived aliases for the 21 known turbines. Station `91` has no invented turbine number. Undocumented codes can be referenced explicitly by number, but their meanings are not guessed. The generator-winding-temperature example in the plan has no matching description here, so it returns no rule.

Rebuild the cache whenever the source dataset changes, then verify:

```bash
./venv/bin/python scripts/build_vocabulary.py
EARSHOT_OFFLINE=1 ./venv/bin/pytest tests/test_parse.py -v
./venv/bin/pytest -q
```

Supported offline corrections name one observed alarm class and optionally one turbine, with suppress, collapse or escalate intent. Different named assets/codes, unknown identities, conflicting intentions, exclusions and unsupported time/sensor conditions return no rule. Negative suppression such as “don't ignore” becomes escalation when the alarm is resolvable. Collapse uses the configured 60-second per-turbine window; it does not infer a physical fault family. Confidence values 0.6 offline and at least 0.85 online are parser labels, not measured correctness probabilities.

## Verify the local teaching engine

With the Phase 3 Parquet files present, run:

```bash
./venv/bin/pytest tests/test_policy.py -v
./venv/bin/pytest -q
```

The policy tests use unchanged real records and temporary correction ledgers. This repeatable example also uses a temporary ledger, so it leaves your site's saved corrections intact:

```bash
./venv/bin/python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import pandas as pd
from earshot.detector import create_detector
from earshot.policy import PolicyLayer
from earshot.rules import SuppressionRule

alarms = pd.read_parquet('data/processed/alarms.parquet')
with TemporaryDirectory() as directory:
    policy = PolicyLayer(create_detector(), 2000, rules_path=Path(directory) / 'rules.jsonl')
    for event in alarms.head(2000).to_dict('records'):
        policy.score(event)
    rule = SuppressionRule(
        rule_id='demo', utterance='ignore that one',
        scope={'turbine_id': alarms.turbine_id.iloc[0],
               'alarm_code': int(alarms.alarm_code.value_counts().index[0]), 'signal': None},
        pattern={'kind': 'code_match', 'window_s': 0, 'conditions': []},
        action='suppress', confidence=0.9, taught_by='demo',
        taught_at='2026-09-20T10:00:00Z', reversible=True,
    )
    print('before:', policy.stats())
    start = perf_counter()
    result = policy.learn(rule)
    print('learn:', result, 'ms:', round((perf_counter() - start) * 1000, 3))
    print('after:', policy.stats())
    policy.undo('demo')
    print('undo:', policy.stats())
PY
```

`PolicyLayer(create_detector(), 2000)` normally saves to `data/processed/rules.jsonl`. Use one live writer per ledger and unique rule IDs. Restart reconstructs active rules and the classifier from recorded, weighted training batches. Detector histories, replay buffers and counters begin fresh. Invalid or truncated journal records raise a clear error; complete learn records without a commit are ignored. The original validation rule was undone; the local audit history remains at version 3 with no active rules.

`config.yaml` selects `detector.method` (`zscore` or `hst`) and the policy probability, learning rate and collapse window. Detectors consume actual numeric `signals`/`features`, or `signal` plus `value`, independently per asset. Alarm-only events have anomaly score zero. HST requires a stable feature schema established by its first observed vector.

`code_match` and `conditions` suppression rules apply immediately. A `learned` pattern uses the classifier threshold inside the rule's full scope and conditions; classifier confidence cannot authorize suppression on another turbine. `collapse` keeps the first matching event in each asset's window, and `escalate` or explicit critical annotations preserve visibility. All-None scope is an explicit wildcard. These rules demonstrate correction mechanics, not measured false-alarm accuracy.

## Project documents

- [Phase 6 text parsing and teaching validation](docs/PHASE_6_REPORT.md)
- [Phase 5 teaching engine validation](docs/PHASE_5_REPORT.md)
- [Phase 4 baseline validation](docs/PHASE_4_REPORT.md)
- [Phase 3 ingestion validation](docs/PHASE_3_REPORT.md)
- [Phase 2 scaffold validation](docs/PHASE_2_REPORT.md)
- [Phase 0 preflight report](preflight_report.md)
- [Original elevator pitch](docs/PITCH.md)
- [Implementation and presentation plan](docs/DEMO_PLAN.md)
