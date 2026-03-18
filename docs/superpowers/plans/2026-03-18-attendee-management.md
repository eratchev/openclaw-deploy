# Attendee Management Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add attendee support to the Google Calendar proxy so the agent can invite people when creating calendar events.

**Architecture:** Six files are modified — no new services, no new OAuth scopes. `CreateEventInput` gains an `attendees` field; `enforce()` gains a `has_attendees` parameter that triggers confirmation; `handle_create_event` passes attendees to the Google Calendar API with `sendUpdates="all"`; the `gcal` CLI gets a repeatable `--attendee` flag; audit logs scrub attendee emails; and the agent guide (`MEMORY_GUIDE.md`) documents the contacts-lookup → gcal-create workflow.

**Tech Stack:** Python 3.12, Pydantic v2, FastMCP, Google Calendar API v3, pytest, fakeredis

---

## File Structure

Files to modify:
- `services/calendar-proxy/models.py` — add `attendees` field + validator to `CreateEventInput`
- `services/calendar-proxy/audit.py` — add `"attendees"` to `_NEVER_LOG`
- `services/calendar-proxy/policies.py` — add `has_attendees` parameter to `enforce()`
- `services/calendar-proxy/server.py` — update `_run_write_pipeline`, `handle_create_event`, and MCP `create_event`
- `services/calendar-proxy/scripts/gcal` — add `_collect_flags()`, `--attendee` flag, update docstring
- `workspace/MEMORY_GUIDE.md` — add contacts-lookup → gcal-create workflow

Tests to modify:
- `tests/calendar_proxy/test_models.py` — add 7 new tests for `attendees` field
- `tests/calendar_proxy/test_policies_enforce.py` — add 2 new tests for `has_attendees`
- `tests/calendar_proxy/test_server.py` — add 5 new tests for server behaviour

---

## Chunk 1: Model + Audit

### Task 1: Add `attendees` field to `CreateEventInput`

**Files:**
- Modify: `services/calendar-proxy/models.py`
- Test: `tests/calendar_proxy/test_models.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/calendar_proxy/test_models.py` (after the existing `CreateEventInput` tests):

```python
# ── CreateEventInput — attendees ──────────────────────────────────────────────

def _base_event(**kwargs):
    d = _future_date()
    defaults = dict(
        title="Test",
        start=f"{d}T10:00:00+00:00",
        end=f"{d}T11:00:00+00:00",
        execution_mode="dry_run",
    )
    defaults.update(kwargs)
    return defaults


def test_attendees_empty_by_default():
    e = CreateEventInput(**_base_event())
    assert e.attendees == []


def test_attendees_valid_single():
    e = CreateEventInput(**_base_event(attendees=["tim@example.com"]))
    assert e.attendees == ["tim@example.com"]


def test_attendees_valid_multiple():
    addrs = [f"user{i}@example.com" for i in range(5)]
    e = CreateEventInput(**_base_event(attendees=addrs))
    assert len(e.attendees) == 5


def test_attendees_rejects_invalid_email():
    with pytest.raises(ValidationError, match="Invalid email"):
        CreateEventInput(**_base_event(attendees=["not-an-email"]))


def test_attendees_rejects_comma_separated():
    with pytest.raises(ValidationError, match="single email"):
        CreateEventInput(**_base_event(attendees=["a@b.com,c@d.com"]))


def test_attendees_rejects_semicolon_separated():
    with pytest.raises(ValidationError, match="single email"):
        CreateEventInput(**_base_event(attendees=["a@b.com;c@d.com"]))


def test_attendees_rejects_over_10():
    addrs = [f"user{i}@example.com" for i in range(11)]
    with pytest.raises(ValidationError):
        CreateEventInput(**_base_event(attendees=addrs))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/calendar_proxy/test_models.py -k "attendees" -v
```

Expected: FAIL — `CreateEventInput` has no `attendees` attribute.

- [ ] **Step 3: Implement attendees field in models.py**

**3a.** Add `Field` to the pydantic import line:

```python
from pydantic import BaseModel, Field, field_validator, model_validator
```

**3b.** Add the email regex constant after `_max_past_hours()`. (`import re` is already present at line 2 — no import change needed.)

```python
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
```

**3c.** Add the `attendees` field and validator inside `CreateEventInput`, after `confirmed: bool = False`:

```python
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

- [ ] **Step 4: Run failing tests to verify they now pass**

```bash
pytest tests/calendar_proxy/test_models.py -k "attendees" -v
```

Expected: 7 tests PASS.

- [ ] **Step 5: Run full model test suite to catch regressions**

```bash
pytest tests/calendar_proxy/test_models.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add services/calendar-proxy/models.py tests/calendar_proxy/test_models.py
git commit -m "feat(calendar): add attendees field to CreateEventInput"
```

---

### Task 2: Scrub `attendees` from audit log

**Files:**
- Modify: `services/calendar-proxy/audit.py`
- Test: `tests/calendar_proxy/test_server.py`

- [ ] **Step 1: Write failing test**

Add to `tests/calendar_proxy/test_server.py` (can go after the existing imports, before the first test):

```python
def test_attendees_scrubbed_from_audit_args():
    """_scrub_args must strip attendee email addresses."""
    import importlib
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))
    import audit as audit_mod
    importlib.reload(audit_mod)
    args = {"title": "Beers", "attendees": ["tim@example.com"], "confirmed": True}
    scrubbed = audit_mod._scrub_args(args)
    assert "attendees" not in scrubbed
    assert scrubbed["title"] == "Beers"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/calendar_proxy/test_server.py::test_attendees_scrubbed_from_audit_args -v
```

Expected: FAIL — `attendees` is present in the scrubbed output (not yet in `_NEVER_LOG`).

- [ ] **Step 3: Add `"attendees"` to `_NEVER_LOG` in audit.py**

Change line 8 of `services/calendar-proxy/audit.py`:

```python
_NEVER_LOG = {"token", "key", "secret", "password", "credential", "attendees"}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/calendar_proxy/test_server.py::test_attendees_scrubbed_from_audit_args -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/calendar-proxy/audit.py tests/calendar_proxy/test_server.py
git commit -m "feat(calendar): scrub attendees from audit log"
```

---

## Chunk 2: Policy + Server

### Task 3: Add `has_attendees` to `enforce()`

**Files:**
- Modify: `services/calendar-proxy/policies.py`
- Test: `tests/calendar_proxy/test_policies_enforce.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/calendar_proxy/test_policies_enforce.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/calendar_proxy/test_policies_enforce.py -k "attendees" -v
```

Expected: FAIL — `enforce()` does not accept `has_attendees`.

- [ ] **Step 3: Implement `has_attendees` in policies.py**

**3a.** Add `has_attendees: bool = False` to the `enforce()` signature:

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

**3b.** Add the `has_attendees` trigger AFTER the `if confirmed` shortcut and BEFORE `if is_delete`:

```python
    # Confirmation required (skipped when caller has already confirmed)
    if confirmed:
        return "safe_to_execute", None

    if has_attendees:
        return "needs_confirmation", "attendees_present"

    if is_delete:
        return "needs_confirmation", None
```

- [ ] **Step 4: Run failing tests to verify they now pass**

```bash
pytest tests/calendar_proxy/test_policies_enforce.py -k "attendees" -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Run full policy test suite to catch regressions**

```bash
pytest tests/calendar_proxy/test_policies_enforce.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add services/calendar-proxy/policies.py tests/calendar_proxy/test_policies_enforce.py
git commit -m "feat(calendar): add has_attendees confirmation gate to enforce()"
```

---

### Task 4: Update server.py — pipeline, handler, MCP registration

**Files:**
- Modify: `services/calendar-proxy/server.py`
- Test: `tests/calendar_proxy/test_server.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/calendar_proxy/test_server.py`:

```python
def test_create_event_needs_confirmation_when_attendees_present(monkeypatch, mock_env):
    """Unconfirmed create with attendees returns needs_confirmation."""
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_build.return_value = MagicMock()

        import server
        d = _future_date()
        result = server.handle_create_event({
            "title": "Beers",
            "start": f"{d}T17:00:00+00:00",
            "end": f"{d}T18:00:00+00:00",
            "execution_mode": "execute",
            "attendees": ["tim@example.com"],
        })

    assert result["status"] == "needs_confirmation"
    mock_build.return_value.events.return_value.insert.assert_not_called()


def test_create_event_with_attendees_confirmed(monkeypatch, mock_env):
    """Confirmed create with attendees calls API with attendees body and sendUpdates=all."""
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_service = MagicMock()
        mock_service.events.return_value.insert.return_value.execute.return_value = {"id": "ev123"}
        mock_build.return_value = mock_service

        import server
        d = _future_date()
        result = server.handle_create_event({
            "title": "Beers",
            "start": f"{d}T17:00:00+00:00",
            "end": f"{d}T18:00:00+00:00",
            "execution_mode": "execute",
            "confirmed": True,
            "attendees": ["tim@example.com"],
        })

    assert result["status"] == "safe_to_execute"
    assert result["attendees_invited"] == 1
    insert_kwargs = mock_service.events.return_value.insert.call_args.kwargs
    assert insert_kwargs["sendUpdates"] == "all"
    assert insert_kwargs["body"]["attendees"] == [{"email": "tim@example.com"}]


def test_create_event_no_attendees_uses_send_updates_none(monkeypatch, mock_env):
    """Events without attendees set sendUpdates=none."""
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_service = MagicMock()
        mock_service.events.return_value.insert.return_value.execute.return_value = {"id": "ev999"}
        mock_build.return_value = mock_service

        import server
        d = _future_date()
        result = server.handle_create_event({
            "title": "Solo",
            "start": f"{d}T10:00:00+00:00",
            "end": f"{d}T11:00:00+00:00",
            "execution_mode": "execute",
            "confirmed": True,
        })

    assert result["status"] == "safe_to_execute"
    insert_kwargs = mock_service.events.return_value.insert.call_args.kwargs
    assert insert_kwargs["sendUpdates"] == "none"


def test_create_event_dry_run_with_attendees(monkeypatch, mock_env):
    """Dry run with attendees returns dry_run status, never calls API."""
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_build.return_value = MagicMock()

        import server
        d = _future_date()
        result = server.handle_create_event({
            "title": "Beers",
            "start": f"{d}T17:00:00+00:00",
            "end": f"{d}T18:00:00+00:00",
            "execution_mode": "dry_run",
            "attendees": ["tim@example.com"],
        })

    assert result["status"] == "dry_run"
    mock_build.return_value.events.return_value.insert.assert_not_called()


def test_attendees_absent_from_audit_write_call(monkeypatch, mock_env):
    """audit.write must not receive attendee emails in its args kwarg."""
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis, \
         patch("server.audit") as mock_audit:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_service = MagicMock()
        mock_service.events.return_value.insert.return_value.execute.return_value = {"id": "ev123"}
        mock_build.return_value = mock_service

        import server
        d = _future_date()
        result = server.handle_create_event({
            "title": "Beers",
            "start": f"{d}T17:00:00+00:00",
            "end": f"{d}T18:00:00+00:00",
            "execution_mode": "execute",
            "confirmed": True,
            "attendees": ["tim@example.com"],
        })

    assert result["attendees_invited"] == 1
    for call in mock_audit.write.call_args_list:
        assert "attendees" not in call.kwargs.get("args", {})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/calendar_proxy/test_server.py -k "attendees" -v
```

Expected: FAIL — server doesn't handle attendees yet.

- [ ] **Step 3: Implement server.py changes**

**Change 1 — `_run_write_pipeline`: pass `has_attendees` to `enforce()`.**

Find the `enforce()` call block (around lines 112-121). Replace it with:

```python
    status, reason = enforce(
        impact or type("I", (), {"overlaps_existing": False, "overlapping_events": [],
                                  "outside_business_hours": False, "is_weekend": False,
                                  "duration_minutes": 0, "recurring": False,
                                  "recurrence_instances_checked": 0, "work_calendar": False})(),
        calendar_id=calendar_id,
        in_allowlist=in_allowlist,
        is_delete=is_delete,
        confirmed=confirmed,
        has_attendees=bool(getattr(event_input, "attendees", [])),
    )
```

**Change 2 — `handle_create_event`: add attendees to API body and set sendUpdates.**

Find the existing `service.events().insert(...)` call (line 188). Replace the insert call and return statement with:

```python
    if event_input.attendees:
        body["attendees"] = [{"email": addr} for addr in event_input.attendees]
    created = service.events().insert(
        calendarId=event_input.calendar_id,
        body=body,
        sendUpdates="all" if event_input.attendees else "none",
    ).execute()
    event_id = created["id"]
    idem_key = event_input.idempotency_key or idempotency_key_for("create", event_input.model_dump())
    record_idempotency(get_redis(), idem_key, event_id=event_id)
    request_id = str(uuid.uuid4())
    audit.write(request_id=request_id, tool="create_event", execution_mode="execute",
                session_id="", args=event_input.model_dump(), status="created", event_id=event_id, duration_ms=0)
    return {
        "request_id": request_id,
        "status": "safe_to_execute",
        "event_id": event_id,
        "attendees_invited": len(event_input.attendees),
    }
```

**Change 3 — MCP `create_event` registration: add `attendees` parameter.**

Replace the existing `@mcp.tool()` decorated `create_event` function with:

```python
@mcp.tool()
def create_event(title: str, start: str, end: str, execution_mode: str,
                 calendar_id: str = "primary", description: str = None,
                 recurrence_rrule: str = None, idempotency_key: str = None,
                 confirmed: bool = False,
                 attendees: list[str] = None) -> dict:
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
    if attendees:
        args["attendees"] = attendees
    return handle_create_event(args)
```

- [ ] **Step 4: Run failing tests to verify they now pass**

```bash
pytest tests/calendar_proxy/test_server.py -k "attendees" -v
```

Expected: 4 new tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
make test
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add services/calendar-proxy/server.py tests/calendar_proxy/test_server.py
git commit -m "feat(calendar): wire attendees through pipeline and handle_create_event"
```

---

## Chunk 3: CLI + Agent Guide + Deploy

### Task 5: Add `--attendee` flag to gcal CLI

**Files:**
- Modify: `services/calendar-proxy/scripts/gcal`

No dedicated test file for the CLI — it is a thin HTTP wrapper. Server-side logic is fully covered by test_server.py. A syntax check is sufficient.

- [ ] **Step 1: Update docstring**

Replace the first line of the docstring (the `create` usage line) in `services/calendar-proxy/scripts/gcal`:

```
  gcal create  --title TITLE --start ISO --end ISO --mode (execute|dry_run)
               [--confirmed] [--calendar-id ID] [--description TEXT] [--rrule RULE]
               [--idem-key KEY] [--attendee EMAIL] ...
```

- [ ] **Step 2: Add `_collect_flags` helper**

After the `_parse_flag` function (line 56), add:

```python
def _collect_flags(args: list[str], flag: str) -> list[str]:
    """Return all values following repeated --flag occurrences."""
    result = []
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            result.append(args[i + 1])
    return result
```

- [ ] **Step 3: Add `--attendee` parsing to the create branch**

In the `create` branch, after the `--idem-key` block (after line 86), add:

```python
        attendees = _collect_flags(rest, "--attendee")
        if attendees:
            args["attendees"] = attendees
```

- [ ] **Step 4: Verify syntax**

```bash
python3 -c "import ast; ast.parse(open('services/calendar-proxy/scripts/gcal').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add services/calendar-proxy/scripts/gcal
git commit -m "feat(calendar): add --attendee flag to gcal CLI"
```

---

### Task 6: Update MEMORY_GUIDE and deploy

**Files:**
- Modify: `workspace/MEMORY_GUIDE.md`

- [ ] **Step 1: Add attendee workflow to MEMORY_GUIDE.md**

In the Google Calendar section, after the `#### Workflow (mandatory: dry_run first)` block and before `#### Quick reference`, insert:

```markdown
#### Scheduling a meeting with someone

When asked to schedule a meeting with a named person:

1. `contacts lookup --name "..."` → resolve their email address
   - Multiple matches: show all to user, ask which to use
   - Zero matches: ask user to provide the email address directly
   - Error (e.g. scope_missing): surface the error before proceeding
2. `gcal create ... --attendee <email> --mode execute` → expect `needs_confirmation`
3. Show user: "This will create the event and invite <email>. Confirm?"
4. Re-run with `--confirmed` to send the invite
```

Also add attendee examples to the `#### Quick reference` block:

```
gcal create --title "Beers" --start "..." --end "..." --attendee tim@example.com --mode execute
gcal create --title "Team sync" --start "..." --end "..." --attendee a@b.com --attendee c@d.com --mode execute --confirmed
```

- [ ] **Step 2: Run full test suite**

```bash
make test
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add workspace/MEMORY_GUIDE.md
git commit -m "docs(agent): add attendee scheduling workflow to MEMORY_GUIDE"
```

- [ ] **Step 4: Deploy CLI binary to VPS**

```bash
make deploy-clis HOST=<vps-host>
```

Expected: `[ok] gcal` in output.

- [ ] **Step 5: Deploy services to VPS**

```bash
make deploy HOST=<vps-host>
```

- [ ] **Step 6: Verify on VPS**

```bash
make doctor HOST=<vps-host>
```

End-to-end test via gcal CLI (use a near-future date):

```bash
# Step 1: dry run with attendee — should return dry_run status, no invite
gcal create --title "Attendee test" \
    --start "2026-03-20T10:00:00-07:00" \
    --end   "2026-03-20T11:00:00-07:00" \
    --attendee <your-email> \
    --mode dry_run

# Step 2: execute without --confirmed — should return needs_confirmation
gcal create --title "Attendee test" \
    --start "2026-03-20T10:00:00-07:00" \
    --end   "2026-03-20T11:00:00-07:00" \
    --attendee <your-email> \
    --mode execute

# Step 3: execute with --confirmed — should return safe_to_execute + attendees_invited: 1
gcal create --title "Attendee test" \
    --start "2026-03-20T10:00:00-07:00" \
    --end   "2026-03-20T11:00:00-07:00" \
    --attendee <your-email> \
    --mode execute \
    --confirmed
```

Expected final response: `{"status": "safe_to_execute", "attendees_invited": 1, "event_id": "..."}` and an invite email arrives.
