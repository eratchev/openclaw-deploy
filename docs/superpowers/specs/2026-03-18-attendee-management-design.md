# Attendee Management Design

**Goal:** Allow the OpenClaw agent to invite people to calendar events by passing email addresses to `gcal create`, so "schedule a meeting with Tim" results in Tim receiving a Google Calendar invite automatically.

---

## Problem

The calendar proxy creates events but has no attendee support. When the agent is asked to schedule a meeting with someone, it creates the event for the user only and then tells the user to add guests manually from the calendar UI. The fix is to accept attendees at creation time and let the Google Calendar API send the invite emails.

---

## Architecture

No new service, no new OAuth scope, no new token. Five existing files are modified.

### Modified files

**`services/calendar-proxy/models.py`** — adds `attendees` field to `CreateEventInput`:

```python
attendees: list[str] = Field(default_factory=list, max_length=10)

@field_validator("attendees")
@classmethod
def validate_attendees(cls, v: list[str]) -> list[str]:
    pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    for addr in v:
        if "," in addr or ";" in addr:
            raise ValueError(f"Each attendee must be a single email address: {addr!r}")
        if not pattern.match(addr):
            raise ValueError(f"Invalid email address: {addr!r}")
    return v
```

- Default: empty list (no attendees — existing behaviour unchanged)
- Max 10 attendees (hard limit)
- Each address validated with regex; comma/semicolon-separated strings rejected

**`services/calendar-proxy/server.py`** — two additions in `handle_create_event`:

1. **Confirmation gate** — when `attendees` is non-empty and `confirmed=False`, return immediately with `needs_confirmation`:

```python
if inp.attendees and not inp.confirmed:
    return {
        "status": "needs_confirmation",
        "message": f"This will send calendar invites to: {', '.join(inp.attendees)}. Re-submit with confirmed=True to proceed.",
    }
```

2. **Google Calendar API call** — add attendees to the body and set `sendUpdates` explicitly:

```python
if inp.attendees:
    body["attendees"] = [{"email": addr} for addr in inp.attendees]

result = service.events().insert(
    calendarId=calendar_id,
    body=body,
    sendUpdates="all" if inp.attendees else "none",
).execute()
```

`sendUpdates="none"` for attendee-free events preserves the current behaviour (no spurious notifications).

3. **Audit** — add `attendee_count` to the existing audit log call. Email addresses are never logged.

**`services/calendar-proxy/scripts/gcal`** — adds a repeatable `--attendee` flag to the `create` command:

```
gcal create --title "Beers" --start ... --end ... --attendee tim@example.com --confirmed
gcal create --title "Team sync" --start ... --end ... --attendee a@b.com --attendee c@d.com --confirmed
```

Collected into a list and passed as `"attendees": [...]` in the JSON payload. No `--attendee` = empty list = today's behaviour.

**`workspace/MEMORY_GUIDE.md`** — adds a note in the calendar quick-reference section:

```
When scheduling a meeting with a named person:
1. Run contacts lookup --name "..." to resolve their email address.
2. Pass the resolved email to gcal create --attendee <email> --confirmed.
If contacts lookup returns multiple matches, show them to the user and ask which one to use.
```

**`tests/calendar_proxy/test_models.py`** and **`tests/calendar_proxy/test_server.py`** — new tests (see Testing section).

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

Multiple attendees:
```
gcal create ... --attendee alice@work.com --attendee bob@work.com --mode execute --confirmed
```

---

## Agent Workflow

```
User: "Schedule a meeting with Tim for Sunday at a beer place near Marina"

Agent:
  contacts lookup --name "Tim"
  → {"matches": [{"name": "Tim Smith", "emails": ["tim@example.com"]}], "total": 1}

  gcal create --title "Beers near Marina" --start ... --end ... --attendee tim@example.com --mode execute
  → {"status": "needs_confirmation", "message": "This will send calendar invites to: tim@example.com. Re-submit with confirmed=True to proceed."}

  [Agent shows user and asks for confirmation]

  gcal create ... --attendee tim@example.com --mode execute --confirmed
  → {"event_id": "abc123", "status": "created", "attendees_invited": 1}
```

If `contacts lookup` returns multiple matches, the agent shows them to the user and asks which one to use before proceeding.

---

## Security

- **Confirmation always required when attendees present** — `confirmed=False` with any attendees returns `needs_confirmation`. No bypass.
- **Email validation** — each address validated with regex; comma/semicolon strings rejected to prevent injection.
- **Hard limit of 10 attendees** — prevents the tool from being used for bulk invite spam.
- **`sendUpdates="all"` explicit** — ensures Google sends invite emails; not left to Google's context-dependent default.
- **No seen-domain check** — the `confirmed` gate is the appropriate control for calendar invites (unlike mail-proxy bulk email). Adding seen-domain would break legitimate use of inviting new contacts.
- **Audit logs `attendee_count` only** — email addresses are PII and are never written to the audit log.

---

## Out of Scope

- Updating attendees on existing events (no `gcal update` command yet)
- Removing attendees
- RSVP status tracking
- Optional vs required attendees
- CC / resource attendees

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
- Confirmed request with attendees calls API and returns event id
- Dry run with attendees returns dry-run response without calling API
- `attendee_count` present in audit log entry; addresses not present
- No attendees → `sendUpdates="none"` (existing behaviour unchanged)
