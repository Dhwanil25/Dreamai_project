"""Verify the working configuration boundary; business logic is still stubbed."""

from pathlib import Path

import pytest
import yaml

from earshot.config import CONFIG, PROJECT_ROOT, find_project_root, load_config


def test_paths_resolve_from_project_not_launch_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    loaded = load_config()
    assert find_project_root() == PROJECT_ROOT
    assert loaded.data.raw_dir == PROJECT_ROOT / "data" / "raw"
    assert loaded.data.processed_dir == PROJECT_ROOT / "data" / "processed"
    assert isinstance(loaded.data.raw_dir, Path)


def test_yaml_values_and_discovered_schema_remain_unmodified():
    assert CONFIG.data.year == 2025
    assert CONFIG.data.months == [1, 2, 3]
    assert CONFIG.schema.alarm_code_col == "Alarmcode"
    assert CONFIG.schema.alarm_time_col == "TimeOn"
    assert CONFIG.schema.alarm_turbine_col == "StationNr"
    assert CONFIG.schema.alarm_end_time_col == "TimeOff"
    assert CONFIG.schema.scada_time_col == "TimeStamp"
    assert CONFIG.schema.scada_turbine_col == "StationId"
    assert CONFIG.schema.scada_exclude_signals == ["wtc_ActRegSt_endvalue"]
    assert CONFIG.schema.timestamp_timezone_assumption == "UTC"


def test_root_search_walks_up_from_module_file(tmp_path):
    module = tmp_path / "nested" / "earshot" / "config.py"
    module.parent.mkdir(parents=True)
    module.touch()
    (tmp_path / "config.yaml").write_text("data: {}\n")
    assert find_project_root(module) == tmp_path


def test_project_env_loads_without_overriding_existing_values(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(
        "data:\n  raw_dir: data/raw\n  processed_dir: data/processed\n"
    )
    (tmp_path / ".env").write_text(
        "EARSHOT_CONFIG_TEST_LOAD=from-project\nEARSHOT_CONFIG_TEST_KEEP=from-file\n"
    )
    # Mark both names for restoration so this test leaves no environment state.
    monkeypatch.delenv("EARSHOT_CONFIG_TEST_LOAD", raising=False)
    monkeypatch.setenv("EARSHOT_CONFIG_TEST_KEEP", "from-process")
    monkeypatch.chdir(tmp_path.parent)
    load_config(tmp_path)
    import os

    assert os.environ["EARSHOT_CONFIG_TEST_LOAD"] == "from-project"
    assert os.environ["EARSHOT_CONFIG_TEST_KEEP"] == "from-process"


@pytest.mark.parametrize("raw_path", ["../outside", "", None, 123])
def test_invalid_data_paths_fail_before_use(tmp_path, raw_path):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "data": {"raw_dir": raw_path, "processed_dir": "data/processed"},
    }))
    with pytest.raises(ValueError, match="data.raw_dir"):
        load_config(tmp_path)


def test_absolute_data_path_is_rejected(tmp_path):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "data": {"raw_dir": str(tmp_path / "raw"), "processed_dir": "data/processed"},
    }))
    with pytest.raises(ValueError, match="relative"):
        load_config(tmp_path)


@pytest.mark.parametrize("contents", ["", "[]", "data: null", "data: {}"])
def test_incomplete_config_has_clear_error(tmp_path, contents):
    (tmp_path / "config.yaml").write_text(contents)
    with pytest.raises(ValueError):
        load_config(tmp_path)
