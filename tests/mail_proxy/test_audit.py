import json
import os
import pytest
from pathlib import Path


def test_write_redacts_body_and_subject(tmp_path):
    """body, subject, snippet must not appear in audit log entries."""
    import audit
    log = audit.AuditLog(log_path=tmp_path / "audit.log")
    log.write(
        request_id="req-1",
        operation="get",
        message_id="msg-123",
        from_addr="alice@example.com",
        status="ok",
        extra={"subject": "SECRET_SUBJECT", "body": "SECRET_BODY"},
    )
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert "SECRET_SUBJECT" not in json.dumps(entry)
    assert "SECRET_BODY" not in json.dumps(entry)
    assert entry["message_id"] == "msg-123"
    assert entry["from_addr"] == "alice@example.com"


def test_all_redacted_fields_suppressed(tmp_path):
    """All five _REDACTED_FIELDS members must not appear in log output."""
    import audit
    log = audit.AuditLog(log_path=tmp_path / "audit.log")
    log.write(
        request_id="req-2",
        operation="get",
        message_id="msg-456",
        from_addr="b@example.com",
        status="ok",
        extra={
            "subject": "S1",
            "body": "B1",
            "snippet": "SN1",
            "text": "T1",
            "content": "C1",
            "safe_field": "SAFE_VALUE",
        },
    )
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    raw = json.dumps(entry)
    for secret in ("S1", "B1", "SN1", "T1", "C1"):
        assert secret not in raw
    assert entry.get("safe_field") == "SAFE_VALUE"


def test_rotate_on_exceed(tmp_path):
    import audit
    log_path = tmp_path / "audit.log"
    log = audit.AuditLog(log_path=log_path, max_bytes=10)  # tiny threshold
    log.write(request_id="r1", operation="list", message_id=None,
              from_addr=None, status="ok")
    log.write(request_id="r2", operation="list", message_id=None,
              from_addr=None, status="ok")
    # After rotation the .1 file should exist
    assert (tmp_path / "audit.log.1").exists()


def test_rotate_on_init(tmp_path):
    """AuditLog.__init__ rotates an oversized file without a write() call."""
    import audit
    log_path = tmp_path / "audit.log"
    # Write content larger than the threshold
    log_path.write_text("x" * 100)
    # Construct instance with tiny threshold — rotation should happen in __init__
    audit.AuditLog(log_path=log_path, max_bytes=10)
    assert (tmp_path / "audit.log.1").exists()
    assert not log_path.exists()  # original file was renamed


def test_write_includes_request_id_and_timestamp(tmp_path):
    import audit
    log = audit.AuditLog(log_path=tmp_path / "audit.log")
    log.write(request_id="req-99", operation="send", message_id="m1",
              from_addr="b@c.com", status="denied", reason="rate_limit")
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert entry["request_id"] == "req-99"
    assert "time" in entry
    assert entry["reason"] == "rate_limit"
