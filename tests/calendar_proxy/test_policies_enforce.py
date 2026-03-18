import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import pytest
from policies import enforce
from models import ImpactModel, ConflictEntry


def _impact(**kwargs):
    defaults = dict(
        overlaps_existing=False,
        overlapping_events=[],
        outside_business_hours=False,
        is_weekend=False,
        duration_minutes=60,
        recurring=False,
        recurrence_instances_checked=1,
        work_calendar=False,
    )
    defaults.update(kwargs)
    return ImpactModel(**defaults)


# ── Denied ────────────────────────────────────────────────────────────────────

def test_denied_not_in_allowlist():
    status, reason = enforce(_impact(), calendar_id="other@group.calendar.google.com", in_allowlist=False)
    assert status == "denied"
    assert "allowlist" in reason


def test_denied_recurring_work_outside_hours():
    impact = _impact(recurring=True, work_calendar=True, outside_business_hours=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "denied"


def test_denied_recurring_work_weekend():
    impact = _impact(recurring=True, work_calendar=True, is_weekend=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "denied"


# ── Needs confirmation ────────────────────────────────────────────────────────

def test_needs_confirmation_overlap():
    impact = _impact(
        overlaps_existing=True,
        overlapping_events=[ConflictEntry(event_id="x", title="X", occurrence_start="2026-03-16T10:00:00+02:00", overlap_minutes=30, severity="partial")]
    )
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_over_2h():
    impact = _impact(duration_minutes=150)  # 2.5 hours
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_outside_hours():
    impact = _impact(outside_business_hours=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_weekend():
    impact = _impact(is_weekend=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_work_calendar():
    impact = _impact(work_calendar=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_recurring():
    impact = _impact(recurring=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_delete_always():
    impact = _impact()
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True, is_delete=True)
    assert status == "needs_confirmation"


def test_confirmed_bypasses_delete_confirmation():
    impact = _impact()
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True, is_delete=True, confirmed=True)
    assert status == "safe_to_execute"
    assert reason is None


def test_confirmed_bypasses_other_confirmation_flags():
    impact = _impact(overlaps_existing=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True, confirmed=True)
    assert status == "safe_to_execute"


def test_confirmed_bypasses_outside_hours():
    impact = _impact(outside_business_hours=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True, confirmed=True)
    assert status == "safe_to_execute"
    assert reason is None


def test_confirmed_does_not_bypass_hard_denial():
    impact = _impact(recurring=True, work_calendar=True, outside_business_hours=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True, confirmed=True)
    assert status == "denied"


# ── Safe to execute ───────────────────────────────────────────────────────────

def test_safe_to_execute_simple_event():
    impact = _impact(duration_minutes=30)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "safe_to_execute"
    assert reason is None


def test_safe_to_execute_exactly_2h():
    impact = _impact(duration_minutes=120)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "safe_to_execute"


def test_safe_to_execute_personal_calendar_inside_hours():
    impact = _impact(duration_minutes=45, work_calendar=False, recurring=False,
                     outside_business_hours=False, is_weekend=False)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "safe_to_execute"


# ── Attendees ─────────────────────────────────────────────────────────────────

def test_needs_confirmation_when_has_attendees():
    status, reason = enforce(
        _impact(),
        calendar_id="primary",
        in_allowlist=True,
        has_attendees=True,
    )
    assert status == "needs_confirmation"
    assert reason == "attendees_present"


def test_confirmed_bypasses_attendees_gate():
    status, reason = enforce(
        _impact(),
        calendar_id="primary",
        in_allowlist=True,
        confirmed=True,
        has_attendees=True,
    )
    assert status == "safe_to_execute"
