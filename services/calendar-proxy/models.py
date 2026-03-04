import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from pydantic import BaseModel, field_validator, model_validator


def _max_recurrence_count() -> int:
    return int(os.getenv("GCAL_MAX_RECURRENCE_COUNT", "52"))

def _max_event_hours() -> int:
    return int(os.getenv("GCAL_MAX_EVENT_HOURS", "8"))

def _max_past_hours() -> int:
    return int(os.getenv("GCAL_MAX_PAST_HOURS", "1"))


class RecurrenceRule(BaseModel):
    rrule: str

    @field_validator("rrule")
    @classmethod
    def validate_rrule(cls, v: str) -> str:
        if "COUNT=" not in v and "UNTIL=" not in v:
            raise ValueError("RRULE must specify COUNT or UNTIL — infinite recurrence not allowed")
        if re.search(r"FREQ=(HOURLY|MINUTELY|SECONDLY)", v, re.IGNORECASE):
            raise ValueError("RRULE frequency must be daily or less frequent")
        count_match = re.search(r"COUNT=(\d+)", v)
        if count_match:
            count = int(count_match.group(1))
            max_count = _max_recurrence_count()
            if count > max_count:
                raise ValueError(f"RRULE COUNT {count} exceeds maximum {max_count}")
        return v


def _parse_dt(v: str, field: str) -> datetime:
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        raise ValueError(f"Invalid datetime for {field}: {v!r}")
    if dt.tzinfo is None:
        raise ValueError(f"Datetime for {field} must include timezone offset, got naive: {v!r}")
    return dt


class CreateEventInput(BaseModel):
    title: str
    start: str
    end: str
    calendar_id: str = "primary"
    description: Optional[str] = None
    recurrence: Optional[RecurrenceRule] = None
    execution_mode: Literal["dry_run", "execute"]
    idempotency_key: Optional[str] = None

    @field_validator("start", "end")
    @classmethod
    def validate_datetime_with_tz(cls, v: str) -> str:
        _parse_dt(v, v)  # raises if naive
        return v

    @model_validator(mode="after")
    def validate_temporal(self) -> "CreateEventInput":
        start = _parse_dt(self.start, "start")
        end = _parse_dt(self.end, "end")
        if start >= end:
            raise ValueError("start must be before end")
        duration_hours = (end - start).total_seconds() / 3600
        max_hours = _max_event_hours()
        if duration_hours > max_hours:
            raise ValueError(f"Duration {duration_hours:.1f}h exceeds maximum {max_hours}h")
        max_past = _max_past_hours()
        now = datetime.now(tz=timezone.utc)
        if start.astimezone(timezone.utc) < now - timedelta(hours=max_past):
            raise ValueError(f"start is more than {max_past}h in the past")
        return self


class UpdateEventInput(BaseModel):
    event_id: str
    changes: dict[str, Any]
    calendar_id: str = "primary"
    execution_mode: Literal["dry_run", "execute"]
    idempotency_key: Optional[str] = None


class DeleteEventInput(BaseModel):
    event_id: str
    calendar_id: str = "primary"
    execution_mode: Literal["dry_run", "execute"]
    idempotency_key: Optional[str] = None
    confirmed: bool = False


class ListEventsInput(BaseModel):
    calendar_id: str = "primary"
    time_min: str
    time_max: str

    @field_validator("time_min", "time_max")
    @classmethod
    def validate_datetime_with_tz(cls, v: str) -> str:
        _parse_dt(v, v)
        return v


class CheckAvailabilityInput(BaseModel):
    time_min: str
    time_max: str
    duration_minutes: int

    @field_validator("time_min", "time_max")
    @classmethod
    def validate_datetime_with_tz(cls, v: str) -> str:
        _parse_dt(v, v)
        return v


class ConflictEntry(BaseModel):
    event_id: str
    title: str
    occurrence_start: str
    overlap_minutes: int
    severity: Literal["partial", "full"]


class ImpactModel(BaseModel):
    overlaps_existing: bool = False
    overlapping_events: list[ConflictEntry] = []
    outside_business_hours: bool = False
    is_weekend: bool = False
    duration_minutes: float = 0
    recurring: bool = False
    recurrence_instances_checked: int = 0
    work_calendar: bool = False


class PolicyResponse(BaseModel):
    request_id: str
    status: Literal["safe_to_execute", "needs_confirmation", "denied", "error"]
    impact: Optional[ImpactModel] = None
    normalized_event: Optional[dict] = None
    event_id: Optional[str] = None
    reason: Optional[str] = None
