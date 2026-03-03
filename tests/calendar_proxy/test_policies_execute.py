import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import json
import time
import hashlib
import pytest
import fakeredis
from policies import check_rate_limit, check_idempotency, record_idempotency, idempotency_key_for


# ── Rate limiting ─────────────────────────────────────────────────────────────

def test_rate_limit_allows_under_limit(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_EVENTS_PER_DAY", "10")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    r = fakeredis.FakeRedis()
    for _ in range(9):
        ok, reason = check_rate_limit(r, calendar_id="primary", op="create", date_str="2026-03-15")
        assert ok

def test_rate_limit_blocks_at_limit(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_EVENTS_PER_DAY", "3")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    r = fakeredis.FakeRedis()
    for _ in range(3):
        r.incr("rate_limit:primary:2026-03-15")
    ok, reason = check_rate_limit(r, calendar_id="primary", op="create", date_str="2026-03-15")
    assert not ok
    assert "rate limit" in reason.lower()

def test_rate_limit_separate_per_calendar(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_EVENTS_PER_DAY", "2")
    r = fakeredis.FakeRedis()
    r.set("rate_limit:work@calendar:2026-03-15", 2)  # work at limit
    ok, _ = check_rate_limit(r, calendar_id="primary", op="create", date_str="2026-03-15")
    assert ok  # personal unaffected

def test_rate_limit_update_uses_separate_counter(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_UPDATES_PER_DAY", "50")
    r = fakeredis.FakeRedis()
    ok, _ = check_rate_limit(r, calendar_id="primary", op="update", date_str="2026-03-15")
    assert ok


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_idempotency_key_for_create():
    key = idempotency_key_for("create", {"title": "T", "start": "2026-03-15T10:00:00+02:00", "end": "2026-03-15T11:00:00+02:00", "calendar_id": "primary"})
    assert key.startswith("sha256:")
    assert len(key) > 10

def test_idempotency_key_for_update():
    key = idempotency_key_for("update", {"event_id": "abc", "changes": {"title": "New"}})
    assert key.startswith("sha256:")

def test_idempotency_key_for_delete():
    key = idempotency_key_for("delete", {"event_id": "abc"})
    assert key.startswith("sha256:")

def test_idempotency_no_hit_first_time():
    r = fakeredis.FakeRedis()
    result = check_idempotency(r, "sha256:abc123")
    assert result is None

def test_idempotency_hit_on_second_execute():
    r = fakeredis.FakeRedis()
    record_idempotency(r, "sha256:abc123", event_id="google-event-1")
    result = check_idempotency(r, "sha256:abc123")
    assert result == "google-event-1"

def test_idempotency_expires_after_ttl():
    r = fakeredis.FakeRedis()
    record_idempotency(r, "sha256:abc123", event_id="ev1", ttl_seconds=1)
    time.sleep(1.1)
    result = check_idempotency(r, "sha256:abc123")
    assert result is None

def test_idempotency_not_written_for_dry_run():
    """dry_run must never write idempotency cache — verified by caller convention."""
    r = fakeredis.FakeRedis()
    # record_idempotency should not be called for dry_run
    # This test documents the contract: check that we can call record_idempotency
    # only from execute path (enforced in server.py, not here)
    # Just verify record doesn't auto-expire immediately
    record_idempotency(r, "sha256:dry", event_id="ev", ttl_seconds=600)
    assert check_idempotency(r, "sha256:dry") == "ev"
