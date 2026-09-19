"""Inspect supplied monthly CSVs in place and write the observed schema report.

Run directly or as ``python -m scripts.explore_schema``. Archive members are
read through BytesIO; no extraction, network access, or substitute data is used.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import re
import sys
import zipfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from earshot.config import CONFIG


COMPANIONS = (
    "Hill_of_Towie_alarms_description.csv",
    "Hill_of_Towie_turbine_metadata.csv",
    "Hill_of_Towie_tables_description.csv",
    "Hill_of_Towie_turbine_fields_description.csv",
)


def _csv_text(path: Path) -> tuple[str, str]:
    """Read a supplied companion, returning decoded text and its encoding."""
    payload = path.read_bytes()
    try:
        return payload.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        # The supplied turbine-fields dictionary contains CP1252 degree signs.
        return payload.decode("cp1252"), "cp1252"


def _member(archive: zipfile.ZipFile, basename: str) -> str:
    """Find exactly one matching CSV basename without extracting a member."""
    matches = [name for name in archive.namelist() if Path(name).name == basename]
    if not matches:
        raise FileNotFoundError(f"Required archive member is missing: {basename}")
    if len(matches) != 1:
        raise ValueError(f"Ambiguous archive member basename: {basename}")
    return matches[0]


def _read_member(archive: zipfile.ZipFile, member: str) -> pd.DataFrame:
    """Read one complete monthly CSV directly from its archive bytes."""
    return pd.read_csv(io.BytesIO(archive.read(member)), encoding="utf-8-sig")


def _profile(frame: pd.DataFrame, member: str, *, head: bool = True) -> str:
    """Format exact columns, inferred dtypes, null counts, and optional full rows."""
    summary = pd.DataFrame({
        "column": frame.columns,
        "dtype": [str(dtype) for dtype in frame.dtypes],
        "null_count": frame.isna().sum().to_numpy(),
    })
    parts = [
        f"## Monthly profile: {member}",
        f"Rows: {len(frame):,}. Columns: {len(frame.columns):,}.",
        "Exact column names, in source order:\n\n```json\n"
        + json.dumps(frame.columns.tolist(), indent=2)
        + "\n```",
        "Dtypes are pandas CSV inference before datetime conversion. Null counts "
        "cover the entire monthly member:\n\n```text\n"
        + summary.to_string(index=False)
        + "\n```",
    ]
    if head:
        parts.append("First 10 rows, with all columns and untruncated values:\n\n```text\n"
                     + frame.head(10).to_string(index=False, max_cols=None, max_colwidth=None)
                     + "\n```")
    return "\n\n".join(parts)


def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    """Reject schema changes instead of silently guessing replacement columns."""
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{name} is missing previously observed columns: {sorted(missing)}")


def build_report() -> str:
    """Read the configured first month and companions; return a Markdown report.

    Inputs are the configured year ZIP, first selected month, and all four local
    description files. This function only reads inputs. Missing inputs raise
    FileNotFoundError; unexpected schemas raise ValueError.
    """
    raw = CONFIG.data.raw_dir
    archive_path = raw / f"{CONFIG.data.year}.zip"
    required = [archive_path, *(raw / name for name in COMPANIONS)]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing required local files: " + ", ".join(missing))
    if not CONFIG.data.months:
        raise ValueError("data.months must contain at least one month")
    month = CONFIG.data.months[0]
    if type(month) is not int or not 1 <= month <= 12:
        raise ValueError("The first configured month must be an integer from 1 to 12")

    inventory = [
        f"{path.name}\t{path.stat().st_size:,} bytes"
        for path in sorted(raw.iterdir()) if path.is_file()
    ]
    sections = [
        "# EARSHOT observed dataset schema",
        f"Configured year: {CONFIG.data.year}; profiled month: {month:02d}. "
        "This report describes supplied files, not generated examples. "
        "No archive files were extracted and no data was downloaded.",
        "## Raw file inventory\n\n```text\n" + "\n".join(inventory) + "\n```",
    ]

    companion_text = {name: _csv_text(raw / name) for name in COMPANIONS}
    metadata = pd.read_csv(io.StringIO(companion_text[COMPANIONS[1]][0]))
    descriptions = pd.read_csv(io.StringIO(companion_text[COMPANIONS[0]][0]))
    fields = pd.read_csv(io.StringIO(companion_text[COMPANIONS[3]][0]))
    _require_columns(metadata, ("Station ID", "Turbine Name"), COMPANIONS[1])
    _require_columns(descriptions, ("Alarm Code", "Description", "Stopping"), COMPANIONS[0])
    _require_columns(fields, ("Manufacturer Name", "Table"), COMPANIONS[3])

    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        prefixes = sorted({
            match.group(1)
            for item in members
            if (match := re.fullmatch(r"(.+)_\d{4}_\d{2}\.csv", Path(item.filename).name))
        })
        alarm_members = [item.filename for item in members if "Alarm" in item.filename]
        sections.append(
            f"## Archive inventory: {archive_path.name}\n\n"
            f"Total member count: {len(members)}.\n\n"
            "Distinct monthly table prefixes:\n\n```text\n" + "\n".join(prefixes)
            + "\n```\n\nEvery member containing `Alarm`:\n\n```text\n"
            + "\n".join(alarm_members) + "\n```\n\n"
            "All members (uncompressed and compressed byte counts):\n\n```text\n"
            + "\n".join(f"{item.filename}\t{item.file_size:,}\t{item.compress_size:,}"
                        for item in members)
            + "\n```"
        )

        suffix = f"_{CONFIG.data.year}_{month:02d}.csv"
        alarm_member = _member(archive, CONFIG.schema.alarm_table_prefix + suffix)
        alarm = _read_member(archive, alarm_member)
        sections.append(_profile(alarm, alarm_member))
        # These names were printed from the supplied January 2025 header before
        # this discovery implementation was written; they are not guesses.
        _require_columns(alarm, ("TimeOn", "TimeOff", "StationNr", "Alarmcode"), alarm_member)

        temp_member = _member(archive, "tblSCTurTemp" + suffix)
        temp = _read_member(archive, temp_member)
        sections.append(_profile(temp, temp_member))
        _require_columns(temp, ("TimeStamp", "StationId"), temp_member)
        temp_stations = set(temp.StationId.dropna().astype(str))
        advertised_temp = set(fields.loc[fields.Table == "tblSCTurTemp", "Manufacturer Name"])
        absent_temp_fields = sorted(advertised_temp - set(temp.columns))
        del temp

        grid_member = _member(archive, "tblSCTurGrid" + suffix)
        grid = _read_member(archive, grid_member)
        _require_columns(grid, ("TimeStamp", "StationId"), grid_member)
        sections.append(_profile(grid, grid_member, head=False))
        nonnumeric = []
        for column in grid.columns:
            if column in {"TimeStamp", "StationId"} or pd.api.types.is_numeric_dtype(grid[column]):
                continue
            values = grid[column].dropna().astype(str)
            counts = values.str.strip().value_counts().head(10)
            nonnumeric.append(
                f"`{column}`: {len(values):,} nonnull values; "
                f"{values.str.strip().nunique():,} distinct values after trimming. "
                f"First raw value: {values.iloc[0]!r}.\n\n```text\n"
                + counts.to_string() + "\n```"
                if len(values) else f"`{column}`: no nonnull values."
            )
        grid_stations = set(grid.StationId.dropna().astype(str))
        del grid

    for name in COMPANIONS[:2]:
        content, encoding = companion_text[name]
        sections.append(f"## Full companion: {name}\n\nEncoding: `{encoding}`.\n\n```csv\n"
                        + content.rstrip("\r\n") + "\n```")

    station_map = metadata[["Station ID", "Turbine Name"]]
    known_stations = set(metadata["Station ID"].dropna().astype(str))
    alarm_stations = set(alarm.StationNr.dropna().astype(str))
    starts = pd.to_datetime(alarm.TimeOn, errors="coerce")
    ends = pd.to_datetime(alarm.TimeOff, errors="coerce")
    invalid_starts = int((alarm.TimeOn.notna() & starts.isna()).sum())
    invalid_ends = int((alarm.TimeOff.notna() & ends.isna()).sum())
    negative_durations = int(((ends - starts).dt.total_seconds() < 0).sum())
    mappings = [
        "Timestamp: `TimeOn` (alarm start).",
        "Turbine identifier: `StationNr`; SCADA uses `StationId`. Both are station "
        "IDs, joined to metadata `Station ID`. Human turbine labels come from "
        "metadata `Turbine Name`; no positional or arithmetic mapping is needed.",
        "Alarm code: `Alarmcode`, joined to companion `Alarm Code`.",
        "Alarm end time: `TimeOff`, nullable. No duration column is present. "
        "A duration can only be derived where both start and end parse; missing "
        "end times do not establish a zero duration or a stopping classification.",
        "No stopping flag is present in the four-column alarm CSV. Documented "
        "classification comes from companion `Stopping`; an undocumented code's "
        "stopping status remains unknown (-1), and its event must be retained.",
        f"Profiled alarm start range: {starts.min()} to {starts.max()}. "
        f"Invalid nonnull start timestamps: {invalid_starts}; "
        f"invalid nonnull end timestamps: {invalid_ends}; "
        f"negative derived durations: {negative_durations}.",
        f"Profiled-month station IDs absent from metadata: alarms={sorted(alarm_stations - known_stations)}, "
        f"temperature={sorted(temp_stations - known_stations)}, grid={sorted(grid_stations - known_stations)}.",
    ]
    sections.append("## Discovered field mappings\n\n" + "\n\n".join(mappings)
                    + "\n\nComplete station-to-turbine mapping:\n\n```text\n"
                    + station_map.to_string(index=False) + "\n```")

    sections.append(
        "## Timezone and schema limitations\n\n"
        "The inspected timestamp strings contain no timezone offsets. The supplied "
        "companion metadata does not declare a timestamp timezone. Source timezone "
        "is therefore unverified. If downstream output uses tz-naive UTC as the "
        "project contract requests, interpreting these naive timestamps as UTC "
        "is an explicit assumption, not an established source fact. No local-time "
        "or daylight-saving conversion should be guessed.\n\n"
        f"Alarm descriptions contain {len(descriptions):,} documented codes, "
        "so unmatched alarm codes must not be discarded.\n\n"
        "Field-dictionary temperature names absent from this monthly member "
        "(dictionary coverage is not a guarantee of actual columns):\n\n```text\n"
        + "\n".join(absent_temp_fields) + "\n```\n\n"
        "Companion decodings:\n\n"
        + "\n".join(f"- `{name}`: `{encoding}`" for name, (_, encoding) in companion_text.items())
    )
    sections.append(
        "## Grid adaptations for numeric long-format SCADA\n\n"
        + ("\n\n".join(nonnumeric) if nonnumeric else "No nonnumeric signal columns observed.")
        + "\n\nThe timestamp and station identifier are keys, not signals. "
        "The Phase 3 numeric long-format output retains all numeric signals, "
        "including missing values. It explicitly excludes `wtc_ActRegSt_endvalue` "
        "through `schema.scada_exclude_signals` because its observed `NotActive` "
        "values are categorical. The source strings remain available in the "
        "unaltered archive. Any other nonnumeric signal requires investigation "
        "rather than silent coercion into missing numbers. Numeric quality/status "
        "codes retain their source signal names; their numeric representation "
        "does not make them physical measurements. Canonical `turbine_id` remains "
        "the source station ID as a string, with friendly turbine names joined "
        "from metadata only when needed."
    )
    return "\n\n".join(sections) + "\n"


def main() -> int:
    """Print the observed schema and save it locally; return a CLI exit status.

    Missing required input exits with status 2 and 'Dataset not present.' without
    writing a report. Successful discovery writes only schema_report.md under
    the configured processed directory; it never changes configuration or data.
    """
    try:
        report = build_report()
    except FileNotFoundError as error:
        print(f"Dataset not present. {error}", file=sys.stderr)
        return 2
    except (ValueError, zipfile.BadZipFile, UnicodeError) as error:
        print(f"Schema discovery failed: {error}", file=sys.stderr)
        return 1
    destination = CONFIG.data.processed_dir / "schema_report.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report, encoding="utf-8")
    print(report, end="")
    print(f"\nReport saved: {destination.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
