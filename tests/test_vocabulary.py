"""Vocabulary checks use the unchanged supplied sources and invalid derivations."""

from copy import deepcopy
from hashlib import sha256
import json
import socket

import pandas as pd
import pytest

from earshot.config import CONFIG
from earshot import vocabulary


@pytest.fixture(scope="module")
def real_sources():
    paths = {
        "alarms": CONFIG.data.processed_dir / "alarms.parquet",
        "descriptions": CONFIG.data.raw_dir / vocabulary.DESCRIPTION_NAME,
        "metadata": CONFIG.data.raw_dir / vocabulary.METADATA_NAME,
    }
    if not all(path.is_file() for path in paths.values()):
        pytest.skip("Vocabulary regression requires real alarm Parquet and both supplied companion CSVs")
    frames = {
        "alarms": pd.read_parquet(paths["alarms"]),
        "descriptions": pd.read_csv(paths["descriptions"]),
        "metadata": pd.read_csv(paths["metadata"]),
    }
    receipts = {name: sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
    return paths, frames, receipts


@pytest.fixture(scope="module")
def context(real_sources):
    _, frames, _ = real_sources
    return vocabulary.create_vocabulary(**frames)


def test_vocabulary_contains_only_actual_ids_and_codes(real_sources, context):
    _, frames, _ = real_sources
    alarms = frames["alarms"]
    assert context["schema_version"] == 1
    assert context["turbines"] == sorted(alarms.turbine_id.unique())
    assert context["turbines"][-1] == "91"
    assert len(context["turbines"]) == 22
    assert [row["alarm_code"] for row in context["codes"]] == sorted(alarms.alarm_code.unique())
    assert len(context["codes"]) == 192
    assert all(isinstance(row["alarm_code"], int) for row in context["codes"])
    assert {row["alarm_code"] for row in context["codes"]}.issuperset(context["documented_codes"])


def test_exact_real_descriptions_and_unknowns_remain_distinct(real_sources, context):
    _, frames, _ = real_sources
    lookup = dict(zip(frames["descriptions"]["Alarm Code"], frames["descriptions"].Description.str.strip()))
    present = set(frames["alarms"].alarm_code)
    assert context["documented_codes"] == [20, 25, 1005, 3130, 8000, 8230, 10105]
    assert len(context["undocumented_codes"]) == 185
    assert set(context["documented_codes"]) | set(context["undocumented_codes"]) == present
    for row in context["codes"]:
        assert row["description"] == lookup.get(row["alarm_code"], "(undocumented)")
    assert "Only documented descriptions" in context["keyword_policy"]
    assert 102 in lookup and 102 not in present
    assert 102 not in {row["alarm_code"] for row in context["codes"]}


def test_aliases_follow_metadata_labels_not_id_or_row_order(real_sources, context):
    _, frames, _ = real_sources
    metadata = frames["metadata"]
    real_t04 = str(metadata.loc[metadata["Turbine Name"].eq("T04"), "Station ID"].iloc[0])
    assert real_t04 == "2304513"
    for alias in ["t04", "t4", "turbine 4", "turbine four", "number four"]:
        assert context["turbine_aliases"][alias] == real_t04
    assert context["turbine_aliases"]["turbine twenty one"] == "2304530"
    assert context["unmapped_station_ids"] == ["91"]
    assert "91" not in context["turbine_aliases"].values()
    assert "91" not in context["turbine_labels"]
    assert set(context["turbine_aliases"].values()) <= set(context["turbines"])
    reversed_context = vocabulary.create_vocabulary(
        frames["alarms"].iloc[::-1], frames["descriptions"].iloc[::-1], metadata.iloc[::-1]
    )
    assert reversed_context == context


def test_real_source_slice_never_gets_unobserved_turbine_aliases(real_sources):
    _, frames, _ = real_sources
    alarms = frames["alarms"].head(100)
    sliced = vocabulary.create_vocabulary(alarms, frames["descriptions"], frames["metadata"])
    assert set(sliced["turbines"]) == set(alarms.turbine_id)
    assert set(sliced["turbine_aliases"].values()) <= set(alarms.turbine_id)
    assert {row["alarm_code"] for row in sliced["codes"]} == set(alarms.alarm_code)


@pytest.mark.parametrize("which", ["lookup", "observed", "between_sources", "ambiguous_description"])
def test_description_conflicts_stop_publication(real_sources, which):
    _, real, _ = real_sources
    frames = {name: frame.copy(deep=True) for name, frame in real.items()}
    descriptions, alarms = frames["descriptions"], frames["alarms"]
    code20 = descriptions.loc[descriptions["Alarm Code"].eq(20)].index[0]
    code25 = descriptions.loc[descriptions["Alarm Code"].eq(25)].index[0]
    if which == "lookup":
        descriptions.loc[code25, "Alarm Code"] = 20
    elif which == "observed":
        alarms.loc[alarms.index[0], "description"] = descriptions.loc[code20, "Description"]
        # Ensure the altered real row uses a different description than its code.
        if alarms.loc[alarms.index[0], "alarm_code"] == 20:
            alarms.loc[alarms.index[0], "description"] = descriptions.loc[code25, "Description"]
    elif which == "between_sources":
        descriptions.loc[code25, "Description"] = descriptions.loc[code20, "Description"]
    else:
        descriptions.loc[code25, "Description"] = descriptions.loc[code20, "Description"]
        alarms.loc[alarms.alarm_code.eq(25), "description"] = descriptions.loc[code20, "Description"]
    with pytest.raises(ValueError, match="(?i)conflict|ambiguous"):
        vocabulary.create_vocabulary(**frames)


@pytest.mark.parametrize("change", ["duplicate_id", "duplicate_alias", "null_id", "blank_name"])
def test_invalid_metadata_is_not_repaired_by_id_order(real_sources, change):
    _, frames, _ = real_sources
    metadata = frames["metadata"].copy(deep=True)
    if change == "duplicate_id":
        metadata.loc[metadata.index[1], "Station ID"] = metadata.loc[metadata.index[0], "Station ID"]
    elif change == "duplicate_alias":
        metadata.loc[metadata.index[1], "Turbine Name"] = metadata.loc[metadata.index[0], "Turbine Name"]
    elif change == "null_id":
        metadata["Station ID"] = metadata["Station ID"].astype("Int64")
        metadata.loc[metadata.index[0], "Station ID"] = pd.NA
    else:
        metadata.loc[metadata.index[0], "Turbine Name"] = " "
    with pytest.raises(ValueError):
        vocabulary.create_vocabulary(frames["alarms"], frames["descriptions"], metadata)


def test_empty_real_slice_is_rejected(real_sources):
    _, frames, _ = real_sources
    with pytest.raises(ValueError, match="nonempty"):
        vocabulary.create_vocabulary(frames["alarms"].iloc[:0], frames["descriptions"], frames["metadata"])


def test_missing_parquet_has_no_fallback_and_keeps_existing_cache(tmp_path, monkeypatch):
    empty_processed = tmp_path / "processed"
    empty_processed.mkdir()
    destination = tmp_path / "vocabulary.json"
    previous = b'{"previous_completed_vocabulary":true}\n'
    destination.write_bytes(previous)
    monkeypatch.setattr(vocabulary.CONFIG.data, "processed_dir", empty_processed)
    with pytest.raises(FileNotFoundError, match="alarms source is missing"):
        vocabulary.build_vocabulary(destination)
    assert destination.read_bytes() == previous
    assert list(empty_processed.iterdir()) == []


def test_failed_stage_preserves_previous_cache_and_removes_temporary_file(context, tmp_path, monkeypatch):
    destination = tmp_path / "vocabulary.json"
    previous = b'{"previous_completed_vocabulary":true}\n'
    destination.write_bytes(previous)

    def fail_sync(*args, **kwargs):
        raise OSError("Vocabulary stage could not be synced")

    monkeypatch.setattr(vocabulary.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="could not be synced"):
        vocabulary._atomic_write(context, destination)
    assert destination.read_bytes() == previous
    assert {path.name for path in tmp_path.iterdir()} == {"vocabulary.json"}


def test_offline_build_is_deterministic_and_preserves_sources(real_sources, tmp_path, monkeypatch):
    paths, frames, receipts = real_sources
    originals = deepcopy(frames)

    def reject_network(*args, **kwargs):
        raise AssertionError("Vocabulary construction must remain offline")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    first_path, second_path = tmp_path / "first.json", tmp_path / "second.json"
    first = vocabulary.build_vocabulary(first_path)
    second = vocabulary.build_vocabulary(second_path)
    assert first == second
    assert first_path.read_bytes() == second_path.read_bytes()
    assert json.loads(first_path.read_text()) == first
    json.dumps(first, allow_nan=False)
    for name, path in paths.items():
        assert first["source"][f"{name}_sha256"] == receipts[name]
        assert sha256(path.read_bytes()).hexdigest() == receipts[name]
        pd.testing.assert_frame_equal(frames[name], originals[name])

