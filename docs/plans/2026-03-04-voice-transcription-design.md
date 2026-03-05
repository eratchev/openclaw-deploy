# Voice Transcription Design

**Date:** 2026-03-04
**Status:** Approved

## Problem

Telegram only auto-transcribes voice notes for Telegram Premium users. OpenClaw's Telegram integration receives raw audio with no transcription, so voice messages are silently ignored by the agent.

## Goal

Add transparent voice-to-text transcription so users can speak to OpenClaw via Telegram voice notes without any UX change.

## Architecture

```
Telegram
  ↓  webhook POST
Caddy:443  (headers preserved, body_size_limit uncapped)
  ↓  reverse_proxy voice-proxy:8090
voice-proxy:8090  ← Telegram webhook transformer
  ↓  voice/audio? → getFile → download to memory → Whisper API → mutate JSON
  ↓  everything else → forward unchanged
openclaw:18789
```

OpenClaw receives a standard text message — it never knows a voice note was involved.

## Components

### `services/voice-proxy/`

New Python service. Transparent HTTP forwarder that transforms Telegram webhook updates containing voice/audio into text-message updates.

**Key responsibilities:**
- Receive all Telegram webhook POSTs from Caddy
- Detect `message.voice` or `message.audio` fields
- Enforce max file size and per-chat rate limit before downloading
- Download audio to **memory** (BytesIO — not disk; container is read-only)
- Transcribe via OpenAI Whisper API (`whisper-1`)
- Mutate JSON: add `message.text`, add `message.voice_transcription = true`, **keep** original `message.voice`/`message.audio` field
- Forward to `openclaw:18789` and return its response

### `Caddyfile`

Single-line change:
```
reverse_proxy openclaw:18789  →  reverse_proxy voice-proxy:8090
```

### `docker-compose.yml`

```yaml
voice-proxy:
  build: ./services/voice-proxy
  networks: [ingress, internal]
  depends_on: [openclaw, redis]
  environment:
    - OPENAI_API_KEY=${OPENAI_API_KEY}
    - TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
    - OPENCLAW_UPSTREAM=http://openclaw:18789
    - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379
    - VOICE_MAX_FILE_SIZE_MB=${VOICE_MAX_FILE_SIZE_MB:-5}
    - VOICE_RATE_LIMIT_PER_MIN=${VOICE_RATE_LIMIT_PER_MIN:-10}
  read_only: true
  tmpfs: [/tmp]
  cap_drop: [ALL]
  security_opt: [no-new-privileges:true]
  mem_limit: 256m
```

### `.env`

Add `OPENAI_API_KEY=sk-...`

## Data Flow (voice note path)

1. Telegram POSTs webhook update to Caddy → forwarded to `voice-proxy:8090`
2. voice-proxy parses JSON, detects `message.voice` or `message.audio`
3. Check `file_size` — if > `VOICE_MAX_FILE_SIZE_MB`, skip transcription
4. Check Redis rate limit — if > `VOICE_RATE_LIMIT_PER_MIN` for this chat_id, skip transcription
5. Call `GET https://api.telegram.org/bot{token}/getFile?file_id=...` (timeout: 5s) → get `file_path`
6. Download `https://api.telegram.org/file/bot{token}/{file_path}` into BytesIO (timeout: 5s)
7. POST audio bytes to OpenAI `/v1/audio/transcriptions` (model: `whisper-1`, timeout: 20s)
8. Mutate update JSON:
   - Set `message.text = transcription`
   - Set `message.voice_transcription = true`
   - Keep original `message.voice` / `message.audio` field intact
9. Forward mutated JSON to `http://openclaw:18789` (same path + headers, timeout: 10s)
10. Return OpenClaw's response to Caddy → Telegram

**Timeouts:** download=5s · Whisper=20s · OpenClaw forward=10s

## Error Handling

| Failure | Behaviour |
|---|---|
| file_size > limit | Forward with `text = "🎤 Voice message received but transcription failed."` |
| Rate limit exceeded | Forward with `text = "🎤 Voice message received but transcription failed."` |
| Whisper API error | Forward with `text = "🎤 Voice message received but transcription failed."` |
| OpenClaw unreachable | Return HTTP 200 to Telegram (prevent retry storm), log error |
| Any exception | Return HTTP 200 to Telegram, log error |

Always return HTTP 200 to Telegram — non-200 triggers retry storms.

## Observability

One structured log line per transcription attempt:

```
voice chat_id=123 duration_s=5 size_bytes=42000 transcription_ms=1200 status=ok
voice chat_id=123 duration_s=8 size_bytes=80000 transcription_ms=0 status=rate_limited
voice chat_id=123 duration_s=3 size_bytes=22000 transcription_ms=1800 status=whisper_error
```

## Security

- **Max file size**: checked from Telegram metadata before download (`file_size` field)
- **Rate limiting**: Redis `voice_rate:{chat_id}:YYYY-MM-DD-HH-MM` sliding window (consistent with calendar-proxy pattern)
- **In-memory audio**: no disk writes for audio data
- **Container hardening**: `read_only`, `cap_drop: ALL`, `no-new-privileges`, `mem_limit: 256m`
- **No MIME validation**: Whisper accepts OGG natively; file_size check is sufficient

## What's Not Included (YAGNI)

- `video_note` transcription
- Voice output / TTS
- Transcription caching
- Language forcing (Whisper auto-detects)
- MIME validation

## STT Provider

OpenAI Whisper API (`whisper-1`). Cost: ~$0.006/min. For typical voice note usage this is negligible.
