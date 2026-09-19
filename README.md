# EARSHOT

**The industrial AI you teach by talking to it — and it never phones home.**

EARSHOT is a proposed local industrial monitoring application for a hackathon demo. It connects sensor replay, anomaly detection, and operator feedback so a site can retain its own operational knowledge.

## Current status

Phase 0 is complete: the development tools, GitHub authentication, both private reference repositories, and required outbound connectivity have been checked. See the [preflight report](preflight_report.md) for evidence and limitations. The application, detector, and speech integration are not implemented yet; Phase 1 is next.

This repository is both the workspace and project root. Execution-plan paths under `~/earshot-hackathon/earshot/` resolve here, and the plan's workspace-level `reference/` directory and `preflight_report.md` also live here. Preserve the existing Git repository and origin when scaffolding later phases.

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

The supplied datasets are `2024.zip`, `2025.zip`, and `2026.zip` in the user's Downloads directory. All three have readable ZIP directories containing alarm-log members; the exact source paths and archive inventory are recorded in the preflight report. They have not been ingested or moved. Data placement and companion metadata collection happen after Phase 2, before Phase 3. Use one of the supplied years in configuration rather than the original plan's default 2016.

Useful context includes timestamp format, sensor units, asset or site identifiers, operating modes, and the meaning of any event labels. Actual columns and coverage will be discovered during Phase 3.

Before choosing a detector, inspect sampling intervals, missing values, repeated records, sensor ranges, operating cycles, and label coverage. Keep a later replay interval separate when evaluating whether a correction generalizes.

## Project documents

- [Phase 0 preflight report](preflight_report.md)
- [Original elevator pitch](docs/PITCH.md)
- [Implementation and presentation plan](docs/DEMO_PLAN.md)
