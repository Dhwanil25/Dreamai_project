"""Reproduce Phase 4 baseline evidence from real local alarm records.

No network, model calls, suppressions or raw-data mutations are performed.
Full-precision JSON and a readable report share the same calculated results.
"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from earshot.config import CONFIG, PROJECT_ROOT
from earshot.metrics import (
    alarms_per_hour, cooccurrence_evidence, find_bursts, pareto,
    stopping_split, top_codes,
)

REFERENCE_PER_HOUR = 12.0
REFERENCE_LABEL = "12/hour operator-console reference"
REFERENCE_URL = "https://www.isa.org/intech-home/2016/may-june/features/getting-the-most-from-your-safety-alarms"
FLOOD_WINDOW = pd.Timedelta(minutes=10)


def _window_record(row) -> dict:
    return {
        "start": row.start.isoformat(), "end": row.end.isoformat(),
        "count": int(row.count), "codes": [int(code) for code in row.codes],
    }


def _flood_summary(df: pd.DataFrame) -> dict:
    """Summarize fixed bins and consecutive flagged runs without recovery claims."""
    bursts = find_bursts(df)
    windows = [_window_record(row) for row in bursts.itertuples(index=False)]
    runs = []
    for window in windows:
        if runs and runs[-1]["end"] == window["start"]:
            run = runs[-1]
            run["end"] = window["end"]
            run["bin_count"] += 1
            run["record_count"] += window["count"]
            run["peak_bin_count"] = max(run["peak_bin_count"], window["count"])
        else:
            runs.append({
                "start": window["start"], "end": window["end"],
                "bin_count": 1, "record_count": window["count"],
                "peak_bin_count": window["count"],
            })
    for run in runs:
        run["duration_minutes"] = run["bin_count"] * 10
    total_bins = int((df.ts.max().floor("10min") - df.ts.min().floor("10min")) / FLOOD_WINDOW) + 1
    worst = max(windows, key=lambda row: row["count"], default=None)
    if worst:
        subset = df.loc[df.ts.ge(pd.Timestamp(worst["start"])) & df.ts.lt(pd.Timestamp(worst["end"]))]
        worst = {**worst, "station_count": int(subset.turbine_id.nunique()),
                 "top_codes": top_codes(subset, n=5).to_dict(orient="records"),
                 "stopping_split": stopping_split(subset),
                 "duplicate_event_records": int(subset.duplicated(["ts", "turbine_id", "alarm_code"]).sum())}
    return {
        "window_seconds": 600, "threshold": 10,
        "alignment": "Clock-aligned, half-open [start, end); count strictly greater than 10",
        "window_count": len(windows), "total_observed_bins": total_bins,
        "flood_bin_share_pct": len(windows) / total_bins * 100,
        "run_count": len(runs), "worst_window": worst,
        "longest_run": max(runs, key=lambda row: row["bin_count"], default=None),
        "windows": windows, "runs": runs,
    }


def _metadata_stations(metadata: pd.DataFrame) -> dict[str, str]:
    if not {"Station ID", "Turbine Name"}.issubset(metadata.columns) or metadata.empty:
        raise ValueError("Turbine metadata must contain Station ID and Turbine Name")
    ids = pd.to_numeric(metadata["Station ID"], errors="raise")
    names = metadata["Turbine Name"].astype("str").str.strip()
    if ids.isna().any() or (ids % 1 != 0).any() or ids.duplicated().any():
        raise ValueError("Metadata Station ID values must be unique non-null integers")
    if names.isna().any() or names.eq("").any():
        raise ValueError("Metadata turbine names must be populated")
    return dict(zip(ids.astype("int64").astype("str"), names))


def _interpret_cluster(members: list[dict]) -> str:
    documented = sorted({row["description"] for row in members if row["description"] != "(undocumented)"})
    if not documented:
        return "Physical meaning unverified: every member code is undocumented."
    anchors = "; ".join(documented)
    return f"Co-occurrence associated with documented event(s): {anchors}. A shared physical cause and the meanings of undocumented members remain unverified."


def calculate_baseline(alarms: pd.DataFrame, metadata: pd.DataFrame, provenance: dict) -> dict:
    """Calculate deterministic, JSON-ready evidence without I/O or input mutation."""
    # Metric validation rejects empty/invalid frames, bad domains, missing
    # labels and zero-duration exposure before any results are published.
    site_rate = alarms_per_hour(alarms)
    all_codes = top_codes(alarms, n=len(alarms))
    concentration = pareto(alarms)
    split = stopping_split(alarms)
    hours = (int(alarms.ts.max().value) - int(alarms.ts.min().value)) / 3_600_000_000_000
    stations = _metadata_stations(metadata)
    known_mask = alarms.turbine_id.isin(stations)
    known_records = int(known_mask.sum())
    per_turbine = known_records / hours / len(stations)
    observed_ids = set(alarms.turbine_id)
    unknown_ids = sorted(observed_ids - set(stations))
    by_turbine = [
        {"turbine_id": station, "turbine_name": stations[station],
         "count": int(alarms.turbine_id.eq(station).sum()),
         "alarms_per_hour": int(alarms.turbine_id.eq(station).sum()) / hours}
        for station in sorted(stations)
    ]
    largest_share = float(all_codes.share_pct.max())
    if site_rate > 10_000 or largest_share > 95:
        leader = all_codes.iloc[0]
        raise ValueError(
            f"Suspicious baseline; publication stopped. Observed hours={hours}, "
            f"records={len(alarms)}, site rate={site_rate}, "
            f"leading code={int(leader.alarm_code)}, share={largest_share}%. "
            "Investigate the observation period and source distribution."
        )
    evidence = cooccurrence_evidence(alarms)
    labels = all_codes.set_index("alarm_code").to_dict(orient="index")
    for group in evidence["groups"]:
        group["code_counts"] = {str(code): int(count) for code, count in group["code_counts"].items()}
        group["members"] = [
            {"alarm_code": code, "description": labels[code]["description"],
             "stopping": int(labels[code]["stopping"]), "count": int(labels[code]["count"])}
            for code in group["codes"]
        ]
        group["interpretation"] = _interpret_cluster(group["members"])
    evidence["group_count"] = len(evidence["groups"])
    evidence["rare_group_count"] = sum(group["low_support"] for group in evidence["groups"])
    grouped_codes = {code for group in evidence["groups"] for code in group["codes"]}
    evidence["documented_codes_without_group"] = [
        {"alarm_code": int(row.alarm_code), "description": row.description, "count": int(row.count)}
        for row in all_codes.itertuples(index=False)
        if row.description != "(undocumented)" and row.alarm_code not in grouped_codes
    ]
    evidence["definition"] = "Same-station inclusive +/-60 seconds; mutual pairwise support >=0.8; maximal cliques, no occurrence floor"
    evidence["low_support_definition"] = "At least one member code has fewer than 20 source records; flagged, never excluded"
    floods = _flood_summary(alarms)

    distinct_events = alarms.drop_duplicates(["ts", "turbine_id", "alarm_code"])
    sensitivity_clusters = cooccurrence_evidence(distinct_events)
    sensitivity = {
        "removed_records": len(alarms) - len(distinct_events),
        "record_count": len(distinct_events),
        "alarms_per_hour": alarms_per_hour(distinct_events),
        "top10_share_pct": pareto(distinct_events)["top10_share_pct"],
        "flood_window_count": len(find_bursts(distinct_events)),
        "cluster_count": len(sensitivity_clusters["groups"]),
        "cluster_memberships_changed": [g["codes"] for g in evidence["groups"]] != [g["codes"] for g in sensitivity_clusters["groups"]],
        "definition": "Comparison only: keep first row for each timestamp/station/code key; primary baseline retains all source rows",
    }
    counts = {
        "record_count": len(alarms), "turbine_count": len(stations),
        "observed_turbine_count": len(observed_ids & set(stations)),
        "station_count": len(observed_ids), "known_turbine_records": known_records,
        "unknown_station_records": len(alarms) - known_records,
        "unknown_station_ids": unknown_ids,
        "duplicate_event_records": sensitivity["removed_records"],
    }
    # Reconciliation is separate from plausibility: neither rounds or fixes data.
    if sum(row["count"] for row in split.values()) != len(alarms):
        raise ValueError("Stopping split does not reconcile to source record count")
    if int(all_codes["count"].sum()) != len(alarms) or sum(row["count"] for row in by_turbine) != known_records:
        raise ValueError("Code or turbine counts do not reconcile to the source")
    caveats = [
        "These are logged event records, not confirmed operator annunciations, false alarms or nuisance labels. Operator/console routing and staffing are unknown.",
        "12/hour is an approximate average operator-console reference; per-turbine comparisons are descriptive asset diagnostics, not compliance or safety-limit assessments.",
        "Source timestamps lack a declared timezone. The Phase 3 naive-UTC interpretation remains an unverified assumption; no daylight-saving conversion is guessed.",
        f"Primary metrics preserve {sensitivity['removed_records']} repeated timestamp/station/code records. The deduplicated comparison is separate and does not change the source.",
        f"Unmapped station IDs {unknown_ids} account for {counts['unknown_station_records']} records included site-wide and excluded from known-turbine rates.",
        "Pairwise co-occurrence does not establish a shared cause, simultaneous whole-family firing, or permission to suppress stopping alarms. Groups may overlap and their counts must not be added.",
        f"{evidence['rare_group_count']} co-occurrence groups have a member with fewer than 20 observations; even perfect support can be a coincidence in a tiny sample.",
        "Fixed threshold-exceeding bins and their consecutive runs are project metrics; their duration does not implement a standards-defined flood recovery threshold.",
    ]
    slide_numbers = [
        f"The site log contains {len(alarms):,} records over {hours:.3f} observed hours, averaging {site_rate:.2f} records/hour ({site_rate / REFERENCE_PER_HOUR:.2f} times the 12/hour operator-console reference).",
        f"The top ten codes account for {concentration['top10_share_pct']:.2f}% of records, and {concentration['codes_to_reach_80pct']} codes reach 80%.",
        f"Across {len(stations)} known turbines, the mean is {per_turbine:.2f} logged records/hour per turbine over the common site observation period.",
        f"{floods['window_count']:,} clock-aligned ten-minute bins contain more than ten records, out of {floods['total_observed_bins']:,} observed bins.",
        f"{evidence['group_count']} pairwise co-occurrence groups meet mutual 80% support within 60 seconds on the same station; {evidence['rare_group_count']} have limited supporting observations.",
    ]
    return {
        "schema_version": 1, "source": dict(provenance),
        "observed_period": {"start": alarms.ts.min().isoformat(), "end": alarms.ts.max().isoformat(),
                            "hours": hours, "timezone_assumption": "Naive UTC; source timezone unverified"},
        "counts": counts,
        "rates": {"site": site_rate, "per_turbine": per_turbine,
                  "reference_per_hour": REFERENCE_PER_HOUR, "reference_label": REFERENCE_LABEL,
                  "reference_url": REFERENCE_URL, "site_vs_reference": site_rate / REFERENCE_PER_HOUR,
                  "per_turbine_vs_reference": per_turbine / REFERENCE_PER_HOUR,
                  "denominator": "Common site observed hours; per-turbine mean uses metadata turbine count and matching records only",
                  "by_turbine": by_turbine},
        "pareto": concentration, "top_codes": all_codes.head(20).to_dict(orient="records"),
        "clusters": evidence, "floods": floods, "stopping_split": split,
        "duplicate_sensitivity": sensitivity,
        "sanity_checks": {"status": "passed", "site_rate_at_or_below_10000": True,
                          "positive_observed_span": hours > 0, "largest_code_share_pct": largest_share,
                          "counts_reconciled": True},
        "caveats": caveats, "slide_numbers": slide_numbers,
    }


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_report(stats: dict) -> str:
    """Render the prompt's ordered sections entirely from calculated evidence."""
    counts, period, rates = stats["counts"], stats["observed_period"], stats["rates"]
    concentration, clusters, floods = stats["pareto"], stats["clusters"], stats["floods"]
    sections = [
        "# EARSHOT baseline alarm report",
        "## A. Headline rates",
        f"**{rates['site']:.6f} logged records/hour site-wide**, or **{rates['site_vs_reference']:.6f}×** the {REFERENCE_LABEL}. "
        f"**{rates['per_turbine']:.6f} records/hour per known turbine**, or **{rates['per_turbine_vs_reference']:.6f}×** that reference. "
        "The turbine mean uses matching records, all metadata turbines, and the common site observation period.",
        f"The benchmark is an approximate average workload reference for an operator console, not a universal safety limit. "
        f"This log does not establish which events were annunciated to which operator. Asset-level comparisons do not assess compliance. [ISA reference]({REFERENCE_URL}).",
        "## B. Observed coverage",
        f"Period: **{period['start']} → {period['end']}**, spanning **{period['hours']:.6f} hours**. "
        f"Records: **{counts['record_count']:,}**. Known turbines observed: **{counts['observed_turbine_count']} / {counts['turbine_count']}**. "
        f"Distinct station IDs: **{counts['station_count']}**; unmapped: `{counts['unknown_station_ids']}` "
        f"({counts['unknown_station_records']} retained records). Timezone: {period['timezone_assumption']}.",
        "| Turbine | Station ID | Records | Records/hour |\n|---|---|---:|---:|\n" + "\n".join(
            f"| {_cell(row['turbine_name'])} | {row['turbine_id']} | {row['count']:,} | {row['alarms_per_hour']:.6f} |"
            for row in rates["by_turbine"]
        ),
        "## C. Pareto concentration",
        f"The top ten codes account for **{concentration['top10_share_pct']:.6f}%** of records. "
        f"**{concentration['codes_to_reach_80pct']} codes** reach 80%, out of **{concentration['distinct_codes']} distinct codes**. "
        "Concentration does not determine whether an event is a nuisance or safe to suppress.",
        "## D. Top twenty codes",
        "| Code | Description | Count | Share % | Stopping |\n|---:|---|---:|---:|---:|\n" + "\n".join(
            f"| {row['alarm_code']} | {_cell(row['description'])} | {row['count']:,} | {row['share_pct']:.6f} | {row['stopping']} |"
            for row in stats["top_codes"]
        ),
        "## E. Every discovered pairwise co-occurrence group",
        f"**{clusters['group_count']} groups**; **{clusters['rare_group_count']} flagged for limited evidence**. "
        "An event matches a candidate code once if that code occurs on the same station within inclusive ±60 seconds. "
        "Every pair must meet 80% support in both directions. Groups are maximal cliques, may overlap, and do not imply simultaneous whole-family firing. "
        "All qualifying groups are retained; rare means at least one member has fewer than 20 source observations. "
        "The JSON records every pair's support numerator and denominator. No code list or physical family is hard-coded.",
    ]
    if not clusters["groups"]:
        sections.append("No group meets the configured criteria; none is invented to satisfy an expectation.")
    for index, group in enumerate(clusters["groups"], 1):
        descriptions = "; ".join(f"{row['alarm_code']}: {_cell(row['description'])} (n={row['count']})" for row in group["members"])
        sections.append(
            f"### Group {index}: {', '.join(map(str, group['codes']))}\n\n{descriptions}.\n\n"
            f"Minimum directional pair support: **{group['min_pairwise_support'] * 100:.6f}%**. "
            f"Member-event coverage: {group['station_count']} stations; {group['distinct_dates']} distinct dates. "
            f"Limited evidence: **{'yes' if group['low_support'] else 'no'}**.\n\n"
            f"Interpretation: {group['interpretation']}"
        )
    if clusters["documented_codes_without_group"]:
        sections.append(
            "Documented codes with no qualifying group:\n\n| Code | Description | Records |\n|---:|---|---:|\n"
            + "\n".join(f"| {row['alarm_code']} | {_cell(row['description'])} | {row['count']} |"
                        for row in clusters["documented_codes_without_group"])
            + "\n\nNo family is inferred for these codes solely from a description or a one-way temporal association."
        )
    sections.extend([
        "## F. Alarm threshold-exceeding windows",
        f"**{floods['window_count']:,} / {floods['total_observed_bins']:,}** fixed ten-minute bins exceed ten records "
        f"(**{floods['flood_bin_share_pct']:.6f}%**). Bins use `[start, end)` and count each record once. "
        f"There are **{floods['run_count']} consecutive flagged-bin runs**. Partial first/last bins are included. "
        "These are project burst metrics, not complete flood episodes with a recovery threshold.",
    ])
    if floods["worst_window"]:
        worst, longest = floods["worst_window"], floods["longest_run"]
        peak_codes = ", ".join(f"{row['alarm_code']} ({row['count']} records)" for row in worst["top_codes"])
        sections.append(
            f"Worst bin: **{worst['count']} records**, {worst['start']} → {worst['end']}, across {worst['station_count']} station IDs. "
            f"Leading codes in that bin: {peak_codes}. This peak alone does not identify its cause.\n\n"
            f"Peak-bin review: {worst['stopping_split']['unknown']['count']} records have unknown stopping classification, "
            f"and {worst['duplicate_event_records']} are repeated timestamp/station/code keys. The triggering condition remains unverified.\n\n"
            f"Longest run: **{longest['bin_count']} bins / {longest['duration_minutes']} minutes**, "
            f"{longest['start']} → {longest['end']}, containing {longest['record_count']:,} records. "
            "All qualifying windows and runs are available in the JSON artifact."
        )
    else:
        sections.append("No qualifying windows or runs; worst and longest results are null in JSON.")
    sections.extend([
        "## G. Stopping classification",
        "| Classification | Count | Share % |\n|---|---:|---:|\n" + "\n".join(
            f"| {label} | {row['count']:,} | {row['share_pct']:.6f} |" for label, row in stats["stopping_split"].items()
        ),
        "Stopping is taken from the documented code lookup. Unknown means -1; it is not folded into non-stopping or interpreted from TimeOff.",
        "## H. SLIDE NUMBERS",
        "\n\n".join(f"- {statement}" for statement in stats["slide_numbers"]),
        "## Validation, sensitivity and limitations",
        "All publication sanity checks passed: positive observed span, site rate at or below 10,000/hour, no code above 95% share, and counts reconciled. "
        "Human-readable values are displayed at stated precision; JSON retains computed precision. No thresholds or records were altered to strengthen the pitch.",
    ])
    sensitivity = stats["duplicate_sensitivity"]
    sections.append(
        f"Removing {sensitivity['removed_records']} repeated timestamp/station/code rows only for comparison leaves "
        f"{sensitivity['record_count']:,} records, {sensitivity['alarms_per_hour']:.6f}/hour, "
        f"{sensitivity['top10_share_pct']:.6f}% top-ten share, {sensitivity['flood_window_count']:,} qualifying bins, "
        f"and {sensitivity['cluster_count']} groups. Cluster membership changed: {sensitivity['cluster_memberships_changed']}. "
        "The primary baseline continues to include every source record."
    )
    sections.extend([
        "\n\n".join(f"- {item}" for item in stats["caveats"]),
        "## Provenance and reproduction",
        "```json\n" + json.dumps(stats["source"], indent=2, sort_keys=True) + "\n```",
        "Run `./venv/bin/python scripts/compute_baseline.py` from the project root. "
        "No credentials, internet access, model calls, detector, replay or teaching are involved. Phase 5 has not started.",
    ])
    return "\n\n".join(sections) + "\n"


def _publish_outputs(report: str, stats: dict, report_path: Path, json_path: Path) -> None:
    """Prepare and validate both complete artifacts before replacing either one."""
    encoded = json.dumps(stats, indent=2, sort_keys=True, allow_nan=False) + "\n"
    json.loads(encoded)
    if not report.strip():
        raise ValueError("Refusing to publish an empty baseline report")
    staged = []
    try:
        for destination, content in ((report_path, report), (json_path, encoded)):
            destination.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, prefix=".baseline-", delete=False) as handle:
                temporary = Path(handle.name)
                staged.append((temporary, destination))
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if temporary.read_text(encoding="utf-8") != content:
                raise ValueError(f"Staged output validation failed: {destination.name}")
        for temporary, destination in staged:
            temporary.replace(destination)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def build_baseline() -> dict:
    """Read configured local evidence and publish only a validated baseline."""
    alarm_path = CONFIG.data.processed_dir / "alarms.parquet"
    metadata_path = CONFIG.data.raw_dir / "Hill_of_Towie_turbine_metadata.csv"
    for path in (alarm_path, metadata_path):
        if not path.is_file():
            raise FileNotFoundError(f"Baseline input not present: {path.name}. Complete Phase 3 first.")
    alarm_bytes, metadata_bytes = alarm_path.read_bytes(), metadata_path.read_bytes()
    alarms = pd.read_parquet(BytesIO(alarm_bytes), engine="pyarrow")
    metadata = pd.read_csv(BytesIO(metadata_bytes), encoding="utf-8-sig")
    if alarms.empty:
        raise ValueError("Baseline input is empty. No report was published.")
    def relative(path: Path) -> str:
        return str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else path.name
    provenance = {
        "alarm_path": relative(alarm_path), "metadata_path": relative(metadata_path),
        "alarms_sha256": sha256(alarm_bytes).hexdigest(),
        "metadata_sha256": sha256(metadata_bytes).hexdigest(),
        "dataset": "Supplied Hill of Towie Wind Farm dataset; processed locally in Phase 3",
    }
    stats = calculate_baseline(alarms, metadata, provenance)
    report = render_report(stats)
    _publish_outputs(report, stats, CONFIG.data.processed_dir / "baseline_report.md", PROJECT_ROOT / "demo" / "baseline_stats.json")
    print(report, end="")
    return stats


def main() -> int:
    """Return nonzero on missing, invalid or suspicious evidence without fallback."""
    try:
        build_baseline()
    except (OSError, ValueError, TypeError) as error:
        print(f"Baseline failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
