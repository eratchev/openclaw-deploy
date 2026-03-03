import os
from datetime import datetime, timezone, timedelta
from typing import Callable
import pytz
from dateutil.rrule import rrulestr

from models import CreateEventInput, ImpactModel, ConflictEntry


def _user_tz() -> pytz.BaseTzInfo:
    return pytz.timezone(os.getenv("GCAL_USER_TIMEZONE", "UTC"))


def _to_user_tz(dt: datetime) -> datetime:
    """Convert any timezone-aware datetime to the user's configured timezone."""
    return dt.astimezone(_user_tz())


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _overlap_minutes(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> int:
    overlap_start = max(a_start, b_start)
    overlap_end = min(a_end, b_end)
    if overlap_end <= overlap_start:
        return 0
    return int((overlap_end - overlap_start).total_seconds() / 60)


def _classify_severity(overlap_mins: int, duration_mins: float) -> str:
    return "full" if overlap_mins >= duration_mins else "partial"


def _check_one_window(
    start: datetime,
    end: datetime,
    calendar_id: str,
    list_events_fn: Callable,
) -> list[ConflictEntry]:
    existing = list_events_fn(
        calendar_id,
        start.isoformat(),
        end.isoformat(),
    )
    conflicts = []
    duration = (end - start).total_seconds() / 60
    for ev in existing:
        ev_start = _parse_dt(ev["start"].get("dateTime") or ev["start"].get("date"))
        ev_end = _parse_dt(ev["end"].get("dateTime") or ev["end"].get("date"))
        mins = _overlap_minutes(start, end, ev_start, ev_end)
        if mins > 0:
            conflicts.append(ConflictEntry(
                event_id=ev["id"],
                title=ev.get("summary", "(no title)"),
                occurrence_start=start.isoformat(),
                overlap_minutes=mins,
                severity=_classify_severity(mins, duration),
            ))
    return conflicts


def assess(event: CreateEventInput, list_events_fn: Callable) -> ImpactModel:
    """Phase 2: produce impact model without making policy decisions."""
    start = _parse_dt(event.start)
    end = _parse_dt(event.end)
    duration_minutes = (end - start).total_seconds() / 60

    # Business hours + weekend (evaluated in user timezone)
    local_start = _to_user_tz(start)
    start_hour_cfg = int(os.getenv("GCAL_ALLOWED_START_HOUR", "8"))
    end_hour_cfg = int(os.getenv("GCAL_ALLOWED_END_HOUR", "20"))
    outside_business_hours = (
        local_start.hour < start_hour_cfg or local_start.hour >= end_hour_cfg
    )
    is_weekend = local_start.weekday() >= 5  # 5=Sat, 6=Sun

    all_conflicts: list[ConflictEntry] = []
    instances_checked = 0

    if event.recurrence:
        # Expand recurrence instances and check each one
        rule = rrulestr(event.recurrence.rrule, dtstart=start)
        occurrences = list(rule)
        instances_checked = len(occurrences)
        for occ_start in occurrences:
            occ_end = occ_start + (end - start)
            conflicts = _check_one_window(occ_start, occ_end, event.calendar_id, list_events_fn)
            all_conflicts.extend(conflicts)
    else:
        # Single event
        instances_checked = 1
        all_conflicts = _check_one_window(start, end, event.calendar_id, list_events_fn)

    return ImpactModel(
        overlaps_existing=len(all_conflicts) > 0,
        overlapping_events=all_conflicts,
        outside_business_hours=outside_business_hours,
        is_weekend=is_weekend,
        duration_minutes=duration_minutes,
        recurring=event.recurrence is not None,
        recurrence_instances_checked=instances_checked,
        work_calendar=event.calendar_id == os.getenv("GCAL_WORK_CALENDAR_ID", "__unset__"),
    )


def enforce(
    impact: ImpactModel,
    *,
    calendar_id: str,
    in_allowlist: bool,
    is_delete: bool = False,
) -> tuple[str, str | None]:
    """Phase 3: apply policy rules → (status, reason)."""

    # Hard denials — not overridable
    if not in_allowlist:
        return "denied", f"calendar_id '{calendar_id}' is not in GCAL_ALLOWED_CALENDARS"

    if impact.recurring and impact.work_calendar and (impact.outside_business_hours or impact.is_weekend):
        return "denied", "recurring event on work calendar outside business hours is not allowed"

    # Confirmation required
    if is_delete:
        return "needs_confirmation", None
    if impact.overlaps_existing:
        return "needs_confirmation", None
    if impact.duration_minutes > 120:
        return "needs_confirmation", None
    if impact.outside_business_hours:
        return "needs_confirmation", None
    if impact.is_weekend:
        return "needs_confirmation", None
    if impact.work_calendar:
        return "needs_confirmation", None
    if impact.recurring:
        return "needs_confirmation", None

    return "safe_to_execute", None
