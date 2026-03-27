import sys
import os

# Ensure voice-proxy is on path (conftest also handles this, but be explicit)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

import json
import pytest
from unittest.mock import AsyncMock, patch
from aiohttp.test_utils import TestClient, TestServer
import fakeredis.aioredis as fakeredis
import aiohttp

pytestmark = pytest.mark.asyncio


def _server():
    """Return the current server module (after conftest may have reloaded it)."""
    return sys.modules["server"]


def make_voice_update(chat_id=1, file_id="fid", file_size=1000, duration=5):
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": chat_id},
            "voice": {"file_id": file_id, "duration": duration, "file_size": file_size},
        },
    }


def make_text_update(text="hello"):
    return {
        "update_id": 2,
        "message": {"chat": {"id": 1}, "text": text},
    }


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


async def test_text_message_forwarded_unchanged(fake_redis):
    """Non-voice updates must be forwarded to openclaw as-is."""
    server = _server()
    update = make_text_update("hello")
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(json.loads(body))
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "forward_raw", side_effect=mock_forward):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})
            assert resp.status == 200

    assert len(forwarded) == 1
    assert forwarded[0]["message"]["text"] == "hello"
    assert "voice" not in forwarded[0]["message"]


async def test_voice_message_transcribed_and_forwarded(fake_redis):
    """Voice updates must be transcribed and forwarded with message.text set."""
    server = _server()
    update = make_voice_update()
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(json.loads(body))
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "_openai", AsyncMock()), \
         patch.object(server, "get_file_path", new_callable=AsyncMock, return_value="voice/f.oga"), \
         patch.object(server, "download_audio", new_callable=AsyncMock, return_value=b"audio"), \
         patch.object(server, "transcribe_audio", new_callable=AsyncMock, return_value="hi there"), \
         patch.object(server, "OPENAI_API_KEY", "sk-test"), \
         patch.object(server, "forward_raw", side_effect=mock_forward):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})
            assert resp.status == 200

    assert forwarded[0]["message"]["text"] == "hi there"
    assert forwarded[0]["message"]["voice_transcription"] is True
    assert "voice" not in forwarded[0]["message"]  # removed so agent doesn't re-transcribe


async def test_transcription_failure_sends_fallback(fake_redis):
    """If transcription fails, fallback text must be forwarded."""
    server = _server()
    update = make_voice_update()
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(json.loads(body))
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "get_file_path", new_callable=AsyncMock, side_effect=RuntimeError("fail")), \
         patch.object(server, "forward_raw", side_effect=mock_forward):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})

    assert "transcription failed" in forwarded[0]["message"]["text"]


async def test_oversized_voice_sends_fallback(fake_redis):
    """Voice messages exceeding max size must skip transcription and send fallback."""
    server = _server()
    update = make_voice_update(file_size=10 * 1024 * 1024)  # 10 MB > 5 MB limit
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(json.loads(body))
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "is_rate_limited", new_callable=AsyncMock, return_value=False) as mock_rate, \
         patch.object(server, "transcribe_audio", new_callable=AsyncMock) as mock_transcribe, \
         patch.object(server, "forward_raw", side_effect=mock_forward):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})

    mock_transcribe.assert_not_called()
    assert "transcription failed" in forwarded[0]["message"]["text"]


async def test_openclaw_down_returns_200(fake_redis):
    """If openclaw is unreachable, voice-proxy must still return 200 to Telegram."""
    server = _server()
    update = make_voice_update()
    raw_body = json.dumps(update).encode()

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "_openai", AsyncMock()), \
         patch.object(server, "get_file_path", new_callable=AsyncMock, return_value="voice/f.oga"), \
         patch.object(server, "download_audio", new_callable=AsyncMock, return_value=b"audio"), \
         patch.object(server, "transcribe_audio", new_callable=AsyncMock, return_value="hi"), \
         patch.object(server, "OPENAI_API_KEY", "sk-test"), \
         patch.object(server, "forward_raw", new_callable=AsyncMock, side_effect=Exception("connection refused")):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})
            assert resp.status == 200


async def test_valid_webhook_secret_accepted(fake_redis):
    """Correct WEBHOOK_SECRET header must allow the request through."""
    server = _server()
    update = make_text_update("hello")
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(json.loads(body))
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "WEBHOOK_SECRET", "test-secret-abc"), \
         patch.object(server, "forward_raw", side_effect=mock_forward):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/",
                data=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Telegram-Bot-Api-Secret-Token": "test-secret-abc",
                },
            )
            assert resp.status == 200

    assert len(forwarded) == 1


async def test_missing_secret_header_returns_403(fake_redis):
    """When WEBHOOK_SECRET is set, requests without the header must be rejected."""
    server = _server()
    update = make_text_update("hello")
    raw_body = json.dumps(update).encode()

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "WEBHOOK_SECRET", "test-secret-abc"):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/", data=raw_body, headers={"Content-Type": "application/json"}
            )
            assert resp.status == 403


async def test_wrong_secret_header_returns_403(fake_redis):
    """Requests with an incorrect secret header must be rejected."""
    server = _server()
    update = make_text_update("hello")
    raw_body = json.dumps(update).encode()

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "WEBHOOK_SECRET", "correct-secret"):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/",
                data=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Telegram-Bot-Api-Secret-Token": "wrong-secret",
                },
            )
            assert resp.status == 403


async def test_empty_webhook_secret_skips_auth(fake_redis):
    """When WEBHOOK_SECRET is empty, all requests are allowed (backward compat)."""
    server = _server()
    update = make_text_update("hello")
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(body)
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "WEBHOOK_SECRET", ""), \
         patch.object(server, "forward_raw", side_effect=mock_forward):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/", data=raw_body, headers={"Content-Type": "application/json"}
            )
            assert resp.status == 200

    assert len(forwarded) == 1


def make_update_from_user(user_id, text="hello"):
    return {
        "update_id": 3,
        "message": {
            "chat": {"id": user_id},
            "from": {"id": user_id},
            "text": text,
        },
    }


async def test_allowed_user_is_forwarded(fake_redis):
    """Updates from an allowed user must be forwarded to openclaw."""
    server = _server()
    update = make_update_from_user(user_id=111)
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(json.loads(body))
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "ALLOWED_USER_IDS", frozenset({111})), \
         patch.object(server, "forward_raw", side_effect=mock_forward):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})
            assert resp.status == 200

    assert len(forwarded) == 1


async def test_blocked_user_returns_200_and_not_forwarded(fake_redis):
    """Updates from a user not in the allowlist must be silently dropped."""
    server = _server()
    update = make_update_from_user(user_id=999)
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(body)
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "ALLOWED_USER_IDS", frozenset({111})), \
         patch.object(server, "forward_raw", side_effect=mock_forward):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})
            assert resp.status == 200

    assert forwarded == []  # update was NOT forwarded to openclaw


async def test_empty_allowlist_allows_all_users(fake_redis):
    """When TELEGRAM_ALLOWED_USER_IDS is empty, all users are forwarded (backward compat)."""
    server = _server()
    update = make_update_from_user(user_id=42)
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(body)
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch.object(server, "ALLOWED_USER_IDS", frozenset()), \
         patch.object(server, "forward_raw", side_effect=mock_forward):
        app = server.make_app()
        app.on_startup.clear()
        app.on_cleanup.clear()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})
            assert resp.status == 200

    assert len(forwarded) == 1
