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
        monkeypatch.delenv("GMAIL_ACCOUNTS", raising=False)

    import importlib
    import server
    importlib.reload(server)
    return TestClient(server.mcp.get_app()), server


def test_health_returns_ok_when_degraded(monkeypatch, tmp_path):
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("GMAIL_ACCOUNTS", raising=False)
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
    monkeypatch.delenv("GMAIL_ACCOUNTS", raising=False)
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
    mock_store = MagicMock()
    s_mod.token_stores = {"": mock_store}
    s_mod.DEFAULT_ACCOUNT = ""
    s_mod.CONFIGURED = True

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
    mock_store = MagicMock()
    s_mod.token_stores = {"": mock_store}
    s_mod.DEFAULT_ACCOUNT = ""
    s_mod.CONFIGURED = True

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
    mock_store = MagicMock()
    s_mod.token_stores = {"": mock_store}
    s_mod.DEFAULT_ACCOUNT = ""
    s_mod.CONFIGURED = True
    client = TestClient(s_mod.mcp.get_app())
    resp = client.post("/call", json={"tool": "nonexistent", "args": {}})
    assert resp.status_code == 404


def test_health_returns_redis_status(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    import importlib, server as s_mod
    importlib.reload(s_mod)
    mock_store = MagicMock()
    s_mod.token_stores = {"": mock_store}
    s_mod.DEFAULT_ACCOUNT = ""
    s_mod.CONFIGURED = True
    with patch("server.get_redis", return_value=fakeredis.FakeRedis()):
        client = TestClient(s_mod.mcp.get_app())
        resp = client.get("/health")
    assert resp.json()["redis"] == "ok"


def test_health_returns_accounts_dict(monkeypatch):
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_JOBS", key2)
    import importlib, server as s_mod
    importlib.reload(s_mod)
    client = TestClient(s_mod.mcp.get_app())
    resp = client.get("/health")
    data = resp.json()
    assert data["configured"] is True
    assert "accounts" in data
    assert "personal" in data["accounts"]
    assert "jobs" in data["accounts"]


def test_call_returns_error_for_unknown_account(monkeypatch):
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal")
    key1 = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    import importlib, server as s_mod
    importlib.reload(s_mod)
    client = TestClient(s_mod.mcp.get_app())
    resp = client.post("/call?account=nonexistent", json={"tool": "list", "args": {}})
    data = resp.json()
    assert data["error"] == "unknown_account"
    assert "available" in data


def test_call_uses_default_account_when_no_param(monkeypatch):
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_JOBS", key2)
    import importlib, server as s_mod
    importlib.reload(s_mod)
    # Default account is first = "personal"
    assert s_mod.DEFAULT_ACCOUNT == "personal"


def test_start_poller_uses_openai_key_and_model(monkeypatch):
    """Scorer must be initialized with OPENAI_API_KEY and an OpenAI model.

    Regression: server previously read ANTHROPIC_API_KEY and defaulted to a
    Claude model after the scorer was switched to the OpenAI client. That
    caused 401s and silent circuit-breaker tripping.
    """
    monkeypatch.delenv("GMAIL_DISABLE_POLLER", raising=False)
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal")
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL",
                       Fernet.generate_key().decode())
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-must-not-be-used")
    monkeypatch.delenv("GMAIL_SCORER_MODEL", raising=False)

    captured = {}

    class _FakeScorer:
        def __init__(self, *, api_key, model, threshold):
            captured["api_key"] = api_key
            captured["model"] = model
            captured["threshold"] = threshold

    with patch("scorer.ImportanceScorer", _FakeScorer), \
         patch("threading.Thread") as fake_thread:
        fake_thread.return_value = MagicMock()
        import importlib, server as s_mod
        importlib.reload(s_mod)

    assert captured["api_key"] == "sk-openai-test"
    assert captured["model"].startswith("gpt-")
