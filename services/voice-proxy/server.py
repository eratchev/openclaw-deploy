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
