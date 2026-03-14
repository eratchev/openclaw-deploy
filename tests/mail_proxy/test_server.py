import json
import os
import pytest
import fakeredis
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet
from starlette.testclient import TestClient


def _make_app(monkeypatch, configured=True):
    """Build a TestClient with server in configured or degraded mode."""
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    if configured:
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    else:
        monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY", raising=False)

    import importlib
    import server
    importlib.reload(server)
    return TestClient(server.mcp.get_app()), server


def test_health_returns_ok_when_degraded(monkeypatch, tmp_path):
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY", raising=False)
    import importlib, server as s_mod
    importlib.reload(s_mod)
    client = TestClient(s_mod.mcp.get_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False


def test_call_returns_not_configured_when_degraded(monkeypatch):
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY", raising=False)
    import importlib, server as s_mod
    importlib.reload(s_mod)
    client = TestClient(s_mod.mcp.get_app())
    resp = client.post("/call", json={"tool": "list", "args": {}})
    assert resp.status_code == 200
    assert resp.json()["error"] == "not_configured"


def test_call_list_returns_messages(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")

    fake_messages = [{"message_id": "m1", "thread_id": "t1", "from_addr": "a@b.com",
                      "subject": "Hi", "snippet": "hello", "date": "Mon, 13 Mar 2026",
                      "unread": True}]
    import importlib, server as s_mod
    importlib.reload(s_mod)
    s_mod.token_store = MagicMock()

    with patch("server.gmail_client.list_messages", return_value=fake_messages), \
         patch("server.gmail_client.build_service", return_value=MagicMock()), \
         patch("server.get_redis", return_value=fakeredis.FakeRedis()):
        client = TestClient(s_mod.mcp.get_app())
        resp = client.post("/call", json={"tool": "list", "args": {"limit": 5}})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["message_id"] == "m1"


def test_call_send_denied_without_confirmation(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")

    import importlib, server as s_mod, fakeredis
    importlib.reload(s_mod)
    s_mod.token_store = MagicMock()

    r = fakeredis.FakeRedis(decode_responses=False)
    import time
    r.zadd("gmail:seen_domains", {"example.com": time.time()})

    with patch("server.get_redis", return_value=r):
        client = TestClient(s_mod.mcp.get_app())
        resp = client.post("/call", json={
            "tool": "send",
            "args": {"to": "bob@example.com", "subject": "Hi", "body": "Hello",
                     "confirmed": False}
        })
    data = resp.json()
    assert data["status"] == "needs_confirmation"


def test_call_unknown_tool_returns_404(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    import importlib, server as s_mod
    importlib.reload(s_mod)
    s_mod.token_store = MagicMock()
    client = TestClient(s_mod.mcp.get_app())
    resp = client.post("/call", json={"tool": "nonexistent", "args": {}})
    assert resp.status_code == 404


def test_health_returns_redis_status(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    import importlib, server as s_mod
    importlib.reload(s_mod)
    s_mod.token_store = MagicMock()
    with patch("server.get_redis", return_value=fakeredis.FakeRedis()):
        client = TestClient(s_mod.mcp.get_app())
        resp = client.get("/health")
    assert resp.json()["redis"] == "ok"
