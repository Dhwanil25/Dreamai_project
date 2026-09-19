"""Alarm metric contracts; all values must later come from observed data."""

import pandas as pd


def alarms_per_hour(df: pd.DataFrame, per_turbine: bool = False) -> float:
    """Measure the alarm rate over the supplied frame's observed time span.

    Input is a normalized alarm DataFrame; per_turbine requests a rate
    normalized by the observed turbine count. Return alarms per hour. Empty
    input and zero-duration spans need explicit handling in the later
    implementation. Do not mutate df or perform I/O; this stub computes nothing.
    """
    raise NotImplementedError


def pareto(df: pd.DataFrame) -> dict[str, float | int]:
    """Summarize alarm-code concentration in a normalized alarm DataFrame.

    Return top10_share_pct, codes_to_reach_80pct and distinct_codes computed
    from the supplied records. The future implementation does not mutate df,
    read external data or write reports. This stub returns no metrics.
    """
    raise NotImplementedError


def top_codes(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Return the n most frequent codes with counts and descriptive context.

    Inputs are normalized alarm records and a positive row limit. Output will
    include code, count, share percentage, description and stopping status.
    The future implementation does not mutate df or perform I/O; this scaffold
    raises without grouping or ranking records.
    """
    raise NotImplementedError


def find_bursts(
    df: pd.DataFrame,
    window: str = "10min",
    threshold: int = 10,
) -> pd.DataFrame:
    """Find time windows whose alarm counts exceed the supplied threshold.

    Inputs are normalized alarm records, a pandas-compatible duration string
    and an alarm-count threshold. Return window boundaries, counts and involved
    codes, with window alignment defined by the later implementation. Do not
    mutate df or perform I/O; this scaffold computes no flood statistics.
    """
    raise NotImplementedError
