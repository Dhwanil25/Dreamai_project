# EARSHOT

**The industrial AI you teach by talking to it — with replay, learning and corrections kept on the site's computer.**

Industrial event logs can bury useful information in repetitive activity. Site operators know which patterns are routine, but their explanations rarely become an immediate, inspectable model correction. EARSHOT connects an operator's instruction to a scoped local correction, updates a River classifier from real buffered observations, shows the resulting visibility change, and preserves an undoable local audit trail.

The demonstration replays the **Hill of Towie wind farm open dataset**: **68,891 alarm records** and **50,327,215 numeric readings**, grouped into **272,039 sensor snapshots**. The site log averages **31.933677 logged alarm records/hour site-wide** over the selected January–March 2025 period; the ten most frequent codes account for **92.022180%** of records. These measurements do not establish a false-alarm rate, incident-detection accuracy or operator workload. Two additional records from unmapped station 91 remain in site totals.

Dataset attribution: [Hill of Towie wind farm open dataset, Zenodo record 22662930](https://zenodo.org/records/22662930), RES on behalf of TRIG, **CC-BY-4.0**. Aggregated project calculations and source hashes are in [baseline_stats.json](baseline_stats.json). The 90-second scenario uses actual logged timestamps and distinguishes presenter-supplied corrections from source labels.

## What we built

Local ingestion and source-quality checks; a reproducible alarm baseline; bounded chronological replay of a signal-major Parquet dataset; per-turbine anomaly detection; validated scoped corrections; local classifier updates; reversible persistence; a conservative vocabulary-backed text parser; FastAPI/WebSocket teaching and replay endpoints; the self-contained operator console; optional voice adapters and explicit offline fallbacks; and a repeatable, source-verified presentation scenario.

A `code_match` correction changes the specified scope deterministically while the local classifier also learns from buffered examples. This demonstration does not claim that a simple instruction retrains a foundational model or proves general anomaly accuracy. An untouched stopping-code example remains visible. The prototype monitors replayed data and does not control industrial equipment.

Reference repositories were reviewed before implementation. See the README's **PRIOR ART VS BUILT TODAY** section and the local, Git-ignored `reference/REUSE_NOTES.md` inventory for the distinction between prior work and this project’s implementation.

## Stack and provider disclosure

- **Python, pandas and PyArrow:** local data ingestion, validation and bounded replay.
- **River:** Half-Space Trees and online logistic learning, alongside a custom rolling Z-score detector.
- **FastAPI, Uvicorn and WebSockets:** one local runtime and live updates.
- **Vanilla HTML/CSS/JavaScript:** one self-contained operator console.
- **ElevenLabs:** optional speech-to-text and text-to-speech adapters. No live provider call was validated here because credentials were absent.
- **Nebius AI Studio:** the example OpenAI-compatible LLM endpoint, configured for `Qwen/Qwen3-235B-A22B`. It is optional; the deterministic local parser is the tested offline path. No live LLM provider call was validated here.
- **Installed macOS speech synthesis:** generated the five explicitly labeled synthetic demo fixtures and their local confirmation cache. This is not live transcription.

With the application offline switch engaged, optional provider requests are blocked and text teaching, fixture teaching, replay, rescoring and cached confirmation remain local. In online mode, optional providers can receive audio or an utterance and controlled vocabulary; that mode does not support a never-phones-home claim. Browser speech recognition may itself use a service and is not an offline guarantee.

## Run

From the repository root, set up the documented virtual environment and dependencies in [README.md](../README.md), and place the supplied dataset files under `data/raw/`. Raw files, Parquet, secrets and learned site state are Git-ignored; they are not bundled in the repository.

```bash
bash scripts/run_demo.sh
```

Open <http://127.0.0.1:8000/>. The launcher checks the sources and assets, rebuilds missing processed data from the supplied raw files, prewarms the real hour, and pauses. The under 60-second startup target applies when preprocessing is already complete; ingesting the year ZIP is separate setup work. Ctrl+C stops only the launcher's own server. Existing corrections and their journal are retained.

Follow [DEMO_SCRIPT.md](DEMO_SCRIPT.md): teach **“ignore generator cut-in on turbine four”**, resume to its recurrence, show the untouched stopping record, engage the offline switch, and teach **“ignore fast cut-out of generator on turbine four”**. Fixture keys 1–5 are visibly labeled scripted replay. Live microphone behavior and projector readability require a human rehearsal on the presentation machine.
