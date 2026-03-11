# Hardening & Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Harden voice-proxy auth, secure the backup execution model, add product profiles, improve the ops runbook, and extend `make doctor` with richer checks.

**Architecture:** Five independent improvements to existing files — no new services, no new dependencies. Tasks are ordered by value: docs first (cheap), then security, then UX.

**Tech Stack:** bash, Python/aiohttp, Docker Compose, pytest/asyncio

---

## Task Order (implement in this sequence)

1. Unified ops runbook (docs only)
2. Secure backup execution (shell security fix)
3. Product profiles (Docker Compose + Makefile)
4. Voice webhook pre-auth (Python security fix)
5. `make doctor` enhancements (shell + diagnositics)

---

### Task 1: Unified Ops Runbook

**Files:**
- Create: `docs/runbook.md`

No tests required — this is a documentation task.

**Step 1: Create `docs/runbook.md`**

```markdown
# OpenClaw Ops Runbook

Quick reference for the most common operational procedures.

---

## First-Time Deploy

```bash
make deploy HOST=user@x.x.x.x
```

Prompts for: domain, Telegram token, Anthropic API key.
Auto-generates: REDIS_PASSWORD, WEBHOOK_SECRET.
Optional: OpenAI key (voice transcription), Hetzner S3 (backups).

After deploy completes:
```bash
make doctor               # verify all checks pass
make logs                 # watch openclaw startup
```

---

## Daily Operations

| Command | Purpose |
|---|---|
| `make logs` | Follow openclaw logs |
| `make logs-all` | Follow all service logs |
| `make status` | Show container CPU/mem usage |
| `make doctor` | Run health checks |
| `make backup` | Local backup to ./backups/ |
| `make backup-remote` | Upload backup to S3 |

---

## Update OpenClaw (new version)

```bash
make backup-remote        # snapshot first
make update               # pull latest image + restart
make doctor               # verify healthy
```

---

## WhatsApp Pairing

```bash
make pair-whatsapp        # renders QR code in your terminal
```

Scan with WhatsApp → Linked Devices → Link a Device.
If the QR never appears or you see status 440 errors:
1. Open WhatsApp on your phone
2. Settings → Linked Devices → remove all "OpenClaw" entries
3. Re-run `make pair-whatsapp`

---

## Voice Transcription

Requires `OPENAI_API_KEY` in `.env`.

```bash
make up-voice             # build + start voice-proxy
make doctor               # verify voice-proxy is healthy
```

To disable, stop and remove the voice-proxy container:
```bash
docker compose stop voice-proxy
docker compose rm -f voice-proxy
```

---

## Emergency Kill Switch

Immediately terminates the OpenClaw process:
```bash
make kill-switch
```

To resume after a kill switch:
```bash
# Remove the kill-switch file from the data volume
docker run --rm -v openclaw-deploy_openclaw_data:/data \
  busybox rm -f /data/GUARDRAIL_DISABLE
make restart
```

---

## Rollback to Previous Image

```bash
# Find the previous image digest
docker image ls ghcr.io/openclaw/openclaw --digests

# Pin to previous digest in docker-compose.yml temporarily
# image: ghcr.io/openclaw/openclaw@sha256:<digest>
docker compose up -d --no-deps openclaw
make doctor
```

---

## Restore from Backup

```bash
# Stop openclaw (keep redis running)
docker compose stop openclaw

# Find the backup to restore
ls backups/
# or: aws s3 ls s3://<BACKUP_S3_BUCKET>/ --endpoint-url <BACKUP_S3_ENDPOINT>

# Restore into volume
docker run --rm \
  -v openclaw-deploy_openclaw_data:/target \
  -v $(pwd)/backups:/backups:ro \
  busybox sh -c "cd /target && tar xzf /backups/<filename>.tar.gz"

docker compose start openclaw
make doctor
```

---

## Troubleshooting

### CPU spike (>100%)

1. `make status` — identify which container
2. If openclaw: check for a WhatsApp session conflict
   - `docker compose logs openclaw | grep "status=440"` — if seen, a competing session exists
   - Fix: WhatsApp → Linked Devices → remove duplicate entries → `make pair-whatsapp`
3. If openclaw running normally but high: `make kill-switch` to stop the current agent session

### `make doctor` shows services not healthy

- Wait 30–60s after start — healthchecks have `start_period`
- `make logs-all` to read container errors
- Rerun `make doctor`

### Telegram webhook not registered

```bash
make doctor               # shows webhook status
# If "not registered":
docker compose exec openclaw openclaw config set channels.telegram.webhookUrl "https://<DOMAIN>/telegram-webhook"
docker compose restart openclaw
make doctor
```

### Redis auth failure

Usually means `.env` was changed after `docker compose up`. Fix:
```bash
docker compose down
docker compose up -d
```

---

## Google Calendar Setup

See `docs/plans/2026-03-03-google-calendar.md` for the full setup procedure.
Quick check: `make doctor` will report whether `gcal_token.enc` is present.
```

**Step 2: Commit**

```bash
git add docs/runbook.md
git commit -m "docs: add ops runbook with deploy, rollback, troubleshooting procedures"
```

---

### Task 2: Secure Backup Execution

**Problem:** `scripts/backup-cron.sh` uses `source "$ENV_FILE"` which executes `.env` as shell code. If `.env` has a value like `KEY=$(malicious-command)`, it runs as root (the cron job runs as root).

**Fix:** Replace `source` with per-variable `grep/cut` parsing.

**Secondary problem:** `install-backup-cron.sh` points the cron job at `$REPO/scripts/backup-cron.sh` — the mutable git repo path. A `git pull` of malicious content would execute as root next cron tick. Fix: install an immutable copy to `/usr/local/sbin/openclaw-backup` at install time.

**Files:**
- Modify: `scripts/backup-cron.sh`
- Modify: `scripts/install-backup-cron.sh`

No automated tests for shell scripts. Manual verification steps are provided.

**Step 1: Verify the parse_env approach handles values containing `=`**

Run this in your shell to confirm `cut -d= -f2-` correctly captures `abc=123`:
```bash
echo "KEY=abc=123=def" | head -1 | cut -d= -f2-
# expected output: abc=123=def
```

Also verify it handles empty values:
```bash
echo "KEY=" | head -1 | cut -d= -f2-
# expected output: (empty string)
```

**Step 2: Modify `scripts/backup-cron.sh`**

Replace lines 15–19 (the `source` block) and lines 21–25 (the `:?` checks) with a safe parser:

Old code (lines 7–25):
```bash
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[backup] ERROR: $ENV_FILE not found" >&2
  exit 1
fi

# Load .env (export all vars)
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

: "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET not set in .env}"
: "${BACKUP_S3_ENDPOINT:?BACKUP_S3_ENDPOINT not set in .env}"
: "${BACKUP_S3_ACCESS_KEY:?BACKUP_S3_ACCESS_KEY not set in .env}"
: "${BACKUP_S3_SECRET_KEY:?BACKUP_S3_SECRET_KEY not set in .env}"
BACKUP_RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-7}"
```

New code (replace with):
```bash
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[backup] ERROR: $ENV_FILE not found" >&2
  exit 1
fi

# Safe .env parser — reads one variable at a time using grep/cut.
# Unlike 'source', this never executes .env content as shell code.
parse_env() {
  local var=$1
  grep "^${var}=" "$ENV_FILE" | head -1 | cut -d= -f2-
}

BACKUP_S3_BUCKET="$(parse_env BACKUP_S3_BUCKET)"
BACKUP_S3_ENDPOINT="$(parse_env BACKUP_S3_ENDPOINT)"
BACKUP_S3_ACCESS_KEY="$(parse_env BACKUP_S3_ACCESS_KEY)"
BACKUP_S3_SECRET_KEY="$(parse_env BACKUP_S3_SECRET_KEY)"
BACKUP_S3_REGION="$(parse_env BACKUP_S3_REGION)"
BACKUP_RETAIN_DAYS="$(parse_env BACKUP_RETAIN_DAYS)"
BACKUP_RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-7}"

: "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET not set in .env}"
: "${BACKUP_S3_ENDPOINT:?BACKUP_S3_ENDPOINT not set in .env}"
: "${BACKUP_S3_ACCESS_KEY:?BACKUP_S3_ACCESS_KEY not set in .env}"
: "${BACKUP_S3_SECRET_KEY:?BACKUP_S3_SECRET_KEY not set in .env}"
```

**Step 3: Modify `scripts/install-backup-cron.sh`**

Old code (lines 6–8, 16, 18–23):
```bash
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO/scripts/backup-cron.sh"
CRON_ENTRY="0 3 * * * $SCRIPT >> /var/log/openclaw-backup.log 2>&1"
CRON_MARKER="openclaw-backup"

...

chmod +x "$SCRIPT"

# Install into root crontab (idempotent — remove existing entry first)
(
  { crontab -l 2>/dev/null || true; } | { grep -v "$CRON_MARKER" || true; }
  echo "# $CRON_MARKER"
  echo "$CRON_ENTRY"
) | crontab -

echo "[install-backup-cron] Cron job installed (runs daily at 03:00 UTC):"
echo "  $CRON_ENTRY"
echo "[install-backup-cron] Logs will go to /var/log/openclaw-backup.log"
echo "[install-backup-cron] Test with: sudo bash $SCRIPT"
```

New code (replace lines 6–28 entirely):
```bash
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO/scripts/backup-cron.sh"
INSTALL_PATH="/usr/local/sbin/openclaw-backup"
CRON_ENTRY="0 3 * * * $INSTALL_PATH >> /var/log/openclaw-backup.log 2>&1"
CRON_MARKER="openclaw-backup"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi

# Install an immutable snapshot to /usr/local/sbin so the cron job
# is not affected by future 'git pull' updates to the repo.
install -m 0700 "$SCRIPT" "$INSTALL_PATH"

# Install into root crontab (idempotent — remove existing entry first)
(
  { crontab -l 2>/dev/null || true; } | { grep -v "$CRON_MARKER" || true; }
  echo "# $CRON_MARKER"
  echo "$CRON_ENTRY"
) | crontab -

echo "[install-backup-cron] Script installed to $INSTALL_PATH"
echo "[install-backup-cron] Cron job installed (runs daily at 03:00 UTC):"
echo "  $CRON_ENTRY"
echo "[install-backup-cron] Logs will go to /var/log/openclaw-backup.log"
echo "[install-backup-cron] Test with: sudo $INSTALL_PATH"
```

**Step 4: Manual smoke test (no S3 required)**

Verify the `parse_env` logic handles the real `.env` format:
```bash
# Set up a minimal test .env
echo -e "BACKUP_S3_BUCKET=mybucket\nBACKUP_S3_ENDPOINT=https://s3.example.com\nBACKUP_S3_ACCESS_KEY=AKID\nBACKUP_S3_SECRET_KEY=sk+abc=123\nBACKUP_RETAIN_DAYS=7" > /tmp/test.env

# Inline-test parse_env against it
(
  ENV_FILE=/tmp/test.env
  parse_env() { grep "^${1}=" "$ENV_FILE" | head -1 | cut -d= -f2-; }
  echo "BUCKET: $(parse_env BACKUP_S3_BUCKET)"
  echo "SECRET (has = and +): $(parse_env BACKUP_S3_SECRET_KEY)"
)
# Expected:
# BUCKET: mybucket
# SECRET (has = and +): sk+abc=123
```

**Step 5: Commit**

```bash
git add scripts/backup-cron.sh scripts/install-backup-cron.sh
git commit -m "security: replace source .env with safe grep/cut parser in backup scripts"
```

---

### Task 3: Product Profiles

**Problem:** `make up` starts voice-proxy even when `OPENAI_API_KEY` is not set, causing the build to fail or the container to exit immediately. Deployments without voice transcription shouldn't have to know to skip it.

**Fix:** Add `profiles: [voice]` to voice-proxy in `docker-compose.yml`. Update `Makefile` and `scripts/setup.sh` to use `--profile voice` when voice is wanted.

**Files:**
- Modify: `docker-compose.yml`
- Modify: `Makefile`
- Modify: `scripts/setup.sh`

**Step 1: Modify `docker-compose.yml`**

Add `profiles: [voice]` to the voice-proxy service (after `build`):

Old:
```yaml
  voice-proxy:
    build: ./services/voice-proxy
    restart: unless-stopped
```

New:
```yaml
  voice-proxy:
    build: ./services/voice-proxy
    profiles: [voice]
    restart: unless-stopped
```

**Step 2: Modify `Makefile` — `up-voice` target**

Old:
```makefile
# Start base services + voice transcription proxy
up-voice:
	docker compose up -d --build voice-proxy
	docker compose up -d caddy
	@echo "Voice proxy started. Test by sending a voice note to your bot."
```

New:
```makefile
# Start base services + voice transcription proxy
up-voice:
	docker compose --profile voice up -d --build
	@echo "Voice proxy started. Test by sending a voice note to your bot."
```

**Step 3: Modify `scripts/setup.sh` — voice-proxy startup**

Old (lines 155–161):
```bash
if [ -n "$OPENAI_API_KEY" ]; then
    rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD up -d --build voice-proxy && $COMPOSE_CMD up -d caddy"
    ok "Started with voice transcription"
else
    rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD up -d"
    ok "Started base stack"
fi
```

New:
```bash
if [ -n "$OPENAI_API_KEY" ]; then
    rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD --profile voice up -d --build"
    ok "Started with voice transcription"
else
    rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD up -d"
    ok "Started base stack"
fi
```

**Step 4: Verify base `make up` no longer builds voice-proxy**

```bash
# Dry-run (shows what would start without actually starting)
docker compose config --services
# Expected: caddy, openclaw, redis, calendar-proxy
# voice-proxy should NOT appear

docker compose --profile voice config --services
# Expected: caddy, openclaw, redis, calendar-proxy, voice-proxy
```

**Step 5: Commit**

```bash
git add docker-compose.yml Makefile scripts/setup.sh
git commit -m "feat: add voice profile — voice-proxy opt-in via make up-voice"
```

---

### Task 4: Voice Webhook Pre-Auth

**Problem:** `handle_request` in `services/voice-proxy/server.py` reads the request body, downloads audio from Telegram, and calls OpenAI Whisper **before** validating who sent the request. Any internet host that can POST to `https://<DOMAIN>/telegram-webhook` can burn OpenAI credits and cause the bot to respond to arbitrary messages.

**Fix:** Validate `X-Telegram-Bot-Api-Secret-Token` header at the top of `handle_request`, before any I/O. Return 403 immediately on mismatch. When `WEBHOOK_SECRET` is empty (not configured), skip validation (backward compat).

The secret is also passed to OpenClaw's `channels.telegram.webhookSecret` config, which causes OpenClaw to register the webhook with that secret. Telegram then includes it in every request.

**Files:**
- Modify: `services/voice-proxy/server.py`
- Modify: `docker-compose.yml`
- Modify: `scripts/setup.sh`
- Modify: `tests/voice_proxy/test_server.py`

**Step 1: Write the failing tests**

Add to `tests/voice_proxy/test_server.py`:

```python
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
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/voice_proxy/test_server.py::test_valid_webhook_secret_accepted \
       tests/voice_proxy/test_server.py::test_missing_secret_header_returns_403 \
       tests/voice_proxy/test_server.py::test_wrong_secret_header_returns_403 \
       tests/voice_proxy/test_server.py::test_empty_webhook_secret_skips_auth \
       -v
```

Expected: all 4 FAIL (WEBHOOK_SECRET not yet read; auth not yet implemented)

**Step 3: Implement auth in `services/voice-proxy/server.py`**

Add `import hmac` to the imports block (after `import copy`):

```python
import copy
import hmac
```

Add `WEBHOOK_SECRET` to the config section (after `FALLBACK_TEXT`):

```python
FALLBACK_TEXT = "🎤 Voice message received but transcription failed."
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
```

At the top of `handle_request`, insert the auth check before `raw_body = await request.read()`:

```python
async def handle_request(request: web.Request) -> web.Response:
    """Main handler: intercepts voice/audio, forwards everything else unchanged."""
    # Validate Telegram webhook secret before any I/O.
    # Telegram sends X-Telegram-Bot-Api-Secret-Token when the webhook is
    # registered with a secret. Reject early to prevent abuse (credit burn,
    # message injection). hmac.compare_digest prevents timing attacks.
    if WEBHOOK_SECRET:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(token.encode(), WEBHOOK_SECRET.encode()):
            return web.Response(status=403, text="forbidden")

    raw_body = await request.read()
    ...
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/voice_proxy/test_server.py::test_valid_webhook_secret_accepted \
       tests/voice_proxy/test_server.py::test_missing_secret_header_returns_403 \
       tests/voice_proxy/test_server.py::test_wrong_secret_header_returns_403 \
       tests/voice_proxy/test_server.py::test_empty_webhook_secret_skips_auth \
       -v
```

Expected: all 4 PASS

**Step 5: Run the full voice-proxy test suite to check for regressions**

```bash
pytest tests/voice_proxy/ -v
```

Expected: all tests PASS

**Step 6: Add `WEBHOOK_SECRET` to `docker-compose.yml` voice-proxy environment**

In the voice-proxy `environment` section, add after `VOICE_RATE_LIMIT_PER_MIN`:

Old:
```yaml
      - VOICE_MAX_FILE_SIZE_MB=${VOICE_MAX_FILE_SIZE_MB:-5}
      - VOICE_RATE_LIMIT_PER_MIN=${VOICE_RATE_LIMIT_PER_MIN:-10}
```

New:
```yaml
      - VOICE_MAX_FILE_SIZE_MB=${VOICE_MAX_FILE_SIZE_MB:-5}
      - VOICE_RATE_LIMIT_PER_MIN=${VOICE_RATE_LIMIT_PER_MIN:-10}
      - WEBHOOK_SECRET=${WEBHOOK_SECRET:-}
```

**Step 7: Add `WEBHOOK_SECRET` generation to `scripts/setup.sh`**

After the REDIS_PASSWORD generation block (around line 98), add:

```bash
# Generate WEBHOOK_SECRET if not set
existing_webhook_secret=$(get_existing WEBHOOK_SECRET)
if [ -z "$existing_webhook_secret" ]; then
    WEBHOOK_SECRET=$(openssl rand -hex 32)
    echo "  WEBHOOK_SECRET  auto-generated"
else
    WEBHOOK_SECRET="$existing_webhook_secret"
    echo "  WEBHOOK_SECRET  keeping existing"
fi
```

In the `.env` heredoc (around line 129), add after `REDIS_PASSWORD`:

```bash
WEBHOOK_SECRET=${WEBHOOK_SECRET}
```

After the health-wait block (around line 183), add a step to configure OpenClaw's webhook secret so Telegram will include it in each request. This must run after services are healthy:

```bash
# ── Step 7: Configure OpenClaw webhook secret ─────────────────────────────
step "Configuring OpenClaw webhook secret"

if [ -n "$WEBHOOK_SECRET" ]; then
    rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD exec -T openclaw openclaw config set channels.telegram.webhookSecret '$WEBHOOK_SECRET'" 2>/dev/null || true
    rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD restart openclaw" 2>/dev/null || true
    ok "Webhook secret configured (openclaw restarted)"
else
    warn "WEBHOOK_SECRET not set — webhook is unauthenticated"
fi
```

**Step 8: Commit**

```bash
git add services/voice-proxy/server.py docker-compose.yml scripts/setup.sh \
        tests/voice_proxy/test_server.py
git commit -m "security: add pre-auth to voice-proxy using Telegram webhook secret"
```

---

### Task 5: `make doctor` Enhancements

**Add two new checks:**
1. Google Calendar token file presence (`gcal_token.enc`)
2. `WEBHOOK_SECRET` — warn if voice-proxy is running but secret is not set

**Files:**
- Modify: `scripts/doctor.sh`

**Step 1: Identify insertion points in `scripts/doctor.sh`**

- `.env` checks section: around line 69–82, add `WEBHOOK_SECRET` optional check
- After the Channels section, add a new "Google Calendar" section

**Step 2: Add `WEBHOOK_SECRET` to the `.env` section**

After the existing `check_optional OPENAI_API_KEY` line:

Old:
```bash
check_optional BACKUP_S3_BUCKET  "BACKUP_S3_BUCKET" "backups disabled"
check_optional OPENAI_API_KEY    "OPENAI_API_KEY"   "voice transcription disabled"
```

New:
```bash
check_optional BACKUP_S3_BUCKET  "BACKUP_S3_BUCKET"  "backups disabled"
check_optional OPENAI_API_KEY    "OPENAI_API_KEY"    "voice transcription disabled"
check_optional WEBHOOK_SECRET    "WEBHOOK_SECRET"    "voice webhook unauthenticated"
```

**Step 3: Add Google Calendar section after the Channels section**

After the WhatsApp check block (after line ~137):

```bash
# ── Google Calendar ────────────────────────────────────────────────────────────

echo ""
echo " Google Calendar"

gcal_token=$(sudo docker compose exec -T openclaw test -f /home/node/.openclaw/gcal_token.enc 2>/dev/null && echo "present" || echo "")
if [ -n "$gcal_token" ]; then
    pass "gcal_token.enc  present"
else
    skip "gcal_token.enc  not configured  →  see docs/plans/2026-03-03-google-calendar.md"
fi
```

**Step 4: Manual verification**

On the VPS (or locally with containers running):
```bash
bash scripts/doctor.sh
```

Expected new output:
```
 .env
 ✅ DOMAIN
 ✅ TELEGRAM_TOKEN
 ✅ REDIS_PASSWORD
 ✅ ANTHROPIC_API_KEY
 ⚠️  BACKUP_S3_BUCKET — not set (backups disabled)    # if not configured
 ⚠️  OPENAI_API_KEY — not set (voice transcription disabled)    # if not configured
 ⚠️  WEBHOOK_SECRET — not set (voice webhook unauthenticated)   # if not configured

 Google Calendar
 ⚪ gcal_token.enc  not configured  →  see docs/plans/...   # if not configured
```

**Step 5: Run the full test suite to ensure doctor.sh changes don't break Python tests**

```bash
pytest tests/ -v
```

Expected: all tests PASS (doctor.sh changes are shell-only)

**Step 6: Commit**

```bash
git add scripts/doctor.sh
git commit -m "feat: add WEBHOOK_SECRET and gcal token checks to make doctor"
```

---

## Final Verification

Run the full test suite:

```bash
pip install -q -r requirements-dev.txt -r services/calendar-proxy/requirements.txt -r services/voice-proxy/requirements.txt
pytest tests/ -v
```

Expected: all tests PASS, no regressions.

Then deploy to VPS:
```bash
make deploy HOST=user@YOUR_VPS_IP
make doctor
```

Expected: doctor shows `WEBHOOK_SECRET ✅` (if re-deployed) or `WEBHOOK_SECRET ⚠️ not set` (if `.env` was not re-generated — inform user to re-run `make deploy` to regenerate).
