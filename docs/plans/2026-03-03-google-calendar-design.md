# Google Calendar Integration — Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Connect OpenClaw to Google Calendar via an isolated MCP proxy service that enforces guardrails, audits every operation, and never exposes OAuth tokens to the LLM.

**Architecture:** A `calendar-proxy` Python MCP server runs inside the existing Docker Compose stack on the internal network. OpenClaw calls it via MCP tools. The proxy holds all Google credentials and enforces a layered policy engine before any Google API call is made.

**Tech Stack:** Python, `mcp` library (SSE transport), `google-api-python-client`, `google-auth-oauthlib`, `pydantic`, `cryptography` (Fernet), Redis (rate limiting + idempotency), Docker Compose.

---

## 1. Topology

```
Telegram/WhatsApp
      │
  OpenClaw (container)
      │  MCP over SSE — http://calendar-proxy:8080 (internal network only)
      ▼
calendar-proxy (container)
      │  Google Calendar API (HTTPS, outbound)
      ▼
   Google
```

- `calendar-proxy` joins the existing `internal` Docker network
- No published ports — unreachable from the internet
- Only `calendar-proxy` holds Google OAuth credentials
- OpenClaw never has direct Google API access (credentials never in OpenClaw container)
- Redis (already running) used for rate limiting and idempotency

---

## 2. File Layout

```
services/calendar-proxy/
  server.py          # MCP server, tool handlers, SSE transport
  policies.py        # Policy engine: allowlist, rate limits, high-impact detection, hard denials
  models.py          # Pydantic schemas for all tool inputs and responses
  auth.py            # OAuth token load/decrypt/refresh/re-encrypt lifecycle
  audit.py           # Append-only JSONL audit log writer
  requirements.txt
  Dockerfile

  scripts/
    auth_setup.py    # One-time local OAuth flow → writes token.json
    encrypt_token.py # Encrypts token.json → token.enc using Fernet key
```

**Changes to existing files:**
- `docker-compose.yml` — add `calendar-proxy` service
- `.env.example` — add all `GCAL_*` variables

---

## 3. MCP Tools

All tools exposed to OpenClaw. All inputs validated by Pydantic before reaching the policy engine.

### `list_events(calendar_id?, time_min, time_max)`
Read-only. Returns events in the given window. OpenClaw uses this to check for overlap before proposing a new event. Not subject to rate limits.

### `check_availability(time_min, time_max, duration_minutes)`
Returns free slots and conflicts in the window. Simplifies LLM reasoning — OpenClaw does not need to manually parse `list_events` output to find gaps.

### `create_event(title, start, end, calendar_id?, description?, recurrence?, execution_mode, idempotency_key?)`

### `update_event(event_id, changes, calendar_id?, execution_mode)`

### `delete_event(event_id, calendar_id?, execution_mode)`
Always treated as high-impact.

**`execution_mode`**: `"dry_run" | "execute"`
- `dry_run` — runs full validation and policy engine, returns impact assessment, never calls Google
- `execute` — runs full pipeline and calls Google if policy allows

**`calendar_id`**: Optional on all tools. Defaults to `primary`. Rejected if not in `GCAL_ALLOWED_CALENDARS`.

**`idempotency_key`**: Optional. If omitted, proxy computes `SHA256(normalized_event_payload)`. Checked against Redis with 60-second TTL. Duplicate returns existing event ID without a second create.

**Datetime format**: All `start`/`end` values must be ISO 8601 with explicit timezone offset (e.g. `2026-03-15T14:00:00+02:00`). Naive datetimes are rejected at the validation layer.

---

## 4. Response Shape

Every write tool returns a consistent envelope:

```json
{
  "status": "safe_to_execute | needs_confirmation | denied",
  "impact": {
    "overlaps_existing": true,
    "overlapping_events": [
      { "event_id": "...", "title": "...", "overlap_minutes": 30, "severity": "partial | full" }
    ],
    "outside_business_hours": false,
    "is_weekend": false,
    "duration_minutes": 180,
    "recurring": false,
    "work_calendar": true
  },
  "normalized_event": { "title": "...", "start": "...", "end": "...", "calendar_id": "..." },
  "event_id": "only present on successful execute",
  "reason": "only present on denied"
}
```

`needs_confirmation` — OpenClaw must relay the impact summary to the user and call again with `execution_mode=execute`.
`denied` — hard policy rejection, not overridable regardless of user confirmation.

---

## 5. Policy Engine (`policies.py`)

Enforcement sequence for every write operation:

```
Input
  → Pydantic validation
  → Allowlist check
  → Rate limit check
  → Duration + temporal validation
  → High-impact detection
  → Hard denial check
  → Idempotency check
  → Conflict check
  → Google API call  (execute mode only)
  → Audit log
```

### Validation layer (Pydantic — rejects before policy runs)
- Datetime must have explicit timezone offset — no naive datetimes
- `start` must be before `end`
- Duration must be > 0
- Duration must be ≤ `GCAL_MAX_EVENT_HOURS`
- `start` must not be more than `GCAL_MAX_PAST_HOURS` in the past

### Allowlist
- `GCAL_ALLOWED_CALENDARS` — comma-separated calendar IDs
- Fails closed: if env var is unset, only `primary` is allowed
- "All calendars" is never implicitly allowed
- Calendar ID format validated

### Rate limit
- Redis `INCR` with TTL reset at midnight in `GCAL_USER_TIMEZONE`
- Limit: `GCAL_MAX_EVENTS_PER_DAY` (default: 10)
- Atomic — no race conditions, no file corruption

### High-impact detection → `needs_confirmation`
Any of the following triggers `status: needs_confirmation`:
- Duration > 2 hours
- Start time before `GCAL_ALLOWED_START_HOUR` (default: 8) in `GCAL_USER_TIMEZONE`
- Start time after `GCAL_ALLOWED_END_HOUR` (default: 20) in `GCAL_USER_TIMEZONE`
- Day is Saturday or Sunday (evaluated in `GCAL_USER_TIMEZONE`)
- `calendar_id` matches `GCAL_WORK_CALENDAR_ID`
- Event has a recurrence rule

### Hard denial → `denied` (not overridable)
Specific combinations are always rejected regardless of user input:
- Recurring event on work calendar outside business hours
- Any event outside allowlist

### Idempotency
- Key: `SHA256(normalized_event_payload)` or caller-supplied `idempotency_key`
- Stored in Redis with 60-second TTL
- On hit: return existing `event_id`, no second create

### Conflict check
- Calls `list_events` for the proposed window
- Returns structured conflict data with `overlap_minutes` and `partial | full` severity
- Included in `impact` field of response

### Dry-run override
- If `GCAL_DRY_RUN=true` (env var), all write operations are forced to `dry_run` regardless of `execution_mode` in the tool call — prevents accidental real writes during testing

---

## 6. Audit Log (`audit.py`)

Append-only JSONL at `/data/calendar-audit.log`.

Every tool call — including dry runs and denials — produces one log entry:

```json
{
  "time": "2026-03-15T14:00:00Z",
  "tool": "create_event",
  "tool_version": "v1",
  "execution_mode": "execute",
  "session_id": "...",
  "request_hash": "sha256:...",
  "args": { "title": "...", "start": "...", "end": "...", "calendar_id": "primary" },
  "status": "created | dry_run | needs_confirmation | denied | error",
  "event_id": "only on created",
  "reason": "only on denied",
  "duration_ms": 142
}
```

**Never logged:** token contents, encryption key, raw OAuth credentials.

Log rotation: when file exceeds `GCAL_AUDIT_MAX_MB` (default: 50MB), current file is renamed to `calendar-audit.log.1` and a new file started.

---

## 7. OAuth Token Lifecycle (`auth.py`)

Token stored encrypted at `/data/gcal_token.enc`. Encryption: Fernet (AES-128-CBC + HMAC-SHA256).

**Startup:**
```python
if not os.environ.get("GCAL_TOKEN_ENCRYPTION_KEY"):
    raise RuntimeError("Missing GCAL_TOKEN_ENCRYPTION_KEY — refusing to start")

encrypted = Path("/data/gcal_token.enc").read_bytes()
credentials = decrypt_and_load(encrypted, key=os.environ["GCAL_TOKEN_ENCRYPTION_KEY"])
# credentials object lives in memory only — never written back decrypted
```

**On token refresh:**
```python
# Atomic write — never overwrite directly
tmp = Path("/data/gcal_token.enc.tmp")
tmp.write_bytes(encrypt(refreshed_credentials, key))
tmp.replace(Path("/data/gcal_token.enc"))  # atomic rename on Linux
```

If container crashes between refresh and rename, the previous `gcal_token.enc` is intact.

File permissions: `600` — readable only by the process user.

---

## 8. Docker Compose Addition

```yaml
calendar-proxy:
  build: ./services/calendar-proxy
  restart: unless-stopped
  networks:
    - internal
  depends_on:
    - redis
  volumes:
    - openclaw_data:/data:rw
  environment:
    - GCAL_ALLOWED_CALENDARS=${GCAL_ALLOWED_CALENDARS:-primary}
    - GCAL_MAX_EVENTS_PER_DAY=${GCAL_MAX_EVENTS_PER_DAY:-10}
    - GCAL_MAX_EVENT_HOURS=${GCAL_MAX_EVENT_HOURS:-8}
    - GCAL_MAX_PAST_HOURS=${GCAL_MAX_PAST_HOURS:-1}
    - GCAL_ALLOWED_START_HOUR=${GCAL_ALLOWED_START_HOUR:-8}
    - GCAL_ALLOWED_END_HOUR=${GCAL_ALLOWED_END_HOUR:-20}
    - GCAL_USER_TIMEZONE=${GCAL_USER_TIMEZONE:-UTC}
    - GCAL_WORK_CALENDAR_ID=${GCAL_WORK_CALENDAR_ID:-}
    - GCAL_AUDIT_MAX_MB=${GCAL_AUDIT_MAX_MB:-50}
    - GCAL_DRY_RUN=${GCAL_DRY_RUN:-false}
    - GCAL_TOKEN_ENCRYPTION_KEY=${GCAL_TOKEN_ENCRYPTION_KEY}
    - REDIS_URL=redis://redis:6379
  cap_drop:
    - ALL
  read_only: true
  tmpfs:
    - /tmp
  security_opt:
    - no-new-privileges:true
  mem_limit: 256m
  cpus: "0.5"
```

No `ports:` — not reachable from outside the internal network.

---

## 9. One-Time Token Setup (local → VPS)

Run once on your Mac after creating a Google Cloud project and enabling the Calendar API.

```bash
# 1. Download OAuth2 credentials from Google Cloud Console → client_secret.json

# 2. Run auth setup script — opens browser, writes token.json
python3 services/calendar-proxy/scripts/auth_setup.py \
  --client-secret client_secret.json \
  --out token.json

# 3. Generate Fernet encryption key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Save output as GCAL_TOKEN_ENCRYPTION_KEY in .env (local + VPS)

# 4. Encrypt token
python3 services/calendar-proxy/scripts/encrypt_token.py \
  --token token.json \
  --key <GCAL_TOKEN_ENCRYPTION_KEY> \
  --out token.enc

# 5. Copy encrypted token to VPS volume
scp token.enc user@YOUR_VPS_IP:/tmp/
ssh user@YOUR_VPS_IP "
  docker run --rm \
    -v openclaw-deploy_openclaw_data:/data \
    -v /tmp:/src \
    busybox sh -c 'cp /src/token.enc /data/gcal_token.enc && chmod 600 /data/gcal_token.enc'
"

# 6. Clean up local files
rm client_secret.json token.json token.enc
# On macOS without FileVault: shred -u token.json before rm
```

Add to VPS `.env`:
```
GCAL_TOKEN_ENCRYPTION_KEY=<key from step 3>
GCAL_USER_TIMEZONE=Europe/Helsinki  # or your timezone
GCAL_WORK_CALENDAR_ID=<your work calendar ID>
GCAL_ALLOWED_CALENDARS=primary,<work calendar ID>
```

---

## 10. Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `GCAL_TOKEN_ENCRYPTION_KEY` | **required** | Fernet key for token.enc — no default, fail fast if missing |
| `GCAL_ALLOWED_CALENDARS` | `primary` | Comma-separated calendar IDs allowed for write operations |
| `GCAL_WORK_CALENDAR_ID` | `` | Calendar ID treated as work calendar (triggers high-impact) |
| `GCAL_MAX_EVENTS_PER_DAY` | `10` | Daily rate limit for create/update/delete |
| `GCAL_MAX_EVENT_HOURS` | `8` | Maximum event duration — longer events rejected |
| `GCAL_MAX_PAST_HOURS` | `1` | How far in the past an event start time is allowed |
| `GCAL_ALLOWED_START_HOUR` | `8` | Business hours start (in user timezone) |
| `GCAL_ALLOWED_END_HOUR` | `20` | Business hours end (in user timezone) |
| `GCAL_USER_TIMEZONE` | `UTC` | Timezone for all business hours and weekend checks |
| `GCAL_AUDIT_MAX_MB` | `50` | Audit log rotation threshold |
| `GCAL_DRY_RUN` | `false` | Force all writes to dry_run mode |
| `REDIS_URL` | `redis://redis:6379` | Redis connection for rate limits and idempotency |

---

## 11. Threat Model

| Scenario | Outcome |
|---|---|
| OpenClaw container compromised | Safe — Google credentials never in OpenClaw container |
| `calendar-proxy` container compromised | Attacker needs both `gcal_token.enc` and `GCAL_TOKEN_ENCRYPTION_KEY` (env var) to get Google access |
| Volume (`/data`) exfiltrated | `gcal_token.enc` without the key is useless |
| LLM prompt injection triggers calendar write | Policy engine enforces limits and confirmation gates regardless of LLM intent |
| Retry storm / duplicate tool calls | Idempotency layer deduplicates within 60-second window |

**Known gap:** `GCAL_TOKEN_ENCRYPTION_KEY` stored in `.env` means VPS shell access + `docker inspect` reveals the key. Acceptable for single-user personal deployment. Upgrade path: Docker secrets or host-level environment injection not stored in the repo.

**Future hardening:** Restrict `calendar-proxy` outbound egress to `*.googleapis.com` only via host iptables rules. Not included in v1 — requires host-level config outside Docker Compose.
