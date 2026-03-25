"""
End-to-end smoke test for the full pipeline using dry-run mode.
No real Google API calls. Uses fakeredis.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import pytest
import fakeredis
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet


def _future_date() -> str:
    """Return a date string 2 days in the future (YYYY-MM-DD)."""
    return (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")


def _future_saturday_date() -> str:
    """Return the next Saturday's date (YYYY-MM-DD)."""
    dt = datetime.now(timezone.utc) + timedelta(days=1)
    while dt.weekday() != 5:  # 5=Saturday
        dt += timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def setup_env(monkeypatch, tmp_path):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GCAL_ALLOWED_CALENDARS", "primary")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    monkeypatch.setenv("GCAL_DRY_RUN", "false")
    monkeypatch.setenv("GCAL_MAX_EVENTS_PER_DAY", "10")
    monkeypatch.setenv("GCAL_AUDIT_LOG_PATH", str(tmp_path / "audit.log"))

    from auth import TokenStore
    from pathlib import Path
    store = TokenStore(key=key.encode(), token_path=tmp_path / "gcal_token.enc")
    store.save({"token": "test", "refresh_token": "ref",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "id", "client_secret": "sec", "scopes": []})

    import server
    monkeypatch.setattr(server, "token_stores", {"": store})


def test_smoke_create_dry_run_simple_event():
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {"items": []}
        mock_build.return_value = mock_service

        import server
        d = _future_date()
        result = server.handle_create_event({
            "title": "Quick sync",
            "start": f"{d}T10:00:00+00:00",
            "end": f"{d}T10:30:00+00:00",
            "execution_mode": "dry_run",
        })
        assert result["status"] in ("dry_run", "safe_to_execute", "needs_confirmation")
        mock_service.events.return_value.insert.assert_not_called()


def test_smoke_denied_outside_allowlist():
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_build.return_value = MagicMock()

        import server
        d = _future_date()
        result = server.handle_create_event({
            "title": "Test",
            "start": f"{d}T10:00:00+00:00",
            "end": f"{d}T11:00:00+00:00",
            "execution_mode": "execute",
            "calendar_id": "notallowed@calendar.google.com",
        })
        assert result["status"] == "denied"
        assert "allowlist" in result["reason"]


def test_smoke_needs_confirmation_weekend():
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {"items": []}
        mock_build.return_value = mock_service

        import server
        s = _future_saturday_date()
        result = server.handle_create_event({
            "title": "Weekend event",
            "start": f"{s}T10:00:00+00:00",
            "end": f"{s}T11:00:00+00:00",
            "execution_mode": "execute",
        })
        assert result["status"] == "needs_confirmation"


def test_smoke_list_events_works():
    with patch("server.build_google_service") as mock_build:
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "1", "summary": "Existing", "start": {"dateTime": "2026-03-16T09:00:00+00:00"}, "end": {"dateTime": "2026-03-16T10:00:00+00:00"}}]
        }
        mock_build.return_value = mock_service

        import server
        events = server.handle_list_events({
            "time_min": "2026-03-16T00:00:00+00:00",
            "time_max": "2026-03-16T23:59:59+00:00",
        })
        assert len(events) == 1
        assert events[0]["id"] == "1"
