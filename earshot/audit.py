"""Local operation receipts. These record observations, not accuracy claims."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock
from uuid import uuid4


class OperationAudit:
    """Keep usable receipts available even if another historical line is damaged.

    Integrity describes this process's read and writes. External file changes are
    detected, but are not silently reloaded or repaired. Invalid bytes stay intact.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = Lock()
        self.recent = deque(maxlen=30)
        self._valid_records = 0
        self._invalid_lines = 0
        self._issues = []
        self._read_error = None
        self._append_failures = 0
        self._rollback_failures = 0
        self._external_change = False
        self._checked_at = datetime.now(timezone.utc).isoformat()
        self._last_stamp = None
        try:
            self._last_stamp = self._file_stamp()
            if self._last_stamp is not None:
                with self.path.open("rb") as handle:
                    for number, line in enumerate(handle, start=1):
                        if not line.strip():
                            continue
                        try:
                            receipt = self._decode_receipt(line)
                        except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError) as error:
                            self._invalid_lines += 1
                            if len(self._issues) < 20:
                                self._issues.append({"line": number, "reason": type(error).__name__})
                        else:
                            self.recent.append(receipt)
                            self._valid_records += 1
                self._external_change = self._file_stamp() != self._last_stamp
        except OSError as error:
            self._read_error = type(error).__name__

    def _file_stamp(self):
        try:
            stamp = self.path.stat()
        except FileNotFoundError:
            return None
        return (stamp.st_dev, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns)

    @staticmethod
    def _decode_receipt(encoded: bytes) -> dict:
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate receipt key")
                result[key] = value
            return result

        receipt = json.loads(encoded.decode("utf-8"), object_pairs_hook=unique_keys)
        if not isinstance(receipt, dict):
            raise ValueError("Receipt must be an object")
        for field in ("receipt_id", "checked_at", "operation"):
            if not isinstance(receipt.get(field), str) or not receipt[field].strip():
                raise ValueError("Receipt identity fields must be nonempty strings")
        timestamp = datetime.fromisoformat(receipt["checked_at"].replace("Z", "+00:00"))
        if timestamp.utcoffset() is None:
            raise ValueError("Receipt timestamp must include its timezone")
        # Also rejects NaN, Infinity and overflowing JSON floats anywhere inside.
        json.dumps(receipt, allow_nan=False)
        return receipt

    def append(self, operation: str, details: dict) -> dict:
        if not isinstance(operation, str) or not operation.strip():
            raise ValueError("Operation must be a nonempty string")
        if not isinstance(details, dict):
            raise TypeError("Receipt details must be a dictionary")
        receipt = {**details, "receipt_id": str(uuid4()),
                   "checked_at": datetime.now(timezone.utc).isoformat(), "operation": operation}
        encoded = (json.dumps(receipt, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        receipt = self._decode_receipt(encoded)
        with self.lock:
            try:
                self._external_change |= self._file_stamp() != self._last_stamp
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Unbuffered writes prevent a close-time flush from recreating
                # bytes removed by rollback after an interrupted append.
                with self.path.open("ab+", buffering=0) as handle:
                    handle.seek(0, os.SEEK_END)
                    original_size = handle.tell()
                    separator = b""
                    if original_size:
                        handle.seek(-1, os.SEEK_END)
                        if handle.read(1) != b"\n":
                            separator = b"\n"
                    payload = separator + encoded
                    try:
                        if handle.write(payload) != len(payload):
                            raise OSError("Incomplete receipt write")
                        handle.flush()
                        os.fsync(handle.fileno())
                    except BaseException:
                        try:
                            handle.truncate(original_size)
                            handle.flush()
                            os.fsync(handle.fileno())
                        except BaseException:
                            self._rollback_failures += 1
                        raise
            except BaseException:
                self._append_failures += 1
                try:
                    self._last_stamp = self._file_stamp()
                except OSError:
                    pass
                raise
            self.recent.append(receipt)
            self._valid_records += 1
            self._last_stamp = self._file_stamp()
        return json.loads(json.dumps(receipt))

    def snapshot(self) -> list[dict]:
        with self.lock:
            return json.loads(json.dumps(list(self.recent)))

    def integrity_status(self) -> dict:
        """Report lost/invalid evidence without exposing corrupt line contents."""
        with self.lock:
            stat_error = None
            try:
                self._external_change |= self._file_stamp() != self._last_stamp
            except OSError as error:
                stat_error = type(error).__name__
            unavailable = bool(self._read_error or stat_error)
            degraded = bool(self._invalid_lines or self._append_failures or self._external_change)
            return {
                "status": "unavailable" if unavailable else "degraded" if degraded else "complete",
                "history_complete": not (unavailable or degraded),
                "checked_at": self._checked_at,
                "valid_receipts": self._valid_records,
                "recent_receipts": len(self.recent),
                "invalid_lines": self._invalid_lines,
                "issues": [dict(issue) for issue in self._issues],
                "issues_truncated": self._invalid_lines > len(self._issues),
                "read_error": self._read_error or stat_error,
                "append_failures": self._append_failures,
                "rollback_failures": self._rollback_failures,
                "external_change_detected": self._external_change,
            }
