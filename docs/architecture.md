# Architecture

## Diagram

```
Internet
   │
   │ :443 only
   ▼
┌─────────────────────────────────────────────┐
│              VPS (Hetzner)                  │
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │           Docker Compose             │   │
│  │                                      │   │
│  │  ┌────────┐   [ingress network]      │   │
│  │  │  Caddy │ ◄─── HTTPS/443           │   │
│  │  └───┬────┘                          │   │
│  │      │                               │   │
│  │  ┌───▼──────────────────────────┐    │   │
│  │  │  voice-proxy (hardened)      │    │   │
│  │  │  - Telegram webhook xformer  │    │   │
│  │  │  - voice → Whisper → text    │    │   │
│  │  │  - non-root, cap_drop        │    │   │
│  │  └───┬──────────────────────────┘    │   │
│  │      │  [ingress + internal]         │   │
│  │  ┌───▼──────────────────────────┐    │   │
│  │  │  openclaw (hardened)         │    │   │
│  │  │  - Gateway daemon            │    │   │
│  │  │  - Telegram + WhatsApp       │    │   │
│  │  │  - LLM + tools               │    │   │
│  │  │  - non-root, cap_drop        │    │   │
│  │  └───┬──────┬───────────────────┘    │   │
│  │      │      │  [internal network]    │   │
│  │  ┌───▼────┐ └──► ┌────────────────┐  │   │
│  │  │ Redis  │      │ calendar-proxy │  │   │
│  │  │session │      │ - MCP server   │  │   │
│  │  │ store  │      │ - policy engine│  │   │
│  │  └────────┘      │ - non-root     │  │   │
│  │      ▲           └────────────────┘  │   │
│  │      │               │               │   │
│  │  voice-proxy         │               │   │
│  │  rate limits         │               │   │
│  │                  /data volume ◄───────┘   │
│  │                  (token.enc, audit.log)   │
│  └──────────────────────────────────────┘   │
│                                             │
│  UFW: 22 (SSH) + 80 (ACME) + 443 only      │
│  Fail2ban, unattended-upgrades              │
└─────────────────────────────────────────────┘
```

## Network Isolation

| Service          | ingress network | internal network |
|------------------|:--------------:|:----------------:|
| Caddy            | yes            | no               |
| voice-proxy      | yes            | yes              |
| OpenClaw         | yes            | yes              |
| Redis            | no             | yes              |
| calendar-proxy   | no             | yes              |

This is enforced explicitly in `docker-compose.yml` — not just implied by convention.

## Service Roles

**Caddy** sits at the edge and is the only service that accepts inbound traffic from the internet. It terminates TLS using an automatically provisioned Let's Encrypt certificate and reverse-proxies HTTPS requests to OpenClaw. Caddy is on the `ingress` network only. It has no path to Redis and no knowledge of session state. Keeping Caddy at the edge means the TLS termination point has no access to any sensitive internal data.

**voice-proxy** is a transparent Python (aiohttp) webhook transformer that sits between Caddy and OpenClaw. It receives every Telegram webhook POST from Caddy. If the update contains a `message.voice` or `message.audio` field, it downloads the audio into memory (BytesIO — no disk writes), transcribes it via OpenAI Whisper API, mutates the JSON payload (adds `message.text`, sets `message.voice_transcription = true`, keeps the original `voice` field), and forwards the modified update to OpenClaw. All other updates are forwarded unchanged. OpenClaw receives a normal text message and has no knowledge of the voice note. voice-proxy needs both networks: `ingress` to receive traffic from Caddy and make outbound calls to the Telegram and OpenAI APIs, and `internal` to forward to OpenClaw and enforce per-minute rate limits via Redis.

**OpenClaw** runs the Gateway daemon that handles Telegram and WhatsApp messaging, invokes LLM APIs, and executes tools and skills. It sits on both networks because it must accept proxied requests from Caddy (via `ingress`) and read and write session state in Redis (via `internal`). It is the only service that spans both networks, which is intentional — it is the integration point, and it is the most constrained: non-root UID 1000, all Linux capabilities dropped, read-only root filesystem, resource limits, and no Docker socket access.

**Redis** stores session state for the OpenClaw Gateway. It runs on the `internal` network only, meaning it is completely unreachable from Caddy and from the internet. It requires a password (`REDIS_PASSWORD`) even on the internal network, providing a second layer of defense if the OpenClaw container is compromised and an attacker gains access to the internal network.

**calendar-proxy** is a Python MCP server that gives OpenClaw controlled access to Google Calendar. It runs on the `internal` network only — OpenClaw calls it via MCP tool invocations, and it is never reachable from the internet or from Caddy. It enforces a policy engine (`validate → assess → enforce → execute`) before making any Google API call, uses Fernet-encrypted token storage on the shared `/data` volume, and backs rate limiting and idempotency against Redis.

## Why Two Networks

The two-network design exists for one reason: Redis must never be reachable from the internet, directly or indirectly. A single shared network would give Caddy a route to Redis. By splitting traffic into an `ingress` network (internet-facing) and an `internal` network (service-to-service only), Docker's network isolation enforces the separation at the kernel level. Even if Caddy were fully compromised, it cannot reach Redis because there is no network path between them.

## calendar-proxy Security Boundaries

The proxy adds a trust boundary between OpenClaw's unbounded tool execution and the Google Calendar API:

- **Token isolation**: OAuth token is Fernet-encrypted at rest (`/data/gcal_token.enc`). The encryption key (`GCAL_TOKEN_ENCRYPTION_KEY`) never touches the token file — decryption happens in memory only.
- **Allowlist enforcement**: Only calendars listed in `GCAL_ALLOWED_CALENDARS` can be written to. Any other calendar ID is hard-denied, not just flagged for confirmation.
- **Hard denials**: Certain combinations are never allowed regardless of user confirmation — recurring events on work calendars outside business hours, sub-daily recurrence, infinite RRULE, COUNT over the configured maximum.
- **Confirmation gate**: All other writes that carry risk (overlaps, long duration, weekend, work calendar, recurring, delete) return `needs_confirmation` and require the LLM to explicitly pass `execution_mode=execute` after presenting the impact to the user.
- **Audit log**: Every call — including denials and dry runs — is appended to `/data/calendar-audit.log` as JSONL. Secrets are scrubbed before logging.
