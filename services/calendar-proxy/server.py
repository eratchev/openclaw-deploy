import os
import uuid
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import redis as redis_lib
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from mcp.server.fastmcp import FastMCP

from auth import TokenStore
from audit import AuditLog
from models import (
    CreateEventInput, DeleteEventInput,
    ListEventsInput, CheckAvailabilityInput,
)
from policies import assess, enforce, check_rate_limit, check_idempotency, record_idempotency, idempotency_key_for

# ── Startup ───────────────────────────────────────────────────────────────────

DRY_RUN = os.getenv("GCAL_DRY_RUN", "false").lower() == "true"
if DRY_RUN:
    print("[calendar-proxy] [WARN] *** DRY_RUN MODE ACTIVE — no calendar writes will be executed ***", flush=True)

token_store = TokenStore.from_env()
audit = AuditLog(log_path=Path(os.getenv("GCAL_AUDIT_LOG_PATH", "/data/calendar-audit.log")))
mcp = FastMCP("calendar-proxy", host="0.0.0.0", port=8080)

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_redis() -> redis_lib.Redis:
    return redis_lib.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))


def build_google_service():
    token_data = token_store.load()
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes"),
    )
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            token_store.save({
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes) if creds.scopes else token_data.get("scopes"),
            })
        else:
            raise RuntimeError(
                "Google credentials are invalid and cannot be refreshed. Re-run auth setup."
            )
    return build("calendar", "v3", credentials=creds)


def _allowed_calendars() -> set[str]:
    raw = os.getenv("GCAL_ALLOWED_CALENDARS", "primary")
    return {c.strip() for c in raw.split(",")}


def _list_events_fn(service):
    def fn(calendar_id: str, time_min: str, time_max: str) -> list:
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
        ).execute()
        return result.get("items", [])
    return fn


def _today_date_str() -> str:
    import pytz
    tz = pytz.timezone(os.getenv("GCAL_USER_TIMEZONE", "UTC"))
    return datetime.now(tz).strftime("%Y-%m-%d")


def _run_write_pipeline(event_input, op: str, is_delete: bool = False):
    """Shared validate → assess → enforce → execute pipeline for write tools."""
    request_id = str(uuid.uuid4())
    start_ms = time.monotonic()
    execution_mode = event_input.execution_mode
    if os.getenv("GCAL_DRY_RUN", "false").lower() == "true":
        execution_mode = "dry_run"

    calendar_id = event_input.calendar_id
    in_allowlist = calendar_id in _allowed_calendars()

    r = get_redis()

    # Assess — only build the Google service if we need to list existing events
    if hasattr(event_input, "title"):
        impact = assess(event_input, _list_events_fn(build_google_service()))
    else:
        impact = None

    # Enforce
    confirmed = getattr(event_input, "confirmed", False)
    status, reason = enforce(
        impact or type("I", (), {"overlaps_existing": False, "overlapping_events": [],
                                  "outside_business_hours": False, "is_weekend": False,
                                  "duration_minutes": 0, "recurring": False,
                                  "recurrence_instances_checked": 0, "work_calendar": False})(),
        calendar_id=calendar_id,
        in_allowlist=in_allowlist,
        is_delete=is_delete,
        confirmed=confirmed,
    )

    duration_ms = int((time.monotonic() - start_ms) * 1000)

    if status == "denied":
        audit.write(request_id=request_id, tool=op, execution_mode=execution_mode,
                    session_id="", args=event_input.model_dump(), status="denied",
                    reason=reason, duration_ms=duration_ms)
        return {"request_id": request_id, "status": "denied", "reason": reason}

    if status == "needs_confirmation" or execution_mode == "dry_run":
        audit.write(request_id=request_id, tool=op, execution_mode=execution_mode,
                    session_id="", args=event_input.model_dump(),
                    status="dry_run" if execution_mode == "dry_run" else "needs_confirmation",
                    duration_ms=duration_ms)
        return {
            "request_id": request_id,
            "status": "dry_run" if execution_mode == "dry_run" else "needs_confirmation",
            "impact": impact.model_dump() if impact else None,
        }

    # Execute path: rate limit → idempotency → Google API
    date_str = _today_date_str()
    ok, rate_reason = check_rate_limit(r, calendar_id=calendar_id, op=op, date_str=date_str)
    if not ok:
        audit.write(request_id=request_id, tool=op, execution_mode=execution_mode,
                    session_id="", args=event_input.model_dump(), status="denied",
                    reason=rate_reason, duration_ms=duration_ms)
        return {"request_id": request_id, "status": "denied", "reason": rate_reason}

    idem_key = event_input.idempotency_key or idempotency_key_for(op, event_input.model_dump())
    existing_event_id = check_idempotency(r, idem_key)
    if existing_event_id:
        return {"request_id": request_id, "status": "safe_to_execute", "event_id": existing_event_id}

    return None  # Caller executes the actual Google API call


# ── Tool handlers (called by tests and MCP tools) ─────────────────────────────

def handle_create_event(args: dict) -> dict:
    event_input = CreateEventInput(**args)
    result = _run_write_pipeline(event_input, op="create")
    if result is not None:
        return result
    # Execute
    service = build_google_service()
    body = {"summary": event_input.title, "start": {"dateTime": event_input.start},
            "end": {"dateTime": event_input.end}}
    if event_input.description:
        body["description"] = event_input.description
    if event_input.recurrence:
        body["recurrence"] = [f"RRULE:{event_input.recurrence.rrule}"]
    created = service.events().insert(calendarId=event_input.calendar_id, body=body).execute()
    event_id = created["id"]
    idem_key = event_input.idempotency_key or idempotency_key_for("create", event_input.model_dump())
    record_idempotency(get_redis(), idem_key, event_id=event_id)
    request_id = str(uuid.uuid4())
    audit.write(request_id=request_id, tool="create_event", execution_mode="execute",
                session_id="", args=event_input.model_dump(), status="created", event_id=event_id, duration_ms=0)
    return {"request_id": request_id, "status": "safe_to_execute", "event_id": event_id}


def handle_list_events(args: dict) -> list:
    inp = ListEventsInput(**args)
    service = build_google_service()
    return _list_events_fn(service)(inp.calendar_id, inp.time_min, inp.time_max)


def get_health() -> dict:
    health: dict[str, Any] = {"dry_run_mode": os.getenv("GCAL_DRY_RUN", "false").lower() == "true"}
    try:
        get_redis().ping()
        health["redis"] = "ok"
    except Exception as e:
        health["redis"] = f"error: {e}"
    try:
        token_store.load()
        health["token"] = "ok"
    except Exception as e:
        health["token"] = f"error: {e}"
    if os.getenv("GCAL_HEALTH_CHECK_GOOGLE", "false").lower() == "true":
        try:
            build_google_service()
            health["google_api"] = "ok"
        except Exception as e:
            health["google_api"] = f"error: {e}"
    else:
        health["google_api"] = "skipped"
    return health


# ── MCP tool registrations ────────────────────────────────────────────────────

@mcp.tool()
def create_event(title: str, start: str, end: str, execution_mode: str,
                 calendar_id: str = "primary", description: str = None,
                 recurrence_rrule: str = None, idempotency_key: str = None,
                 confirmed: bool = False) -> dict:
    """Create a Google Calendar event."""
    args = {"title": title, "start": start, "end": end, "execution_mode": execution_mode,
            "calendar_id": calendar_id, "confirmed": confirmed}
    if description:
        args["description"] = description
    if recurrence_rrule:
        from models import RecurrenceRule
        args["recurrence"] = RecurrenceRule(rrule=recurrence_rrule)
    if idempotency_key:
        args["idempotency_key"] = idempotency_key
    return handle_create_event(args)


@mcp.tool()
def list_events(time_min: str, time_max: str, calendar_id: str = "primary") -> list:
    """List Google Calendar events in a time window."""
    return handle_list_events({"time_min": time_min, "time_max": time_max, "calendar_id": calendar_id})


@mcp.tool()
def check_availability(time_min: str, time_max: str, duration_minutes: int) -> dict:
    """Find free slots in a time window."""
    inp = CheckAvailabilityInput(time_min=time_min, time_max=time_max, duration_minutes=duration_minutes)
    service = build_google_service()
    existing = _list_events_fn(service)("primary", inp.time_min, inp.time_max)
    return {"events": existing, "duration_requested_minutes": duration_minutes}


@mcp.tool()
def delete_event(event_id: str, execution_mode: str, calendar_id: str = "primary",
                 idempotency_key: str = None, confirmed: bool = False) -> dict:
    """Delete a Google Calendar event. Set confirmed=True after showing the user the impact."""
    event_input = DeleteEventInput(event_id=event_id, execution_mode=execution_mode,
                                   calendar_id=calendar_id, idempotency_key=idempotency_key,
                                   confirmed=confirmed)
    result = _run_write_pipeline(event_input, op="delete", is_delete=True)
    if result is not None:
        return result
    service = build_google_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    idem_key = idempotency_key or idempotency_key_for("delete", event_input.model_dump())
    record_idempotency(get_redis(), idem_key, event_id=event_id)
    request_id = str(uuid.uuid4())
    audit.write(request_id=request_id, tool="delete_event", execution_mode="execute",
                session_id="", args=event_input.model_dump(), status="deleted", event_id=event_id, duration_ms=0)
    return {"request_id": request_id, "status": "safe_to_execute", "event_id": event_id}


# ── REST API (for gcal CLI) ───────────────────────────────────────────────────

from starlette.requests import Request
from starlette.responses import JSONResponse

_TOOL_HANDLERS = {
    "create_event": handle_create_event,
    "list_events": handle_list_events,
}


def _handle_check_availability(args: dict) -> dict:
    inp = CheckAvailabilityInput(**args)
    service = build_google_service()
    existing = _list_events_fn(service)("primary", inp.time_min, inp.time_max)
    return {"events": existing, "duration_requested_minutes": inp.duration_minutes}


def _handle_delete_event(args: dict) -> dict:
    event_input = DeleteEventInput(**args)
    result = _run_write_pipeline(event_input, op="delete", is_delete=True)
    if result is not None:
        return result
    service = build_google_service()
    service.events().delete(calendarId=event_input.calendar_id, eventId=event_input.event_id).execute()
    idem_key = event_input.idempotency_key or idempotency_key_for("delete", event_input.model_dump())
    record_idempotency(get_redis(), idem_key, event_id=event_input.event_id)
    request_id = str(uuid.uuid4())
    audit.write(request_id=request_id, tool="delete_event", execution_mode="execute",
                session_id="", args=event_input.model_dump(), status="deleted", event_id=event_input.event_id, duration_ms=0)
    return {"request_id": request_id, "status": "safe_to_execute", "event_id": event_input.event_id}


_TOOL_HANDLERS["check_availability"] = _handle_check_availability
_TOOL_HANDLERS["delete_event"] = _handle_delete_event


@mcp.custom_route("/health", methods=["GET"])
async def http_health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(get_health())


@mcp.custom_route("/call", methods=["POST"])
async def http_call(request: Request) -> JSONResponse:
    """Call a calendar tool by name. Body: {\"tool\": \"<name>\", \"args\": {...}}"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    tool = body.get("tool")
    args = body.get("args", {})
    handler = _TOOL_HANDLERS.get(tool)
    if handler is None:
        return JSONResponse({"error": f"unknown tool: {tool}", "available": list(_TOOL_HANDLERS)}, status_code=404)
    try:
        result = handler(args)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="sse")
