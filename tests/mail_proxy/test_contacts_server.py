import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/mail-proxy'))

import pytest
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet
from starlette.testclient import TestClient


def _make_app(monkeypatch, configured=True):
    """Reload server module in configured or degraded (no token) state."""
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


def test_contacts_lookup_returns_matches(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    fake_matches = [{"name": "Alice Johnson", "emails": ["alice@work.com"], "phones": []}]
    with patch("server.people_client.build_service", return_value=MagicMock()), \
         patch("server.people_client.search_contacts", return_value=fake_matches):
        resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Alice"}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["matches"][0]["name"] == "Alice Johnson"


def test_contacts_lookup_passes_limit_to_search(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    with patch("server.people_client.build_service", return_value=MagicMock()), \
         patch("server.people_client.search_contacts", return_value=[]) as mock_search:
        client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Bob", "limit": 3}})
    assert mock_search.call_count == 1, "search_contacts should be called exactly once"
    assert mock_search.call_args.kwargs["limit"] == 3


def test_contacts_lookup_rejects_limit_over_10(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Alice", "limit": 99}})
    assert "error" in resp.json()


def test_contacts_lookup_rejects_empty_name(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": ""}})
    assert "error" in resp.json()


def test_contacts_lookup_rejects_name_over_200_chars(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "A" * 201}})
    assert "error" in resp.json()


def test_contacts_lookup_not_configured(monkeypatch):
    client, _ = _make_app(monkeypatch, configured=False)
    resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Alice"}})
    data = resp.json()
    assert data["error"] == "not_configured"
    assert "message" in data


def test_contacts_lookup_scope_missing_returns_scope_error(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    scope_err = ValueError(
        "contacts.readonly scope not granted — re-run: make setup-gmail CLIENT_SECRET=..."
    )
    with patch("server.people_client.build_service", return_value=MagicMock()), \
         patch("server.people_client.search_contacts", side_effect=scope_err):
        resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Alice"}})
    data = resp.json()
    assert data["error"] == "scope_missing"


def test_contacts_lookup_audit_log_omits_emails_and_phones(monkeypatch, tmp_path):
    monkeypatch.setenv("GMAIL_AUDIT_LOG_PATH", str(tmp_path / "audit.log"))
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    fake_matches = [{"name": "Alice", "emails": ["alice@secret.com"], "phones": ["+1-800-SECRET"]}]
    with patch("server.people_client.build_service", return_value=MagicMock()), \
         patch("server.people_client.search_contacts", return_value=fake_matches):
        client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Alice"}})
    log_content = (tmp_path / "audit.log").read_text()
    assert "alice@secret.com" not in log_content
    assert "+1-800-SECRET" not in log_content
    assert "result_count" in log_content
