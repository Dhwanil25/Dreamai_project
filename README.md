# EARSHOT

**The industrial AI you teach by talking to it — and it never phones home.**

EARSHOT is a proposed local industrial monitoring application for a hackathon demo. It connects sensor replay, anomaly detection, and operator feedback so a site can retain its own operational knowledge.

## Current status

This workspace contains the project brief and presentation plan. The application, detector, and speech integration are not implemented yet. The next input is the user's dataset; its structure and labels will determine the first working demonstration.

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

Upload the dataset as provided, together with a data dictionary or README if available. Useful context includes timestamp format, sensor units, asset or site identifiers, operating modes, and the meaning of any event labels. Unknown fields can be investigated after upload.

Before choosing a detector, inspect sampling intervals, missing values, repeated records, sensor ranges, operating cycles, and label coverage. Keep a later replay interval separate when evaluating whether a correction generalizes.

## Project documents

- [Original elevator pitch](docs/PITCH.md)
- [Implementation and presentation plan](docs/DEMO_PLAN.md)
