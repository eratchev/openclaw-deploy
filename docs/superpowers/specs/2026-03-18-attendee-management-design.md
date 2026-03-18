# Attendee Management Design

**Goal:** Allow the OpenClaw agent to invite people to calendar events by passing email addresses to `gcal create`, so "schedule a meeting with Tim" results in Tim receiving a Google Calendar invite automatically.

---

## Problem

The calendar proxy creates events but has no attendee support. When the agent is asked to schedule a meeting with someone, it creates the event for the user only and then tells the user to add guests manually from the calendar UI. The fix is to accept attendees at creation time and let the Google Calendar API send the invite emails.

---

## Architecture

No new service, no new OAuth scope, no new token. Six existing files are modified.

### Modified files

**`services/calendar-proxy/models.py`** — adds `attendees` field to `CreateEventInput`:

```python
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Inside CreateEventInput:
attendees: list[str] = Field(default_factory=list, max_length=10)

@field_validator("attendees")
@classmethod
def validate_attendees(cls, v: list[str]) -> list[str]:
    for addr in v:
        if "," in addr or ";" in addr:
            raise ValueError(f"Each attendee must be a single email address: {addr!r}")
        if not _EMAIL_RE.match(addr):
            raise ValueError(f"Invalid email address: {addr!r}")
    return v
```

- Default: empty list (no attendees — existing behaviour unchanged)
- Max 10 attendees enforced by `Field(max_length=10)` (Pydantic v2 syntax for list length)
- Each address validated with regex; comma/semicolon strings rejected to prevent injection

**`services/calendar-proxy/policies.py`** — adds `has_attendees` parameter to `enforce()`:

```python
def enforce(
    impact: ImpactModel,
    *,
    calendar_id: str,
    in_allowlist: bool,
    is_delete: bool = False,
    confirmed: bool = False,
    has_attendees: bool = False,
) -> tuple[str, str | None]:
```

A new confirmation trigger added after the existing hard-denial checks and before the `if confirmed: return "safe_to_execute"` shortcut:

```python
if has_attendees:
    return "needs_confirmation", "attendees_present"
```

This runs before the `if confirmed: return "safe_to_execute"` shortcut — so when `has_attendees=True` and `confirmed=False`, the function returns `needs_confirmation`. When `confirmed=True`, the shortcut fires first and `has_attendees` has no effect. This preserves the existing pattern where all `needs_confirmation` paths flow through `_run_write_pipeline`, which handles audit logging and the `request_id`/`impact` response shape.

**`services/calendar-proxy/server.py`** — three changes:

1. **`_run_write_pipeline`** — passes `has_attendees` to `enforce()`:

```python
status, reason = enforce(
    impact or ...,
    calendar_id=calendar_id,
    in_allowlist=in_allowlist,
    is_delete=is_delete,
    confirmed=confirmed,
    has_attendees=bool(getattr(event_input, "attendees", [])),
)
```

The `needs_confirmation` response already includes `request_id` and `impact` — no change to response shape. When attendees are present, the agent sees the standard `needs_confirmation` response and re-submits with `confirmed=True`. The attendee list is visible in the `args` field of the audit log entry (via `event_input.model_dump()`), but since attendee emails are PII, the audit log must scrub them. See Audit section.

2. **`handle_create_event`** — adds attendees to the Google Calendar API body and sets `sendUpdates` explicitly:

```python
if inp.attendees:
    body["attendees"] = [{"email": addr} for addr in inp.attendees]

result = service.events().insert(
    calendarId=calendar_id,
    body=body,
    sendUpdates="all" if inp.attendees else "none",
).execute()
```

`sendUpdates="none"` for attendee-free events preserves the current behaviour (no spurious notifications). `sendUpdates="all"` ensures Google sends invite emails to all attendees.

Success response adds `attendees_invited`:

```python
return {
    "request_id": request_id,
    "status": "safe_to_execute",
    "event_id": event_id,
    "attendees_invited": len(inp.attendees),
}
```

3. **`create_event` MCP tool registration** — adds `attendees` parameter:

```python
@mcp.tool()
def create_event(title: str, start: str, end: str, execution_mode: str,
                 calendar_id: str = "primary", description: str = None,
                 recurrence_rrule: str = None, idempotency_key: str = None,
                 confirmed: bool = False,
                 attendees: list[str] = None) -> dict:
    """Create a Google Calendar event."""
    args = {...}
    if attendees:
        args["attendees"] = attendees
    return handle_create_event(args)
```

Without this change the MCP path (direct tool invocation, as opposed to the CLI) would silently ignore attendees.

**`services/calendar-proxy/scripts/gcal`** — adds `--attendee` (repeatable) to the `create` command.

The existing `_flag()` helper returns only the first occurrence of a flag, so a new helper is needed:

```python
def _collect_flags(args: list[str], flag: str) -> list[str]:
    result = []
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            result.append(args[i + 1])
    return result
```

In the `create` branch:

```python
attendees = _collect_flags(rest, "--attendee")
if attendees:
    args["attendees"] = attendees
```

Usage:

```
gcal create --title "Beers" --start ... --end ... --attendee tim@example.com --confirmed
gcal create --title "Team sync" --start ... --end ... --attendee a@b.com --attendee c@d.com --confirmed
```

**`workspace/MEMORY_GUIDE.md`** — adds a note in the calendar quick-reference section:

```
When scheduling a meeting with a named person:
1. Run contacts lookup --name "..." to resolve their email address.
   - If multiple matches are returned, show them to the user and ask which one to use.
   - If zero matches are returned, ask the user to provide the email address directly.
   - If contacts lookup returns an error (e.g. scope_missing), surface it to the user
     before proceeding.
2. Pass the resolved email to gcal create --attendee <email> --mode execute.
   The proxy will require confirmation before sending the invite.
```

**`tests/calendar_proxy/test_models.py`** and **`tests/calendar_proxy/test_server.py`** — new tests (see Testing section).

---

## Audit

`event_input.model_dump()` is passed to `audit.write(args=...)` in `_run_write_pipeline`. With the attendees field added to `CreateEventInput`, the audit log would contain attendee email addresses — PII.

The existing `_scrub_args()` in `audit.py` removes keys matching `{"token", "key", "secret", "password", "credential"}`. It does not cover `attendees`.

**Fix:** add `"attendees"` to `_SCRUB_KEYS` in `audit.py` so the field is stripped before writing. The `attendees_invited` count in the success response is sufficient metadata for audit purposes.

---

## Dry-run behaviour with attendees

With `execution_mode="dry_run"` and attendees present and `confirmed=False`:
- `enforce()` returns `needs_confirmation` (due to `has_attendees=True`)
- `_run_write_pipeline` checks `status == "needs_confirmation" or execution_mode == "dry_run"` — both are true
- Response status is `"dry_run"` (because `execution_mode == "dry_run"` takes precedence in the ternary)
- No invite is sent; the dry-run response shows impact only

This is correct behaviour: dry-run never sends invites regardless of attendees.

---

## CLI Interface

```
gcal create --title "Beers near Marina" \
            --start 2026-03-22T17:00:00+00:00 \
            --end   2026-03-22T19:00:00+00:00 \
            --attendee tim@example.com \
            --mode execute \
            --confirmed
```

---

## Agent Workflow

```
User: "Schedule a meeting with Tim for Sunday at a beer place near Marina"

Agent:
  contacts lookup --name "Tim"
  → {"matches": [{"name": "Tim Smith", "emails": ["tim@example.com"]}], "total": 1}

  gcal create --title "Beers near Marina" --start ... --end ... \
              --attendee tim@example.com --mode execute
  → {"request_id": "abc", "status": "needs_confirmation", "impact": {...}}

  [Agent shows user: "This will create the event and invite tim@example.com. Confirm?"]

  gcal create ... --attendee tim@example.com --mode execute --confirmed
  → {"request_id": "xyz", "status": "safe_to_execute", "event_id": "abc123", "attendees_invited": 1}
```

**Zero contacts results:** ask the user to provide Tim's email address directly.

**Multiple contacts results:** show all matches to the user and ask which one to use.

**Contacts lookup error (e.g. scope_missing):** surface the error to the user before proceeding.

---

## Security

- **Confirmation always required when attendees present** — `has_attendees=True` in `enforce()` always returns `needs_confirmation` unless `confirmed=True` is already set. No bypass.
- **Email validation** — each address validated with regex; comma/semicolon strings rejected.
- **Hard limit of 10 attendees** — `Field(max_length=10)` in Pydantic v2.
- **`sendUpdates="all"` explicit** — ensures Tim gets the invite email regardless of Google's context-dependent default.
- **No seen-domain check** — `confirmed` gate is the appropriate control for calendar invites. Adding seen-domain would block legitimate use of inviting new contacts.
- **`attendees` scrubbed from audit log** — added to `_SCRUB_KEYS` in `audit.py`; `attendees_invited` count is sufficient audit metadata.

---

## Out of Scope

- Updating attendees on existing events — will need revisiting when `gcal update` is built
- Removing attendees
- RSVP status tracking
- Optional vs required attendees

---

## Testing

**`tests/calendar_proxy/test_models.py`** additions:
- Valid single attendee accepted
- Valid multiple attendees (≤ 10) accepted
- Invalid email format rejected
- Comma-separated string in one slot rejected
- More than 10 attendees rejected
- Empty list accepted (backward compat)

**`tests/calendar_proxy/test_server.py`** additions:
- Attendees passed to Google Calendar API body with `sendUpdates="all"`
- `needs_confirmation` returned when attendees present and `confirmed=False`
- Confirmed request with attendees calls API and returns `attendees_invited` in response
- Dry run with attendees returns `"dry_run"` status without calling API
- `attendees` key absent from audit log entry; `attendees_invited` present in success response
- No attendees → `sendUpdates="none"` (existing behaviour unchanged)

**`tests/calendar_proxy/test_policies.py`** additions:
- `enforce()` returns `needs_confirmation` when `has_attendees=True` and `confirmed=False`
- `enforce()` returns `safe_to_execute` when `has_attendees=True` and `confirmed=True`
