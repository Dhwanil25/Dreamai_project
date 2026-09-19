# Phase 3 — real dataset ingestion

Phase 3 is complete for the supplied `2025.zip`, January–March. Discovery and ingestion run locally without API keys or network access. The ZIPs remain compressed and unchanged; no substitute records were generated. Phase 4 has not started.

## Discovered schema

The alarm CSV has exactly four columns, printed from the actual source before implementation:

| Raw column | Meaning | Processed field |
|---|---|---|
| `TimeOn` | Alarm start | `ts` |
| `TimeOff` | Nullable alarm end | Profiled; remains in the raw archive |
| `StationNr` | Source station identifier | String `turbine_id` |
| `Alarmcode` | Alarm code | Integer `alarm_code` |

The companion alarm dictionary supplies `Alarm Code`, `Description`, and `Stopping`. A validated left join supplies `description` and `stopping`; unmatched codes retain `(undocumented)` and `-1`. A missing `TimeOff` does not determine stopping status.

Temperature and grid CSVs use `TimeStamp` and `StationId`. Canonical IDs preserve the station numbers; metadata maps them to human names T01–T21. All 136 numeric temperature columns and 49 numeric grid columns are included. The categorical `wtc_ActRegSt_endvalue` is explicitly excluded through configuration. No numeric nulls are filled and no units or similarly named fields are guessed.

## Measured coverage

| Measure | Result |
|---|---:|
| January alarm rows | 20,032 |
| February alarm rows | 17,263 |
| March alarm rows | 31,596 |
| Total alarm rows | **68,891** |
| First alarm | 2025-01-01 00:21:00 |
| Last alarm | 2025-03-31 21:39:54 |
| Distinct alarm codes | 192 |
| Documented alarm rows | 56,334 / 68,891 = **81.7727%** |
| Undocumented alarm rows retained | 12,557 |
| Known turbines represented | 21 of 21 |
| Additional unmapped station | `91`: two alarm records |
| Total distinct alarm station IDs | 22 |
| Numeric SCADA rows | **50,327,215** |
| SCADA station IDs | 21, all matching metadata |
| Numeric SCADA signals | 185 |
| Missing numeric measurements retained | 1,352,185 |

SCADA spans 2025-01-01 00:00:00 through 2025-03-31 23:50:00. Each table has 272,039 observed time/station keys after monthly endpoint filtering. There are 121 absent keys against a regular ten-minute calendar grid and eight present rows with all numeric fields null per table. These gaps remain visible.

Top ten alarm codes by source-row frequency:

| Code | Rows | Description |
|---|---:|---|
| 25 | 27,858 | Fast cut-out of generator |
| 20 | 27,837 | Large generator Cut-in |
| 127 | 1,747 | (undocumented) |
| 191 | 1,499 | (undocumented) |
| 115 | 1,423 | (undocumented) |
| 111 | 1,064 | (undocumented) |
| 67 | 516 | (undocumented) |
| 68 | 516 | (undocumented) |
| 69 | 516 | (undocumented) |
| 61 | 419 | (undocumented) |

These counts include retained source duplicates. Documentation and stopping classifications are not false-alarm labels.

## Source contradictions and unresolved meanings

- The archive layout matches the expected monthly naming: 156 members across 13 tables, including 12 alarm members. Only the configured three months and two SCADA tables were ingested.
- The metadata contains 21 turbines, but the alarm log also contains two station-91/code-50358 records. Their identity cannot be confidently mapped. Both are retained, making 22 alarm station IDs; the pipeline does not manufacture a twenty-second turbine or discard rows to force a count of 21.
- Monthly SCADA CSVs include the following month's midnight. Filtering each file to `[month start, next month start)` excludes 126 endpoint rows across six files: it prevents duplicated February/March observations and removes April 1 from the configured quarter. No time/station duplicates remain in the processed SCADA grain.
- The alarm source contains 345 exact duplicate extras and 348 duplicate onset/station/code extras. Three event keys have conflicting endings. All alarm rows remain; 65,913 `TimeOff` values are missing.
- No timezone is declared in the supplied companions. Interpreting naive timestamps as UTC follows the requested output contract but is **unverified**. No daylight-saving conversion is guessed.
- The field dictionary contains temperature names absent from the source. Its CP1252 encoding differs from the UTF-8 companions. Similar names are not silently treated as aliases. Numeric quality code `3` has no supplied meaning and is not used to filter measurements.
- Undocumented codes are numerous, but undocumented events are only 18.2273% of this quarter's rows. The plan's “majority” statement must distinguish distinct codes from event counts.
- The detailed Phase 3 prompt specifies a `ts` column and four SCADA fields. Those contracts take precedence over the overview's reference to a datetime index and alarm-shaped SCADA output.

## Outputs and verification

Local, Git-ignored outputs:

- `data/processed/alarms.parquet`: 683,312 bytes.
- `data/processed/scada.parquet`: 198,054,217 bytes.
- `data/processed/schema_report.md`: full source inventory, headers, dtypes, null counts, untruncated samples, companion contents and coverage.
- `data/processed/ingestion_report.json`: machine-readable counts and measured read timings.
- `data/processed/phase3_validation.txt`: requested alarm/SCADA inspection output, full schema report and populated configuration.

The final network-blocked build loaded the complete Parquets with pandas in approximately **0.016 s** and **0.174 s**, respectively, below the two-second target. These are local measurements immediately after writing, not cold-cache or cross-machine guarantees. A full build also succeeded with socket connection attempts blocked. Staged files are read and validated before replacing the prior completed outputs.

Run from the repository root:

```bash
./venv/bin/python scripts/explore_schema.py
./venv/bin/python scripts/build_dataset.py
./venv/bin/pytest -q
```

All **37 tests passed**. One existing Starlette/AnyIO dependency deprecation warning remains. The scripts support module invocation and resolve data paths independently of the launch directory. Missing required sources fail clearly; no network fallback exists. Regression tests use unchanged records from the supplied ZIP, including undocumented codes, null measurements, station 91, duplicate alarm rows and overlapping month boundaries. An injected full-read failure also verifies that prior completed outputs remain unchanged and temporary files are cleaned up. Real-data tests explicitly skip if that source is absent from another checkout.

The local backend remains a scaffold: `/` and `/health` return 200, health reports Phase 3 ingestion capability, and unfinished business APIs return intentional 501 responses. Replay, detection, voice and teaching remain for later phases.
