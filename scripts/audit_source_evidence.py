"""Verify real local sources and publish a compact evidence receipt atomically."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from earshot.config import CONFIG
from earshot.evidence import build_source_evidence


def publish_evidence() -> dict:
    result = build_source_evidence()
    encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + '\n'
    destination = CONFIG.data.processed_dir / 'source_evidence.json'
    temporary = None
    try:
        with NamedTemporaryFile('w', encoding='utf-8', dir=destination.parent,
                                prefix='.source-evidence-', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return result


def main() -> int:
    try:
        result = publish_evidence()
    except Exception as error:
        print(f'Source evidence audit failed: {type(error).__name__}: {error}', file=sys.stderr)
        return 2
    print(json.dumps({'status': result['status'], 'source_kind': result['source_kind'],
                      'raw_alarm_rows_verified': result['alarm_verification']['record_count'],
                      'alarm_source_examples': len(result['alarm_verification']['sample_rows']),
                      'scada_readings_in_parquet': result['scada_verification']['parquet_reading_count'],
                      'scada_cells_matched_to_original': result['scada_verification']['sample_snapshot']['verified_reading_count'],
                      'baseline': result['baseline_recomputed'],
                      'receipt': 'data/processed/source_evidence.json'}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
