"""Prompt injection and novel-domain block security tests."""
import time
import pytest
import fakeredis
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet
from starlette.testclient import TestClient


def _configured_client(monkeypatch):
    """Set up a test client with a configured token store."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    import importlib
    import server as s
    importlib.reload(s)
    s.token_store = MagicMock()
    return TestClient(s.mcp.get_app()), s


def test_send_blocked_for_novel_domain(monkeypatch):
    """send to a domain not in seen-domains cache is hard-denied."""
    client, s = _configured_client(monkeypatch)
    r = fakeredis.FakeRedis(decode_responses=False)
    # Do NOT add any domains to seen-domains

    with patch("server.get_redis", return_value=r):
        resp = client.post("/call", json={
            "tool": "send",
            "args": {"to": "hacker@evil.com", "subject": "Hi", "body": "...",
                     "confirmed": True}
        })
    data = resp.json()
    assert data["status"] == "denied"
    assert "domain_not_allowed" in data["reason"]


def test_send_allowed_for_seen_domain(monkeypatch):
    """send to a seen domain is allowed (proceeds past novel-domain check)."""
    client, s = _configured_client(monkeypatch)
    r = fakeredis.FakeRedis(decode_responses=False)
    r.zadd("gmail:seen_domains", {"example.com": time.time()})

    fake_send = MagicMock(return_value="new-msg-id")
    with patch("server.get_redis", return_value=r), \
         patch("server.gmail_client.build_service", return_value=MagicMock()), \
         patch("server.gmail_client.send_email", fake_send):
        resp = client.post("/call", json={
            "tool": "send",
            "args": {"to": "bob@example.com", "subject": "Hi", "body": "Hello",
                     "confirmed": True}
        })
    assert resp.json()["status"] == "sent"


def test_list_response_does_not_include_full_body(monkeypatch):
    """list operation returns snippets only — no full email body in response."""
    client, s = _configured_client(monkeypatch)
    fake_messages = [{
        "message_id": "m1", "thread_id": "t1", "from_addr": "a@b.com",
        "subject": "Hi", "snippet": "short snippet", "date": "Mon, 13 Mar 2026",
        "unread": True,
        # 'body' should NOT be present in list output
    }]
    with patch("server.gmail_client.list_messages", return_value=fake_messages), \
         patch("server.gmail_client.build_service", return_value=MagicMock()), \
         patch("server.get_redis", return_value=fakeredis.FakeRedis()):
        resp = client.post("/call", json={"tool": "list", "args": {}})
    data = resp.json()
    for msg in data:
        assert "body" not in msg


def test_multiple_recipients_rejected_at_model_layer(monkeypatch):
    """Pydantic rejects comma-separated recipients before any API call."""
    client, s = _configured_client(monkeypatch)
    r = fakeredis.FakeRedis(decode_responses=False)
    with patch("server.get_redis", return_value=r):
        resp = client.post("/call", json={
            "tool": "send",
            "args": {"to": "a@example.com,b@example.com", "subject": "Hi",
                     "body": "spam", "confirmed": True}
        })
    # Either validation error or error response — should never reach send
    data = resp.json()
    assert "error" in data or data.get("status") == "denied"


def test_prompt_injection_in_importance_scorer_prompt():
    """Scorer system prompt includes 'untrusted data' instruction."""
    import scorer
    s = scorer.ImportanceScorer(api_key="k", model="m", threshold=7)
    # Check the system prompt content that will be sent to Claude
    # We verify the _call_api method embeds the safety instruction
    import inspect
    source = inspect.getsource(scorer.ImportanceScorer._call_api)
    assert "untrusted" in source
