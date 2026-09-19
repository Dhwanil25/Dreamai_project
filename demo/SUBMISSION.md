# EARSHOT

**Local industrial alarm learning from operator instructions, with inspectable source and model evidence.**

EARSHOT connects an operator’s instruction to a scoped, reversible correction on the site’s computer. It replays real industrial observations, updates a local River classifier from buffered examples, changes the specified event visibility and records what happened. It is a monitoring prototype using recorded data, not a live plant connection or an industrial control system.

## The problem and the measured data

Operators understand recurring local operating patterns that generic monitoring software cannot infer reliably. EARSHOT provides a direct correction path and an audit trail, while preserving source records and allowing undo.

The selected **Hill of Towie** data contains **68,891 alarm records**, **50,327,215 numeric readings**, **272,039 grouped sensor snapshots** and **21 known turbines** over January–March 2025. The site log averages **31.933677 alarm records/hour**; the ten most frequent codes account for **92.022180%** of records. Two records from unmapped station 91 remain in site totals. These measurements establish event volume, not ground-truth nuisance labels, false-alarm accuracy or operator workload.

Attribution: Alex Clerc and Elizabeth Lingkan, RES on behalf of TRIG, [Hill of Towie wind farm open dataset, Zenodo 22662930](https://zenodo.org/records/22662930), version 2.1.0, **CC-BY-4.0**. Project calculations are in [baseline_stats.json](baseline_stats.json).

## What is inspectable

The running **`/evidence`** endpoint exposes source hashes and row traces, current classifier-state hashes/counts, and actual operation/provider receipts. Its source audit runs afresh at server startup with a `verified_at` timestamp; detected file changes invalidate that proof until a restart. An old on-disk receipt is never substituted for the startup audit. An independent local audit checks all 68,891 alarm timestamp/station/code records and duplicate multiplicities against the original ZIP, verifies description/stopping lookups and matches 185 selected SCADA cells to their original temperature/grid CSV rows. The full SCADA count is verified through Parquet metadata; a complete raw-quarter SCADA cell comparison is not claimed.

Teaching records the accepted rule and before/after local learning state. Simple code-based corrections change visibility through an explicit scoped rule while the River classifier learns from actual buffered examples. The language model is **not fine-tuned**, and an immediate rule match is not presented as an independently learned classifier decision. Undo is inspectable and restores learning from the remaining batches.

The website serves **no prerecorded operator inputs or fixed-transcript shortcuts**. Instructions come from actual typed text or available live microphone paths. Source timestamps can be navigated without inventing observations or automatically teaching a correction. Computer-generated speech is labeled confirmation output only.

## Stack and actual provider results

- **Python, pandas, PyArrow:** local ingestion, validation, chronological replay and source auditing.
- **River:** online logistic learning and Half-Space Trees; a separate custom rolling Z-score detector is the default.
- **FastAPI, Uvicorn, WebSockets:** one local runtime, current state and auditable operator actions.
- **Vanilla HTML/CSS/JavaScript:** the self-contained operator console.
- **Nebius Token Factory:** a real parse succeeded in **3.652 seconds** at `https://api.tokenfactory.nebius.com/v1`, using `Qwen/Qwen3-30B-A3B-Instruct-2507`. The provider request receipt is exposed in `/evidence` and documented in [LIVE_VERIFICATION.md](../docs/LIVE_VERIFICATION.md).
- **ElevenLabs:** both website endpoints succeeded after the earlier permission failures were resolved. `/listen` returned **HTTP 200 in 831.529 ms** for a real human reference WAV, transcribing “I have that curiosity beside me at this moment.” This non-industrial sentence returned `parsed: false` and left learning unchanged. `/speak` returned **HTTP 200 in 780.125 ms** with a **79,038-byte MP3**, provider request `AShN6GPZSPhe1VkahNjB`. The local receipt is `data/processed/website_voice_verification.json`; `/evidence` exposes recorded provider outcomes.

Local text teaching works without provider access. Verified on-device browser recognition is optional and depends on installed browser support. With online access enabled, audio may go to ElevenLabs and utterance/vocabulary text to Nebius. With the application offline switch engaged, new provider calls are blocked and local replay, parsing and learning continue. It does not switch off Wi-Fi or cancel requests already sent.

The successful reference-audio and speech-output checks verify the website endpoints, not the user's microphone setup or a spoken industrial command through the complete teaching flow. Browser permission, device capture and that interaction still require a human rehearsal.

## Built in this project

The dataset adapter and baseline; bounded replay; per-asset detectors; validated rules and local classifier training; reversible persistence; conservative local/provider parsing; HTTP/WebSocket integration; operator console; live voice adapters; source audit; and operation evidence. No functions were copied from the two reviewed reference repositories. See [README’s prior-art account](../README.md#prior-art-vs-built-today) and the local Git-ignored `reference/REUSE_NOTES.md` inventory.

Historical phase reports describe earlier milestones. [Current live verification](../docs/LIVE_VERIFICATION.md) records the current provider results and removal of prerecorded-input paths.

## Run

Follow [README setup](../README.md#setup), preserving any existing local `.env`, and place the supplied sources under `data/raw/`. Raw data, credentials and learned site state remain Git-ignored.

```bash
./venv/bin/python scripts/audit_source_evidence.py
bash scripts/run_demo.sh
```

Open <http://127.0.0.1:8000/> and inspect <http://127.0.0.1:8000/evidence>. The launcher validates inputs, rebuilds missing Parquet, prewarms a genuine recorded hour and pauses. The under 60-second startup target assumes dependencies and preprocessing are complete. Ctrl+C stops only its own server; saved corrections remain.

The process environment takes precedence over `.env`. A fresh unconfigured checkout defaults offline; the current local configuration can enable providers with `EARSHOT_OFFLINE=0`. To force local-only execution, run `EARSHOT_OFFLINE=1 ./scripts/run_demo.sh`.

The [90-second guide](DEMO_SCRIPT.md) uses instructions entered during the presentation, a real recurrence and an untouched stopping record. Browser microphone permission, device capture, a spoken industrial instruction and projector legibility still need to be checked before claiming a successful live microphone teaching demonstration.
