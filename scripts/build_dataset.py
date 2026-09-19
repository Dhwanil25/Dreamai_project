"""Build local PyArrow Parquet datasets and append reproducible coverage evidence."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from earshot.config import CONFIG
from earshot.ingest import (
    SCADA_TABLES, UNDOCUMENTED, iter_scada, load_alarm_log,
    read_month_table, validate_dataset,
)

COVERAGE_MARKER = "## Processed dataset coverage"


def _alarm_quality(alarms: pd.DataFrame) -> dict:
    """Reconcile normalized events to real rows, metadata and nullable endings."""
    schema = CONFIG.schema
    raw = pd.concat([
        read_month_table(CONFIG.data.year, month, schema.alarm_table_prefix)
        for month in CONFIG.data.months
    ], ignore_index=True)
    if len(raw) != len(alarms) or alarms.empty:
        raise ValueError("Alarm normalization must preserve all source rows and produce nonempty data")
    metadata = pd.read_csv(CONFIG.data.raw_dir / "Hill_of_Towie_turbine_metadata.csv", encoding="utf-8-sig")
    known = set(metadata["Station ID"].astype("str"))
    observed = set(alarms.turbine_id)
    keys = [schema.alarm_time_col, schema.alarm_turbine_col, schema.alarm_code_col]
    endings = raw.groupby(keys)[schema.alarm_end_time_col].nunique(dropna=False)
    return {
        "source_rows": len(raw),
        "exact_duplicate_source_rows": int(raw.duplicated().sum()),
        "duplicate_event_key_rows": int(raw.duplicated(keys).sum()),
        "event_keys_with_conflicting_endings": int((endings > 1).sum()),
        "missing_end_times": int(raw[schema.alarm_end_time_col].isna().sum()),
        "metadata_turbines": len(known),
        "known_turbines_observed": len(observed & known),
        "unknown_station_ids": sorted(observed - known),
        "unknown_station_rows": int((~alarms.turbine_id.isin(known)).sum()),
    }


def build_dataset() -> dict:
    """Build the configured quarter without extracting files or network calls."""
    validate_dataset(CONFIG.data.year)
    output = CONFIG.data.processed_dir
    report_path = output / "schema_report.md"
    if not report_path.is_file():
        raise FileNotFoundError("Run scripts/explore_schema.py before building the dataset")
    alarms = load_alarm_log(CONFIG.data.year, CONFIG.data.months)
    quality = _alarm_quality(alarms)
    documented = int(alarms.description.ne(UNDOCUMENTED).sum())
    top_codes = (
        alarms.groupby(["alarm_code", "description"], sort=False)
        .size().rename("rows").reset_index()
        .sort_values(["rows", "alarm_code"], ascending=[False, True]).head(10)
    )
    manifest = {
        "year": CONFIG.data.year,
        "months": sorted(CONFIG.data.months),
        "timezone": "UTC assumed for naive timestamps; source timezone unverified",
        "alarms": {
            "rows": len(alarms),
            "first_timestamp": str(alarms.ts.min()),
            "last_timestamp": str(alarms.ts.max()),
            "distinct_station_ids": int(alarms.turbine_id.nunique()),
            "distinct_codes": int(alarms.alarm_code.nunique()),
            "documented_rows": documented,
            "documented_row_percent": documented / len(alarms) * 100,
            "top_codes": top_codes.to_dict(orient="records"),
            **quality,
        },
        "scada": {"rows": 0, "sources": []},
    }
    # Stage complete files on the same filesystem. A failed conversion leaves
    # the previous completed files intact, never a partially written Parquet.
    with TemporaryDirectory(prefix=".build-", dir=output) as staging:
        staged = Path(staging)
        alarms.to_parquet(staged / "alarms.parquet", engine="pyarrow", index=False)
        writer = None
        try:
            for frame in iter_scada(CONFIG.data.year, CONFIG.data.months, SCADA_TABLES):
                profile = frame.attrs["source"]
                if frame.empty or profile["duplicate_time_station_rows"]:
                    raise ValueError(f"{profile['member']}: empty or duplicate SCADA grain")
                if len(profile["turbine_ids"]) != quality["metadata_turbines"]:
                    raise ValueError(f"{profile['member']}: turbine coverage does not match metadata")
                manifest["scada"]["sources"].append(profile)
                manifest["scada"]["rows"] += len(frame)
                frame.attrs = {}
                arrow = pa.Table.from_pandas(frame, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(
                        staged / "scada.parquet", arrow.schema,
                        compression="snappy", use_dictionary=["turbine_id", "signal"],
                    )
                writer.write_table(arrow, row_group_size=1_000_000)
                print(f"{profile['member']}: {profile['included_rows']:,} source rows -> {len(frame):,} numeric measurements", flush=True)
                del arrow, frame
        finally:
            if writer is not None:
                writer.close()
        if not manifest["scada"]["rows"]:
            raise ValueError("No SCADA measurements were produced")
        for name, expected in (("alarms", len(alarms)), ("scada", manifest["scada"]["rows"])):
            if pq.read_metadata(staged / f"{name}.parquet").num_rows != expected:
                raise ValueError(f"{name}: Parquet row count does not reconcile")
        # Validate complete staged files before publishing. Timings are local
        # post-write measurements, not a portable or cold-cache guarantee.
        manifest["parquet"] = {}
        for name in ("alarms", "scada"):
            path = staged / f"{name}.parquet"
            started = perf_counter()
            loaded = pd.read_parquet(path, engine="pyarrow")
            seconds = perf_counter() - started
            manifest["parquet"][name] = {"bytes": path.stat().st_size, "read_seconds": seconds}
            print(f"{name}.parquet: {len(loaded):,} rows; full pandas read {seconds:.3f}s", flush=True)
            del loaded

        coverage = coverage_report(manifest)
        previous = report_path.read_text(encoding="utf-8").split(COVERAGE_MARKER, 1)[0].rstrip()
        (staged / "schema_report.md").write_text(previous + "\n\n" + coverage + "\n", encoding="utf-8")
        (staged / "ingestion_report.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        for name in ("alarms.parquet", "scada.parquet", "schema_report.md", "ingestion_report.json"):
            (staged / name).replace(output / name)
    print(coverage)
    return manifest


def coverage_report(result: dict) -> str:
    """Render measured coverage, source contradictions and explicit assumptions."""
    alarm, scada = result["alarms"], result["scada"]
    top = pd.DataFrame(alarm["top_codes"])[["alarm_code", "rows", "description"]]
    sources = scada["sources"]
    station_note = (
        f"The plan's assumption that all alarm identifiers are turbines is contradicted by unmapped station IDs {alarm['unknown_station_ids']}. "
        "No mapping is invented and no alarm is discarded to force a metadata match."
        if alarm["unknown_station_ids"] else
        "Every observed alarm station ID matches the supplied turbine metadata."
    )
    numeric_fields = ", ".join(
        f"{p['member'].rsplit('_', 2)[0]}: {p['numeric_signals']} numeric fields"
        for p in sources[:len(SCADA_TABLES)]
    )
    sparse = sorted({column for profile in sources for column in profile["sparse_numeric_columns"]})
    excluded_values = {
        column: sorted({value for profile in sources for value in profile["excluded_values"].get(column, [])})
        for column in {column for profile in sources for column in profile["excluded_columns"]}
    }
    gaps = "\n".join(
        f"- `{table}`: {sum(p['included_rows'] for p in sources if p['member'].startswith(table + '_')):,} observed time/station keys; "
        f"{sum(p['expected_ten_minute_station_keys'] - p['included_rows'] for p in sources if p['member'].startswith(table + '_')):,} absent keys against a regular ten-minute calendar grid; "
        f"{sum(p['all_numeric_null_rows'] for p in sources if p['member'].startswith(table + '_')):,} present rows have all numeric fields null."
        for table in SCADA_TABLES
    )
    source_table = pd.DataFrame(sources)[[
        "member", "source_rows", "included_rows", "numeric_signals",
        "null_measurements", "next_month_endpoint_rows_excluded",
    ]]
    return f"""{COVERAGE_MARKER}

Configured source: {result['year']}.zip; months {result['months']}. Built locally using PyArrow; no extracted or substituted data.

Alarm rows: **{alarm['rows']:,}**, equal to the source count.
Date range: **{alarm['first_timestamp']} → {alarm['last_timestamp']}**.
Distinct station IDs: **{alarm['distinct_station_ids']}**, comprising **{alarm['known_turbines_observed']} known turbines** (metadata: {alarm['metadata_turbines']}) plus **{alarm['unknown_station_ids']} unmapped**.
Unmapped-station alarm rows retained: **{alarm['unknown_station_rows']}**.
Distinct alarm codes: **{alarm['distinct_codes']}**.
Documented alarm rows: **{alarm['documented_rows']:,} / {alarm['rows']:,} = {alarm['documented_row_percent']:.4f}%**.
Undocumented rows retained: **{alarm['rows'] - alarm['documented_rows']:,}**, with description `(undocumented)` and stopping `-1`.

Top 10 alarm codes by source-row frequency:

```text
{top.to_string(index=False)}
```

### Alarm quality and interpretation

- Source exact duplicate extras: {alarm['exact_duplicate_source_rows']:,}; duplicate (TimeOn, StationNr, Alarmcode) extras: {alarm['duplicate_event_key_rows']:,}. All are preserved. {alarm['event_keys_with_conflicting_endings']} event keys have conflicting TimeOff values, so blindly deduplicating would discard differing source records.
- Missing TimeOff: {alarm['missing_end_times']:,}. No end time, duration or stopping status is inferred from a missing value. TimeOff remains in the source archive and is profiled, but is not part of the five-column alarm output contract.
- {station_note}
- Undocumented event rows account for {100 - alarm['documented_row_percent']:.4f}% of the selected data. Documentation or stopping status is not a false-alarm label.

### Numeric SCADA coverage

Long rows: **{scada['rows']:,}**. Only `tblSCTurTemp` and `tblSCTurGrid` are loaded ({numeric_fields}). All numeric fields retain their exact names and float64 values. String-valued station and signal categories provide compact storage. Missing numeric measurements remain null; no imputation, rounding or invented samples are applied.

```text
{source_table.to_string(index=False)}
```

- Null numeric measurements retained: {sum(p['null_measurements'] for p in sources):,}.
{gaps}
- Each CSV is filtered to its own half-open calendar month, `[month start, next month start)`. This removes {sum(p['next_month_endpoint_rows_excluded'] for p in sources):,} next-month endpoint rows across {len(sources)} files, preventing overlapping monthly endpoints and keeping observations inside the selected months. Other unexpected out-of-month timestamps cause a build error.
- Duplicate (timestamp, station) rows after monthly filtering: {sum(p['duplicate_time_station_rows'] for p in sources)}. All SCADA station IDs match metadata.
- Explicitly excluded categorical columns and their observed nonnull values: `{excluded_values}`. Their source columns remain in the ZIP. No category is converted to a fabricated number.
- Numeric columns with over 95% nulls in at least one processed member: `{sparse}`. Their observed values and nulls are retained. Numeric quality/status meanings are unverified and are not used to filter data.
- Numeric gaps are not filled. Source units are not guessed for names absent from the field dictionary; similar manufacturer names are not silently equated.

### Timezone and output contract

Source timestamps have no offset and the companions do not identify a timezone. UTC is an **unverified interpretation required by the output contract**; no daylight-saving conversion is guessed. Both files expose tz-naive `datetime64[ns]` in `ts` (a column, as specified by the detailed phase prompt). Alarms use string station IDs and integer codes/stopping; SCADA has `ts`, `turbine_id`, `signal`, `value`.

### Local load validation

```text
alarms.parquet: {result['parquet']['alarms']['bytes']:,} bytes; full pandas read {result['parquet']['alarms']['read_seconds']:.3f} s
scada.parquet:  {result['parquet']['scada']['bytes']:,} bytes; full pandas read {result['parquet']['scada']['read_seconds']:.3f} s
```

These are full reads measured immediately after writing on this machine; cache state and hardware affect performance. No Phase 4 baseline metrics, replay, detection or teaching were implemented.
"""


def main() -> int:
    """Return zero after a successful build or a clear nonzero local-input error."""
    try:
        build_dataset()
    except (FileNotFoundError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
