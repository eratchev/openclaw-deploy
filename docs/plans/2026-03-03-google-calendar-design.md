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
  server.py          # MCP server, tool handlers, SSE transport, health endpoint
  policies.py        # Policy engine: validate → assess → enforce → execute
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
Read-only. Returns events in the given window. OpenClaw uses this to check for overlap before proposing a new event. Not subject to rate limits. Works even if Redis is unavailable.

### `check_availability(time_min, time_max, duration_minutes)`
Returns free slots and conflicts in the window. Simplifies LLM reasoning — OpenClaw does not need to manually parse `list_events` output to find gaps. Works even if Redis is unavailable.

### `create_event(title, start, end, calendar_id?, description?, recurrence?, execution_mode, idempotency_key?)`
Subject to full policy pipeline. Counts against daily create/delete rate limit.

### `update_event(event_id, changes, calendar_id?, execution_mode)`
Not counted against the daily create/delete rate limit. Separate counter (`GCAL_MAX_UPDATES_PER_DAY`, default: 50) to prevent abuse without blocking normal rescheduling.

### `delete_event(event_id, calendar_id?, execution_mode)`
Always treated as high-impact. Counts against daily create/delete rate limit.

**`execution_mode`**: `"dry_run" | "execute"`
- `dry_run` — runs full validation and policy engine, returns impact assessment, never calls Google, never writes idempotency cache
- `execute` — runs full pipeline and calls Google if policy allows

**`calendar_id`**: Optional on all tools. Defaults to `primary`. Rejected if not in `GCAL_ALLOWED_CALENDARS`.

**Idempotency key semantics (per operation):**
- `create_event`: `SHA256(normalized_event_payload)` or caller-supplied key
- `update_event`: `SHA256(event_id + normalized_changes_payload)` or caller-supplied key
- `delete_event`: `SHA256(event_id)` or caller-supplied key
- Redis TTL: 10 minutes
- Only written on successful `execute` path — dry_run, denied, and error responses never write the cache
- `GCAL_DRY_RUN=true` override also never writes idempotency cache

**Datetime format**: All `start`/`end` values must be ISO 8601 with explicit timezone offset (e.g. `2026-03-15T14:00:00+02:00`). Naive datetimes are rejected at the validation layer. All business hour and weekend evaluation converts the input datetime to `GCAL_USER_TIMEZONE` first — never raw offset comparison.

**Recurrence rules**: Must specify either `COUNT` or `UNTIL` — infinite RRULEs are rejected. Minimum frequency: daily (`FREQ=DAILY`). Hourly and more frequent recurrence is rejected. Maximum recurrence count: `GCAL_MAX_RECURRENCE_COUNT` (default: 52).

---

## 4. Response Shape

Every write tool returns a consistent envelope:

```json
{
  "request_id": "uuid4",
  "status": "safe_to_execute | needs_confirmation | denied | error",
  "impact": {
    "overlaps_existing": true,
    "overlapping_events": [
      {
        "event_id": "...",
        "title": "...",
        "occurrence_start": "...",
        "overlap_minutes": 30,
        "severity": "partial | full"
      }
    ],
    "outside_business_hours": false,
    "is_weekend": false,
    "duration_minutes": 180,
    "recurring": false,
    "recurrence_instances_checked": 12,
    "work_calendar": true
  },
  "normalized_event": { "title": "...", "start": "...", "end": "...", "calendar_id": "..." },
  "event_id": "only present on successful execute",
  "reason": "only present on denied or error"
}
```

`needs_confirmation` — OpenClaw must relay the impact summary to the user and call again with `execution_mode=execute`.
`denied` — hard policy rejection, not overridable regardless of user confirmation.
`error` — Redis unavailable or unexpected failure; no Google call made.

---

## 5. Policy Engine (`policies.py`)

Four explicit phases for every write operation:

### Phase 1: `validate(input)`
Pydantic layer — rejects malformed input before any policy logic runs:
- Datetime must have explicit timezone offset — no naive datetimes
- `start` must be before `end`
- Duration must be > 0
- Duration must be ≤ `GCAL_MAX_EVENT_HOURS`
- `start` must not be more than `GCAL_MAX_PAST_HOURS` in the past
- Recurrence: must have `COUNT` or `UNTIL`, no `FREQ=HOURLY` or more frequent, count ≤ `GCAL_MAX_RECURRENCE_COUNT`

### Phase 2: `assess(input)` → `ImpactModel`
Produces a complete impact assessment without making any policy decisions.

All datetime evaluation converts to `GCAL_USER_TIMEZONE` before applying business hour or weekend rules.

**For non-recurring events:**
- Check overlap: call `list_events` for the proposed window, return overlapping events with `overlap_minutes` and `partial | full` severity

**For recurring events:**
- Expand all recurrence instances (bounded by `COUNT` or `UNTIL`)
- For each occurrence: run conflict check against existing events
- Aggregate all conflicts into `overlapping_events` list with per-instance `occurrence_start`
- `recurrence_instances_checked` field reports how many occurrences were evaluated

**All events:**
- Check business hours and weekend
- Check duration threshold
- Check work calendar flag
- Check recurrence presence

### Phase 3: `enforce(impact)` → `status`
Applies policy rules to the impact model. Returns one of four outcomes:

**`error`** — Redis unavailable (for write operations that depend on rate limiting/idempotency):
- Write operations fail closed — no Google call
- Read-only tools (`list_events`, `check_availability`) are unaffected

**`denied`** — hard rejection, not overridable:
- `calendar_id` not in allowlist
- Recurring event on work calendar outside business hours
- Recurrence rule violates frequency or count limits

**`needs_confirmation`** — any of:
- Any overlap with existing event (including any recurrence instance)
- Duration > 2 hours
- Outside business hours
- Is weekend
- Is work calendar (recurring or not)
- Has recurrence rule
- `delete_event` (always)

**`safe_to_execute`** — none of the above triggered.

**Intent note on recurring + work calendar inside business hours:** This combination always requires confirmation (not denial). Recurring work meetings carry inherent risk of long-term calendar impact; confirmation is the right gate, not a hard block.

### Phase 4: `execute(impact, status, execution_mode)`
Only runs when `status == safe_to_execute` AND `execution_mode == execute` AND `GCAL_DRY_RUN != true`:
1. Check rate limit — Redis key: `rate_limit:<calendar_id>:YYYY-MM-DD` (TTL 48h, scoped per calendar so work and personal limits are independent)
2. Check idempotency — Redis, 10-minute TTL
3. Call Google Calendar API
4. Write audit log entry

In all other cases (dry_run, needs_confirmation, denied, error): write audit log entry and return. Never write idempotency cache.

### Dry-run override
If `GCAL_DRY_RUN=true`, all write operations are forced to `execution_mode=dry_run` regardless of tool input. Emits a loud startup warning:
```
[WARN] *** DRY_RUN MODE ACTIVE — no calendar writes will be executed ***
```

---

## 6. Denial Matrix

| Combination | Status |
|---|---|
| `calendar_id` not in allowlist | `denied` |
| Recurring + work calendar + outside business hours | `denied` |
| Recurrence frequency < daily (hourly etc.) | `denied` |
| Infinite RRULE (no COUNT or UNTIL) | `denied` |
| Recurrence count > `GCAL_MAX_RECURRENCE_COUNT` | `denied` |
| Redis unavailable (write operations only) | `error` |
| Recurring + work calendar + inside business hours | `needs_confirmation` |
| Recurring + personal calendar | `needs_confirmation` |
| Non-recurring + work calendar (any time) | `needs_confirmation` |
| Non-recurring + outside business hours | `needs_confirmation` |
| Non-recurring + weekend | `needs_confirmation` |
| Non-recurring + duration > 2h | `needs_confirmation` |
| `delete_event` (always) | `needs_confirmation` |
| Any overlap (including any recurrence instance) | `needs_confirmation` |
| Non-recurring + personal + inside hours + ≤ 2h + no overlap | `safe_to_execute` |

---

## 7. Audit Log (`audit.py`)

Append-only JSONL at `/data/calendar-audit.log`.

Every tool call — including dry runs, denials, and errors — produces one log entry:

```json
{
  "time": "2026-03-15T14:00:00Z",
  "request_id": "uuid4",
  "tool": "create_event",
  "tool_version": "v1",
  "execution_mode": "execute",
  "session_id": "...",
  "request_hash": "sha256:...",
  "args": { "title": "...", "start": "...", "end": "...", "calendar_id": "primary" },
  "status": "created | dry_run | needs_confirmation | denied | error",
  "event_id": "only on created",
  "reason": "only on denied or error",
  "duration_ms": 142
}
```

**Never logged:** token contents, encryption key, raw OAuth credentials, full environment.

**Log rotation:** checked at startup only — avoids concurrent write issues. If `/data/calendar-audit.log` exceeds `GCAL_AUDIT_MAX_MB` (default: 50MB) at startup, it is renamed to `calendar-audit.log.1` before writing begins.

**For long-running containers:** configure host-level `logrotate` to handle unbounded growth between restarts. Example `/etc/logrotate.d/openclaw-calendar`:
```
/var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data/calendar-audit.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}
```

---

## 8. OAuth Token Lifecycle (`auth.py`)

Token stored encrypted at `/data/gcal_token.enc`. Encryption: Fernet (AES-128-CBC + HMAC-SHA256).

OAuth scope: `https://www.googleapis.com/auth/calendar.events` — minimal scope, no full calendar management access.

**Startup:**
```python
if not os.environ.get("GCAL_TOKEN_ENCRYPTION_KEY"):
    raise RuntimeError("Missing GCAL_TOKEN_ENCRYPTION_KEY — refusing to start")

encrypted = Path("/data/gcal_token.enc").read_bytes()
credentials = decrypt_and_load(encrypted, key=os.environ["GCAL_TOKEN_ENCRYPTION_KEY"])
# credentials object lives in memory only — never written back decrypted
# key is never logged or printed; environment is never dumped to logs
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

## 9. Health Endpoint

`GET /health` (internal network only) returns:

```json
{
  "redis": "ok | error",
  "token": "ok | error",
  "google_api": "ok | error | skipped",
  "dry_run_mode": false
}
```

- `redis`: ping check — always run
- `token`: decrypt and validate credentials object (non-destructive, no API call) — always run
- `google_api`: lightweight non-destructive API call (e.g. list calendar metadata) — only run if `GCAL_HEALTH_CHECK_GOOGLE=true`. Defaults to `skipped` to avoid health failures due to Google API unavailability or rate limits triggering container restart loops.

HTTP 200 if `redis` and `token` are both `ok`. HTTP 503 otherwise.

---

## 10. Docker Compose Addition

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
    - GCAL_MAX_UPDATES_PER_DAY=${GCAL_MAX_UPDATES_PER_DAY:-50}
    - GCAL_MAX_EVENT_HOURS=${GCAL_MAX_EVENT_HOURS:-8}
    - GCAL_MAX_PAST_HOURS=${GCAL_MAX_PAST_HOURS:-1}
    - GCAL_ALLOWED_START_HOUR=${GCAL_ALLOWED_START_HOUR:-8}
    - GCAL_ALLOWED_END_HOUR=${GCAL_ALLOWED_END_HOUR:-20}
    - GCAL_USER_TIMEZONE=${GCAL_USER_TIMEZONE:-UTC}
    - GCAL_WORK_CALENDAR_ID=${GCAL_WORK_CALENDAR_ID:-}
    - GCAL_MAX_RECURRENCE_COUNT=${GCAL_MAX_RECURRENCE_COUNT:-52}
    - GCAL_AUDIT_MAX_MB=${GCAL_AUDIT_MAX_MB:-50}
    - GCAL_DRY_RUN=${GCAL_DRY_RUN:-false}
    - GCAL_HEALTH_CHECK_GOOGLE=${GCAL_HEALTH_CHECK_GOOGLE:-false}
    - GCAL_TOKEN_ENCRYPTION_KEY=${GCAL_TOKEN_ENCRYPTION_KEY}
    - REDIS_URL=redis://redis:6379
  healthcheck:
    test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
    interval: 30s
    timeout: 5s
    retries: 3
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

## 11. One-Time Token Setup (local → VPS)

Run once on your Mac after creating a Google Cloud project and enabling the Calendar API.
Use OAuth scope `https://www.googleapis.com/auth/calendar.events` when configuring credentials.

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

## 12. Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `GCAL_TOKEN_ENCRYPTION_KEY` | **required** | Fernet key for token.enc — no default, fail fast if missing |
| `GCAL_ALLOWED_CALENDARS` | `primary` | Comma-separated calendar IDs allowed for write operations |
| `GCAL_WORK_CALENDAR_ID` | `` | Calendar ID treated as work calendar (triggers needs_confirmation) |
| `GCAL_MAX_EVENTS_PER_DAY` | `10` | Daily rate limit for create + delete, scoped per calendar |
| `GCAL_MAX_UPDATES_PER_DAY` | `50` | Daily rate limit for update, scoped per calendar |
| `GCAL_MAX_EVENT_HOURS` | `8` | Maximum event duration — longer events rejected at validation |
| `GCAL_MAX_PAST_HOURS` | `1` | How far in the past an event start time is allowed |
| `GCAL_ALLOWED_START_HOUR` | `8` | Business hours start (evaluated in GCAL_USER_TIMEZONE) |
| `GCAL_ALLOWED_END_HOUR` | `20` | Business hours end (evaluated in GCAL_USER_TIMEZONE) |
| `GCAL_USER_TIMEZONE` | `UTC` | Timezone for all business hours, weekend, and rate limit date checks |
| `GCAL_MAX_RECURRENCE_COUNT` | `52` | Maximum recurrence count in RRULE |
| `GCAL_AUDIT_MAX_MB` | `50` | Audit log rotation threshold (checked at startup) |
| `GCAL_DRY_RUN` | `false` | Force all writes to dry_run mode — emits loud startup warning, never writes idempotency cache |
| `GCAL_HEALTH_CHECK_GOOGLE` | `false` | Include Google API liveness check in /health — disabled by default to avoid third-party restart cascades |
| `REDIS_URL` | `redis://redis:6379` | Redis connection for rate limits and idempotency |

---

## 13. Threat Model

| Scenario | Outcome |
|---|---|
| OpenClaw container compromised | Safe — Google credentials never in OpenClaw container |
| `calendar-proxy` container compromised | Attacker needs both `gcal_token.enc` and `GCAL_TOKEN_ENCRYPTION_KEY` (env var) to get Google access. Containment relies on container isolation, host security, and SSH hygiene. |
| Volume (`/data`) exfiltrated | `gcal_token.enc` without the key is useless |
| LLM prompt injection triggers calendar write | Policy engine enforces hard denials and confirmation gates regardless of LLM intent |
| Prompt injection: "create recurring work event every day at 3am" | Denied — recurring + work calendar + outside business hours = hard denial |
| Prompt injection: "create recurring hourly event" | Denied — FREQ=HOURLY rejected at validation layer |
| Retry storm / duplicate tool calls | Idempotency layer deduplicates within 10-minute window (execute path only) |
| Redis unavailable | Write operations fail closed (`status=error`, no Google call). Read-only tools (list_events, check_availability) continue to work. |
| Environment variable exposure via `docker inspect` | `GCAL_TOKEN_ENCRYPTION_KEY` visible to VPS shell access. Acceptable for single-user personal deployment. Upgrade path: Docker secrets or host-level env injection. |
| Accidental environment dump in logs | Key is never logged or printed; environment is never dumped |

**Not protected against:** full container compromise, VPS root compromise, Google account compromise, SSH key compromise. Acceptable for personal single-node deployment.

**Future hardening:** Restrict `calendar-proxy` outbound egress to `*.googleapis.com` only via host iptables rules. Not included in v1 — requires host-level config outside Docker Compose.
