import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import pytest
from datetime import datetime, timedelta, timezone
from pydantic import ValidationError
from models import CreateEventInput, UpdateEventInput, DeleteEventInput, RecurrenceRule


def _future_date() -> str:
    """Return a date string 2 days in the future (YYYY-MM-DD)."""
    return (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")


# ── RecurrenceRule ────────────────────────────────────────────────────────────

def test_rrule_requires_count_or_until():
    with pytest.raises(ValidationError, match="COUNT or UNTIL"):
        RecurrenceRule(rrule="FREQ=WEEKLY")

def test_rrule_rejects_infinite():
    with pytest.raises(ValidationError):
        RecurrenceRule(rrule="FREQ=DAILY")  # no COUNT or UNTIL

def test_rrule_rejects_hourly():
    with pytest.raises(ValidationError, match="daily or less"):
        RecurrenceRule(rrule="FREQ=HOURLY;COUNT=10")

def test_rrule_rejects_minutely():
    with pytest.raises(ValidationError, match="daily or less"):
        RecurrenceRule(rrule="FREQ=MINUTELY;COUNT=10")

def test_rrule_rejects_count_over_max(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_RECURRENCE_COUNT", "52")
    with pytest.raises(ValidationError, match="exceeds maximum"):
        RecurrenceRule(rrule="FREQ=WEEKLY;COUNT=100")

def test_rrule_valid_weekly_count():
    r = RecurrenceRule(rrule="FREQ=WEEKLY;COUNT=12")
    assert r.rrule == "FREQ=WEEKLY;COUNT=12"

def test_rrule_valid_daily_until():
    r = RecurrenceRule(rrule="FREQ=DAILY;UNTIL=20261231T000000Z")
    assert "UNTIL" in r.rrule


# ── CreateEventInput ──────────────────────────────────────────────────────────

def test_create_rejects_naive_start():
    with pytest.raises(ValidationError, match="timezone"):
        CreateEventInput(
            title="Test", start="2026-03-15T14:00:00",
            end="2026-03-15T15:00:00+02:00", execution_mode="dry_run"
        )

def test_create_rejects_naive_end():
    with pytest.raises(ValidationError, match="timezone"):
        CreateEventInput(
            title="Test", start="2026-03-15T14:00:00+02:00",
            end="2026-03-15T15:00:00", execution_mode="dry_run"
        )

def test_create_rejects_start_after_end():
    with pytest.raises(ValidationError, match="before end"):
        CreateEventInput(
            title="Test", start="2026-03-15T16:00:00+02:00",
            end="2026-03-15T15:00:00+02:00", execution_mode="dry_run"
        )

def test_create_rejects_zero_duration():
    with pytest.raises(ValidationError, match="before end"):
        CreateEventInput(
            title="Test", start="2026-03-15T14:00:00+02:00",
            end="2026-03-15T14:00:00+02:00", execution_mode="dry_run"
        )

def test_create_rejects_duration_over_max(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_EVENT_HOURS", "8")
    with pytest.raises(ValidationError, match="exceeds maximum"):
        CreateEventInput(
            title="Test", start="2026-03-15T08:00:00+02:00",
            end="2026-03-15T18:00:00+02:00", execution_mode="dry_run"  # 10h
        )

def test_create_defaults_calendar_id():
    d = _future_date()
    ev = CreateEventInput(
        title="Test", start=f"{d}T14:00:00+02:00",
        end=f"{d}T15:00:00+02:00", execution_mode="dry_run"
    )
    assert ev.calendar_id == "primary"

def test_create_valid_event():
    d = _future_date()
    ev = CreateEventInput(
        title="Standup", start=f"{d}T09:00:00+02:00",
        end=f"{d}T09:30:00+02:00", execution_mode="execute"
    )
    assert ev.title == "Standup"
    assert ev.execution_mode == "execute"

def test_create_valid_with_recurrence():
    d = _future_date()
    ev = CreateEventInput(
        title="Weekly sync", start=f"{d}T10:00:00+02:00",
        end=f"{d}T11:00:00+02:00", execution_mode="dry_run",
        recurrence=RecurrenceRule(rrule="FREQ=WEEKLY;COUNT=4")
    )
    assert ev.recurrence.rrule == "FREQ=WEEKLY;COUNT=4"


# ── UpdateEventInput ──────────────────────────────────────────────────────────

def test_update_requires_event_id():
    with pytest.raises(ValidationError):
        UpdateEventInput(changes={"title": "New"}, execution_mode="dry_run")

def test_update_valid():
    u = UpdateEventInput(
        event_id="abc123", changes={"title": "Updated"}, execution_mode="dry_run"
    )
    assert u.event_id == "abc123"


# ── DeleteEventInput ──────────────────────────────────────────────────────────

def test_delete_requires_event_id():
    with pytest.raises(ValidationError):
        DeleteEventInput(execution_mode="dry_run")

def test_delete_valid():
    d = DeleteEventInput(event_id="abc123", execution_mode="execute")
    assert d.event_id == "abc123"
