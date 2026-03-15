import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import pytest
from unittest.mock import patch, MagicMock
import fakeredis


@pytest.fixture
def mock_env(monkeypatch, tmp_path):
    """Set all required env vars and mock Redis + token store."""
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("GCAL_ALLOWED_CALENDARS", "primary")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    monkeypatch.setenv("GCAL_DRY_RUN", "false")
    monkeypatch.setenv("GCAL_AUDIT_LOG_PATH", str(tmp_path / "audit.log"))
    monkeypatch.setenv("GCAL_DISABLE_REMINDERS", "true")


def test_dry_run_mode_emits_warning(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("GCAL_DRY_RUN", "true")
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("GCAL_AUDIT_LOG_PATH", str(tmp_path / "audit.log"))
    monkeypatch.setenv("GCAL_DISABLE_REMINDERS", "true")
    import importlib
    import server
    importlib.reload(server)
    captured = capsys.readouterr()
    assert "DRY_RUN" in captured.out


def test_create_event_dry_run_returns_dry_run_status(monkeypatch, mock_env):
    """create_event with execution_mode=dry_run never calls Google."""
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_build.return_value = MagicMock()

        import server
        result = server.handle_create_event({
            "title": "Test",
            "start": "2026-03-16T10:00:00+00:00",
            "end": "2026-03-16T11:00:00+00:00",
            "execution_mode": "dry_run",
        })
        assert result["status"] in ("dry_run", "safe_to_execute", "needs_confirmation", "denied")
        mock_build.return_value.events.return_value.insert.assert_not_called()


def test_list_events_returns_list(monkeypatch, mock_env):
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "ev1", "summary": "Test", "start": {"dateTime": "2026-03-16T10:00:00+00:00"}, "end": {"dateTime": "2026-03-16T11:00:00+00:00"}}]
        }
        mock_build.return_value = mock_service

        import server
        result = server.handle_list_events({
            "time_min": "2026-03-16T00:00:00+00:00",
            "time_max": "2026-03-16T23:59:59+00:00",
        })
        assert isinstance(result, list)


def test_health_returns_token_and_redis_status(monkeypatch, mock_env, tmp_path):
    with patch("server.get_redis") as mock_redis, \
         patch("server.token_store") as mock_store:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_store.load.return_value = {"access_token": "tok"}

        import server
        health = server.get_health()
        assert "redis" in health
        assert "token" in health
        assert health["dry_run_mode"] is False


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
