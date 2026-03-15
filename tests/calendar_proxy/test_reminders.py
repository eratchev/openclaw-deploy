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
