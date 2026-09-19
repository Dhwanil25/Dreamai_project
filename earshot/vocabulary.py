"""Build a controlled vocabulary from the supplied local event data.

Only observed station IDs and alarm codes are eligible. Friendly turbine aliases
come from metadata display names, never from station-ID ordering.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import os

import pandas as pd

from earshot.config import CONFIG, PROJECT_ROOT


UNDOCUMENTED = "(undocumented)"
DESCRIPTION_NAME = "Hill_of_Towie_alarms_description.csv"
METADATA_NAME = "Hill_of_Towie_turbine_metadata.csv"


def _integers(series: pd.Series, label: str) -> pd.Series:
    """Require populated integral identities without accepting booleans."""
    if series.isna().any() or series.map(lambda value: isinstance(value, bool)).any():
        raise ValueError(f"{label} must contain non-null integer identifiers")
    try:
        values = pd.to_numeric(series, errors="raise")
        if not values.map(lambda value: math.isfinite(value) and value % 1 == 0).all():
            raise ValueError
        return values.astype("int64")
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{label} must contain finite integer identifiers") from error


def _text(series: pd.Series, label: str) -> pd.Series:
    if series.isna().any() or not series.map(lambda value: isinstance(value, str)).all():
        raise ValueError(f"{label} must contain non-null strings")
    values = series.str.strip()
    if values.eq("").any():
        raise ValueError(f"{label} must contain nonblank strings")
    return values


def _number_words(number: int) -> str | None:
    """Spell a display-name number; larger names retain their numeric aliases."""
    small = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
             "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
             "sixteen", "seventeen", "eighteen", "nineteen")
    tens = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
    if 0 <= number < 20:
        return small[number]
    if 20 <= number < 100:
        return tens[number // 10] + (" " + small[number % 10] if number % 10 else "")
    return None


def _aliases(label: str) -> set[str]:
    aliases = {label.casefold()}
    match = re.fullmatch(r"T(\d+)", label, flags=re.IGNORECASE)
    if match:
        number = int(match.group(1))
        aliases.update({
            f"t{number}", f"t {number}", f"turbine{number}",
            f"turbine {number}", f"turbine {match.group(1)}",
            f"number {number}", f"number {match.group(1)}",
        })
        words = _number_words(number)
        if words is not None:
            aliases.update({f"turbine {words}", f"number {words}"})
    return aliases


def create_vocabulary(
    alarms: pd.DataFrame,
    descriptions: pd.DataFrame,
    metadata: pd.DataFrame,
    provenance: dict | None = None,
) -> dict:
    """Return deterministic vocabulary after checking source consistency.

    Input frames are unchanged. Missing or conflicting descriptions, metadata
    identities, or aliases raise ValueError; no IDs or source rows are invented.
    Undocumented observed codes remain available for explicit-code references
    but their placeholder is never an eligible keyword description.
    """
    required = (
        (alarms, {"turbine_id", "alarm_code", "description"}, "alarms"),
        (descriptions, {"Alarm Code", "Description"}, "alarm descriptions"),
        (metadata, {"Station ID", "Turbine Name"}, "turbine metadata"),
    )
    for frame, columns, label in required:
        if frame.empty or not columns.issubset(frame.columns):
            raise ValueError(f"{label} must be nonempty and contain {sorted(columns)}")
    station_ids = _text(alarms["turbine_id"], "Alarm station IDs")
    codes = _integers(alarms["alarm_code"], "Alarm codes")
    event_descriptions = _text(alarms["description"], "Observed descriptions")
    lookup_codes = _integers(descriptions["Alarm Code"], "Description alarm codes")
    lookup_descriptions = _text(descriptions["Description"], "Alarm descriptions")
    metadata_ids = _integers(metadata["Station ID"], "Metadata station IDs").astype(str)
    metadata_names = _text(metadata["Turbine Name"], "Metadata turbine names")
    if metadata_ids.duplicated().any():
        raise ValueError("Metadata station IDs must be unique")

    lookup = {}
    for code, description in zip(lookup_codes, lookup_descriptions):
        if code in lookup and lookup[code] != description:
            raise ValueError(f"Conflicting descriptions for alarm code {code}")
        lookup[int(code)] = description
    observed = pd.DataFrame({
        "alarm_code": codes.to_numpy(),
        "description": event_descriptions.to_numpy(),
    })
    conflicts = observed.groupby("alarm_code").description.nunique()
    if conflicts.gt(1).any():
        raise ValueError(f"Conflicting observed descriptions for alarm codes {conflicts[conflicts.gt(1)].index.tolist()}")
    observed_lookup = observed.drop_duplicates("alarm_code").set_index("alarm_code").description.to_dict()
    code_rows = []
    description_owners = defaultdict(list)
    for code in sorted(observed_lookup):
        description = lookup.get(code, UNDOCUMENTED)
        if observed_lookup[code] != description:
            raise ValueError(f"Description conflict between processed data and lookup for alarm code {code}")
        code_rows.append({"alarm_code": int(code), "description": description})
        if description != UNDOCUMENTED:
            description_owners[" ".join(description.casefold().split())].append(int(code))
    ambiguous = {description: owners for description, owners in description_owners.items() if len(owners) > 1}
    if ambiguous:
        raise ValueError(f"Ambiguous descriptions identify multiple alarm codes: {ambiguous}")

    turbines = sorted(set(station_ids))
    present = set(turbines)
    aliases = {}
    labels = {}
    for station, label in zip(metadata_ids, metadata_names):
        if station not in present:
            continue
        labels[station] = label
        for alias in sorted(_aliases(label)):
            if alias in aliases and aliases[alias] != station:
                raise ValueError(f"Turbine alias {alias!r} identifies multiple observed stations")
            # A friendly name cannot override the spelling of another real ID.
            if alias in present and alias != station:
                raise ValueError(f"Turbine alias {alias!r} conflicts with a raw station identifier")
            aliases[alias] = station

    documented = [row["alarm_code"] for row in code_rows if row["description"] != UNDOCUMENTED]
    return {
        "schema_version": 1,
        "turbines": turbines,
        "codes": code_rows,
        "turbine_aliases": dict(sorted(aliases.items())),
        "turbine_labels": dict(sorted(labels.items())),
        "documented_codes": documented,
        "undocumented_codes": [row["alarm_code"] for row in code_rows if row["description"] == UNDOCUMENTED],
        "unmapped_station_ids": sorted(present - set(metadata_ids)),
        "keyword_policy": "Only documented descriptions are keyword-matchable; (undocumented) is a placeholder.",
        "source": dict(provenance or {}),
    }


def _atomic_write(context: dict, output: Path) -> None:
    """Validate and stage the complete JSON before replacing a previous cache."""
    encoded = json.dumps(context, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if json.loads(encoded) != context:
        raise ValueError("Vocabulary must round-trip through JSON without information loss")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                prefix=".vocabulary-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.read_text(encoding="utf-8") != encoded:
            raise ValueError("Staged vocabulary validation failed")
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_vocabulary(output_path: Path | None = None) -> dict:
    """Read the configured real sources, then publish an atomic local cache.

    No raw archives, fallback records, network, credentials, or source writes
    are involved. Missing Parquet or companion CSVs stop publication.
    """
    paths = {
        "alarms": CONFIG.data.processed_dir / "alarms.parquet",
        "descriptions": CONFIG.data.raw_dir / DESCRIPTION_NAME,
        "metadata": CONFIG.data.raw_dir / METADATA_NAME,
    }
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"Vocabulary {name} source is missing: {path.name}")
    payloads = {name: path.read_bytes() for name, path in paths.items()}
    provenance = {
        f"{name}_path": str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else path.name
        for name, path in paths.items()
    }
    provenance.update({f"{name}_sha256": sha256(payload).hexdigest() for name, payload in payloads.items()})
    alarms = pd.read_parquet(BytesIO(payloads["alarms"]), columns=["turbine_id", "alarm_code", "description"])
    descriptions = pd.read_csv(BytesIO(payloads["descriptions"]), encoding="utf-8-sig")
    metadata = pd.read_csv(BytesIO(payloads["metadata"]), encoding="utf-8-sig")
    context = create_vocabulary(alarms, descriptions, metadata, provenance)
    _atomic_write(context, Path(output_path) if output_path is not None else PROJECT_ROOT / "demo" / "vocabulary.json")
    return context

