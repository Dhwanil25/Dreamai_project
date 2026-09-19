"""Metric regressions on unchanged rows from the processed real dataset.

These tests select, reorder or remove columns from real records; no alarm
records, timestamps, codes, descriptions or labels are synthesized.
"""

from itertools import combinations

import pandas as pd
import pytest

from earshot.config import CONFIG
from earshot import metrics


@pytest.fixture(scope="module")
def alarms():
    path = CONFIG.data.processed_dir / "alarms.parquet"
    if not path.is_file():
        pytest.skip("Real-source metric regressions require processed alarms.parquet")
    frame = pd.read_parquet(path)
    assert not frame.empty, "The processed real alarm dataset must be nonempty"
    return frame


@pytest.fixture(scope="module")
def evidence(alarms):
    return metrics.cooccurrence_evidence(alarms)


def test_rates_use_observed_time_and_explicit_station_population(alarms):
    hours = (alarms.ts.max() - alarms.ts.min()).total_seconds() / 3600
    assert metrics.alarms_per_hour(alarms) == pytest.approx(len(alarms) / hours)
    assert metrics.alarms_per_hour(alarms, True) == pytest.approx(
        len(alarms) / hours / alarms.turbine_id.nunique()
    )
    assert "91" in set(alarms.turbine_id), "The real unknown station must remain included"


def test_rate_rejects_empty_and_zero_observed_span(alarms):
    with pytest.raises(ValueError, match="nonempty"):
        metrics.alarms_per_hour(alarms.iloc[:0])
    same_instant = alarms.loc[alarms.ts.eq(alarms.ts.iloc[0])]
    with pytest.raises(ValueError, match="positive observed time span"):
        metrics.alarms_per_hour(same_instant)


def test_pareto_meets_the_threshold_with_the_fewest_codes(alarms):
    result = metrics.pareto(alarms)
    counts = alarms.alarm_code.value_counts().sort_values(ascending=False)
    needed = result["codes_to_reach_80pct"]
    assert result["top10_share_pct"] == pytest.approx(counts.head(10).sum() / len(alarms) * 100)
    assert result["distinct_codes"] == alarms.alarm_code.nunique()
    assert counts.iloc[:needed].sum() >= len(alarms) * 0.8
    assert counts.iloc[:needed - 1].sum() < len(alarms) * 0.8


def test_top_codes_preserves_labels_counts_and_deterministic_ties(alarms):
    result = metrics.top_codes(alarms, n=alarms.alarm_code.nunique())
    assert list(result.columns) == ["alarm_code", "count", "share_pct", "description", "stopping"]
    assert result["count"].sum() == len(alarms)
    assert result.share_pct.sum() == pytest.approx(100)
    assert list(zip(-result["count"], result.alarm_code)) == sorted(
        zip(-result["count"], result.alarm_code)
    )
    source_labels = alarms[["alarm_code", "description", "stopping"]].drop_duplicates()
    pd.testing.assert_frame_equal(
        result[["alarm_code", "description", "stopping"]].sort_values("alarm_code").reset_index(drop=True),
        source_labels.sort_values("alarm_code").reset_index(drop=True),
        check_dtype=False,
    )


def test_bursts_assign_real_clock_boundary_to_later_half_open_bin(alarms):
    start = pd.Timestamp("2025-01-02 23:30:00")
    end = start + pd.Timedelta(minutes=10)
    selected = alarms.loc[alarms.ts.ge(start) & alarms.ts.lt(end)]
    assert len(selected) == 2 and selected.ts.eq(start).any()

    result = metrics.find_bursts(selected, threshold=1)

    assert result.to_dict("records") == [{
        "start": start, "end": end, "count": 2, "codes": [127],
    }]
    assert metrics.find_bursts(selected, threshold=2).empty


def test_burst_threshold_is_strictly_greater_than_ten(alarms):
    counts = alarms.groupby(alarms.ts.dt.floor("10min")).size()
    starts = counts.index[counts.eq(10)]
    assert len(starts), "The real quarter contains ten-row bins"
    start = starts[0]
    selected = alarms.loc[alarms.ts.ge(start) & alarms.ts.lt(start + pd.Timedelta(minutes=10))]
    assert len(selected) == 10
    assert metrics.find_bursts(selected).empty
    result = metrics.find_bursts(selected, threshold=9)
    assert result["count"].tolist() == [10]
    assert result.codes.iloc[0] == sorted(selected.alarm_code.unique().tolist())


def test_stopping_split_accounts_for_all_three_real_labels(alarms):
    result = metrics.stopping_split(alarms)
    assert set(result) == {"stopping", "non_stopping", "unknown"}
    for name, flag in (("stopping", 1), ("non_stopping", 0), ("unknown", -1)):
        count = int(alarms.stopping.eq(flag).sum())
        assert result[name] == {"count": count, "share_pct": count / len(alarms) * 100}
    assert sum(item["count"] for item in result.values()) == len(alarms)
    assert sum(item["share_pct"] for item in result.values()) == pytest.approx(100)


def test_empty_non_rate_contracts_use_real_empty_selection(alarms):
    empty = alarms.iloc[:0]
    assert metrics.pareto(empty) == {
        "top10_share_pct": 0.0, "codes_to_reach_80pct": 0, "distinct_codes": 0,
    }
    assert metrics.top_codes(empty).empty
    assert list(metrics.top_codes(empty).columns) == [
        "alarm_code", "count", "share_pct", "description", "stopping",
    ]
    assert metrics.find_bursts(empty).empty
    assert list(metrics.find_bursts(empty).columns) == ["start", "end", "count", "codes"]
    assert metrics.cooccurrence_clusters(empty) == []
    assert metrics.cooccurrence_evidence(empty) == {
        "window_s": 60.0, "min_support": 0.8, "edges": [], "groups": [],
    }
    assert all(item == {"count": 0, "share_pct": 0.0} for item in metrics.stopping_split(empty).values())


def test_real_sixty_second_neighbors_are_inclusive(alarms):
    selected = alarms.loc[
        alarms.turbine_id.eq("2304515")
        & alarms.ts.isin(pd.to_datetime(["2025-01-03 21:49:02", "2025-01-03 21:50:02"]))
        & alarms.alarm_code.isin([20, 25])
    ]
    assert len(selected) == 2
    assert metrics.cooccurrence_clusters(selected.iloc[::-1], window_s=60) == [{20, 25}]
    assert metrics.cooccurrence_clusters(selected, window_s=59) == []


def test_simultaneous_events_on_different_stations_do_not_match(alarms):
    selected = alarms.loc[
        alarms.ts.eq(pd.Timestamp("2025-01-03 23:34:13"))
        & alarms.turbine_id.isin(["2304514", "2304529"])
        & alarms.alarm_code.isin([20, 25])
    ]
    assert len(selected) == 2 and selected.turbine_id.nunique() == 2
    assert selected.alarm_code.nunique() == 2
    assert metrics.cooccurrence_clusters(selected) == []


def test_mutual_support_rejects_real_one_way_icing_association(alarms):
    selected = alarms.loc[alarms.alarm_code.isin([25, 8230])]
    assert set(selected.alarm_code) == {25, 8230}
    # All five actual icing detections occur near cut-out, but cut-out also
    # occurs thousands of times without icing; one direction cannot form a group.
    assert metrics.cooccurrence_clusters(selected) == []


def test_maximal_cliques_do_not_merge_a_real_transitive_chain(alarms):
    selected = alarms.loc[alarms.alarm_code.isin([1001, 1020, 50000])]
    assert metrics.cooccurrence_clusters(selected) == [{1001, 1020}, {1020, 50000}]


def test_full_quarter_evidence_keeps_rare_groups_and_duplicate_weight(alarms, evidence):
    assert len(alarms) == 68_891 and alarms.alarm_code.nunique() == 192
    assert len(evidence["edges"]) == 208
    assert len(evidence["groups"]) == 38
    groups = {tuple(group["codes"]): group for group in evidence["groups"]}
    rare = groups[(19, 62008)]
    assert rare["low_support"] is True
    assert rare["code_counts"] == {19: 1, 62008: 1}
    assert rare["station_count"] == rare["distinct_dates"] == 1
    assert groups[(29, 10105)]["low_support"] is False
    assert groups[(29, 10105)]["code_counts"] == {29: 76, 10105: 76}
    assert groups[(29, 10105)]["min_pairwise_support"] == 1.0
    duplicate_edge = next(edge for edge in evidence["edges"] if (edge["code_a"], edge["code_b"]) == (61, 67))
    assert duplicate_edge["total_b"] == 516
    assert duplicate_edge["matched_b"] == 513
    assert duplicate_edge["support_b"] == pytest.approx(513 / 516)


def test_each_group_is_a_maximal_pairwise_clique_with_deterministic_order(alarms, evidence):
    pairs = {(edge["code_a"], edge["code_b"]) for edge in evidence["edges"]}
    all_codes = set(alarms.alarm_code)
    tuples = [tuple(group["codes"]) for group in evidence["groups"]]
    assert tuples == sorted(tuples)
    for group in evidence["groups"]:
        codes = group["codes"]
        assert codes == sorted(codes) and len(codes) >= 2
        assert set(combinations(codes, 2)).issubset(pairs)
        for outside in all_codes - set(codes):
            assert any(tuple(sorted((outside, inside))) not in pairs for inside in codes)
        assert group["low_support"] == (min(group["code_counts"].values()) < 20)
        for edge in group["edges"]:
            assert edge["support_a"] >= 0.8 and edge["support_b"] >= 0.8
            assert edge["matched_a"] <= edge["total_a"]
            assert edge["matched_b"] <= edge["total_b"]
    assert metrics.cooccurrence_evidence(alarms.sample(frac=1, random_state=42)) == evidence


@pytest.mark.parametrize(("function", "column"), [
    (metrics.alarms_per_hour, "ts"), (metrics.pareto, "alarm_code"),
    (metrics.top_codes, "description"), (metrics.find_bursts, "alarm_code"),
    (metrics.cooccurrence_evidence, "turbine_id"), (metrics.stopping_split, "stopping"),
])
def test_required_columns_are_checked(function, column, alarms):
    with pytest.raises(ValueError, match="Missing required columns"):
        function(alarms.head(8).drop(columns=[column]))


@pytest.mark.parametrize(("function", "kwargs"), [
    (metrics.top_codes, {"n": 0}), (metrics.find_bursts, {"threshold": -1}),
    (metrics.find_bursts, {"window": "0min"}),
    (metrics.cooccurrence_evidence, {"window_s": -1}),
    (metrics.cooccurrence_evidence, {"min_support": 0}),
    (metrics.cooccurrence_evidence, {"min_support": 1.1}),
])
def test_invalid_metric_parameters_fail(function, kwargs, alarms):
    with pytest.raises(ValueError):
        function(alarms.head(8), **kwargs)


def test_metrics_do_not_mutate_records_or_read_other_data(alarms, monkeypatch):
    selected = alarms.head(1000).copy(deep=True)
    before = selected.copy(deep=True)

    def reject_read(*args, **kwargs):
        raise AssertionError("Metric functions must use only their supplied frame")

    monkeypatch.setattr(pd, "read_parquet", reject_read)
    monkeypatch.setattr(pd, "read_csv", reject_read)
    for function in (
        metrics.alarms_per_hour, metrics.pareto, metrics.top_codes,
        metrics.find_bursts, metrics.cooccurrence_evidence,
        metrics.cooccurrence_clusters, metrics.stopping_split,
    ):
        function(selected)
    pd.testing.assert_frame_equal(selected, before)
