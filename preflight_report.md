# EARSHOT Phase 0 — Preflight report

Checked: 2026-09-19, approximately 18:58 UTC. Result: **PASS — Phase 0 complete.**

## Workspace

PASS — Workspace and project root: `/Users/dhwanil/Desktop/DOM/Dreamai_Project`.
The plan's `~/earshot-hackathon` workspace and `~/earshot-hackathon/earshot` project paths map to this existing GitHub-connected repository. Future references belong in `reference/`, and raw-data placement belongs in `data/raw/`. No second repository is needed.

PASS — Git branch: `main`; origin: `https://github.com/Dhwanil25/Dreamai_project.git`.

## Environment

| Check | Result | Evidence |
| --- | --- | --- |
| OS / architecture | PASS | macOS 26.6, Darwin 25.6.0, arm64 |
| Python >= 3.11 | PASS | Python 3.13.7 |
| pip3 | PASS | pip 25.2, using Python 3.13 |
| git | PASS | 2.39.3 (Apple Git-146) |
| gh | PASS | 2.98.0 (2026-08-20) |
| curl | PASS | 8.7.1 |
| unzip | PASS | Info-ZIP UnZip 6.00 |
| Browser | PASS | Google Chrome 153.0.8010.48; Safari 26.6 installed |
| GitHub authentication | PASS | `Dhwanil25`, active keyring account, HTTPS Git protocol |
| GitHub token scopes | PASS | `gist`, `project`, `read:org`, `repo`, `workflow`; token omitted |
| First reference repository | PASS | `Dhwanil25/autonomous-ml-pipeline`: PRIVATE, readable |
| Second reference repository | PASS | `RAIN-Lab-AI/DOM_OS_Simulator`: PRIVATE, readable |
| Zenodo outbound connectivity | PASS | HEAD `https://zenodo.org/records/22662930`: HTTP 200 |
| ElevenLabs outbound connectivity | PASS | HEAD `https://api.elevenlabs.io/v1/models`: HTTP 405 |

ElevenLabs returned a method-not-allowed response: DNS, TLS and outbound HTTP reachability passed. Authenticated API access, quota, speech recognition and speech generation were not tested. No API key or audio was sent. GitHub checks used the system keychain outside the restricted sandbox.

## Supplied datasets — informational

| Original location | Archive bytes | ZIP members | Alarm-log members |
| --- | ---: | ---: | --- |
| `/Users/dhwanil/Downloads/2026.zip` | 553,982,874 | 52 | 4; filenames indicate January–April 2026 |
| `/Users/dhwanil/Downloads/2025.zip` | 1,596,220,683 | 156 | 12; filenames indicate January–December 2025 |
| `/Users/dhwanil/Downloads/2024.zip` | 1,557,682,397 | 156 | 12; filenames indicate January–December 2024 |

All three files exist and have readable ZIP directories containing `tblAlarmLog_<year>_<month>.csv` members. This inspection does not establish CRC integrity, actual row date coverage, schema or analytical suitability. Archives remain at their supplied paths; nothing was copied, extracted, ingested or uploaded.

The following companions were not found at their exact filenames in Downloads or this project's `data/raw/`: `Hill_of_Towie_alarms_description.csv`, `Hill_of_Towie_tables_description.csv`, `Hill_of_Towie_turbine_fields_description.csv`, `Hill_of_Towie_turbine_metadata.csv`. These belong to the dataset gate before Phase 3 and do not block Phase 0 or Phase 1. Later configuration must use the supplied years instead of the plan's default 2016.

## Completion

- Failed Phase 0 checks: **none**.
- Human action required before Phase 1: **none**.
- No tool installation or new GitHub login was needed.
- Phase 1 has not started. No reference clones, application code, dependency installation or dataset ingestion was performed.

The later cloud speech/LLM design still needs reconciliation with the project's local-only requirement before those integrations are implemented; it does not block preflight.
