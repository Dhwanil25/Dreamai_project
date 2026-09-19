"""Build the Phase 6 vocabulary from local, source-validated records."""

from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earshot.vocabulary import build_vocabulary


def main() -> int:
    """Build the cache or report why the real inputs cannot support one."""
    try:
        context = build_vocabulary()
    except (OSError, ValueError, TypeError) as error:
        print(f"Vocabulary build failed: {error}", file=sys.stderr)
        return 2
    print(
        f"Vocabulary written: {len(context['turbines'])} observed station IDs, "
        f"{len(context['codes'])} observed alarm codes "
        f"({len(context['documented_codes'])} documented), "
        f"{len(context['turbine_aliases'])} metadata-derived aliases."
    )
    print(f"Unmapped station IDs retained without friendly aliases: {context['unmapped_station_ids']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

