"""Pure descriptive metrics; all source rows count and no input is modified.

Co-occurrence describes temporal association, not causation or nuisance status.
No function in this module performs I/O or changes model state.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations
import math
from numbers import Integral, Real
from typing import Any

import numpy as np
import pandas as pd


def _check_frame(df: pd.DataFrame) -> None:
    """Require a DataFrame with unambiguous column names."""
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")
    if not df.columns.is_unique:
        raise ValueError("DataFrame columns must be unique")


def _require(df: pd.DataFrame, columns: Sequence[str]) -> None:
    """Validate needed canonical columns without coercing or changing data."""
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    for column in columns:
        values = df[column]
        if values.isna().any():
            raise ValueError(f"{column} must not contain null values")
        if column == "ts":
            if not pd.api.types.is_datetime64_any_dtype(values.dtype):
                raise ValueError("ts must contain parsed datetime values")
            if values.dt.tz is not None:
                raise ValueError("ts must be timezone-naive under the normalized UTC assumption")
            try:
                values.dt.as_unit("ns")
            except (OverflowError, ValueError) as error:
                raise ValueError("ts must be representable at nanosecond precision") from error
        elif column in {"alarm_code", "stopping"}:
            if not pd.api.types.is_integer_dtype(values.dtype):
                raise ValueError(f"{column} must contain integers")
            if column == "stopping" and not values.isin([-1, 0, 1]).all():
                raise ValueError("stopping values must be -1, 0 or 1")
        elif column in {"turbine_id", "description"}:
            if not all(isinstance(value, str) and value.strip() for value in values):
                raise ValueError(f"{column} must contain nonblank strings")


def _integer_parameter(value: int, name: str, minimum: int) -> int:
    """Validate an integral count argument without accepting boolean flags."""
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def alarms_per_hour(df: pd.DataFrame, per_turbine: bool = False) -> float:
    """Return row count divided by the observed min/max ts span in hours.

    Require nonempty records, parsed timezone-naive timestamps and positive
    duration. per_turbine additionally divides by distinct string turbine_id
    values in this frame, including unmapped stations if supplied. Select
    metadata-only records explicitly when that is the desired population.
    Empty/zero-span input raises ValueError. No I/O or input mutation occurs.
    """
    _check_frame(df)
    if not isinstance(per_turbine, bool):
        raise ValueError("per_turbine must be a boolean")
    if df.empty:
        raise ValueError("alarms_per_hour requires nonempty records")
    _require(df, ["ts", "turbine_id"] if per_turbine else ["ts"])
    times = df.ts.dt.as_unit("ns")
    span_ns = int(times.max().value) - int(times.min().value)
    if span_ns <= 0:
        raise ValueError("alarms_per_hour requires a positive observed time span")
    rate = len(df) / (span_ns / 3_600_000_000_000)
    return float(rate / df.turbine_id.nunique() if per_turbine else rate)


def pareto(df: pd.DataFrame) -> dict[str, float | int]:
    """Return top10_share_pct, codes_to_reach_80pct and distinct_codes.

    Count every record by integral alarm_code, retaining duplicate event keys.
    Shares use all supplied rows; reaching 80% means at least four fifths of
    that count. Empty input returns zeros. No I/O or input mutation occurs.
    """
    _check_frame(df)
    if df.empty:
        return {"top10_share_pct": 0.0, "codes_to_reach_80pct": 0, "distinct_codes": 0}
    _require(df, ["alarm_code"])
    counts = df.alarm_code.value_counts().sort_values(ascending=False)
    target = (4 * len(df) + 4) // 5  # Exact ceiling of 80% of the source rows.
    needed = int(np.searchsorted(counts.cumsum().to_numpy(), target, side="left")) + 1
    return {
        "top10_share_pct": float(counts.head(10).sum() / len(df) * 100),
        "codes_to_reach_80pct": needed,
        "distinct_codes": int(len(counts)),
    }


def top_codes(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Return alarm_code/count/share_pct/description/stopping for top n codes.

    Sort by descending row count, then ascending integer code. Require positive
    n, nonblank descriptions and consistent description/stopping labels per
    code; conflicts raise ValueError even outside the requested top n. Empty
    input returns those columns without rows. No I/O or input mutation occurs.
    """
    _check_frame(df)
    limit = _integer_parameter(n, "n", 1)
    columns = ["alarm_code", "count", "share_pct", "description", "stopping"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    _require(df, ["alarm_code", "description", "stopping"])
    grouped = df.groupby("alarm_code", sort=True, observed=True)
    labels = grouped[["description", "stopping"]].nunique()
    conflicts = labels.index[labels.gt(1).any(axis=1)].tolist()
    if conflicts:
        raise ValueError(f"Conflicting descriptions or stopping flags for alarm codes: {conflicts}")
    result = grouped.agg(
        count=("alarm_code", "size"), description=("description", "first"),
        stopping=("stopping", "first"),
    ).reset_index()
    result["share_pct"] = result["count"] / len(df) * 100
    return result.sort_values(
        ["count", "alarm_code"], ascending=[False, True], kind="stable"
    ).head(limit)[columns].reset_index(drop=True)


def find_bursts(
    df: pd.DataFrame, window: str = "10min", threshold: int = 10,
) -> pd.DataFrame:
    """Return fixed clock-aligned half-open bins whose count exceeds threshold.

    Require timezone-naive ts, integral alarm_code and a positive fixed duration
    string. Default bins are [start, start+10min), with boundary events assigned
    to the later bin. Count all rows site-wide. Output start/end/count/codes is
    ordered by start; codes holds sorted distinct integers. Empty input or no
    qualifying bins returns no rows. No I/O or input mutation occurs.
    """
    _check_frame(df)
    cutoff = _integer_parameter(threshold, "threshold", 0)
    if not isinstance(window, str):
        raise ValueError("window must be a positive fixed duration string")
    try:
        duration = pd.Timedelta(window)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("window must be a positive fixed duration string") from error
    if pd.isna(duration) or duration <= pd.Timedelta(0):
        raise ValueError("window must be a positive fixed duration string")
    columns = ["start", "end", "count", "codes"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    _require(df, ["ts", "alarm_code"])
    bins = df.ts.dt.floor(duration)
    result = df.groupby(bins, sort=True, observed=True).agg(
        count=("alarm_code", "size"),
        codes=("alarm_code", lambda values: sorted({int(value) for value in values})),
    )
    result.index.name = "start"
    result = result.loc[result["count"].gt(cutoff)].reset_index()
    result["end"] = result.start + duration
    return result[columns]


def _cooccurrence_parameters(window_s: float, min_support: float) -> tuple[int, float]:
    """Return the nanosecond window and a validated directional threshold."""
    if (isinstance(window_s, bool) or not isinstance(window_s, Real)
            or not math.isfinite(float(window_s)) or window_s < 0):
        raise ValueError("window_s must be finite and nonnegative")
    if (isinstance(min_support, bool) or not isinstance(min_support, Real)
            or not math.isfinite(float(min_support)) or not 0 < min_support <= 1):
        raise ValueError("min_support must be greater than 0 and at most 1")
    try:
        width = pd.Timedelta(seconds=float(window_s)).value
    except (ValueError, OverflowError) as error:
        raise ValueError("window_s exceeds the supported timestamp range") from error
    return int(width), float(min_support)


def _maximal_cliques(graph: dict[int, set[int]]) -> list[tuple[int, ...]]:
    """Enumerate nonsingleton maximal cliques with deterministic pivoting."""
    found: list[tuple[int, ...]] = []

    def visit(current: set[int], possible: set[int], excluded: set[int]) -> None:
        if not possible and not excluded:
            if len(current) > 1:
                found.append(tuple(sorted(current)))
            return
        pivot = min(possible | excluded, key=lambda c: (-len(possible & graph[c]), c))
        for code in sorted(possible - graph[pivot]):
            visit(current | {code}, possible & graph[code], excluded & graph[code])
            possible.remove(code)
            excluded.add(code)

    if graph:
        visit(set(), set(graph), set())
    return sorted(found)


def cooccurrence_evidence(
    df: pd.DataFrame, window_s: float = 60, min_support: float = 0.8,
) -> dict[str, Any]:
    """Return auditable same-station pairwise co-occurrence edges and groups.

    An A event matches when any different B code occurs on the same turbine_id
    within inclusive +/-window_s seconds. Multiple B neighbors count once for
    that A event. Both matched_A/total_A and matched_B/total_B must meet
    min_support. Keep all source rows and apply no occurrence floor or self-pair.

    Return window_s, min_support, edges and groups. Each edge contains code_a,
    code_b, matched_a, total_a, support_a, matched_b, total_b and support_b.
    Groups are lexicographically ordered maximal cliques with sorted codes,
    code_counts, station_count, distinct_dates, min_pairwise_support, low_support
    and their edges. Coverage counts include all member-code events. low_support
    flags any code with fewer than 20 rows without discarding the group. Pairwise
    support does not establish whole-group simultaneity or a shared cause.

    Require normalized ts/turbine_id/alarm_code, finite nonnegative window_s
    (zero means exact timestamps), and 0 < min_support <= 1. Empty input returns
    empty lists. No I/O, model updates or input mutation occurs.
    """
    _check_frame(df)
    width, support_threshold = _cooccurrence_parameters(window_s, min_support)
    result: dict[str, Any] = {
        "window_s": float(window_s), "min_support": support_threshold,
        "edges": [], "groups": [],
    }
    if df.empty:
        return result
    _require(df, ["ts", "turbine_id", "alarm_code"])
    counts = df.alarm_code.value_counts().sort_index()
    codes = [int(code) for code in counts.index]
    positions = {code: position for position, code in enumerate(codes)}
    totals = counts.to_numpy(dtype=np.int64)
    matched = np.zeros((len(codes), len(codes)), dtype=np.int64)
    limits = np.iinfo(np.int64)
    for _, station in df.groupby("turbine_id", sort=True, observed=True):
        streams = {
            int(code): np.sort(events.ts.to_numpy(dtype="datetime64[ns]").astype(np.int64))
            for code, events in station.groupby("alarm_code", sort=True, observed=True)
        }
        for times in streams.values():
            if int(times[0]) < limits.min + width or int(times[-1]) > limits.max - width:
                raise ValueError("window_s extends beyond the supported timestamp range")
        for code_a, code_b in combinations(sorted(streams), 2):
            a, b = streams[code_a], streams[code_b]
            matched[positions[code_a], positions[code_b]] += np.count_nonzero(
                np.searchsorted(b, a + width, side="right")
                > np.searchsorted(b, a - width, side="left")
            )
            matched[positions[code_b], positions[code_a]] += np.count_nonzero(
                np.searchsorted(a, b + width, side="right")
                > np.searchsorted(a, b - width, side="left")
            )
    graph: dict[int, set[int]] = {}
    edges: list[dict[str, int | float]] = []
    for i, j in combinations(range(len(codes)), 2):
        support_a = float(matched[i, j] / totals[i])
        support_b = float(matched[j, i] / totals[j])
        if min(support_a, support_b) < support_threshold:
            continue
        code_a, code_b = codes[i], codes[j]
        graph.setdefault(code_a, set()).add(code_b)
        graph.setdefault(code_b, set()).add(code_a)
        edges.append({
            "code_a": code_a, "code_b": code_b,
            "matched_a": int(matched[i, j]), "total_a": int(totals[i]),
            "support_a": support_a,
            "matched_b": int(matched[j, i]), "total_b": int(totals[j]),
            "support_b": support_b,
        })
    result["edges"] = edges
    for clique in _maximal_cliques(graph):
        members = set(clique)
        group_edges = [e for e in edges if e["code_a"] in members and e["code_b"] in members]
        group_events = df.loc[df.alarm_code.isin(clique)]
        code_counts = {code: int(totals[positions[code]]) for code in clique}
        result["groups"].append({
            "codes": list(clique), "code_counts": code_counts,
            "station_count": int(group_events.turbine_id.nunique()),
            "distinct_dates": int(group_events.ts.dt.normalize().nunique()),
            "min_pairwise_support": min(min(e["support_a"], e["support_b"]) for e in group_edges),
            "low_support": min(code_counts.values()) < 20,
            "edges": group_edges,
        })
    return result


def cooccurrence_clusters(
    df: pd.DataFrame, window_s: float = 60, min_support: float = 0.8,
) -> list[set[int]]:
    """Return maximal code cliques meeting mutual same-station pairwise support.

    Arguments and validation match cooccurrence_evidence. Inclusive neighbors
    count once per anchor event; duplicate source rows and rare groups remain
    included. Return nonsingleton sets in sorted-code tuple order, or [] for
    empty input. No I/O or mutation; cooccurrence_evidence provides audit counts
    and explains the distinction from simultaneous whole-group occurrence.
    """
    evidence = cooccurrence_evidence(df, window_s, min_support)
    return [set(group["codes"]) for group in evidence["groups"]]


def stopping_split(df: pd.DataFrame) -> dict[str, dict[str, int | float]]:
    """Return stopping/non_stopping/unknown counts and share_pct over all rows.

    Integral stopping flags 1, 0 and -1 define the three categories. Missing or
    other values are invalid. Empty input returns zero counts/shares. Stopping
    metadata is not a nuisance label. No I/O or input mutation occurs.
    """
    _check_frame(df)
    if not df.empty:
        _require(df, ["stopping"])
    result: dict[str, dict[str, int | float]] = {}
    for name, value in (("stopping", 1), ("non_stopping", 0), ("unknown", -1)):
        count = int(df.stopping.eq(value).sum()) if not df.empty else 0
        result[name] = {
            "count": count,
            "share_pct": float(count / len(df) * 100) if len(df) else 0.0,
        }
    return result
