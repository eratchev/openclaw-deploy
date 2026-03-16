import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta, timezone
import pytz
from policies import assess
from models import CreateEventInput, ImpactModel, RecurrenceRule


def _future_weekday_date() -> str:
    """Return a weekday date at least 2 days in the future (YYYY-MM-DD)."""
    dt = datetime.now(timezone.utc) + timedelta(days=2)
    while dt.weekday() >= 5:  # 5=Sat, 6=Sun
        dt += timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def _future_saturday_date() -> str:
    """Return the next Saturday's date (YYYY-MM-DD)."""
    dt = datetime.now(timezone.utc) + timedelta(days=1)
    while dt.weekday() != 5:  # 5=Saturday
        dt += timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def _make_input(**kwargs):
    d = _future_weekday_date()
    defaults = dict(
        title="Test",
        start=f"{d}T10:00:00+02:00",
        end=f"{d}T11:00:00+02:00",
        execution_mode="dry_run",
    )
    defaults.update(kwargs)
    return CreateEventInput(**defaults)


def _no_conflicts(calendar_id, time_min, time_max):
    return []


def _one_conflict(calendar_id, time_min, time_max):
    return [{"id": "existing-1", "summary": "Other meeting",
             "start": {"dateTime": time_min}, "end": {"dateTime": time_max}}]


# ── Business hours ────────────────────────────────────────────────────────────

def test_inside_business_hours(monkeypatch):
    monkeypatch.setenv("GCAL_ALLOWED_START_HOUR", "8")
    monkeypatch.setenv("GCAL_ALLOWED_END_HOUR", "20")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "Europe/Helsinki")
    # 10:00 Helsinki on Monday
    impact = assess(_make_input(), list_events_fn=_no_conflicts)
    assert impact.outside_business_hours is False
    assert impact.is_weekend is False


def test_outside_business_hours_early(monkeypatch):
    monkeypatch.setenv("GCAL_ALLOWED_START_HOUR", "8")
    monkeypatch.setenv("GCAL_ALLOWED_END_HOUR", "20")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "Europe/Helsinki")
    d = _future_weekday_date()
    inp = _make_input(start=f"{d}T06:00:00+02:00", end=f"{d}T07:00:00+02:00")
    impact = assess(inp, list_events_fn=_no_conflicts)
    assert impact.outside_business_hours is True


def test_outside_business_hours_late(monkeypatch):
    monkeypatch.setenv("GCAL_ALLOWED_START_HOUR", "8")
    monkeypatch.setenv("GCAL_ALLOWED_END_HOUR", "20")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "Europe/Helsinki")
    d = _future_weekday_date()
    inp = _make_input(start=f"{d}T21:00:00+02:00", end=f"{d}T22:00:00+02:00")
    impact = assess(inp, list_events_fn=_no_conflicts)
    assert impact.outside_business_hours is True


def test_weekend_detection(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "Europe/Helsinki")
    s = _future_saturday_date()
    inp = _make_input(start=f"{s}T10:00:00+02:00", end=f"{s}T11:00:00+02:00")
    impact = assess(inp, list_events_fn=_no_conflicts)
    assert impact.is_weekend is True


def test_weekday_not_weekend(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "Europe/Helsinki")
    impact = assess(_make_input(), list_events_fn=_no_conflicts)
    assert impact.is_weekend is False


# ── Conflict detection ────────────────────────────────────────────────────────

def test_no_conflict_when_no_existing_events(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    impact = assess(_make_input(), list_events_fn=_no_conflicts)
    assert impact.overlaps_existing is False
    assert impact.overlapping_events == []


def test_conflict_detected(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    impact = assess(_make_input(), list_events_fn=_one_conflict)
    assert impact.overlaps_existing is True
    assert len(impact.overlapping_events) == 1
    assert impact.overlapping_events[0].event_id == "existing-1"


# ── Recurrence expansion ──────────────────────────────────────────────────────

def test_recurrence_expands_instances(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    inp = _make_input(
        recurrence=RecurrenceRule(rrule="FREQ=WEEKLY;COUNT=4")
    )
    impact = assess(inp, list_events_fn=_no_conflicts)
    assert impact.recurring is True
    assert impact.recurrence_instances_checked == 4


def test_recurrence_conflict_on_second_instance(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    call_count = {"n": 0}

    def conflicts_on_second_call(calendar_id, time_min, time_max):
        call_count["n"] += 1
        if call_count["n"] == 2:
            return [{"id": "clash", "summary": "Clash",
                     "start": {"dateTime": time_min}, "end": {"dateTime": time_max}}]
        return []

    inp = _make_input(recurrence=RecurrenceRule(rrule="FREQ=WEEKLY;COUNT=3"))
    impact = assess(inp, list_events_fn=conflicts_on_second_call)
    assert impact.overlaps_existing is True
    assert impact.recurrence_instances_checked == 3


def test_non_recurring_checks_one_window(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    call_count = {"n": 0}
    def count_calls(calendar_id, time_min, time_max):
        call_count["n"] += 1
        return []
    assess(_make_input(), list_events_fn=count_calls)
    assert call_count["n"] == 1


# ── Duration ──────────────────────────────────────────────────────────────────

def test_duration_minutes_calculated(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    impact = assess(_make_input(), list_events_fn=_no_conflicts)
    assert impact.duration_minutes == 60.0
