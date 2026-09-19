# EARSHOT Phase 2 — Scaffold and environment

Completed: 2026-09-19. Result: **PASS**. Phase 3 has not started.

## Delivered

- Existing GitHub-connected repository preserved as the project root.
- Python 3.13.7 virtual environment at `venv/`; pip upgraded to 26.2.1.
- All fourteen requested direct dependencies installed and exactly pinned in `requirements.txt`; resolved transitive versions recorded in `requirements.lock`.
- Working `config.yaml` and `earshot/config.py`: module-relative root discovery, attribute access, rooted Path values, and project-local .env loading without replacing process variables.
- All requested package, script, test, UI and demo files; no application business implementation.
- Forty-two explicit business/script/route functions have contract docstrings and `NotImplementedError` bodies. Dataclasses and request models declare contracts only.
- Thirteen real configuration tests; policy and parser tests remain documented placeholders.
- Pytest discovery confined to `tests/` so reference checkouts are not collected.
- Raw data, processed data, credentials, environment, reference repositories and recordings remain ignored by Git.

## Scoped adaptations from the execution prompt

- Use the current repository rather than creating `~/earshot-hackathon/earshot` or reinitializing Git.
- Select supplied year **2025**, months **[1, 2, 3]**, rather than unavailable 2016. Raw schema column names remain null.
- `.env.example` defaults to **EARSHOT_OFFLINE=1** to preserve the original local-only requirement. Cloud credentials are optional, unused by the scaffold and not required for these checks. Provider settings are retained from the plan as unverified future examples. Offline enforcement and speech are not implemented.
- Configuration functions are working infrastructure; business functions are stubs. The real configuration tests, `pytest.ini`, dependency lock and this report supplement the requested tree to make validation reproducible.
- No reference implementation was copied. A new scaffold commit uses the existing branch and remote; the user's execution-plan source is preserved.

## Required validation output

Configuration and requested import commands:

```text
raw: /Users/dhwanil/Desktop/DOM/Dreamai_Project/data/raw
processed: /Users/dhwanil/Desktop/DOM/Dreamai_Project/data/processed
all modules import OK
```

Test collection followed by test execution:

```text
tests/test_config.py::test_paths_resolve_from_project_not_launch_directory
tests/test_config.py::test_yaml_values_and_unknown_schema_remain_unmodified
tests/test_config.py::test_root_search_walks_up_from_module_file
tests/test_config.py::test_project_env_loads_without_overriding_existing_values
tests/test_config.py::test_invalid_data_paths_fail_before_use[../outside]
tests/test_config.py::test_invalid_data_paths_fail_before_use[]
tests/test_config.py::test_invalid_data_paths_fail_before_use[None]
tests/test_config.py::test_invalid_data_paths_fail_before_use[123]
tests/test_config.py::test_absolute_data_path_is_rejected
tests/test_config.py::test_incomplete_config_has_clear_error[]
tests/test_config.py::test_incomplete_config_has_clear_error[[]]
tests/test_config.py::test_incomplete_config_has_clear_error[data: null]
tests/test_config.py::test_incomplete_config_has_clear_error[data: {}]

13 tests collected in 0.01s
.............                                                            [100%]
13 passed in 0.02s
```

Dependency consistency and direct-dependency imports:

```text
No broken requirements found.
pandas: import OK
pyarrow: import OK
numpy: import OK
river: import OK
fastapi: import OK
uvicorn: import OK
websockets: import OK
pydantic: import OK
yaml: import OK
dotenv: import OK
requests: import OK
openai: import OK
pytest: import OK
httpx: import OK
```

All ten `earshot` submodules were also imported successfully, including replay, voice and metrics.

Static scaffold validation:

```text
PASS: complete scaffold; 42 explicit business/script/route functions are documented NotImplementedError stubs.
PASS: no absolute filesystem path literals in earshot/ or scripts/.
PASS: data/raw/ and data/processed/ exist and are empty.
```

Server process smoke check:

```text
PASS: uvicorn started on loopback; /openapi.json returned HTTP 200.
PASS: all seven HTTP route contracts are present; /stream is declared separately as a WebSocket.
Scaffold server stopped; no background process retained.
```

This proves application startup and schema generation only. The root UI, health, teaching and other business endpoints still raise `NotImplementedError`; there is no working replay, model or voice demonstration yet. API documentation pages are disabled to avoid CDN assets.

Project directories (excluding environment, caches, Git internals and reference clones):

```text
data/
data/raw/
data/processed/
demo/
demo/utterances/
earshot/
scripts/
tests/
ui/
```

The requested source tree and `venv/` exist. `data/raw/` contains **0 entries**; `data/processed/` contains **0 entries**. Empty data directories are intentionally untracked; README setup commands recreate them after cloning.

## Installation failures

**None.** Pip upgrade and dependency installation both exited successfully. `pip check` reports no broken requirements. Import smoke checks succeeded for all direct dependencies. Exact versions were derived from this installed environment; other operating systems and Python versions are not yet validated.

## Dataset gate and human handoff

The supplied `2024.zip`, `2025.zip` and `2026.zip` remain in Downloads. No archive was copied, extracted or ingested in Phase 2.

Before Phase 3, place **2025.zip** and these four original companion files in this repository's **data/raw/**:

- `Hill_of_Towie_alarms_description.csv`
- `Hill_of_Towie_tables_description.csv`
- `Hill_of_Towie_turbine_fields_description.csv`
- `Hill_of_Towie_turbine_metadata.csv`

The other supplied years may also be placed there for later use. Do not unzip them. No new 2016 download is needed.

The original prompt's handoff line is retained verbatim below; apply the workspace and optional-credentials clarifications immediately after it:

> >>> HUMAN ACTION REQUIRED: (a) copy .env.example to .env and fill in your API keys; (b) place the dataset in ~/earshot-hackathon/earshot/data/raw/ per the Dataset Placement Instructions. Phase 3 will fail without the dataset.

**Effective handoff for this workspace:** `~/earshot-hackathon/earshot/data/raw/` maps to `/Users/dhwanil/Desktop/DOM/Dreamai_Project/data/raw/`. Copy `.env.example` to `.env` locally if desired and keep `EARSHOT_OFFLINE=1`; API keys are not needed for scaffolding or Phase 3 local dataset ingestion. Keep any future credentials in the ignored local `.env`. Dataset placement is the remaining prerequisite for Phase 3.
