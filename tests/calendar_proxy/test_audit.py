import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import json
import uuid
import pytest
from pathlib import Path
from audit import AuditLog


def test_audit_writes_jsonl_entry(tmp_path):
    log = AuditLog(log_path=tmp_path / "audit.log")
    log.write(
        request_id=str(uuid.uuid4()),
        tool="create_event",
        execution_mode="dry_run",
        session_id="s1",
        args={"title": "Test", "start": "2026-03-15T09:00:00+02:00"},
        status="dry_run",
        duration_ms=42,
    )
    lines = (tmp_path / "audit.log").read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "create_event"
    assert entry["tool_version"] == "v1"
    assert entry["status"] == "dry_run"
    assert entry["execution_mode"] == "dry_run"
    assert "time" in entry
    assert "request_id" in entry


def test_audit_appends_multiple_entries(tmp_path):
    log = AuditLog(log_path=tmp_path / "audit.log")
    for i in range(3):
        log.write(
            request_id=str(uuid.uuid4()),
            tool="list_events",
            execution_mode="execute",
            session_id="s1",
            args={},
            status="dry_run",
            duration_ms=i,
        )
    lines = (tmp_path / "audit.log").read_text().strip().splitlines()
    assert len(lines) == 3


def test_audit_never_logs_token(tmp_path):
    log = AuditLog(log_path=tmp_path / "audit.log")
    log.write(
        request_id="r1",
        tool="create_event",
        execution_mode="execute",
        session_id="s1",
        args={"token": "SECRET", "title": "Test"},
        status="created",
        duration_ms=10,
    )
    content = (tmp_path / "audit.log").read_text()
    assert "SECRET" not in content


def test_audit_rotates_at_startup_when_over_limit(tmp_path):
    log_path = tmp_path / "audit.log"
    # Write a file that pretends to be 1 byte over the 1-byte limit
    log_path.write_text("x" * 10)
    log = AuditLog(log_path=log_path, max_bytes=5)  # 10 > 5 → rotate
    assert (tmp_path / "audit.log.1").exists()
    assert not log_path.exists() or log_path.stat().st_size == 0


def test_audit_no_rotation_when_under_limit(tmp_path):
    log_path = tmp_path / "audit.log"
    log_path.write_text("small")
    log = AuditLog(log_path=log_path, max_bytes=1000)
    assert not (tmp_path / "audit.log.1").exists()


def test_audit_includes_event_id_on_created(tmp_path):
    log = AuditLog(log_path=tmp_path / "audit.log")
    log.write(
        request_id="r1",
        tool="create_event",
        execution_mode="execute",
        session_id="s1",
        args={"title": "Test"},
        status="created",
        event_id="google-event-123",
        duration_ms=100,
    )
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert entry["event_id"] == "google-event-123"


def test_audit_includes_reason_on_denied(tmp_path):
    log = AuditLog(log_path=tmp_path / "audit.log")
    log.write(
        request_id="r1",
        tool="create_event",
        execution_mode="execute",
        session_id="s1",
        args={"title": "Test"},
        status="denied",
        reason="calendar_id not in allowlist",
        duration_ms=5,
    )
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert entry["reason"] == "calendar_id not in allowlist"
    assert "event_id" not in entry
