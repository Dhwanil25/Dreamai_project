"""Detector regressions using numeric values from unchanged real SCADA rows.

Training/scoring samples are selected from the supplied CSV; no telemetry
dataset is generated. Invalid argument sentinels exercise API rejection only.
"""

import math
import pickle
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pytest

from earshot.config import CONFIG
from earshot.detector import HSTDetector, STD_FLOOR, ZScoreDetector, create_detector


@pytest.fixture(scope="module")
def raw_scada():
    archive_path = CONFIG.data.raw_dir / "2025.zip"
    if not archive_path.is_file():
        pytest.skip("Real detector regressions require the supplied data/raw/2025.zip")
    with ZipFile(archive_path) as archive:
        members = [name for name in archive.namelist() if Path(name).name == "tblSCTurGrid_2025_01.csv"]
        assert len(members) == 1
        with archive.open(members[0]) as handle:
            return pd.read_csv(handle, nrows=1024)


@pytest.fixture(scope="module")
def numeric_samples(raw_scada):
    first = raw_scada.StationId.iloc[0]
    rows = raw_scada.loc[raw_scada.StationId.eq(first)]
    samples = rows[["wtc_VoltPhR_min", "wtc_VoltPhR_max"]].dropna()
    assert len(samples) >= 16
    # Renaming opaque feature keys does not alter any observed numeric values.
    return [dict(zip(("feature_a", "feature_b"), row)) for row in samples.itertuples(index=False, name=None)]


@pytest.fixture(scope="module")
def constant_transition(raw_scada):
    rows = raw_scada.loc[raw_scada.StationId.eq(raw_scada.StationId.iloc[0])]
    for column in rows.select_dtypes("number").drop(columns=["StationId"]):
        values = rows[column].dropna().tolist()
        for index in range(len(values) - 2):
            selected = values[index:index + 3]
            if selected[0] == selected[1] and selected[1] != selected[2]:
                return selected
    pytest.fail("The supplied real slice should contain an observed constant-to-change transition")


def test_zscore_warmup_and_population_score_use_only_past_values(numeric_samples):
    model = ZScoreDetector(window=8)
    assert model.score(numeric_samples[0]) == 0.0
    model.update(numeric_samples[0])
    assert model.score(numeric_samples[1]) == 0.0
    model.update(numeric_samples[1])
    expected = max(
        abs(numeric_samples[2][key] - np.mean([row[key] for row in numeric_samples[:2]]))
        / max(float(np.std([row[key] for row in numeric_samples[:2]], ddof=0)), STD_FLOOR)
        for key in numeric_samples[2]
    )
    before = pickle.dumps(model)
    assert model.score(numeric_samples[2]) == pytest.approx(expected)
    assert pickle.dumps(model) == before


def test_zscore_discards_old_samples_and_keeps_feature_histories_separate(numeric_samples):
    model = ZScoreDetector(window=2)
    for row in numeric_samples[:3]:
        model.update({"feature_a": row["feature_a"]})
    model.update({"feature_b": numeric_samples[0]["feature_b"]})
    model.update({"feature_b": numeric_samples[1]["feature_b"]})
    history = [row["feature_a"] for row in numeric_samples[1:3]]
    expected = abs(numeric_samples[3]["feature_a"] - np.mean(history)) / max(float(np.std(history)), STD_FLOOR)
    assert model.score({"feature_a": numeric_samples[3]["feature_a"]}) == pytest.approx(expected)
    assert len(model._history["feature_a"]) == 2
    assert list(model._history["feature_a"]) == history


def test_zscore_constant_history_handles_real_change_without_nan_or_infinity(constant_transition):
    same, repeated, changed = constant_transition
    model = ZScoreDetector(window=3)
    model.update({"feature": same})
    model.update({"feature": repeated})
    assert model.score({"feature": same}) == 0.0
    score = model.score({"feature": changed})
    assert math.isfinite(score)
    assert score == pytest.approx(abs(changed - same) / STD_FLOOR)
    assert score > 0


@pytest.mark.parametrize("detector_class", [ZScoreDetector, HSTDetector])
def test_missing_values_and_empty_vectors_do_not_learn(detector_class, numeric_samples, raw_scada):
    model = detector_class(window=8)
    model.update(numeric_samples[0])
    observed_values = raw_scada.select_dtypes("number").to_numpy()
    missing = observed_values[np.isnan(observed_values)][0]
    before = pickle.dumps(model)
    assert model.score({"feature_a": missing, "missing_only": None}) == 0.0
    assert model.score({}) == 0.0
    model.update({"feature_a": missing, "missing_only": None})
    model.update({})
    assert pickle.dumps(model) == before


@pytest.mark.parametrize("detector_class", [ZScoreDetector, HSTDetector])
@pytest.mark.parametrize("operation", ["score", "update"])
@pytest.mark.parametrize(("bad_value", "error"), [
    (True, TypeError), ("not a number", TypeError), ([], TypeError),
    (float("inf"), ValueError), (float("-inf"), ValueError),
])
def test_invalid_numeric_arguments_fail_without_partial_learning(
    detector_class, operation, bad_value, error, numeric_samples
):
    model = detector_class(window=8)
    before = pickle.dumps(model)
    with pytest.raises(error):
        getattr(model, operation)({"feature_a": numeric_samples[0]["feature_a"], "invalid_argument": bad_value})
    assert pickle.dumps(model) == before


def test_hst_is_seeded_and_score_does_not_train_or_allocate_unknown_features(numeric_samples):
    first = HSTDetector(window=8)
    second = HSTDetector(window=8)
    for row in numeric_samples[:8]:
        assert first.score(row) == second.score(row) == 0.0
        first.update(row)
        second.update(row)
    before = pickle.dumps(first)
    first_score = first.score(numeric_samples[8])
    assert 0.0 <= first_score <= 1.0
    assert first_score == second.score(numeric_samples[8])
    assert first.score({"unseen_key": numeric_samples[8]["feature_a"]}) == 0.0
    assert pickle.dumps(first) == before
    assert first.model.seed == second.model.seed == 42
    assert first.model.window_size == 8


def test_hst_clamps_unseen_range_extremes_without_updating_scaler(numeric_samples, monkeypatch):
    observed = sorted({row["feature_a"] for row in numeric_samples})
    assert len(observed) >= 10
    model = HSTDetector(window=8)
    for value in observed[1:9]:
        model.update({"feature": value})
    bounds = (model.scaler.min["feature"].get(), model.scaler.max["feature"].get())
    captured = []
    score_one = model.model.score_one

    def capture_scaled(features):
        captured.append(features.copy())
        return score_one(features)

    monkeypatch.setattr(model.model, "score_one", capture_scaled)
    assert 0.0 <= model.score({"feature": observed[0]}) <= 1.0
    assert 0.0 <= model.score({"feature": observed[-1]}) <= 1.0
    assert captured == [{"feature": 0.0}, {"feature": 1.0}]
    assert (model.scaler.min["feature"].get(), model.scaler.max["feature"].get()) == bounds


def test_hst_new_feature_rejected_before_mutation_but_known_subsets_work(numeric_samples):
    model = HSTDetector(window=8)
    model.update(numeric_samples[0])
    before = pickle.dumps(model)
    with pytest.raises(ValueError, match="feature schema cannot grow"):
        model.update({"new_key": numeric_samples[1]["feature_a"]})
    assert pickle.dumps(model) == before
    model.update({"feature_a": numeric_samples[1]["feature_a"]})
    assert model.model.counter == 2


@pytest.mark.parametrize("detector_class", [ZScoreDetector, HSTDetector])
def test_clone_preserves_configuration_but_has_no_learned_state(detector_class, numeric_samples):
    model = detector_class(window=8)
    for row in numeric_samples[:8]:
        model.update(row)
    clone = model.clone()
    assert type(clone) is detector_class
    assert clone.window == 8
    assert clone.score(numeric_samples[8]) == 0.0
    assert pickle.dumps(clone) == pickle.dumps(detector_class(window=8))


@pytest.mark.parametrize(("method", "expected"), [("zscore", ZScoreDetector), ("hst", HSTDetector)])
def test_factory_selects_only_configured_method_and_window(method, expected, monkeypatch):
    monkeypatch.setattr(CONFIG.detector, "method", method)
    monkeypatch.setattr(CONFIG.detector, "window", 8)
    model = create_detector()
    assert isinstance(model, expected)
    assert model.window == 8


def test_factory_rejects_an_unknown_configured_method(monkeypatch):
    monkeypatch.setattr(CONFIG.detector, "method", "unsupported")
    with pytest.raises(ValueError, match="Unknown detector method"):
        create_detector()


@pytest.mark.parametrize("detector_class", [ZScoreDetector, HSTDetector])
@pytest.mark.parametrize("window", [0, 1, True, 2.5])
def test_invalid_window_configuration_is_rejected(detector_class, window):
    with pytest.raises(ValueError, match="window must be an integer"):
        detector_class(window=window)
