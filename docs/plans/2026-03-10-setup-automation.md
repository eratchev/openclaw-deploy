# Setup Automation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce first-message setup from 8 manual steps to 2 (`make deploy HOST=user@vps`, send a message) by adding a remote setup wizard, first-boot config bootstrap, and a `make doctor` health check.

**Architecture:** `scripts/setup.sh` runs locally over SSH to provision, configure, and start the stack on a fresh VPS. `entrypoint.sh` is extended to bootstrap OpenClaw config from `.env` env vars on first boot (eliminating the local OpenClaw prerequisite). `scripts/doctor.sh` runs on the VPS and prints structured health output. All three feed into the Makefile via `deploy`, `doctor`, and `pair-whatsapp` targets.

**Tech Stack:** bash, SSH, Docker Compose, OpenClaw CLI (`openclaw config set`), Telegram Bot API (`getWebhookInfo`), redis-cli, shellcheck (linting)

---

### Task 1: Scaffolding — .gitignore, Makefile targets, empty scripts

**Files:**
- Modify: `Makefile`
- Modify: `.gitignore`
- Create: `scripts/setup.sh` (empty, executable)
- Create: `scripts/doctor.sh` (empty, executable)

**Step 1: Add `.deploy` to .gitignore**

Append to `.gitignore`:
```
.deploy
.venv/
```

**Step 2: Add `HOST` loader and new targets to Makefile**

Add at the top of the Makefile, after the PROJECT/DATA_VOLUME lines:

```makefile
# Load HOST from .deploy file written by 'make deploy'
-include .deploy
```

Add new targets (before the closing of the .PHONY line, add `deploy doctor pair-whatsapp`):

```makefile
# Deploy to a remote VPS from this local machine
# Usage: make deploy HOST=user@x.x.x.x  (saved to .deploy for future targets)
deploy:
	@[ -n "$(HOST)" ] || (echo "Usage: make deploy HOST=user@x.x.x.x" && exit 1)
	@echo "HOST=$(HOST)" > .deploy
	@bash scripts/setup.sh "$(HOST)"

# Run health checks on the VPS
doctor:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	@ssh "$(HOST)" "cd ~/openclaw-deploy && bash scripts/doctor.sh"

# Pair WhatsApp interactively (renders QR code in your terminal)
pair-whatsapp:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	ssh -t "$(HOST)" "cd ~/openclaw-deploy && sudo docker compose exec -it openclaw openclaw configure --section whatsapp"
```

**Step 3: Create empty scripts with correct shebang and permissions**

```bash
printf '#!/bin/bash\nset -euo pipefail\n' > scripts/setup.sh
printf '#!/bin/bash\nset -euo pipefail\n' > scripts/doctor.sh
chmod +x scripts/setup.sh scripts/doctor.sh
```

**Step 4: Verify shellcheck is installed (install if not)**

```bash
shellcheck --version || brew install shellcheck
```

**Step 5: Run shellcheck on empty scripts to confirm toolchain works**

```bash
shellcheck scripts/setup.sh scripts/doctor.sh
```
Expected: no output (empty scripts pass).

**Step 6: Commit**

```bash
git add .gitignore Makefile scripts/setup.sh scripts/doctor.sh
git commit -m "feat: scaffold deploy/doctor/pair-whatsapp targets and empty scripts"
```

---

### Task 2: `scripts/doctor.sh` — .env and service checks

**Files:**
- Modify: `scripts/doctor.sh`

**Step 1: Write the test (manual — run on VPS with a known state)**

Before implementing, note the expected output when run on the live VPS (all services up, .env complete):
```
openclaw-deploy doctor
──────────────────────────────────────────
 .env
  ✅ DOMAIN             set
  ✅ TELEGRAM_TOKEN     set
  ✅ REDIS_PASSWORD     set
  ✅ ANTHROPIC_API_KEY  set
  ⚠️  BACKUP_S3_BUCKET  not set — backups disabled

 Services
  ✅ openclaw           running (healthy)
  ✅ caddy              running (healthy)
  ✅ redis              running (healthy)
  ✅ voice-proxy        running (healthy)
  ⚪ calendar-proxy     not started (optional)
```

**Step 2: Implement .env and service checks**

```bash
#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# ── Helpers ────────────────────────────────────────────────────────────────────

OK="✅"; WARN="⚠️ "; FAIL="❌"; SKIP="⚪"
_warnings=0; _errors=0

pass() { printf " %s %s\n" "$OK"  "$1"; }
warn() { printf " %s %s\n" "$WARN" "$1"; ((_warnings++)) || true; }
fail() { printf " %s %s\n" "$FAIL" "$1"; ((_errors++)) || true; }
skip() { printf " %s %s\n" "$SKIP" "$1"; }

# Source .env without exporting (we inspect values locally)
if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a; source .env; set +a
fi

check_required() {
    local var=$1 label=$2
    local val="${!var:-}"
    if [ -n "$val" ]; then
        pass "$label"
    else
        fail "$label — not set (required)"
    fi
}

check_optional() {
    local var=$1 label=$2 hint=$3
    local val="${!var:-}"
    if [ -n "$val" ]; then
        pass "$label"
    else
        warn "$label — not set ($hint)"
    fi
}

# ── .env ───────────────────────────────────────────────────────────────────────

echo ""
echo "openclaw-deploy doctor"
echo "──────────────────────────────────────────"
echo " .env"

check_required DOMAIN            "DOMAIN"
check_required TELEGRAM_TOKEN    "TELEGRAM_TOKEN"
check_required REDIS_PASSWORD    "REDIS_PASSWORD"
check_required ANTHROPIC_API_KEY "ANTHROPIC_API_KEY"
check_optional BACKUP_S3_BUCKET  "BACKUP_S3_BUCKET" "backups disabled"
check_optional OPENAI_API_KEY    "OPENAI_API_KEY"   "voice transcription disabled"

# ── Services ───────────────────────────────────────────────────────────────────

echo ""
echo " Services"

check_service() {
    local name=$1 optional=${2:-false}
    local status
    status=$(sudo docker compose ps --format '{{.Name}} {{.Status}}' 2>/dev/null | grep "^openclaw-deploy-${name}-" | awk '{print $2}' || echo "")
    if echo "$status" | grep -q "healthy"; then
        pass "$name  running (healthy)"
    elif echo "$status" | grep -q "Up"; then
        warn "$name  running (not yet healthy)"
    else
        if [ "$optional" = "true" ]; then
            skip "$name  not started (optional)"
        else
            fail "$name  not running"
        fi
    fi
}

check_service openclaw
check_service caddy
check_service redis
check_service voice-proxy  true
check_service calendar-proxy true
```

**Step 3: Run shellcheck**

```bash
shellcheck scripts/doctor.sh
```
Expected: no warnings.

**Step 4: Test on VPS**

```bash
make doctor HOST=user@YOUR_VPS_IP
```
Expected: .env section shows ✅ for set vars, services section shows running containers.

**Step 5: Commit**

```bash
git add scripts/doctor.sh
git commit -m "feat: doctor.sh — .env and service checks"
```

---

### Task 3: `scripts/doctor.sh` — connectivity, channels, backups, and summary

**Files:**
- Modify: `scripts/doctor.sh`

**Step 1: Append connectivity, channels, and backup checks**

Add to end of `scripts/doctor.sh`:

```bash
# ── Connectivity ───────────────────────────────────────────────────────────────

echo ""
echo " Connectivity"

# Telegram webhook
if [ -n "${TELEGRAM_TOKEN:-}" ]; then
    webhook_info=$(curl -sf "https://api.telegram.org/bot${TELEGRAM_TOKEN}/getWebhookInfo" 2>/dev/null || echo "")
    if echo "$webhook_info" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('ok') and d['result'].get('url') else 1)" 2>/dev/null; then
        webhook_url=$(echo "$webhook_info" | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['url'])" 2>/dev/null)
        pending=$(echo "$webhook_info" | python3 -c "import sys,json; print(json.load(sys.stdin)['result'].get('pending_update_count',0))" 2>/dev/null)
        webhook_err=$(echo "$webhook_info" | python3 -c "import sys,json; print(json.load(sys.stdin)['result'].get('last_error_message',''))" 2>/dev/null)
        if [ -n "$webhook_err" ]; then
            warn "Telegram webhook  $webhook_url (last error: $webhook_err)"
        else
            pass "Telegram webhook  $webhook_url (pending: $pending)"
        fi
    else
        fail "Telegram webhook  not registered or token invalid"
    fi
else
    skip "Telegram webhook  TELEGRAM_TOKEN not set"
fi

# Redis
if sudo docker compose exec -T redis redis-cli -a "${REDIS_PASSWORD:-}" ping 2>/dev/null | grep -q PONG; then
    pass "Redis  reachable + authenticated"
else
    fail "Redis  unreachable or auth failed"
fi

# Guardrail
if sudo docker compose exec -T openclaw pgrep -f guardrail.py > /dev/null 2>&1; then
    pass "Guardrail  running"
else
    fail "Guardrail  not running"
fi

# ── Channels ───────────────────────────────────────────────────────────────────

echo ""
echo " Channels"

# WhatsApp
whatsapp_state=$(sudo docker compose exec -T openclaw openclaw config get channels.whatsapp.enabled 2>/dev/null || echo "false")
if echo "$whatsapp_state" | grep -q "true"; then
    pass "WhatsApp  enabled"
else
    skip "WhatsApp  not paired  →  run: make pair-whatsapp"
fi

# ── Backups ────────────────────────────────────────────────────────────────────

echo ""
echo " Backups"

if [ -n "${BACKUP_S3_BUCKET:-}" ] && [ -n "${BACKUP_S3_ACCESS_KEY:-}" ]; then
    pass "S3 credentials  configured"
else
    warn "S3 credentials  not set — daily backups disabled"
fi

if sudo crontab -l 2>/dev/null | grep -q "backup-cron.sh"; then
    pass "Cron  backup job installed"
else
    warn "Cron  not installed  →  run: sudo bash scripts/install-backup-cron.sh"
fi

# ── Summary ────────────────────────────────────────────────────────────────────

echo ""
echo "──────────────────────────────────────────"
if [ "$_errors" -gt 0 ]; then
    echo "$_errors error(s), $_warnings warning(s). Fix errors above before using the bot."
    exit 1
elif [ "$_warnings" -gt 0 ]; then
    echo "$_warnings warning(s). Bot should work; review warnings above."
    exit 0
else
    echo "All checks passed."
    exit 0
fi
```

**Step 2: Run shellcheck**

```bash
shellcheck scripts/doctor.sh
```
Expected: no warnings.

**Step 3: Test on VPS**

```bash
make doctor HOST=user@YOUR_VPS_IP
```
Expected: full output with all sections, exits 0 if healthy.

**Step 4: Test failure exit code**

```bash
make doctor HOST=user@YOUR_VPS_IP; echo "exit: $?"
```
Expected: `exit: 0` when all services healthy.

**Step 5: Commit**

```bash
git add scripts/doctor.sh
git commit -m "feat: doctor.sh — connectivity, channels, backups, and summary"
```

---

### Task 4: First-boot config bootstrap in `entrypoint.sh`

This eliminates the local OpenClaw prerequisite for Telegram. The container generates its own `openclaw.json` from env vars if none exists.

**Files:**
- Modify: `entrypoint.sh`

**⚠️ Discovery step required before implementing:**

Run the following on the VPS to find the correct OpenClaw config key for the Anthropic API key:

```bash
# Stop openclaw, clear its config, restart and inspect what config keys get created
sudo docker compose exec openclaw openclaw config get agents 2>&1 | head -20
sudo docker compose exec openclaw env | grep -i anthropic
```

If OpenClaw auto-detects `ANTHROPIC_API_KEY` from the environment (common for AI tools), no config set is needed for the LLM. If not, find the key via:

```bash
sudo docker compose exec openclaw openclaw config get agents.main.model 2>&1
```

Document the correct key(s) before proceeding with bootstrap implementation.

**Step 1: Write the test**

On VPS: backup existing openclaw config, remove it, restart container, verify it bootstraps:

```bash
# On VPS
ssh user@YOUR_VPS_IP
cd ~/openclaw-deploy

# Backup
sudo docker run --rm \
  -v openclaw-deploy_openclaw_data:/data \
  -v /tmp:/backup \
  busybox cp /data/openclaw.json /backup/openclaw.json.bak

# Remove config
sudo docker run --rm \
  -v openclaw-deploy_openclaw_data:/data \
  busybox rm /data/openclaw.json

# Restart and watch logs
sudo docker compose restart openclaw
sudo docker compose logs -f openclaw --since 0s | grep -E "bootstrap|entrypoint|telegram|webhook" | head -20
```

Expected: logs show `[entrypoint] No config found — bootstrapping from .env` and `webhook local listener`.

**Step 2: Implement bootstrap in entrypoint.sh**

```sh
#!/bin/sh
set -e

# ── First-boot bootstrap ──────────────────────────────────────────────────────
# Generate minimal openclaw.json from env vars if no config exists.
# This eliminates the local OpenClaw install prerequisite.
CONFIG_FILE="/home/node/.openclaw/openclaw.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "[entrypoint] No config found — bootstrapping from .env..."

    # Verify required env vars
    for var in TELEGRAM_TOKEN DOMAIN; do
        eval "val=\$$var"
        if [ -z "$val" ]; then
            echo "[entrypoint] ERROR: $var is not set. Cannot bootstrap config."
            exit 1
        fi
    done

    # Generate webhook secret (stored in config only — not written back to .env)
    WEBHOOK_SECRET=$(openssl rand -hex 32)

    openclaw config set channels.telegram.botToken  "${TELEGRAM_TOKEN}"
    openclaw config set channels.telegram.webhookSecret "${WEBHOOK_SECRET}"
    openclaw config set channels.telegram.webhookUrl "https://${DOMAIN}/telegram-webhook"
    openclaw config set channels.telegram.webhookHost "0.0.0.0"

    # Configure Anthropic LLM provider if key is present
    # NOTE: if OpenClaw auto-detects ANTHROPIC_API_KEY from env, skip these lines
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        openclaw config set agents.main.provider anthropic || true
    fi

    echo "[entrypoint] Bootstrap complete. Starting gateway..."
fi

# ── Guardrail supervisor ──────────────────────────────────────────────────────
echo "[entrypoint] Starting guardrail supervisor..."

while true; do
  code=0
  python3 /home/node/guardrail.py || code=$?
  echo "[entrypoint] guardrail exited (code ${code}), restarting in 5s..."
  sleep 5
done &

echo "[entrypoint] Starting OpenClaw Gateway..."
exec openclaw gateway --port 18789
```

**Step 3: Run shellcheck**

```bash
shellcheck entrypoint.sh
```
Expected: no warnings.

**Step 4: Test bootstrap on VPS**

Follow the test procedure from Step 1. Verify:
- `[entrypoint] No config found — bootstrapping from .env...` appears in logs
- `webhook local listener on http://0.0.0.0:8787/telegram-webhook` appears
- Bot responds to a Telegram message

**Step 5: Restore backup if something goes wrong**

```bash
sudo docker run --rm \
  -v openclaw-deploy_openclaw_data:/data \
  -v /tmp:/backup \
  busybox cp /backup/openclaw.json.bak /data/openclaw.json
sudo docker compose restart openclaw
```

**Step 6: Test idempotency — bootstrap skipped on second start**

```bash
sudo docker compose restart openclaw
sudo docker compose logs openclaw --tail 5 | grep bootstrap
```
Expected: no bootstrap log line (config already exists, bootstrap skipped).

**Step 7: Commit**

```bash
git add entrypoint.sh
git commit -m "feat: bootstrap openclaw config from env vars on first boot"
```

---

### Task 5: `scripts/setup.sh` — SSH preflight and VPS provision

**Files:**
- Modify: `scripts/setup.sh`

**Step 1: Implement SSH preflight and provision**

```bash
#!/bin/bash
set -euo pipefail

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "Usage: $0 user@host"
    exit 1
fi

# ── Colours ───────────────────────────────────────────────────────────────────
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step()  { echo -e "\n${BOLD}▶ $1${NC}"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC}  $1"; }
die()   { echo -e "  ${RED}✗${NC} $1"; exit 1; }

rsh() { ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" "$@"; }

# ── Step 1: SSH preflight ─────────────────────────────────────────────────────
step "Checking SSH access to $HOST"
rsh "echo ok" > /dev/null 2>&1 || die "Cannot connect to $HOST with key-based auth.
  Run: ssh-copy-id $HOST  then retry."
ok "SSH access confirmed"

# ── Step 2: Install prerequisites on VPS ─────────────────────────────────────
step "Checking prerequisites on VPS"

rsh "command -v docker > /dev/null 2>&1" || {
    warn "Docker not found — running provision.sh"
    # Copy provision.sh and run it
    scp scripts/provision.sh "$HOST:/tmp/provision.sh"
    rsh "sudo bash /tmp/provision.sh"
    ok "Provision complete"
}
ok "Docker available"

rsh "command -v git > /dev/null 2>&1" || rsh "sudo apt-get install -y git > /dev/null 2>&1"
ok "git available"

# ── Step 3: Clone or pull repo ────────────────────────────────────────────────
step "Syncing repository on VPS"

REPO_URL="https://github.com/eratchev/openclaw-deploy.git"
REMOTE_DIR="\$HOME/openclaw-deploy"

rsh "
if [ -d '$REMOTE_DIR/.git' ]; then
    cd '$REMOTE_DIR' && git pull --ff-only
else
    git clone '$REPO_URL' '$REMOTE_DIR'
fi
"
ok "Repository up to date at ~/openclaw-deploy"
```

**Step 2: Run shellcheck**

```bash
shellcheck scripts/setup.sh
```

**Step 3: Smoke-test SSH preflight**

```bash
bash scripts/setup.sh user@YOUR_VPS_IP
```
Expected: gets past SSH check and provision check, pauses (no .env wizard yet).

**Step 4: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat: setup.sh — SSH preflight, provision, and clone/pull"
```

---

### Task 6: `scripts/setup.sh` — `.env` wizard

**Files:**
- Modify: `scripts/setup.sh`

**Step 1: Append `.env` wizard**

Add after the clone/pull section:

```bash
# ── Step 4: .env wizard ───────────────────────────────────────────────────────
step "Configuring .env on VPS"

# Fetch existing .env from VPS (empty string if not present)
existing_env=$(rsh "cat '$REMOTE_DIR/.env' 2>/dev/null || echo ''" )

get_existing() {
    echo "$existing_env" | grep "^$1=" | cut -d= -f2- | tr -d '\r'
}

ask() {
    local var=$1 prompt=$2 default=${3:-}
    local existing; existing=$(get_existing "$var")
    local hint=""
    [ -n "$existing" ] && hint=" [current: ${existing:0:20}...]" || { [ -n "$default" ] && hint=" [default: $default]"; }
    printf "  %s%s: " "$prompt" "$hint"
    read -r input
    # Use input if provided, else existing, else default
    echo "${input:-${existing:-$default}}"
}

ask_secret() {
    local var=$1 prompt=$2
    local existing; existing=$(get_existing "$var")
    local hint=""
    [ -n "$existing" ] && hint=" [current: set — press enter to keep]"
    printf "  %s%s: " "$prompt" "$hint"
    read -rs input; echo ""
    echo "${input:-$existing}"
}

echo "  Required vars (press enter to keep existing value):"
echo ""

DOMAIN=$(ask         DOMAIN            "Domain name (e.g. bot.example.com)")
TELEGRAM_TOKEN=$(ask_secret TELEGRAM_TOKEN "Telegram bot token (@BotFather)")
ANTHROPIC_API_KEY=$(ask_secret ANTHROPIC_API_KEY "Anthropic API key")

# Generate REDIS_PASSWORD if not set
existing_redis_pw=$(get_existing REDIS_PASSWORD)
if [ -z "$existing_redis_pw" ]; then
    REDIS_PASSWORD=$(openssl rand -hex 32)
    echo "  REDIS_PASSWORD  auto-generated"
else
    REDIS_PASSWORD="$existing_redis_pw"
    echo "  REDIS_PASSWORD  keeping existing"
fi

echo ""
echo "  Optional integrations:"
printf "  Enable voice transcription (requires OpenAI key)? [y/N]: "; read -r voice_yn
OPENAI_API_KEY=""
if [[ "${voice_yn,,}" == "y" ]]; then
    OPENAI_API_KEY=$(ask_secret OPENAI_API_KEY "OpenAI API key")
fi

printf "  Configure Hetzner S3 backups? [y/N]: "; read -r backup_yn
BACKUP_S3_ENDPOINT=""; BACKUP_S3_BUCKET=""; BACKUP_S3_ACCESS_KEY=""; BACKUP_S3_SECRET_KEY=""; BACKUP_S3_REGION="hel1"; BACKUP_RETAIN_DAYS=7
if [[ "${backup_yn,,}" == "y" ]]; then
    BACKUP_S3_ENDPOINT=$(ask BACKUP_S3_ENDPOINT "S3 endpoint" "https://hel1.your-objectstorage.com")
    BACKUP_S3_BUCKET=$(ask   BACKUP_S3_BUCKET   "S3 bucket name")
    BACKUP_S3_ACCESS_KEY=$(ask_secret BACKUP_S3_ACCESS_KEY "S3 access key")
    BACKUP_S3_SECRET_KEY=$(ask_secret BACKUP_S3_SECRET_KEY "S3 secret key")
    BACKUP_S3_REGION=$(ask   BACKUP_S3_REGION   "S3 region" "hel1")
    BACKUP_RETAIN_DAYS=$(ask BACKUP_RETAIN_DAYS "Backup retention (days)" "7")
fi

# Write .env to VPS
step "Writing .env to VPS"

rsh "cat > '$REMOTE_DIR/.env'" << EOF
# Generated by make deploy $(date -u +%Y-%m-%dT%H:%M:%SZ)
DOMAIN=${DOMAIN}
TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
REDIS_PASSWORD=${REDIS_PASSWORD}
OPENAI_API_KEY=${OPENAI_API_KEY}
MAX_SESSION_SECONDS=300
MAX_TOOL_CALLS=50
MAX_LLM_CALLS=30
MAX_IDLE_SECONDS=60
MAX_MEMORY_PCT=90
BACKUP_S3_ENDPOINT=${BACKUP_S3_ENDPOINT}
BACKUP_S3_BUCKET=${BACKUP_S3_BUCKET}
BACKUP_S3_ACCESS_KEY=${BACKUP_S3_ACCESS_KEY}
BACKUP_S3_SECRET_KEY=${BACKUP_S3_SECRET_KEY}
BACKUP_S3_REGION=${BACKUP_S3_REGION}
BACKUP_RETAIN_DAYS=${BACKUP_RETAIN_DAYS}
EOF
ok ".env written"
```

**Step 2: Run shellcheck**

```bash
shellcheck scripts/setup.sh
```

**Step 3: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat: setup.sh — interactive .env wizard"
```

---

### Task 7: `scripts/setup.sh` — stack start, health wait, and summary

**Files:**
- Modify: `scripts/setup.sh`

**Step 1: Append stack start, health wait, and summary**

```bash
# ── Step 5: Start the stack ───────────────────────────────────────────────────
step "Starting services on VPS"

COMPOSE_CMD="sudo docker compose"

if [ -n "$OPENAI_API_KEY" ]; then
    rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD up -d --build voice-proxy && $COMPOSE_CMD up -d caddy"
    ok "Started with voice transcription"
else
    rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD up -d"
    ok "Started base stack"
fi

# ── Step 6: Health wait ───────────────────────────────────────────────────────
step "Waiting for services to become healthy (up to 60s)"

deadline=$(($(date +%s) + 60))
all_healthy=false
while [ "$(date +%s)" -lt "$deadline" ]; do
    status=$(rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD ps --format '{{.Name}} {{.Health}}' 2>/dev/null" || echo "")
    unhealthy=$(echo "$status" | grep -v "healthy\|optional\|^$" | grep -c "starting\|unhealthy" || true)
    if [ "$unhealthy" -eq 0 ]; then
        all_healthy=true
        break
    fi
    printf "."
    sleep 3
done
echo ""

if $all_healthy; then
    ok "All services healthy"
else
    warn "Some services not yet healthy — run 'make doctor' to check"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  openclaw-deploy is running${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${GREEN}✅${NC} Telegram    @your_bot — send it a message"
echo -e "  ${YELLOW}⚪${NC} WhatsApp    not paired — run: make pair-whatsapp"
[ -z "$BACKUP_S3_BUCKET" ] && echo -e "  ${YELLOW}⚠️ ${NC} Backups     not configured — run: sudo bash scripts/install-backup-cron.sh"
echo ""
echo "  Health check:  make doctor"
echo "  Logs:          make logs"
echo "  Upgrade:       make update"
echo ""
echo -e "HOST=${HOST}" > .deploy
ok "Saved HOST to .deploy — future make targets will use it automatically"
```

**Step 2: Run shellcheck**

```bash
shellcheck scripts/setup.sh
```

**Step 3: End-to-end test on fresh-ish VPS**

```bash
make deploy HOST=user@YOUR_VPS_IP
```
Expected: wizard prompts, stack starts, summary prints, bot responds to a Telegram message.

**Step 4: Run doctor immediately after**

```bash
make doctor
```
Expected: all required checks ✅.

**Step 5: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat: setup.sh — stack start, health wait, and deployment summary"
```

---

### Task 8: README update

**Files:**
- Modify: `README.md`

**Step 1: Replace the Quickstart section**

Replace the existing 8-step Quickstart with:

```markdown
## Quickstart

**Prerequisites:**
- A VPS running Ubuntu 24.04 (Hetzner CX22 ~$5/mo works well)
- A domain pointing at the VPS IP
- SSH key access: `ssh-copy-id user@<your-vps>`
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- An [Anthropic API key](https://console.anthropic.com)

**Deploy:**

```bash
git clone https://github.com/eratchev/openclaw-deploy.git
cd openclaw-deploy
make deploy HOST=user@<your-vps>
```

The wizard provisions the VPS, configures everything interactively, and starts the stack. When it finishes, send a message to your bot.

**Add WhatsApp (optional):**

```bash
make pair-whatsapp
```

Renders a QR code in your terminal. Scan with WhatsApp on your phone.

**Check health:**

```bash
make doctor
```
```

**Step 2: Update the Troubleshooting section**

Add a new entry:

```markdown
### Bootstrap fails with `config set` error on first start

**Symptom:** `[entrypoint] ERROR: TELEGRAM_TOKEN is not set`

**Cause:** `.env` is missing a required variable. The bootstrap runs before the gateway and fails fast.

**Fix:** Verify `.env` has `TELEGRAM_TOKEN`, `DOMAIN`, and `ANTHROPIC_API_KEY` set, then restart:
```bash
make doctor  # shows which vars are missing
sudo docker compose restart openclaw
```
```

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: replace 8-step quickstart with make deploy one-liner"
```

---

### Task 9: Final verification

**Step 1: Full shellcheck pass**

```bash
shellcheck scripts/setup.sh scripts/doctor.sh entrypoint.sh
```
Expected: no warnings.

**Step 2: Full test suite still passes**

```bash
make test
```
Expected: 140 passed.

**Step 3: End-to-end on VPS**

1. `make deploy HOST=user@YOUR_VPS_IP` — should complete without errors
2. `make doctor` — all required checks ✅
3. Send a Telegram message — bot responds
4. `make pair-whatsapp` — QR code renders (test pairing flow, don't need to complete it)

**Step 4: Commit**

No new code — this is a verification task only.
