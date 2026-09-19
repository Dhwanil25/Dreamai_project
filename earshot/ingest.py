"""Normalize real local ZIP records without extracting or changing source files.

Naive timestamps are assumed UTC to meet the output contract; the supplied
metadata does not confirm that timezone. Unknown alarms and missing numeric
measurements are retained. See the local schema report for source limitations.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from earshot.config import CONFIG

COMPANION_FILES = (
    "Hill_of_Towie_alarms_description.csv",
    "Hill_of_Towie_turbine_metadata.csv",
    "Hill_of_Towie_turbine_fields_description.csv",
    "Hill_of_Towie_tables_description.csv",
)
SCADA_TABLES = ("tblSCTurTemp", "tblSCTurGrid")
UNDOCUMENTED = "(undocumented)"


def validate_dataset(year: int) -> Path:
    """Require the configured local archive and companion files before reading."""
    if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
        raise ValueError("year must be an integer calendar year")
    raw = CONFIG.data.raw_dir
    required = [raw / f"{year}.zip", *(raw / name for name in COMPANION_FILES)]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Dataset not present. Missing in {raw}: {', '.join(missing)}")
    return required[0]


def _months(months: Sequence[int]) -> list[int]:
    selected = list(months)
    if not selected or any(
        isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12
        for month in selected
    ):
        raise ValueError("months must contain integer month numbers from 1 through 12")
    if len(set(selected)) != len(selected):
        raise ValueError("months must not contain duplicates")
    return sorted(selected)


def read_month_table(year: int, month: int, table: str) -> pd.DataFrame:
    """Read exactly one discovered monthly CSV directly from a ZIP into memory."""
    _months([month])
    archive = validate_dataset(year)
    filename = f"{table}_{year}_{month:02d}.csv"
    with ZipFile(archive) as zipped:
        matches = [name for name in zipped.namelist() if Path(name).name == filename]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one {filename} in {archive.name}; found {len(matches)}")
        return pd.read_csv(BytesIO(zipped.read(matches[0])), encoding="utf-8-sig")


def _required(frame: pd.DataFrame, columns: Sequence[str], source: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{source}: missing required columns {missing}; rerun schema discovery")


def _integers(values: pd.Series, label: str) -> pd.Series:
    numbers = pd.to_numeric(values, errors="raise")
    if numbers.isna().any() or (numbers % 1 != 0).any():
        raise ValueError(f"{label} must contain non-null integers")
    return numbers.astype("int64")


def _timestamps(values: pd.Series, label: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="raise", utc=True, format="ISO8601")
    if parsed.isna().any():
        raise ValueError(f"{label} must contain non-null timestamps")
    return parsed.dt.tz_localize(None).dt.as_unit("ns")


def load_alarm_descriptions() -> pd.DataFrame:
    """Read and validate the discovered code/description/stopping lookup table."""
    source = CONFIG.data.raw_dir / COMPANION_FILES[0]
    if not source.is_file():
        raise FileNotFoundError(f"Dataset not present. Missing {source.name}")
    raw = pd.read_csv(source, encoding="utf-8-sig")
    _required(raw, ["Alarm Code", "Description", "Stopping"], source.name)
    result = pd.DataFrame({
        "alarm_code": _integers(raw["Alarm Code"], "Alarm Code"),
        "description": raw["Description"].astype("str").str.strip(),
        "stopping": _integers(raw["Stopping"], "Stopping"),
    })
    if result.alarm_code.duplicated().any():
        raise ValueError("Alarm descriptions contain duplicate codes; a many-to-one join is required")
    if result.description.isna().any() or result.description.eq("").any():
        raise ValueError("Documented alarm descriptions must not be empty")
    if not result.stopping.isin([0, 1]).all():
        raise ValueError("Documented Stopping values must be 0 or 1")
    return result


def load_alarm_log(year: int, months: Sequence[int]) -> pd.DataFrame:
    """Return every alarm row, retaining unknown codes, station IDs and duplicates.

    TimeOn is the event timestamp. TimeOff is not used to infer stopping status.
    A validated left join assigns undocumented codes '(undocumented)' and -1.
    """
    selected = _months(months)
    validate_dataset(year)
    schema = CONFIG.schema
    frames = []
    for month in selected:
        raw = read_month_table(year, month, schema.alarm_table_prefix)
        _required(raw, [schema.alarm_time_col, schema.alarm_turbine_col, schema.alarm_code_col], "alarm log")
        frames.append(pd.DataFrame({
            "ts": _timestamps(raw[schema.alarm_time_col], schema.alarm_time_col),
            "turbine_id": _integers(raw[schema.alarm_turbine_col], schema.alarm_turbine_col).astype("str"),
            "alarm_code": _integers(raw[schema.alarm_code_col], schema.alarm_code_col),
        }))
    alarms = pd.concat(frames, ignore_index=True)
    joined = alarms.merge(load_alarm_descriptions(), on="alarm_code", how="left", validate="many_to_one")
    joined["description"] = joined.description.fillna(UNDOCUMENTED).astype("str")
    joined["stopping"] = joined.stopping.fillna(-1).astype("int64")
    return joined.sort_values(["ts", "turbine_id", "alarm_code"], kind="stable").reset_index(drop=True)


def iter_scada(year: int, months: Sequence[int], tables: Sequence[str]) -> Iterator[pd.DataFrame]:
    """Yield one numeric long-format frame per month/table, bounding build memory.

    String-valued categories keep station IDs and signal names compact. Numeric
    nulls remain null; categorical exclusions are explicit in config. Monthly
    endpoint overlaps are resolved by half-open calendar-month membership.
    Quality counters are attached as frame.attrs['source'].
    """
    selected = _months(months)
    selected_tables = list(tables)
    if not selected_tables or len(set(selected_tables)) != len(selected_tables):
        raise ValueError("tables must contain distinct discovered SCADA table names")
    if any(table not in SCADA_TABLES for table in selected_tables):
        raise ValueError(f"Phase 3 supports only {', '.join(SCADA_TABLES)}")
    validate_dataset(year)
    schema = CONFIG.schema
    keys = [schema.scada_time_col, schema.scada_turbine_col]
    exclusions = set(schema.scada_exclude_signals)
    signal_columns: dict[str, list[str]] = {}
    # Common dictionaries across all chunks preserve compact categories when
    # concatenating or writing tens of millions of string-valued records.
    with ZipFile(CONFIG.data.raw_dir / f"{year}.zip") as zipped:
        for table in selected_tables:
            filename = f"{table}_{year}_{selected[0]:02d}.csv"
            matches = [name for name in zipped.namelist() if Path(name).name == filename]
            if len(matches) != 1:
                raise ValueError(f"Expected exactly one {filename}; found {len(matches)}")
            with zipped.open(matches[0]) as member:
                header = pd.read_csv(BytesIO(member.readline()), encoding="utf-8-sig", nrows=0)
            _required(header, keys, table)
            signal_columns[table] = [name for name in header if name not in keys and name not in exclusions]
    all_signals = [signal for columns in signal_columns.values() for signal in columns]
    if not all_signals or len(set(all_signals)) != len(all_signals):
        raise ValueError("SCADA signal names must be nonempty and unambiguous across selected tables")
    metadata = pd.read_csv(CONFIG.data.raw_dir / COMPANION_FILES[1], encoding="utf-8-sig")
    _required(metadata, ["Station ID"], "turbine metadata")
    stations = _integers(metadata["Station ID"], "Station ID").astype("str").tolist()
    if len(set(stations)) != len(stations):
        raise ValueError("Turbine metadata contains duplicate Station ID values")
    for month in selected:
        for table in selected_tables:
            raw = read_month_table(year, month, table)
            _required(raw, keys, table)
            signals = [column for column in raw if column not in keys and column not in exclusions]
            if set(signals) != set(signal_columns[table]):
                raise ValueError(f"{table} {year}-{month:02d}: signal schema changed; rerun schema discovery")
            times = _timestamps(raw[keys[0]], keys[0])
            start = pd.Timestamp(year=year, month=month, day=1)
            end = start + pd.offsets.MonthBegin(1)
            keep = times.ge(start) & times.lt(end)
            outside = int((~keep).sum())
            if (times[~keep] != end).any():
                raise ValueError(f"{table} {year}-{month:02d}: unexpected timestamps outside its month")
            raw = raw.loc[keep].reset_index(drop=True)
            times = times.loc[keep].reset_index(drop=True)
            ids = _integers(raw[keys[1]], keys[1]).astype("str")
            unknown = set(ids) - set(stations)
            if unknown:
                raise ValueError(f"{table}: station IDs missing from metadata: {sorted(unknown)}")
            wide = pd.DataFrame({"ts": times, "turbine_id": pd.Categorical(ids, categories=stations)})
            numeric = raw[signals].apply(pd.to_numeric, errors="raise").astype("float64")
            wide = pd.concat([wide, numeric], axis=1).sort_values(["ts", "turbine_id"], kind="stable")
            profile = {
                "member": f"{table}_{year}_{month:02d}.csv",
                "source_rows": len(raw) + outside,
                "included_rows": len(raw),
                "next_month_endpoint_rows_excluded": outside,
                "numeric_signals": len(signals),
                "null_measurements": int(numeric.isna().sum().sum()),
                "sparse_numeric_columns": numeric.columns[numeric.isna().mean().gt(0.95)].tolist(),
                "all_numeric_null_rows": int(numeric.isna().all(axis=1).sum()),
                "duplicate_time_station_rows": int(wide.duplicated(["ts", "turbine_id"]).sum()),
                "expected_ten_minute_station_keys": int((end - start) / pd.Timedelta(minutes=10)) * len(stations),
                "excluded_columns": sorted(set(raw.columns) & exclusions),
                "excluded_values": {
                    column: raw[column].dropna().astype("str").str.strip().unique().tolist()
                    for column in sorted(set(raw.columns) & exclusions)
                },
                "turbine_ids": sorted(set(ids)),
                "first_timestamp": str(times.min()),
                "last_timestamp": str(times.max()),
            }
            long = wide.melt(id_vars=["ts", "turbine_id"], value_vars=signals, var_name="signal", value_name="value")
            long["signal"] = pd.Categorical(long.signal, categories=all_signals)
            long.attrs["source"] = profile
            yield long


def load_scada(year: int, months: Sequence[int], tables: Sequence[str]) -> pd.DataFrame:
    """Return ts, turbine_id, signal, value without rounding or filling gaps.

    Prefer iter_scada for builds to avoid holding the whole quarter in memory.
    """
    result = pd.concat(iter_scada(year, months, tables), ignore_index=True)
    result.attrs = {}
    return result
