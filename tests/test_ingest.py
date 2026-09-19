"""Ingestion regressions using unchanged records from the supplied archive.

No dataset is generated here. SCADA normalization tests substitute small real
CSV slices at the archive-reader boundary to avoid repeatedly melting an
entire month's millions of values. Tests that require source data skip with an
explicit reason when the supplied archive is unavailable.
"""

from collections import Counter, deque
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pytest

from earshot import ingest
from earshot.config import CONFIG


SOURCE_YEAR = 2025


@pytest.fixture(scope="module")
def real_archive():
    path = CONFIG.data.raw_dir / f"{SOURCE_YEAR}.zip"
    if not path.is_file():
        pytest.skip("Real-source ingestion regression requires data/raw/2025.zip")
    return path


@pytest.fixture(scope="module")
def raw_descriptions():
    path = CONFIG.data.raw_dir / "Hill_of_Towie_alarms_description.csv"
    if not path.is_file():
        pytest.skip("Real-source regression requires the supplied alarm descriptions CSV")
    return pd.read_csv(path)


def _read_source(archive_path, table, month, *, nrows=None, tail=None):
    """Read original CSV rows directly from the ZIP, without source edits."""
    basename = f"{table}_{SOURCE_YEAR}_{month:02d}.csv"
    with ZipFile(archive_path) as archive:
        members = [name for name in archive.namelist() if Path(name).name == basename]
        assert len(members) == 1, f"Expected one real archive member: {basename}"
        with archive.open(members[0]) as handle:
            if tail is None:
                return pd.read_csv(handle, nrows=nrows)
            header = handle.readline()
            rows = deque(handle, maxlen=tail)
            return pd.read_csv(BytesIO(header + b"".join(rows)))


@pytest.fixture(scope="module")
def january_alarms(real_archive):
    return _read_source(real_archive, "tblAlarmLog", 1)


@pytest.fixture(scope="module")
def scada_slices(real_archive):
    return {
        table: _read_source(real_archive, table, 1, nrows=64)
        for table in ("tblSCTurTemp", "tblSCTurGrid")
    }


def _patch_real_slices(monkeypatch, frames):
    """Replace disk reads with copies of unchanged source slices; record calls."""
    calls = []

    def read_selected(year, month, table):
        assert year == SOURCE_YEAR
        calls.append((year, month, table))
        return frames[(month, table)].copy(deep=True)

    monkeypatch.setattr(ingest, "read_month_table", read_selected)
    return calls


def _alarm_keys(frame, *, normalized):
    if normalized:
        return Counter(zip(frame.ts, frame.turbine_id.astype(str), frame.alarm_code))
    return Counter(zip(
        pd.to_datetime(frame.TimeOn), frame.StationNr.astype(str), frame.Alarmcode
    ))


def _sorted_long(frame):
    frame = frame.copy()
    frame["turbine_id"] = frame.turbine_id.astype(str)
    frame["signal"] = frame.signal.astype(str)
    return frame.sort_values(["ts", "turbine_id", "signal"]).reset_index(drop=True)


def test_description_mapping_preserves_real_codes_labels_and_stopping(raw_descriptions):
    result = ingest.load_alarm_descriptions()

    assert set(result.columns) == {"alarm_code", "description", "stopping"}
    assert len(result) == len(raw_descriptions)
    actual = result.set_index("alarm_code")
    assert set(actual.index) == set(raw_descriptions["Alarm Code"])
    for row in raw_descriptions.to_dict("records"):
        code = row["Alarm Code"]
        assert actual.loc[code, "description"].strip() == row["Description"].strip()
        assert actual.loc[code, "stopping"] == row["Stopping"]


def test_alarm_normalization_preserves_rows_duplicates_ids_and_event_time(
    january_alarms, raw_descriptions
):
    result = ingest.load_alarm_log(SOURCE_YEAR, [1])

    assert list(result.columns) == [
        "ts", "turbine_id", "alarm_code", "description", "stopping"
    ]
    assert len(result) == len(january_alarms)
    assert str(result.ts.dtype) == "datetime64[ns]"
    assert result.ts.dt.tz is None
    assert pd.api.types.is_integer_dtype(result.alarm_code)
    assert pd.api.types.is_integer_dtype(result.stopping)
    assert all(isinstance(value, str) for value in result.turbine_id.unique())
    assert _alarm_keys(result, normalized=True) == _alarm_keys(
        january_alarms, normalized=False
    )
    # This fixture contains actual duplicate rows; they must not be deduplicated.
    assert january_alarms.duplicated().any()
    assert result.duplicated(["ts", "turbine_id", "alarm_code"]).any()


def test_undocumented_alarms_are_kept_and_timeoff_is_not_a_stopping_flag(
    january_alarms, raw_descriptions
):
    result = ingest.load_alarm_log(SOURCE_YEAR, [1])
    known_codes = set(raw_descriptions["Alarm Code"])
    source_unknown = january_alarms.loc[~january_alarms.Alarmcode.isin(known_codes)]
    unknown = result.loc[~result.alarm_code.isin(known_codes)]

    assert len(unknown) == len(source_unknown) > 0
    assert unknown.description.eq("(undocumented)").all()
    assert unknown.stopping.eq(-1).all()
    assert source_unknown.TimeOff.notna().any(), "Fixture must exercise real end times"
    assert set(raw_descriptions.Stopping) == {0, 1}
    for row in raw_descriptions.to_dict("records"):
        matching = result.loc[result.alarm_code.eq(row["Alarm Code"])]
        if not matching.empty:
            assert matching.stopping.eq(row["Stopping"]).all()
    assert result.stopping.eq(0).any() and result.stopping.eq(1).any()


def test_unmapped_station_identifiers_are_preserved(real_archive, raw_descriptions):
    source = _read_source(real_archive, "tblAlarmLog", 3)
    source_unmapped = source.loc[source.StationNr.eq(91)]
    assert not source_unmapped.empty, "The supplied March archive contains station 91"

    result = ingest.load_alarm_log(SOURCE_YEAR, [3])
    actual = result.loc[result.turbine_id.eq("91")]

    assert len(result) == len(source)
    assert _alarm_keys(actual, normalized=True) == _alarm_keys(
        source_unmapped, normalized=False
    )


@pytest.mark.parametrize("table", ["tblSCTurTemp", "tblSCTurGrid"])
def test_scada_preserves_every_numeric_value_and_null(
    table, scada_slices, monkeypatch
):
    source = scada_slices[table]
    _patch_real_slices(monkeypatch, {(1, table): source})
    signal_columns = list(source.drop(columns=["StationId"]).select_dtypes("number"))

    result = ingest.load_scada(SOURCE_YEAR, [1], [table])

    assert list(result.columns) == ["ts", "turbine_id", "signal", "value"]
    assert len(result) == len(source) * len(signal_columns)
    assert set(result.signal.astype(str)) == set(signal_columns)
    assert "wtc_ActRegSt_endvalue" not in set(result.signal.astype(str))
    assert str(result.ts.dtype) == "datetime64[ns]"
    assert result.ts.dt.tz is None
    assert set(result.turbine_id.astype(str)) == set(source.StationId.astype(str))
    assert result.value.isna().sum() == source[signal_columns].isna().sum().sum()
    if table == "tblSCTurGrid":
        assert source[signal_columns].isna().any().any(), "Use real missing readings"
    for signal in signal_columns:
        actual = result.loc[result.signal.eq(signal)].copy()
        actual["turbine_id"] = actual.turbine_id.astype(str)
        actual = actual.sort_values(["ts", "turbine_id"]).reset_index(drop=True)
        expected = source.sort_values(["TimeStamp", "StationId"]).reset_index(drop=True)
        assert actual.ts.tolist() == pd.to_datetime(expected.TimeStamp).tolist()
        assert actual.turbine_id.tolist() == expected.StationId.astype(str).tolist()
        pd.testing.assert_series_equal(
            actual.value, expected[signal], check_names=False,
            check_dtype=False, check_exact=True,
        )


def test_scada_iterator_yields_one_table_at_a_time(scada_slices, monkeypatch):
    tables = ["tblSCTurTemp", "tblSCTurGrid"]
    calls = _patch_real_slices(
        monkeypatch, {(1, table): scada_slices[table] for table in tables}
    )
    iterator = iter(ingest.iter_scada(SOURCE_YEAR, [1], tables))
    assert calls == []

    first = next(iterator)
    assert calls == [(SOURCE_YEAR, 1, tables[0])]
    second = next(iterator)
    assert calls == [(SOURCE_YEAR, 1, table) for table in tables]
    with pytest.raises(StopIteration):
        next(iterator)

    combined = ingest.load_scada(SOURCE_YEAR, [1], tables)
    expected = pd.concat([first, second], ignore_index=True)
    pd.testing.assert_frame_equal(
        _sorted_long(combined), _sorted_long(expected),
        check_dtype=False, check_categorical=False, check_exact=True,
    )


def test_real_next_month_boundary_rows_are_not_replayed_twice(real_archive, monkeypatch):
    table = "tblSCTurGrid"
    january = _read_source(real_archive, table, 1, tail=32)
    february = _read_source(real_archive, table, 2, nrows=32)
    boundary = pd.Timestamp("2025-02-01")
    repeated_source = january.loc[pd.to_datetime(january.TimeStamp).eq(boundary)]
    assert len(repeated_source) > 0
    _patch_real_slices(monkeypatch, {(1, table): january, (2, table): february})

    result = ingest.load_scada(SOURCE_YEAR, [1, 2], [table])
    signals = list(january.drop(columns=["StationId"]).select_dtypes("number"))
    retained_january = int(pd.to_datetime(january.TimeStamp).lt(boundary).sum())
    assert len(result) == (retained_january + len(february)) * len(signals)
    assert not result.duplicated(["ts", "turbine_id", "signal"]).any()
    expected_boundary_rows = int(pd.to_datetime(february.TimeStamp).eq(boundary).sum())
    assert int(result.ts.eq(boundary).sum()) == expected_boundary_rows * len(signals)


def test_archive_reader_does_not_extract_members(real_archive, january_alarms, monkeypatch):
    def reject_extraction(*args, **kwargs):
        raise AssertionError("Raw archive members must be read without extraction")

    monkeypatch.setattr(ZipFile, "extract", reject_extraction)
    monkeypatch.setattr(ZipFile, "extractall", reject_extraction)

    result = ingest.read_month_table(SOURCE_YEAR, 1, "tblAlarmLog")

    pd.testing.assert_frame_equal(result, january_alarms, check_dtype=False)


def test_missing_archive_has_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG.data, "raw_dir", tmp_path)

    with pytest.raises(FileNotFoundError, match="Dataset not present"):
        ingest.read_month_table(SOURCE_YEAR, 1, "tblAlarmLog")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("month", [0, 13])
def test_invalid_months_are_rejected_before_reading(month, monkeypatch):
    def reject_read(*args, **kwargs):
        raise AssertionError("Invalid month must be rejected before reading data")

    monkeypatch.setattr(ingest, "read_month_table", reject_read)

    with pytest.raises(ValueError, match="[Mm]onth"):
        ingest.load_alarm_log(SOURCE_YEAR, [month])


def test_failed_full_read_preserves_previous_outputs_and_cleans_staging(
    scada_slices, raw_descriptions, tmp_path, monkeypatch
):
    from scripts import build_dataset as builder

    # Normalize unchanged real rows through the production iterator, then
    # restore the reader before the build so its alarm reads remain complete.
    table = "tblSCTurGrid"
    with monkeypatch.context() as source_patch:
        _patch_real_slices(source_patch, {(1, table): scada_slices[table]})
        frame = next(ingest.iter_scada(SOURCE_YEAR, [1], [table]))
    assert len(frame.attrs["source"]["turbine_ids"]) == 21
    assert frame.attrs["source"]["duplicate_time_station_rows"] == 0

    def selected_real_scada(year, months, tables):
        assert year == SOURCE_YEAR
        assert table in tables
        yield frame.copy(deep=True)

    monkeypatch.setattr(builder, "iter_scada", selected_real_scada)
    monkeypatch.setattr(CONFIG.data, "processed_dir", tmp_path)
    # These are prior-file state sentinels, never input records or mock data.
    previous = {
        "alarms.parquet": b"previous completed alarm artifact\n",
        "scada.parquet": b"previous completed SCADA artifact\n",
        "schema_report.md": b"# Previous validated schema report\n",
        "ingestion_report.json": b"previous completed ingestion manifest\n",
    }
    for name, contents in previous.items():
        (tmp_path / name).write_bytes(contents)

    real_read_parquet = builder.pd.read_parquet
    validated_paths = []

    def fail_second_full_read(path, *args, **kwargs):
        path = Path(path)
        validated_paths.append(path)
        if path.name == "scada.parquet":
            raise OSError("SCADA full-read validation failed")
        return real_read_parquet(path, *args, **kwargs)

    monkeypatch.setattr(builder.pd, "read_parquet", fail_second_full_read)

    with pytest.raises(OSError, match="SCADA full-read validation failed"):
        builder.build_dataset()

    assert [path.name for path in validated_paths] == ["alarms.parquet", "scada.parquet"]
    assert all(path.parent != tmp_path for path in validated_paths)
    assert {path.name for path in tmp_path.iterdir()} == set(previous)
    for name, contents in previous.items():
        assert (tmp_path / name).read_bytes() == contents
