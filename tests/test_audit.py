"""Failure injection for operation metadata; no monitored records are generated."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from earshot import audit as audit_module
from earshot.audit import OperationAudit


def receipt_line(operation="teach", **details):
    return (json.dumps({"receipt_id": "test-receipt", "checked_at": "2026-09-19T12:00:00+00:00",
                        "operation": operation, **details}) + "\n").encode()


def test_append_roundtrip_and_defensive_copies(tmp_path):
    path = tmp_path / "nested/operations.jsonl"
    audit = OperationAudit(path)
    details = {"scope": {"alarm_code": 20}, "receipt_id": "untrusted", "operation": "untrusted"}
    result = audit.append("teach", details)
    original = json.loads(path.read_text())
    assert result == original
    assert result["receipt_id"] != "untrusted"
    assert result["operation"] == "teach"
    details["scope"]["alarm_code"] = 25
    result["scope"]["alarm_code"] = 99
    snapshot = audit.snapshot()
    snapshot[0]["scope"]["alarm_code"] = 100
    assert audit.snapshot() == [original]
    restored = OperationAudit(path)
    assert restored.snapshot() == [original]
    assert restored.integrity_status()["status"] == "complete"
    assert restored.integrity_status()["valid_receipts"] == 1


def test_missing_history_is_complete_empty_history(tmp_path):
    audit = OperationAudit(tmp_path / "operations.jsonl")
    assert audit.snapshot() == []
    assert audit.integrity_status()["history_complete"] is True
    assert not audit.path.exists()


def test_recent_cap_does_not_claim_whole_history_is_only_thirty(tmp_path):
    path = tmp_path / "operations.jsonl"
    path.write_bytes(b"".join(receipt_line(index=index) for index in range(35)))
    audit = OperationAudit(path)
    assert [item["index"] for item in audit.snapshot()] == list(range(5, 35))
    assert audit.integrity_status()["valid_receipts"] == 35
    assert audit.integrity_status()["recent_receipts"] == 30


@pytest.mark.parametrize("damaged", [
    b'{"truncated":\n', b'\xff\xfe\n', b'[]\n', b'null\n', b'{}\n',
    receipt_line(value=float("nan")), receipt_line(value=float("inf")),
    receipt_line(checked_at="not-a-date"), receipt_line(checked_at="2026-09-19T12:00:00"),
    receipt_line().replace(b'"operation": "teach"', b'"operation": "teach", "operation": "undo"'),
])
def test_corrupt_history_preserved_and_valid_neighbors_recovered(tmp_path, damaged):
    path = tmp_path / "operations.jsonl"
    original = receipt_line("teach") + damaged + receipt_line("undo")
    path.write_bytes(original)
    audit = OperationAudit(path)
    assert [item["operation"] for item in audit.snapshot()] == ["teach", "undo"]
    status = audit.integrity_status()
    assert status["status"] == "degraded"
    assert status["history_complete"] is False
    assert status["valid_receipts"] == 2
    assert status["invalid_lines"] == 1
    assert status["issues"][0]["line"] == 2
    assert set(status["issues"][0]) == {"line", "reason"}
    assert path.read_bytes() == original
    audit.append("teach", {"new": True})
    assert path.read_bytes().startswith(original)
    assert OperationAudit(path).integrity_status()["valid_receipts"] == 3


@pytest.mark.parametrize("tail,valid_before", [(b'{"unfinished":', 0), (receipt_line().rstrip(b"\n"), 1)])
def test_append_separates_existing_unterminated_line_without_rewriting_it(tmp_path, tail, valid_before):
    path = tmp_path / "operations.jsonl"
    path.write_bytes(tail)
    audit = OperationAudit(path)
    added = audit.append("undo", {})
    assert path.read_bytes().startswith(tail + b"\n")
    restored = OperationAudit(path)
    assert restored.snapshot()[-1] == added
    assert restored.integrity_status()["valid_receipts"] == valid_before + 1
    assert restored.integrity_status()["invalid_lines"] == 1 - valid_before


def test_issue_preview_is_bounded_and_copied(tmp_path):
    path = tmp_path / "operations.jsonl"
    path.write_bytes(b"invalid\n" * 25)
    audit = OperationAudit(path)
    status = audit.integrity_status()
    assert status["invalid_lines"] == 25
    assert len(status["issues"]) == 20
    assert status["issues_truncated"] is True
    status["issues"][0]["line"] = -1
    assert audit.integrity_status()["issues"][0]["line"] == 1


class FaultyFile:
    def __init__(self, handle, failure):
        self.handle = handle
        self.failure = failure
        self.flush_calls = 0

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.handle.close()

    def write(self, payload):
        if self.failure in {"short", "write", "rollback"}:
            count = self.handle.write(payload[:12])
            if self.failure == "short":
                return count
            raise OSError("Injected interrupted append")
        return self.handle.write(payload)

    def flush(self):
        self.flush_calls += 1
        if self.failure == "flush" and self.flush_calls == 1:
            raise OSError("Injected flush failure")
        return self.handle.flush()

    def truncate(self, size):
        if self.failure == "rollback":
            raise OSError("Injected rollback failure")
        return self.handle.truncate(size)


def inject_file_failure(monkeypatch, path, failure):
    original_open = Path.open

    def faulty_open(self, mode="r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self == path and mode == "ab+":
            assert kwargs["buffering"] == 0
            return FaultyFile(handle, failure)
        return handle

    monkeypatch.setattr(Path, "open", faulty_open)


@pytest.mark.parametrize("failure", ["short", "write", "flush", "fsync"])
def test_failed_append_restores_exact_bytes_and_does_not_publish_receipt(tmp_path, monkeypatch, failure):
    path = tmp_path / "operations.jsonl"
    original = receipt_line() + b'{"unfinished":'
    path.write_bytes(original)
    audit = OperationAudit(path)
    before = audit.snapshot()
    if failure == "fsync":
        original_fsync = audit_module.os.fsync
        calls = 0

        def fail_once(fd):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("Injected fsync failure")
            return original_fsync(fd)

        monkeypatch.setattr(audit_module.os, "fsync", fail_once)
    else:
        inject_file_failure(monkeypatch, path, failure)
    with pytest.raises(OSError):
        audit.append("undo", {"must_not_be_committed": True})
    assert path.read_bytes() == original
    assert audit.snapshot() == before
    status = audit.integrity_status()
    assert status["status"] == "degraded"
    assert status["append_failures"] == 1
    assert status["rollback_failures"] == 0
    assert status["valid_receipts"] == 1


def test_rollback_failure_is_visible_and_not_mistaken_for_valid_history(tmp_path, monkeypatch):
    path = tmp_path / "operations.jsonl"
    original = receipt_line()
    path.write_bytes(original)
    audit = OperationAudit(path)
    inject_file_failure(monkeypatch, path, "rollback")
    with pytest.raises(OSError, match="interrupted"):
        audit.append("undo", {})
    assert path.read_bytes().startswith(original)
    assert len(path.read_bytes()) > len(original)
    status = audit.integrity_status()
    assert status["status"] == "degraded"
    assert status["rollback_failures"] == 1
    assert status["history_complete"] is False
    assert audit.snapshot() == [json.loads(original)]
    assert OperationAudit(path).integrity_status()["invalid_lines"] == 1


def test_unreadable_history_does_not_prevent_object_creation(tmp_path, monkeypatch):
    path = tmp_path / "operations.jsonl"
    path.write_bytes(receipt_line())
    original_open = Path.open

    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError("Private failure context must not be exposed")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    audit = OperationAudit(path)
    assert audit.snapshot() == []
    status = audit.integrity_status()
    assert status["status"] == "unavailable"
    assert status["read_error"] == "PermissionError"
    assert "Private" not in json.dumps(status)
    with pytest.raises(PermissionError):
        audit.append("teach", {})
    assert audit.integrity_status()["append_failures"] == 1


def test_external_changes_invalidate_current_history_claim(tmp_path):
    path = tmp_path / "operations.jsonl"
    audit = OperationAudit(path)
    audit.append("teach", {})
    with path.open("ab") as handle:
        handle.write(b"invalid external edit\n")
    status = audit.integrity_status()
    assert status["external_change_detected"] is True
    assert status["history_complete"] is False
    assert status["status"] == "degraded"
    audit.append("undo", {})
    assert audit.integrity_status()["external_change_detected"] is True


@pytest.mark.parametrize("operation,details,error", [
    ("", {}, ValueError), (None, {}, ValueError), ("teach", [], TypeError),
    ("teach", {"value": float("nan")}, ValueError),
])
def test_invalid_receipt_is_rejected_before_any_write(tmp_path, operation, details, error):
    path = tmp_path / "operations.jsonl"
    audit = OperationAudit(path)
    with pytest.raises(error):
        audit.append(operation, details)
    assert not path.exists()
    assert audit.snapshot() == []


def test_concurrent_appends_preserve_every_receipt(tmp_path):
    path = tmp_path / "operations.jsonl"
    audit = OperationAudit(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda index: audit.append("teach", {"index": index}), range(40)))
    persisted = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(persisted) == 40
    assert {item["receipt_id"] for item in persisted} == {item["receipt_id"] for item in results}
    assert {item["index"] for item in persisted} == set(range(40))
    assert audit.integrity_status()["status"] == "complete"
    assert OperationAudit(path).integrity_status()["valid_receipts"] == 40
