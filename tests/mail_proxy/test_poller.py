import time
import pytest
import fakeredis
from unittest.mock import MagicMock, patch, call


def _redis():
    return fakeredis.FakeRedis(decode_responses=False)


def _make_scorer(results=None, circuit_open=False):
    mock = MagicMock()
    mock.score.return_value = (results or [], circuit_open)
    mock.is_circuit_open.return_value = circuit_open
    mock.failure_count.return_value = 0
    return mock


def test_first_run_records_history_id_without_notifying():
    """On first run (no historyId in Redis), record current and send nothing."""
    import poller
    r = _redis()
    mock_service = MagicMock()
    mock_service.users().history().list().execute.return_value = {
        "historyId": "100"
    }
    # simulate users().getProfile() returning historyId
    mock_service.users().getProfile().execute.return_value = {"historyId": "100"}
    notify_calls = []

    poller.poll_once(
        service=mock_service,
        r=r,
        scorer=_make_scorer(),
        notify_fn=lambda msgs: notify_calls.append(msgs),
        poll_label="INBOX",
    )
    assert r.get("gmail:historyId") == b"100"
    assert notify_calls == []  # no notifications on first run


def test_poll_skips_seen_messages():
    """Messages already in gmail:seen:{id} are not scored or notified."""
    import poller
    r = _redis()
    r.set("gmail:historyId", b"50")
    r.setex("gmail:seen:msg-old", 3600, b"1")

    mock_service = MagicMock()
    mock_service.users().history().list().execute.return_value = {
        "history": [{"messagesAdded": [{"message": {"id": "msg-old"}}]}],
        "historyId": "51",
    }
    mock_service.users().messages().get().execute.return_value = {
        "id": "msg-old",
        "threadId": "t1",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {"headers": [
            {"name": "From", "value": "a@b.com"},
            {"name": "Subject", "value": "Test"},
            {"name": "Date", "value": "Mon, 13 Mar 2026 10:00:00 +0000"},
        ]},
        "snippet": "snippet text",
    }

    scored = []
    poller.poll_once(
        service=mock_service,
        r=r,
        scorer=_make_scorer(),
        notify_fn=lambda msgs: scored.extend(msgs),
        poll_label="INBOX",
    )
    assert scored == []  # msg-old was deduped


def test_poll_updates_history_id_after_processing():
    """historyId in Redis updated to latest after successful poll."""
    import poller
    r = _redis()
    r.set("gmail:historyId", b"50")

    mock_service = MagicMock()
    mock_service.users().history().list().execute.return_value = {
        "history": [],
        "historyId": "75",
    }
    poller.poll_once(
        service=mock_service, r=r, scorer=_make_scorer(),
        notify_fn=lambda _: None, poll_label="INBOX",
    )
    assert r.get("gmail:historyId") == b"75"


def test_poll_sets_dedup_key_before_notify():
    """Dedup key set before notify_fn called — prevents double-notify on crash/restart."""
    import poller
    r = _redis()
    r.set("gmail:historyId", b"50")

    mock_service = MagicMock()
    mock_service.users().history().list().execute.return_value = {
        "history": [{"messagesAdded": [{"message": {"id": "msg-new"}}]}],
        "historyId": "51",
    }
    mock_service.users().messages().get().execute.return_value = {
        "id": "msg-new", "threadId": "t1", "labelIds": ["INBOX", "UNREAD"],
        "payload": {"headers": [
            {"name": "From", "value": "x@y.com"},
            {"name": "Subject", "value": "Hi"},
            {"name": "Date", "value": "Mon, 13 Mar 2026 10:00:00 +0000"},
        ]},
        "snippet": "hello",
    }

    dedup_set_at = {}
    original_setex = r.setex

    def tracking_setex(name, *args, **kwargs):
        dedup_set_at[name] = True
        return original_setex(name, *args, **kwargs)

    r.setex = tracking_setex

    notify_calls = []
    scorer = _make_scorer(results=[
        {"message_id": "msg-new", "score": 9, "summary": "Important"}
    ])

    poller.poll_once(
        service=mock_service, r=r, scorer=scorer,
        notify_fn=lambda msgs: notify_calls.append(list(msgs)),
        poll_label="INBOX",
    )
    assert "gmail:seen:msg-new" in dedup_set_at  # str key, not bytes
    assert len(notify_calls) == 1


def test_run_forever_sends_circuit_breaker_alert_once(monkeypatch):
    """When circuit opens, one Telegram alert is sent — not on every subsequent poll."""
    import poller
    r = _redis()
    sent_alerts = []
    call_count = [0]

    # poll_once will be called via run_forever; stop after 3 iterations
    def limited_poll_once(**kwargs):
        call_count[0] += 1
        if call_count[0] > 3:
            raise StopIteration("done")
        # First run: record historyId (no-op notification)
        r.set("gmail:historyId", b"100")

    scorer = MagicMock()
    # is_circuit_open: was_open=False then now_open=True on first real iteration → alert fires
    # subsequent iterations: was_open=True, now_open=True → no second alert
    scorer.is_circuit_open.side_effect = [False, True, True, True, True, True, True]

    with patch("poller.poll_once", limited_poll_once), \
         patch("poller._send_telegram", lambda token, chat_id, text: sent_alerts.append(text)), \
         pytest.raises(StopIteration):
        poller.run_forever(
            build_service_fn=MagicMock(return_value=MagicMock()),
            token_store=MagicMock(),
            r=r,
            scorer=scorer,
            telegram_token="tok",
            chat_id="12345",
            poll_interval=0,
            poll_label="INBOX",
        )

    # Exactly one alert sent when circuit opened; no repeats
    assert len(sent_alerts) == 1
    assert "scorer unavailable" in sent_alerts[0]


def test_send_telegram_notification_formats_message():
    import poller
    sent = []

    def fake_send(token, chat_id, text):
        sent.append({"token": token, "chat_id": chat_id, "text": text})

    with patch("poller._send_telegram", fake_send):
        poller.notify_telegram(
            messages=[{
                "message_id": "m1",
                "from_addr": "Alice <alice@example.com>",
                "subject": "Budget approval",
                "summary": "Alice needs Q4 budget signed off.",
            }],
            token="bot-token",
            chat_id="12345",
        )

    assert len(sent) == 1
    assert "Alice" in sent[0]["text"]
    assert "Budget approval" in sent[0]["text"]
    assert "Alice needs Q4 budget signed off." in sent[0]["text"]


def test_poll_once_uses_account_namespaced_history_key():
    """When account='personal', historyId stored under gmail:historyId:personal."""
    import poller
    r = _redis()
    mock_service = MagicMock()
    mock_service.users().getProfile().execute.return_value = {"historyId": "200"}

    poller.poll_once(
        service=mock_service,
        r=r,
        scorer=_make_scorer(),
        notify_fn=lambda msgs: None,
        poll_label="INBOX",
        account="personal",
    )
    assert r.get("gmail:historyId:personal") == b"200"
    assert r.get("gmail:historyId") is None  # legacy key not touched


def test_poll_once_uses_account_namespaced_seen_key():
    """Dedup key uses account namespace."""
    import poller
    r = _redis()
    r.set("gmail:historyId:jobs", b"50")

    mock_service = MagicMock()
    mock_service.users().history().list().execute.return_value = {
        "history": [{"messagesAdded": [{"message": {"id": "msg-new"}}]}],
        "historyId": "51",
    }
    mock_service.users().messages().get().execute.return_value = {
        "id": "msg-new", "threadId": "t1",
        "payload": {"headers": [
            {"name": "From", "value": "a@b.com"},
            {"name": "Subject", "value": "Hi"},
            {"name": "Date", "value": "Mon"},
        ]},
        "snippet": "hello",
    }
    scored = [{"message_id": "msg-new", "from_addr": "a@b.com",
               "subject": "Hi", "score": 9}]
    notify_calls = []

    poller.poll_once(
        service=mock_service, r=r,
        scorer=_make_scorer(results=scored),
        notify_fn=lambda msgs: notify_calls.append(msgs),
        poll_label="INBOX",
        account="jobs",
    )
    # Dedup key should be namespaced
    assert r.exists("gmail:seen:jobs:msg-new")
    assert not r.exists("gmail:seen:msg-new")


def test_poll_once_legacy_keys_when_account_empty():
    """account='' → old un-prefixed key names (backward compat)."""
    import poller
    r = _redis()
    mock_service = MagicMock()
    mock_service.users().getProfile().execute.return_value = {"historyId": "300"}

    poller.poll_once(
        service=mock_service, r=r,
        scorer=_make_scorer(),
        notify_fn=lambda msgs: None,
        poll_label="INBOX",
        account="",
    )
    assert r.get("gmail:historyId") == b"300"
