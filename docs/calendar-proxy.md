# Google Calendar Proxy — Operations Guide

MCP server that gives OpenClaw controlled Google Calendar access. Runs on the internal Docker network; never reachable from the internet.

## First Deploy

See the [README](../README.md#google-calendar-integration) for the one-time OAuth setup (key generation, token auth, encryption, copy to VPS).

Required `.env` additions on the VPS before `make up`:

```bash
GCAL_TOKEN_ENCRYPTION_KEY=<fernet-key>
GCAL_USER_TIMEZONE=Europe/Helsinki   # your local timezone
GCAL_ALLOWED_CALENDARS=primary       # comma-separated calendar IDs
```

## Configuration Reference

All variables have safe defaults. Only `GCAL_TOKEN_ENCRYPTION_KEY` is required.

| Variable | Default | Description |
|----------|---------|-------------|
| `GCAL_TOKEN_ENCRYPTION_KEY` | — | **Required.** Fernet key for OAuth token encryption |
| `GCAL_ALLOWED_CALENDARS` | `primary` | Comma-separated calendar IDs that may be written to |
| `GCAL_WORK_CALENDAR_ID` | _(unset)_ | Calendar treated as work — any write requires confirmation |
| `GCAL_USER_TIMEZONE` | `UTC` | Timezone for business hours + weekend evaluation |
| `GCAL_ALLOWED_START_HOUR` | `8` | Business hours start (local time) |
| `GCAL_ALLOWED_END_HOUR` | `20` | Business hours end (local time) |
| `GCAL_MAX_EVENTS_PER_DAY` | `10` | Max create operations per calendar per day |
| `GCAL_MAX_UPDATES_PER_DAY` | `50` | Max update operations per calendar per day |
| `GCAL_MAX_EVENT_HOURS` | `8` | Max event duration in hours (validation, not policy) |
| `GCAL_MAX_PAST_HOURS` | `1` | How far in the past a start time may be |
| `GCAL_MAX_RECURRENCE_COUNT` | `52` | Max COUNT in a RRULE |
| `GCAL_AUDIT_MAX_MB` | `50` | Audit log rotated at startup when it exceeds this |
| `GCAL_DRY_RUN` | `false` | Force dry-run on all writes (for testing) |
| `GCAL_HEALTH_CHECK_GOOGLE` | `false` | Include Google API check in `/health` |

After changing `.env`, run `docker compose up -d` (not `restart`) to pick up new values.

## Health Check

```bash
# From the VPS
docker compose exec calendar-proxy python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/health').read().decode())"
```

Expected response:
```json
{"dry_run_mode": false, "redis": "ok", "token": "ok", "google_api": "skipped"}
```

- `redis: "error: ..."` — Redis is down or misconfigured. Check `REDIS_URL` and that the `redis` container is running.
- `token: "error: ..."` — Token file missing or key wrong. Re-run the token setup steps.
- `google_api: "error: ..."` — Only shown when `GCAL_HEALTH_CHECK_GOOGLE=true`. Token may be expired; re-authenticate.

## Token Refresh

Google OAuth tokens expire. The proxy refreshes them automatically using the stored `refresh_token` and writes the updated token back atomically (encrypt → tmp → rename). No manual intervention needed under normal operation.

If the refresh token itself expires (e.g. Google revokes it after 6 months of inactivity, or you revoke access in Google's security settings), you will see `token: "error: ..."` in the health check. Re-run the one-time setup:

```bash
# On your local machine
python3 services/calendar-proxy/scripts/auth_setup.py \
  --client-secret client_secret.json --out token.json

python3 services/calendar-proxy/scripts/encrypt_token.py \
  --token token.json --key <GCAL_TOKEN_ENCRYPTION_KEY> --out token.enc

scp token.enc user@<vps>:/tmp/
ssh user@<vps> "
  docker run --rm \
    -v openclaw-deploy_openclaw_data:/data \
    -v /tmp:/src \
    busybox sh -c 'cp /src/token.enc /data/gcal_token.enc && chmod 600 /data/gcal_token.enc'
"
rm client_secret.json token.json token.enc
```

No container restart needed — the proxy reads the token file on each request.

## Audit Log

Every call is appended to `/data/calendar-audit.log` as JSONL (one JSON object per line). The log is rotated at startup when it exceeds `GCAL_AUDIT_MAX_MB`.

```bash
# Tail the audit log live
docker compose exec calendar-proxy tail -f /data/calendar-audit.log | python3 -m json.tool

# Count executions by status today
docker compose exec calendar-proxy grep "$(date +%Y-%m-%d)" /data/calendar-audit.log \
  | python3 -c "import sys,json; [print(json.loads(l)['status']) for l in sys.stdin]" \
  | sort | uniq -c
```

Fields in each entry: `time`, `request_id`, `tool`, `tool_version`, `execution_mode`, `session_id`, `args` (secrets scrubbed), `status`, `duration_ms`, and optionally `event_id`, `reason`, `request_hash`.

## Policy Engine

All write operations flow through four phases:

1. **Validate** — Pydantic model checks (timezone-aware datetimes, duration limits, valid RRULE).
2. **Assess** — Expand all recurrence instances, check conflicts against existing events, evaluate business hours and weekend in `GCAL_USER_TIMEZONE`.
3. **Enforce** — Apply policy rules → `safe_to_execute`, `needs_confirmation`, or `denied`.
4. **Execute** — Rate limit check → idempotency check → Google API call → record idempotency.

### Hard Denials (not overridable)

- Calendar not in `GCAL_ALLOWED_CALENDARS`
- Recurring event on work calendar outside business hours or on weekend
- RRULE with frequency finer than daily (`HOURLY`, `MINUTELY`)
- Infinite RRULE (no `COUNT` or `UNTIL`)
- `COUNT` exceeding `GCAL_MAX_RECURRENCE_COUNT`

### Requires Confirmation

The LLM must present the impact to the user and receive explicit `execution_mode=execute`:

- Any time overlap with an existing event (checked on every recurrence instance)
- Duration > 2 hours
- Outside business hours
- Weekend
- Work calendar
- Any recurring event
- Any deletion

### Idempotency

Repeated identical requests within 10 minutes return the original `event_id` without re-calling Google. Keys are SHA256 hashes of the operation payload. Only successful executions are cached; dry runs are never cached.

## Dry-Run Mode

Set `GCAL_DRY_RUN=true` in `.env` and `docker compose up -d` to force all writes into dry-run mode regardless of what the LLM passes. The container logs a loud warning at startup. Useful for testing policy behaviour without touching real calendar data.

## Rate Limits

Rate limit counters are stored in Redis, keyed by `rate_limit:<calendar_id>:<YYYY-MM-DD>`. They use a 48-hour TTL (no DST arithmetic needed). You can inspect or reset them directly:

```bash
# Check current daily create count for primary calendar
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" \
  GET "rate_limit:primary:$(date +%Y-%m-%d)"

# Reset (e.g. after a test run)
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" \
  DEL "rate_limit:primary:$(date +%Y-%m-%d)"
```

## Troubleshooting

### Container fails to start — `Missing GCAL_TOKEN_ENCRYPTION_KEY`

`GCAL_TOKEN_ENCRYPTION_KEY` is not set in `.env`. Generate and add it:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Add GCAL_TOKEN_ENCRYPTION_KEY=<output> to .env
docker compose up -d
```

### Health check shows `token: "error: ..."`

Either the token file is missing from `/data/gcal_token.enc` or the encryption key doesn't match. Re-run the token setup steps above.

### Health check shows `redis: "error: ..."`

The proxy cannot reach Redis. Check:
```bash
docker compose ps redis          # is it running?
docker compose logs redis        # any errors?
```

The `REDIS_URL` in the `calendar-proxy` service is hardcoded to `redis://redis:6379` (no auth), which assumes both containers are on the `internal` network. This is correct — the proxy does not need to use `REDIS_PASSWORD` because it connects via the internal network, not the host interface.

### `denied: calendar_id '...' is not in the allowlist`

The LLM passed a calendar ID not in `GCAL_ALLOWED_CALENDARS`. Either add the calendar ID to the env var (comma-separated) and `docker compose up -d`, or confirm the LLM is using the right ID.

### `needs_confirmation` returned unexpectedly

Inspect the `impact` field in the response to see what triggered confirmation. Common causes: the event overlaps an existing one, it's outside business hours, or the calendar is the configured work calendar. The LLM should surface this to the user before re-submitting with `execution_mode=execute`.
