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
    msg_key = next(
        (k for k in ("message", "edited_message") if k in mutated),
        None,
    )
    if msg_key is None:
        return mutated  # nothing to mutate; return unchanged deep copy
    mutated[msg_key]["text"] = transcription
    mutated[msg_key]["voice_transcription"] = True
    return mutated


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
