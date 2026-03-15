# Calendar Reminders Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add background polling to calendar-proxy that sends Telegram reminders for upcoming Google Calendar events a configurable lead time before they start.

**Architecture:** New `reminders.py` module encapsulates all reminder logic (event polling, Redis dedup, Telegram notification). `server.py` starts a daemon thread at startup by calling `_start_reminders()`. Redis `setex` prevents duplicate notifications per event. Pattern mirrors `mail-proxy/poller.py`.

**Tech Stack:** Python 3.11, Google Calendar API v3, Redis (`fakeredis` in tests), `urllib` for Telegram (no new deps), `threading.Thread` (daemon)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `services/calendar-proxy/reminders.py` | Create | `remind_once`, `notify_telegram`, `_send_telegram`, `run_forever` |
| `tests/calendar_proxy/test_reminders.py` | Create | 6 unit tests for reminder polling logic |
| `services/calendar-proxy/server.py` | Modify | Add `import threading`, `_start_reminders()`, call at module end, update `get_health()` |
| `tests/calendar_proxy/test_server.py` | Modify | Update `mock_env` + existing reload test, add 3 new tests |
| `docker-compose.yml` | Modify | Add 5 env vars to `calendar-proxy` stanza |
| `.env.example` | Modify | Add `# Calendar Reminders` section with defaults |

**conftest.py:** No change needed — `reminders` is unique to calendar-proxy and is not a shared module name with mail-proxy, so it does not belong in `_SHARED_MODULES`.

---

## Chunk 1: Core reminders module

### Task 1: reminders.py — polling core (TDD)

**Files:**
- Create: `services/calendar-proxy/reminders.py`
- Create: `tests/calendar_proxy/test_reminders.py`

- [ ] **Step 1: Write failing tests**

File: `tests/calendar_proxy/test_reminders.py`

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import fakeredis
import pytest


def _future_event(event_id, minutes_from_now):
    now = datetime.now(timezone.utc)
    start = (now + timedelta(minutes=minutes_from_now)).replace(microsecond=0)
    return {
        "id": event_id,
        "summary": f"Event {event_id}",
        "start": {"dateTime": start.isoformat()},
    }


def _make_service(events):
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": events}
    return service


def test_remind_once_notifies_event_within_window():
    r = fakeredis.FakeRedis()
    event = _future_event("ev1", minutes_from_now=10)
    service = _make_service([event])
    notified = []

    from reminders import remind_once
    remind_once(service, r, lead_minutes=15, notify_fn=notified.extend, calendar_ids=["primary"])

    assert len(notified) == 1
    assert notified[0]["id"] == "ev1"


def test_remind_once_skips_already_reminded():
    r = fakeredis.FakeRedis()
    r.set("gcal:reminded:ev2", b"1")
    event = _future_event("ev2", minutes_from_now=10)
    service = _make_service([event])
    notified = []

    from reminders import remind_once
    remind_once(service, r, lead_minutes=15, notify_fn=notified.extend, calendar_ids=["primary"])

    assert notified == []


def test_remind_once_skips_all_day_events():
    r = fakeredis.FakeRedis()
    all_day = {"id": "ev3", "summary": "Holiday", "start": {"date": "2026-03-20"}}
    service = _make_service([all_day])
    notified = []

    from reminders import remind_once
    remind_once(service, r, lead_minutes=15, notify_fn=notified.extend, calendar_ids=["primary"])

    assert notified == []


def test_remind_once_no_events_does_not_call_notify():
    """When API returns no events within window, notify_fn must not be called."""
    r = fakeredis.FakeRedis()
    service = _make_service([])  # API returns nothing — all events are outside window
    called = []

    from reminders import remind_once
    remind_once(service, r, lead_minutes=15, notify_fn=called.append, calendar_ids=["primary"])

    assert called == []


def test_remind_once_sets_redis_key_before_notify():
    """Crash-safe ordering: Redis key must be set BEFORE notify_fn is called."""
    r = fakeredis.FakeRedis()
    event = _future_event("ev4", minutes_from_now=5)
    service = _make_service([event])
    key_set_before_notify = []

    def checking_notify(events):
        # Key must exist at the moment notify_fn fires
        key_set_before_notify.append(r.exists("gcal:reminded:ev4"))

    from reminders import remind_once
    remind_once(service, r, lead_minutes=15, notify_fn=checking_notify, calendar_ids=["primary"])

    assert key_set_before_notify == [1]


def test_notify_telegram_formats_title_and_start(monkeypatch):
    sent = []
    monkeypatch.setattr("reminders._send_telegram", lambda token, chat_id, text: sent.append(text))

    event = _future_event("ev5", minutes_from_now=10)
    from reminders import notify_telegram
    notify_telegram([event], token="tok", chat_id="123", lead_minutes=15)

    assert len(sent) == 1
    assert "Event ev5" in sent[0]                       # summary present
    assert event["start"]["dateTime"][:10] in sent[0]  # date portion (YYYY-MM-DD) present
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/calendar_proxy/test_reminders.py -v
```

Expected: 6 failures with `ModuleNotFoundError: No module named 'reminders'`

- [ ] **Step 3: Implement `services/calendar-proxy/reminders.py`**

```python
"""Background polling loop for proactive calendar reminders."""
import json
import logging
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable

import redis as redis_lib

logger = logging.getLogger(__name__)

_REMINDED_PREFIX = "gcal:reminded:"


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def notify_telegram(events: list[dict], token: str, chat_id: str, lead_minutes: int) -> None:
    for event in events:
        summary = event.get("summary", "(no title)")
        start_raw = event.get("start", {}).get("dateTime", "")
        try:
            dt = datetime.fromisoformat(start_raw)
            # %-d / %-I are Linux (glibc) only — fine in Docker container
            start_str = dt.strftime("%-d %b at %-I:%M %p")
        except Exception:
            start_str = start_raw
        text = f"📅 <b>{summary}</b>\nStarts {start_str}"
        try:
            _send_telegram(token, chat_id, text)
        except Exception as exc:
            logger.warning("Telegram reminder failed for %s: %s", event.get("id"), exc)


def remind_once(
    service,
    r: redis_lib.Redis,
    lead_minutes: int,
    notify_fn: Callable[[list[dict]], None],
    calendar_ids: list[str],
) -> None:
    """Single reminder poll: find events starting within lead_minutes and notify."""
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(minutes=lead_minutes)).isoformat()

    to_notify = []
    for cal_id in calendar_ids:
        try:
            result = service.events().list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
        except Exception as exc:
            logger.warning("Failed to list events for %s: %s", cal_id, exc)
            continue

        for event in result.get("items", []):
            # Skip all-day events (they have 'date' not 'dateTime')
            if "dateTime" not in event.get("start", {}):
                continue
            event_id = event["id"]
            if not r.exists(f"{_REMINDED_PREFIX}{event_id}"):
                to_notify.append(event)

    if to_notify:
        # Set dedup keys BEFORE notifying (crash-safe)
        ttl = lead_minutes * 60 * 3
        for event in to_notify:
            r.setex(f"{_REMINDED_PREFIX}{event['id']}", ttl, b"1")
        notify_fn(to_notify)


def run_forever(
    *,
    build_service_fn: Callable,
    r: redis_lib.Redis,
    telegram_token: str,
    chat_id: str,
    lead_minutes: int,
    poll_interval: int,
    calendar_ids: list[str],
) -> None:
    """Blocking loop. Run in a daemon thread."""
    if not telegram_token or not chat_id:
        logger.warning("TELEGRAM_TOKEN or ALERT_TELEGRAM_CHAT_ID not set — calendar reminders disabled")
        return

    def _notify(events: list[dict]) -> None:
        notify_telegram(events, token=telegram_token, chat_id=chat_id, lead_minutes=lead_minutes)

    while True:
        try:
            service = build_service_fn()
            remind_once(
                service=service,
                r=r,
                lead_minutes=lead_minutes,
                notify_fn=_notify,
                calendar_ids=calendar_ids,
            )
        except StopIteration:
            raise
        except Exception as exc:
            logger.error("Reminder poller error: %s", exc)
        time.sleep(poll_interval)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/calendar_proxy/test_reminders.py -v
```

Expected: 6 passed

- [ ] **Step 5: Run full calendar_proxy suite to check for regressions**

```
pytest tests/calendar_proxy/ -v
```

Expected: all existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add services/calendar-proxy/reminders.py tests/calendar_proxy/test_reminders.py
git commit -m "feat(calendar-proxy): add reminders module with polling and Telegram notification"
```

---

## Chunk 2: Server wiring

### Task 2: Wire reminders into server.py + update health

**Files:**
- Modify: `services/calendar-proxy/server.py`
- Modify: `tests/calendar_proxy/test_server.py`

**Key constraint:** `_start_reminders()` is called at module level and runs every time `server.py` is imported (or reloaded). The `mock_env` fixture and the `test_dry_run_mode_emits_warning` test both need `GCAL_DISABLE_REMINDERS=true` to prevent the module-level call from attempting thread startup in tests.

- [ ] **Step 1: Add failing tests to `tests/calendar_proxy/test_server.py`**

Append these tests to the file:

```python
def test_start_reminders_disabled_by_flag(monkeypatch, mock_env):
    monkeypatch.setenv("GCAL_DISABLE_REMINDERS", "true")
    monkeypatch.setenv("TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("ALERT_TELEGRAM_CHAT_ID", "123")

    import server
    with patch("server.threading") as mock_threading:
        server._start_reminders()
        mock_threading.Thread.assert_not_called()


def test_start_reminders_no_op_without_telegram(monkeypatch, mock_env):
    monkeypatch.setenv("GCAL_DISABLE_REMINDERS", "false")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("ALERT_TELEGRAM_CHAT_ID", raising=False)

    import server
    with patch("server.threading") as mock_threading:
        server._start_reminders()
        mock_threading.Thread.assert_not_called()


def test_health_includes_reminders_enabled(monkeypatch, mock_env):
    with patch("server.get_redis") as mock_redis, \
         patch("server.token_store") as mock_store:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_store.load.return_value = {}

        import server
        health = server.get_health()
        assert "reminders_enabled" in health
        # mock_env sets GCAL_DISABLE_REMINDERS=true → must be False
        assert health["reminders_enabled"] is False
```

- [ ] **Step 2: Run failing tests**

```
pytest tests/calendar_proxy/test_server.py -v -k "reminders"
```

Expected: 3 failures — `AttributeError: module 'server' has no attribute '_start_reminders'`

- [ ] **Step 3: Update `mock_env` fixture in `test_server.py` to disable reminders**

Add this line inside the `mock_env` fixture body (prevents the module-level `_start_reminders()` from attempting thread startup in tests):

```python
monkeypatch.setenv("GCAL_DISABLE_REMINDERS", "true")
```

- [ ] **Step 4: Also add `GCAL_DISABLE_REMINDERS=true` to `test_dry_run_mode_emits_warning`**

That test uses `importlib.reload(server)` which re-fires the module-level `_start_reminders()` call. Update it to also set the flag:

```python
def test_dry_run_mode_emits_warning(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("GCAL_DRY_RUN", "true")
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("GCAL_AUDIT_LOG_PATH", str(tmp_path / "audit.log"))
    monkeypatch.setenv("GCAL_DISABLE_REMINDERS", "true")   # ← add this line
    import importlib
    import server
    importlib.reload(server)
    captured = capsys.readouterr()
    assert "DRY_RUN" in captured.out
```

- [ ] **Step 5: Add `import threading` to `server.py`**

In the imports block, after `import time`, add:

```python
import threading
```

- [ ] **Step 6: Add `_start_reminders()` function to `server.py`**

Add after the `get_health()` function (before the MCP tool registrations section):

```python
def _start_reminders() -> None:
    """Start background reminder thread if configured. No-op if disabled or Telegram not set."""
    if os.getenv("GCAL_DISABLE_REMINDERS", "false").lower() == "true":
        return
    telegram_token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("ALERT_TELEGRAM_CHAT_ID", "")
    if not telegram_token or not chat_id:
        print(
            "[calendar-proxy] Reminders: TELEGRAM_TOKEN or ALERT_TELEGRAM_CHAT_ID not set — disabled",
            flush=True,
        )
        return
    from reminders import run_forever
    lead_minutes = int(os.getenv("GCAL_REMINDER_LEAD_TIME_MINUTES", "15"))
    poll_interval = int(os.getenv("GCAL_REMINDER_POLL_INTERVAL_SECONDS", "60"))
    r = get_redis()
    t = threading.Thread(
        target=run_forever,
        kwargs=dict(
            build_service_fn=build_google_service,
            r=r,
            telegram_token=telegram_token,
            chat_id=chat_id,
            lead_minutes=lead_minutes,
            poll_interval=poll_interval,
            calendar_ids=list(_allowed_calendars()),
        ),
        daemon=True,
    )
    t.start()
    print(
        f"[calendar-proxy] Reminders: started (lead={lead_minutes}m, poll={poll_interval}s)",
        flush=True,
    )
```

- [ ] **Step 7: Add `reminders_enabled` to `get_health()`**

Inside `get_health()`, after the `health["google_api"]` block, add:

```python
health["reminders_enabled"] = (
    os.getenv("GCAL_DISABLE_REMINDERS", "false").lower() != "true"
    and bool(os.getenv("TELEGRAM_TOKEN"))
    and bool(os.getenv("ALERT_TELEGRAM_CHAT_ID"))
)
```

- [ ] **Step 8: Call `_start_reminders()` at module level**

At the end of `server.py`, just before `if __name__ == "__main__":`, add:

```python
_start_reminders()
```

- [ ] **Step 9: Run tests**

```
pytest tests/calendar_proxy/test_server.py -v
```

Expected: all pass

- [ ] **Step 10: Run full suite**

```
pytest tests/calendar_proxy/ -v
```

Expected: all pass

- [ ] **Step 11: Commit**

```bash
git add services/calendar-proxy/server.py tests/calendar_proxy/test_server.py
git commit -m "feat(calendar-proxy): wire reminders background thread into server startup"
```

---

## Chunk 3: Configuration

### Task 3: docker-compose.yml + .env.example

**Files:**
- Modify: `docker-compose.yml` (after `REDIS_URL` line in `calendar-proxy` stanza, currently line ~121)
- Modify: `.env.example` (after `GCAL_HEALTH_CHECK_GOOGLE=false`)

No tests needed for config-only changes; correctness verified by `docker compose config`.

- [ ] **Step 1: Add env vars to `calendar-proxy` stanza in `docker-compose.yml`**

After the line `- REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379`, add:

```yaml
      - TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
      - ALERT_TELEGRAM_CHAT_ID=${ALERT_TELEGRAM_CHAT_ID:-}
      - GCAL_REMINDER_LEAD_TIME_MINUTES=${GCAL_REMINDER_LEAD_TIME_MINUTES:-15}
      - GCAL_REMINDER_POLL_INTERVAL_SECONDS=${GCAL_REMINDER_POLL_INTERVAL_SECONDS:-60}
      - GCAL_DISABLE_REMINDERS=${GCAL_DISABLE_REMINDERS:-false}
```

- [ ] **Step 2: Add reminder section to `.env.example`**

After `GCAL_HEALTH_CHECK_GOOGLE=false`, add:

```
# ── Calendar Reminders ─────────────────────────────────────────────────────────
# Minutes before event start to send Telegram reminder (default: 15)
GCAL_REMINDER_LEAD_TIME_MINUTES=15

# How often to poll for upcoming events in seconds (default: 60)
GCAL_REMINDER_POLL_INTERVAL_SECONDS=60

# Set to true to disable proactive reminders entirely
# GCAL_DISABLE_REMINDERS=false

# Telegram chat ID for reminder delivery (message @userinfobot in Telegram to find yours)
# Also used for guardrail and Gmail importance alerts
# ALERT_TELEGRAM_CHAT_ID=
```

Note: `ALERT_TELEGRAM_CHAT_ID` is used by guardrail alerts, Gmail importance notifications, and calendar reminders, but was not yet documented in `.env.example`.

- [ ] **Step 3: Verify docker-compose config parses cleanly**

```
docker compose config --quiet
```

Expected: exits 0 with no errors

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(calendar-proxy): add reminder env vars to docker-compose and .env.example"
```

---

## Post-implementation checklist

After all 3 tasks are committed:

- [ ] Run full test suite one final time: `pytest tests/ -v`
- [ ] Review the diff: `git diff main..HEAD`
