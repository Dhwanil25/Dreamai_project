"""Dataset ingestion contracts; all operations are Phase 2 stubs."""

from collections.abc import Sequence

import pandas as pd


def load_alarm_log(year: int, months: Sequence[int]) -> pd.DataFrame:
    """Load the requested year's alarm records for the selected months.

    Inputs are a calendar year and month numbers. Return a DataFrame with
    canonical ts, turbine_id, alarm_code, description and stopping columns,
    using raw column mappings discovered from the actual dataset. The future
    implementation reads configured local archives and description files
    without extracting or modifying them. This stub performs no I/O.
    """
    raise NotImplementedError


def load_scada(
    year: int,
    months: Sequence[int],
    tables: Sequence[str],
) -> pd.DataFrame:
    """Load selected SCADA tables for the supplied year and months.

    Table names must come from archive discovery. Return a long-format
    DataFrame with canonical ts, turbine_id, signal and value columns. The
    future implementation reads configured local archive members without
    extracting them or modifying source files. This stub performs no I/O.
    """
    raise NotImplementedError


def load_alarm_descriptions() -> pd.DataFrame:
    """Load the configured local alarm-description table.

    No arguments are required; the configured dataset location supplies the
    source. Return a DataFrame preserving its discovered code/description
    mapping. The future implementation reads local files only and does not
    discard undocumented alarm records. This stub performs no I/O.
    """
    raise NotImplementedError
