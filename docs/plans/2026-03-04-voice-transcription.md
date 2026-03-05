# Voice Transcription Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a transparent voice-to-text layer so Telegram voice notes are automatically transcribed and delivered to OpenClaw as text messages.

**Architecture:** A new `voice-proxy` Python service (aiohttp) sits between Caddy and OpenClaw. Caddy routes all webhook traffic to voice-proxy (one-line Caddyfile change). voice-proxy inspects every Telegram update: if it contains `message.voice` or `message.audio`, it downloads the audio into memory, transcribes via OpenAI Whisper, mutates the JSON (adds `message.text`, keeps `message.voice`), and forwards to `openclaw:18789`. All other traffic is forwarded unchanged. OpenClaw sees a normal text message.

**Tech Stack:** Python 3.11, aiohttp 3.x (async HTTP server + client), openai SDK (Whisper API), redis.asyncio (rate limiting), pytest + fakeredis + aioresponses (tests)

---

## Context for the implementer

- Repo: `/home/evgueni/openclaw-deploy` (or your local clone)
- Existing service to model after: `services/calendar-proxy/` — same Dockerfile pattern, same security config
- Tests live in `tests/` — see `tests/calendar_proxy/` for examples
- `make test` runs `pytest tests/ -v`
- The Caddyfile currently has one line: `reverse_proxy openclaw:18789` — we change it to `reverse_proxy voice-proxy:8090`
- `voice-proxy` needs two networks: `ingress` (to receive from Caddy, call Telegram + OpenAI) and `internal` (to reach Redis and openclaw)
- openclaw listens on port 18789 on both networks

---

### Task 1: Service skeleton

**Files:**
- Create: `services/voice-proxy/requirements.txt`
- Create: `services/voice-proxy/Dockerfile`
- Create: `services/voice-proxy/server.py`
- Create: `tests/voice_proxy/__init__.py`
- Modify: `requirements-dev.txt`

**Step 1: Create `services/voice-proxy/requirements.txt`**

```
aiohttp>=3.9
openai>=1.0
redis>=5.2.0
```

**Step 2: Create `services/voice-proxy/Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

USER 1000

EXPOSE 8090
CMD ["python", "server.py"]
```

**Step 3: Create `services/voice-proxy/server.py` with skeleton only**

```python
"""
voice-proxy — Telegram webhook transformer.

Sits between Caddy and OpenClaw. Intercepts Telegram webhook updates,
transcribes voice/audio messages via OpenAI Whisper API (in-memory),
mutates the JSON payload, and forwards to openclaw:18789.
All non-voice traffic is forwarded unchanged.
"""
import asyncio
import copy
import io
import json
import logging
import os
import time
from typing import Optional

import aiohttp
from aiohttp import web
import openai
import redis.asyncio as aioredis

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Config ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENCLAW_UPSTREAM = os.environ.get("OPENCLAW_UPSTREAM", "http://openclaw:18789")
REDIS_URL = os.environ["REDIS_URL"]
VOICE_MAX_BYTES = float(os.environ.get("VOICE_MAX_FILE_SIZE_MB", "5")) * 1024 * 1024
VOICE_RATE_LIMIT_PER_MIN = int(os.environ.get("VOICE_RATE_LIMIT_PER_MIN", "10"))
WHISPER_TIMEOUT = 20.0
FALLBACK_TEXT = "🎤 Voice message received but transcription failed."

TELEGRAM_API = "https://api.telegram.org"

# ── Module-level singletons (initialised in on_startup) ────────────────────
_redis: Optional[aioredis.Redis] = None
_session: Optional[aiohttp.ClientSession] = None
```

**Step 4: Create `tests/voice_proxy/__init__.py`** (empty file)

**Step 5: Add test dependencies to `requirements-dev.txt`**

```
pytest>=8.0
fakeredis>=2.20.0
pytest-asyncio>=0.23
aioresponses>=0.7
# voice-proxy service deps (needed to run tests/voice_proxy/*)
# install alongside: pip install -r services/voice-proxy/requirements.txt
# calendar-proxy service deps (needed to run tests/calendar_proxy/*)
# install alongside: pip install -r services/calendar-proxy/requirements.txt
```

**Step 6: Verify tests still pass**

```bash
pip install -q -r requirements-dev.txt -r services/calendar-proxy/requirements.txt -r services/voice-proxy/requirements.txt
pytest tests/ -v
```

Expected: all existing tests pass (voice_proxy tests: 0 collected, no errors)

**Step 7: Commit**

```bash
git add services/voice-proxy/ tests/voice_proxy/__init__.py requirements-dev.txt
git commit -m "feat: add voice-proxy service skeleton"
```

---

### Task 2: Pure transform functions (TDD)

**Files:**
- Modify: `services/voice-proxy/server.py` (add 3 functions)
- Create: `tests/voice_proxy/test_transforms.py`

**Step 1: Write the failing tests**

Create `tests/voice_proxy/test_transforms.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

from server import detect_voice, get_chat_id, mutate_update

# ── detect_voice ─────────────────────────────────────────────────────────

def test_detect_voice_returns_voice_dict():
    update = {"message": {"voice": {"file_id": "abc", "duration": 5, "file_size": 1000}}}
    result = detect_voice(update)
    assert result == {"file_id": "abc", "duration": 5, "file_size": 1000}

def test_detect_audio_returns_audio_dict():
    update = {"message": {"audio": {"file_id": "xyz", "duration": 30, "file_size": 5000}}}
    result = detect_voice(update)
    assert result == {"file_id": "xyz", "duration": 30, "file_size": 5000}

def test_detect_voice_returns_none_for_text_message():
    update = {"message": {"text": "hello"}}
    assert detect_voice(update) is None

def test_detect_voice_returns_none_for_empty_update():
    assert detect_voice({}) is None

def test_detect_voice_handles_edited_message():
    update = {"edited_message": {"voice": {"file_id": "def", "duration": 3}}}
    result = detect_voice(update)
    assert result is not None
    assert result["file_id"] == "def"

# ── get_chat_id ───────────────────────────────────────────────────────────

def test_get_chat_id_returns_id():
    update = {"message": {"chat": {"id": 12345, "type": "private"}}}
    assert get_chat_id(update) == 12345

def test_get_chat_id_returns_none_for_missing_chat():
    assert get_chat_id({}) is None

def test_get_chat_id_handles_edited_message():
    update = {"edited_message": {"chat": {"id": 99}}}
    assert get_chat_id(update) == 99

# ── mutate_update ─────────────────────────────────────────────────────────

def test_mutate_update_adds_text():
    update = {"message": {"voice": {"file_id": "abc"}, "chat": {"id": 1}}}
    result = mutate_update(update, "hello world")
    assert result["message"]["text"] == "hello world"

def test_mutate_update_keeps_voice_field():
    update = {"message": {"voice": {"file_id": "abc"}, "chat": {"id": 1}}}
    result = mutate_update(update, "hello")
    assert "voice" in result["message"]
    assert result["message"]["voice"]["file_id"] == "abc"

def test_mutate_update_sets_voice_transcription_flag():
    update = {"message": {"voice": {"file_id": "abc"}}}
    result = mutate_update(update, "hi")
    assert result["message"]["voice_transcription"] is True

def test_mutate_update_does_not_modify_original():
    update = {"message": {"voice": {"file_id": "abc"}}}
    mutate_update(update, "hi")
    assert "text" not in update["message"]

def test_mutate_update_handles_edited_message():
    update = {"edited_message": {"voice": {"file_id": "abc"}}}
    result = mutate_update(update, "hi")
    assert result["edited_message"]["text"] == "hi"
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/voice_proxy/test_transforms.py -v
```

Expected: `ImportError` or `AttributeError` — functions not yet implemented

**Step 3: Implement the functions in `services/voice-proxy/server.py`**

Add after the config block:

```python
# ── Pure transform functions ────────────────────────────────────────────────

def detect_voice(update: dict) -> Optional[dict]:
    """Return the voice or audio dict from a Telegram update, or None."""
    msg = update.get("message") or update.get("edited_message") or {}
    return msg.get("voice") or msg.get("audio") or None


def get_chat_id(update: dict) -> Optional[int]:
    """Return the chat ID from a Telegram update, or None."""
    msg = update.get("message") or update.get("edited_message") or {}
    return (msg.get("chat") or {}).get("id")


def mutate_update(update: dict, transcription: str) -> dict:
    """Return a deep copy of update with transcription injected as message.text.

    Keeps the original voice/audio field intact (downstream may check it).
    Adds voice_transcription=True flag.
    """
    mutated = copy.deepcopy(update)
    msg_key = "message" if "message" in mutated else "edited_message"
    mutated[msg_key]["text"] = transcription
    mutated[msg_key]["voice_transcription"] = True
    return mutated
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/voice_proxy/test_transforms.py -v
```

Expected: 13 PASSED

**Step 5: Commit**

```bash
git add services/voice-proxy/server.py tests/voice_proxy/test_transforms.py
git commit -m "feat: voice-proxy transform functions (detect_voice, mutate_update)"
```

---

### Task 3: Rate limiting (TDD)

**Files:**
- Modify: `services/voice-proxy/server.py` (add `is_rate_limited`)
- Create: `tests/voice_proxy/test_rate_limit.py`

**Step 1: Write the failing tests**

Create `tests/voice_proxy/test_rate_limit.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

import pytest
import fakeredis.aioredis as fakeredis

from server import is_rate_limited

pytestmark = pytest.mark.asyncio


async def make_redis():
    return fakeredis.FakeRedis()


async def test_first_message_is_not_rate_limited():
    r = await make_redis()
    assert await is_rate_limited(r, chat_id=1, limit=10) is False


async def test_at_limit_is_rate_limited():
    r = await make_redis()
    for _ in range(10):
        await is_rate_limited(r, chat_id=1, limit=10)
    # 11th call exceeds limit=10
    assert await is_rate_limited(r, chat_id=1, limit=10) is True


async def test_under_limit_is_not_rate_limited():
    r = await make_redis()
    for _ in range(9):
        result = await is_rate_limited(r, chat_id=1, limit=10)
        assert result is False


async def test_different_chats_have_separate_limits():
    r = await make_redis()
    for _ in range(10):
        await is_rate_limited(r, chat_id=1, limit=10)
    # chat_id=2 is a fresh bucket
    assert await is_rate_limited(r, chat_id=2, limit=10) is False


async def test_limit_of_one_blocks_second_call():
    r = await make_redis()
    assert await is_rate_limited(r, chat_id=1, limit=1) is False
    assert await is_rate_limited(r, chat_id=1, limit=1) is True
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/voice_proxy/test_rate_limit.py -v
```

Expected: `ImportError` — `is_rate_limited` not yet defined

**Step 3: Implement in `services/voice-proxy/server.py`**

Add after the transform functions:

```python
# ── Rate limiting ───────────────────────────────────────────────────────────

async def is_rate_limited(r: aioredis.Redis, chat_id: int, limit: int) -> bool:
    """Return True if chat_id has exceeded limit voice messages in the current minute.

    Uses a per-minute bucket key with 2-minute TTL.
    """
    bucket = int(time.time()) // 60
    key = f"voice_rate:{chat_id}:{bucket}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 120)
    return count > limit
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/voice_proxy/test_rate_limit.py -v
```

Expected: 5 PASSED

**Step 5: Commit**

```bash
git add services/voice-proxy/server.py tests/voice_proxy/test_rate_limit.py
git commit -m "feat: voice-proxy rate limiting"
```

---

### Task 4: Telegram file download (TDD)

**Files:**
- Modify: `services/voice-proxy/server.py` (add `get_file_path`, `download_audio`)
- Create: `tests/voice_proxy/test_download.py`

**Step 1: Write the failing tests**

Create `tests/voice_proxy/test_download.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

import pytest
from aioresponses import aioresponses
import aiohttp

from server import get_file_path, download_audio

pytestmark = pytest.mark.asyncio

TOKEN = "testtoken"


async def test_get_file_path_returns_path():
    with aioresponses() as m:
        m.get(
            f"https://api.telegram.org/bot{TOKEN}/getFile?file_id=abc",
            payload={"ok": True, "result": {"file_path": "voice/file_10.oga"}},
        )
        async with aiohttp.ClientSession() as session:
            path = await get_file_path(TOKEN, "abc", session)
    assert path == "voice/file_10.oga"


async def test_get_file_path_raises_on_error():
    with aioresponses() as m:
        m.get(
            f"https://api.telegram.org/bot{TOKEN}/getFile?file_id=bad",
            payload={"ok": False, "description": "file not found"},
        )
        async with aiohttp.ClientSession() as session:
            with pytest.raises(RuntimeError, match="getFile failed"):
                await get_file_path(TOKEN, "bad", session)


async def test_download_audio_returns_bytes():
    fake_audio = b"\x00\x01\x02\x03"
    with aioresponses() as m:
        m.get(
            f"https://api.telegram.org/file/bot{TOKEN}/voice/file_10.oga",
            body=fake_audio,
            status=200,
        )
        async with aiohttp.ClientSession() as session:
            data = await download_audio(TOKEN, "voice/file_10.oga", session)
    assert data == fake_audio


async def test_download_audio_raises_on_http_error():
    with aioresponses() as m:
        m.get(
            f"https://api.telegram.org/file/bot{TOKEN}/bad/path",
            status=404,
        )
        async with aiohttp.ClientSession() as session:
            with pytest.raises(Exception):
                await download_audio(TOKEN, "bad/path", session)
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/voice_proxy/test_download.py -v
```

Expected: `ImportError` — functions not yet defined

**Step 3: Implement in `services/voice-proxy/server.py`**

Add after rate limiting:

```python
# ── Telegram file download ──────────────────────────────────────────────────

_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=5)


async def get_file_path(token: str, file_id: str, session: aiohttp.ClientSession) -> str:
    """Call getFile API to resolve file_id → file_path for download."""
    url = f"{TELEGRAM_API}/bot{token}/getFile"
    async with session.get(url, params={"file_id": file_id}, timeout=_DOWNLOAD_TIMEOUT) as resp:
        data = await resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"getFile failed: {data}")
    return data["result"]["file_path"]


async def download_audio(token: str, file_path: str, session: aiohttp.ClientSession) -> bytes:
    """Download audio file bytes into memory (no disk write)."""
    url = f"{TELEGRAM_API}/file/bot{token}/{file_path}"
    async with session.get(url, timeout=_DOWNLOAD_TIMEOUT) as resp:
        resp.raise_for_status()
        return await resp.read()
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/voice_proxy/test_download.py -v
```

Expected: 4 PASSED

**Step 5: Commit**

```bash
git add services/voice-proxy/server.py tests/voice_proxy/test_download.py
git commit -m "feat: voice-proxy Telegram file download"
```

---

### Task 5: Whisper transcription (TDD)

**Files:**
- Modify: `services/voice-proxy/server.py` (add `transcribe_audio`)
- Create: `tests/voice_proxy/test_transcription.py`

**Step 1: Write the failing tests**

Create `tests/voice_proxy/test_transcription.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from server import transcribe_audio

pytestmark = pytest.mark.asyncio


async def test_transcribe_returns_text():
    mock_result = MagicMock()
    mock_result.text = "hello world"
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_result)

    with patch("server.openai.AsyncOpenAI", return_value=mock_client):
        result = await transcribe_audio(b"fake_audio_bytes", "test-key")

    assert result == "hello world"


async def test_transcribe_calls_whisper_1_model():
    mock_result = MagicMock()
    mock_result.text = "test"
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_result)

    with patch("server.openai.AsyncOpenAI", return_value=mock_client):
        await transcribe_audio(b"audio", "test-key")

    call_kwargs = mock_client.audio.transcriptions.create.call_args
    assert call_kwargs.kwargs.get("model") == "whisper-1" or call_kwargs.args[0] if call_kwargs.args else False or \
        any(v == "whisper-1" for v in (call_kwargs.kwargs or {}).values())


async def test_transcribe_passes_bytes_as_file():
    mock_result = MagicMock()
    mock_result.text = "hi"
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_result)

    with patch("server.openai.AsyncOpenAI", return_value=mock_client):
        await transcribe_audio(b"\x00\x01\x02", "key")

    call_kwargs = mock_client.audio.transcriptions.create.call_args.kwargs
    audio_file = call_kwargs["file"]
    assert hasattr(audio_file, "read")  # file-like object
    assert audio_file.name == "voice.ogg"


async def test_transcribe_timeout_raises():
    import asyncio
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(
        side_effect=asyncio.TimeoutError()
    )

    with patch("server.openai.AsyncOpenAI", return_value=mock_client):
        with pytest.raises(asyncio.TimeoutError):
            await transcribe_audio(b"audio", "key")
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/voice_proxy/test_transcription.py -v
```

Expected: `ImportError` — `transcribe_audio` not yet defined

**Step 3: Implement in `services/voice-proxy/server.py`**

Add after the download functions:

```python
# ── Whisper transcription ───────────────────────────────────────────────────

async def transcribe_audio(audio_bytes: bytes, api_key: str) -> str:
    """Transcribe audio bytes via OpenAI Whisper API (in-memory, no disk)."""
    client = openai.AsyncOpenAI(api_key=api_key)
    buf = io.BytesIO(audio_bytes)
    buf.name = "voice.ogg"  # OpenAI SDK uses name to detect content-type
    result = await asyncio.wait_for(
        client.audio.transcriptions.create(model="whisper-1", file=buf),
        timeout=WHISPER_TIMEOUT,
    )
    return result.text
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/voice_proxy/test_transcription.py -v
```

Expected: 4 PASSED

**Step 5: Commit**

```bash
git add services/voice-proxy/server.py tests/voice_proxy/test_transcription.py
git commit -m "feat: voice-proxy Whisper transcription"
```

---

### Task 6: HTTP forwarding (TDD)

**Files:**
- Modify: `services/voice-proxy/server.py` (add `forward_raw`)
- Create: `tests/voice_proxy/test_forward.py`

**Step 1: Write the failing tests**

Create `tests/voice_proxy/test_forward.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

import json
import pytest
from aioresponses import aioresponses
import aiohttp

from server import forward_raw

pytestmark = pytest.mark.asyncio

UPSTREAM = "http://openclaw:18789"


async def test_forward_raw_returns_upstream_response():
    body = b'{"update_id": 1}'
    with aioresponses() as m:
        m.post(f"{UPSTREAM}/", body=b"ok", status=200)
        async with aiohttp.ClientSession() as session:
            resp = await forward_raw(body, "/", {}, UPSTREAM, session)
    assert resp.status == 200


async def test_forward_raw_strips_host_header():
    body = b'{"update_id": 1}'
    with aioresponses() as m:
        m.post(f"{UPSTREAM}/", body=b"ok", status=200)
        async with aiohttp.ClientSession() as session:
            # Should not raise even with host header present
            resp = await forward_raw(
                body, "/", {"host": "example.com", "content-type": "application/json"}, UPSTREAM, session
            )
    assert resp.status == 200


async def test_forward_raw_sends_body_unchanged():
    body = b'{"update_id": 42, "message": {"text": "hi"}}'
    captured = {}

    with aioresponses() as m:
        async def capture_request(url, **kwargs):
            captured["data"] = kwargs.get("data")
            from aioresponses.core import CallbackResult
            return CallbackResult(status=200, body=b"ok")
        m.post(f"{UPSTREAM}/", callback=capture_request)
        async with aiohttp.ClientSession() as session:
            await forward_raw(body, "/", {}, UPSTREAM, session)

    assert captured["data"] == body


async def test_forward_raw_returns_502_response_from_upstream():
    body = b'{}'
    with aioresponses() as m:
        m.post(f"{UPSTREAM}/", body=b"error", status=502)
        async with aiohttp.ClientSession() as session:
            resp = await forward_raw(body, "/", {}, UPSTREAM, session)
    assert resp.status == 502
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/voice_proxy/test_forward.py -v
```

Expected: `ImportError` — `forward_raw` not yet defined

**Step 3: Implement in `services/voice-proxy/server.py`**

Add after the transcription function:

```python
# ── HTTP forwarding ─────────────────────────────────────────────────────────

_FORWARD_TIMEOUT = aiohttp.ClientTimeout(total=10)
_SKIP_HEADERS = frozenset({"host", "content-length", "transfer-encoding"})


async def forward_raw(
    body: bytes,
    path: str,
    headers: dict,
    upstream: str,
    session: aiohttp.ClientSession,
) -> web.Response:
    """Forward raw bytes to openclaw upstream, return its response."""
    forward_headers = {k: v for k, v in headers.items() if k.lower() not in _SKIP_HEADERS}
    async with session.post(
        f"{upstream}{path}",
        data=body,
        headers=forward_headers,
        timeout=_FORWARD_TIMEOUT,
    ) as resp:
        resp_body = await resp.read()
        return web.Response(
            status=resp.status,
            body=resp_body,
            content_type=resp.content_type or "application/json",
        )
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/voice_proxy/test_forward.py -v
```

Expected: 4 PASSED

**Step 5: Commit**

```bash
git add services/voice-proxy/server.py tests/voice_proxy/test_forward.py
git commit -m "feat: voice-proxy HTTP forwarding"
```

---

### Task 7: Main request handler + app (TDD)

**Files:**
- Modify: `services/voice-proxy/server.py` (add `handle_request`, `make_app`, startup/cleanup)
- Create: `tests/voice_proxy/test_server.py`

**Step 1: Write the failing tests**

Create `tests/voice_proxy/test_server.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp.test_utils import TestClient, TestServer
import fakeredis.aioredis as fakeredis
import aiohttp

import server
from server import make_app

pytestmark = pytest.mark.asyncio


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


@pytest.fixture
def app_with_mocks(fake_redis):
    """Create app with Redis and session mocked."""
    mock_session = AsyncMock()
    app = make_app()
    app["_test_redis"] = fake_redis
    app["_test_session"] = mock_session
    return app, mock_session


async def test_text_message_forwarded_unchanged(fake_redis):
    """Non-voice updates must be forwarded to openclaw as-is."""
    update = make_text_update("hello")
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(json.loads(body))
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch("server.forward_raw", side_effect=mock_forward):
        app = make_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})
            assert resp.status == 200

    assert len(forwarded) == 1
    assert forwarded[0]["message"]["text"] == "hello"
    assert "voice" not in forwarded[0]["message"]


async def test_voice_message_transcribed_and_forwarded(fake_redis):
    """Voice updates must be transcribed and forwarded with message.text set."""
    update = make_voice_update()
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(json.loads(body))
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch("server.get_file_path", new_callable=AsyncMock, return_value="voice/f.oga"), \
         patch("server.download_audio", new_callable=AsyncMock, return_value=b"audio"), \
         patch("server.transcribe_audio", new_callable=AsyncMock, return_value="hi there"), \
         patch("server.forward_raw", side_effect=mock_forward):
        app = make_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})
            assert resp.status == 200

    assert forwarded[0]["message"]["text"] == "hi there"
    assert forwarded[0]["message"]["voice_transcription"] is True
    assert "voice" in forwarded[0]["message"]  # original voice field kept


async def test_transcription_failure_sends_fallback(fake_redis):
    """If transcription fails, fallback text must be forwarded."""
    update = make_voice_update()
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(json.loads(body))
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch("server.get_file_path", new_callable=AsyncMock, side_effect=RuntimeError("fail")), \
         patch("server.forward_raw", side_effect=mock_forward):
        app = make_app()
        async with TestClient(TestServer(app)) as client:
            await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})

    assert "transcription failed" in forwarded[0]["message"]["text"]


async def test_oversized_voice_sends_fallback(fake_redis):
    """Voice messages exceeding max size must skip transcription and send fallback."""
    update = make_voice_update(file_size=10 * 1024 * 1024)  # 10 MB > 5 MB limit
    raw_body = json.dumps(update).encode()
    forwarded = []

    async def mock_forward(body, path, headers, upstream, session):
        forwarded.append(json.loads(body))
        return aiohttp.web.Response(status=200, body=b"ok")

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch("server.transcribe_audio", new_callable=AsyncMock) as mock_transcribe, \
         patch("server.forward_raw", side_effect=mock_forward):
        app = make_app()
        async with TestClient(TestServer(app)) as client:
            await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})

    mock_transcribe.assert_not_called()
    assert "transcription failed" in forwarded[0]["message"]["text"]


async def test_openclaw_down_returns_200(fake_redis):
    """If openclaw is unreachable, voice-proxy must still return 200 to Telegram."""
    update = make_voice_update()
    raw_body = json.dumps(update).encode()

    with patch.object(server, "_redis", fake_redis), \
         patch.object(server, "_session", AsyncMock()), \
         patch("server.get_file_path", new_callable=AsyncMock, return_value="voice/f.oga"), \
         patch("server.download_audio", new_callable=AsyncMock, return_value=b"audio"), \
         patch("server.transcribe_audio", new_callable=AsyncMock, return_value="hi"), \
         patch("server.forward_raw", new_callable=AsyncMock, side_effect=Exception("connection refused")):
        app = make_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/", data=raw_body, headers={"Content-Type": "application/json"})
            assert resp.status == 200
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/voice_proxy/test_server.py -v
```

Expected: `ImportError` or `AttributeError` — `make_app`, `handle_request` not yet defined

**Step 3: Implement the handler and app in `services/voice-proxy/server.py`**

Add after the forwarding functions:

```python
# ── Request handler ─────────────────────────────────────────────────────────

async def handle_request(request: web.Request) -> web.Response:
    """Main handler: intercepts voice/audio, forwards everything else unchanged."""
    raw_body = await request.read()
    path = request.path_qs
    headers = dict(request.headers)

    # Parse JSON; if not JSON forward raw
    try:
        update = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        return await forward_raw(raw_body, path, headers, OPENCLAW_UPSTREAM, _session)

    voice = detect_voice(update)
    if not voice:
        return await forward_raw(raw_body, path, headers, OPENCLAW_UPSTREAM, _session)

    # Voice/audio path
    t0 = time.monotonic()
    chat_id = get_chat_id(update) or 0
    file_size = voice.get("file_size", 0)
    duration = voice.get("duration", 0)
    transcription: Optional[str] = None
    status = "ok"

    if file_size > VOICE_MAX_BYTES:
        status = "size_exceeded"
    elif await is_rate_limited(_redis, chat_id, VOICE_RATE_LIMIT_PER_MIN):
        status = "rate_limited"
    else:
        try:
            file_id = voice["file_id"]
            file_path = await get_file_path(TELEGRAM_TOKEN, file_id, _session)
            audio_bytes = await download_audio(TELEGRAM_TOKEN, file_path, _session)
            if OPENAI_API_KEY:
                transcription = await transcribe_audio(audio_bytes, OPENAI_API_KEY)
            else:
                status = "no_api_key"
        except Exception as exc:
            log.warning("transcription error chat_id=%s: %s", chat_id, exc)
            status = "error"

    text = transcription if transcription else FALLBACK_TEXT
    mutated = mutate_update(update, text)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    log.info(
        "voice chat_id=%s duration_s=%s size_bytes=%s transcription_ms=%s status=%s",
        chat_id, duration, file_size, elapsed_ms, status,
    )

    mutated_body = json.dumps(mutated).encode()
    try:
        return await forward_raw(mutated_body, path, headers, OPENCLAW_UPSTREAM, _session)
    except Exception as exc:
        log.error("forward to openclaw failed: %s", exc)
        return web.Response(status=200, text="ok")


# ── App factory ─────────────────────────────────────────────────────────────

async def on_startup(app: web.Application) -> None:
    global _redis, _session
    _redis = aioredis.from_url(REDIS_URL)
    _session = aiohttp.ClientSession()
    log.info("voice-proxy started upstream=%s", OPENCLAW_UPSTREAM)


async def on_cleanup(app: web.Application) -> None:
    if _session:
        await _session.close()
    if _redis:
        await _redis.aclose()


def make_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_route("*", "/{path_info:.*}", handle_request)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=8090)
```

**Step 4: Run all voice-proxy tests**

```bash
pytest tests/voice_proxy/ -v
```

Expected: all 26 tests PASS

**Step 5: Run full suite to confirm no regressions**

```bash
pytest tests/ -v
```

Expected: all tests PASS

**Step 6: Commit**

```bash
git add services/voice-proxy/server.py tests/voice_proxy/test_server.py
git commit -m "feat: voice-proxy main handler and aiohttp app"
```

---

### Task 8: Infrastructure — docker-compose, Caddyfile, .env, Makefile, README

**Files:**
- Modify: `docker-compose.yml`
- Modify: `Caddyfile`
- Modify: `.env.example`
- Modify: `Makefile`
- Modify: `README.md`

No unit tests for infrastructure config. Verify by running `docker compose config` (validates YAML) and `make up` on the VPS.

**Step 1: Add voice-proxy to `docker-compose.yml`**

Add after the `calendar-proxy` service block, before `volumes:`:

```yaml
  voice-proxy:
    build: ./services/voice-proxy
    restart: unless-stopped
    networks:
      - ingress
      - internal
    depends_on:
      - openclaw
      - redis
    environment:
      - TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
      - OPENCLAW_UPSTREAM=http://openclaw:18789
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379
      - VOICE_MAX_FILE_SIZE_MB=${VOICE_MAX_FILE_SIZE_MB:-5}
      - VOICE_RATE_LIMIT_PER_MIN=${VOICE_RATE_LIMIT_PER_MIN:-10}
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp
    security_opt:
      - no-new-privileges:true
    mem_limit: 256m
    cpus: "0.5"
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8090/')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

**Step 2: Update `Caddyfile`**

Change:
```
{$DOMAIN} {
    reverse_proxy openclaw:18789
}
```

To:
```
{$DOMAIN} {
    reverse_proxy voice-proxy:8090
}
```

**Step 3: Add to `.env.example`**

Add below the existing TELEGRAM_TOKEN line:

```bash
# OpenAI API key — required for voice transcription (voice-proxy)
OPENAI_API_KEY=sk-...

# Voice proxy tuning (optional, defaults shown)
# VOICE_MAX_FILE_SIZE_MB=5
# VOICE_RATE_LIMIT_PER_MIN=10
```

**Step 4: Add Makefile targets**

Add after the existing `up-calendar` target:

```makefile
# Start base services + voice transcription proxy
up-voice:
	docker compose up -d --build voice-proxy
	docker compose restart caddy
	@echo "Voice proxy started. Test by sending a voice note to your bot."
```

Also add `up-voice` to the `.PHONY` line.

**Step 5: Update `README.md`**

Find the integrations section and add (after the Telegram section):

```markdown
### Voice Transcription *(optional)*

Automatically transcribes Telegram voice notes via OpenAI Whisper so you can speak to OpenClaw hands-free.

**Setup:**
1. Add `OPENAI_API_KEY=sk-...` to `.env`
2. `make up-voice`
3. Send a voice note to your bot — it should reply as if you typed the text

**Cost:** ~$0.006/min (OpenAI Whisper). Negligible for personal use.
**Rate limit:** 10 voice messages/minute per chat (configurable via `VOICE_RATE_LIMIT_PER_MIN`).
```

**Step 6: Validate docker-compose config**

```bash
docker compose config > /dev/null && echo "config valid"
```

Expected: `config valid`

**Step 7: Commit**

```bash
git add docker-compose.yml Caddyfile .env.example Makefile README.md
git commit -m "feat: wire voice-proxy into docker-compose and Caddyfile"
```

**Step 8: Deploy to VPS**

```bash
ssh user@YOUR_VPS_IP "cd openclaw-deploy && git pull && make up-voice"
```

Expected: voice-proxy container starts, Caddy restarts routing through it. Send a voice note to confirm transcription works end-to-end.

---

## Final verification

```bash
pytest tests/ -v
```

Expected: all tests pass (26 voice-proxy + 78 calendar-proxy/guardrail = 104+ total)

To confirm end-to-end on the VPS:
```bash
ssh user@YOUR_VPS_IP "cd openclaw-deploy && docker compose logs -f voice-proxy"
```

Send a Telegram voice note → look for `voice chat_id=... status=ok` in logs.
