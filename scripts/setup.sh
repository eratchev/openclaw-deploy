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

# ── Step 2b: Apply container egress allowlist ─────────────────────────────────
step "Applying container egress allowlist"
if scp scripts/egress.sh "$HOST:/tmp/egress.sh" && rsh "sudo bash /tmp/egress.sh"; then
    ok "Egress allowlist active (HTTPS/DNS/NTP only)"
else
    warn "Egress setup failed — run: make setup-egress"
fi

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

# ── Step 4: .env wizard ───────────────────────────────────────────────────────
step "Configuring .env on VPS"

# Fetch existing .env from VPS (empty string if not present)
existing_env=$(rsh "cat '$REMOTE_DIR/.env' 2>/dev/null || echo ''" )

get_existing() {
    echo "$existing_env" | grep "^$1=" | cut -d= -f2- | tr -d '\r' || true
}

ask() {
    local var=$1 prompt=$2 default=${3:-}
    local existing; existing=$(get_existing "$var")
    local hint=""
    if [ -n "$existing" ]; then
        hint=" [current: ${existing:0:20}...]"
    elif [ -n "$default" ]; then
        hint=" [default: $default]"
    fi
    printf "  %s%s: " "$prompt" "$hint" >&2
    read -r input
    # Use input if provided, else existing, else default
    echo "${input:-${existing:-$default}}"
}

ask_secret() {
    local var=$1 prompt=$2
    local existing; existing=$(get_existing "$var")
    local hint=""
    [ -n "$existing" ] && hint=" [current: set — press enter to keep]"
    printf "  %s%s: " "$prompt" "$hint" >&2
    read -rs input; echo "" >&2
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

# Generate WEBHOOK_SECRET if not set
existing_webhook_secret=$(get_existing WEBHOOK_SECRET)
if [ -z "$existing_webhook_secret" ]; then
    WEBHOOK_SECRET=$(openssl rand -hex 32)
    echo "  WEBHOOK_SECRET  auto-generated"
else
    WEBHOOK_SECRET="$existing_webhook_secret"
    echo "  WEBHOOK_SECRET  keeping existing"
fi

echo ""
echo "  Optional integrations:"
printf "  Enable voice transcription (requires OpenAI key)? [y/N]: " >&2; read -r voice_yn
OPENAI_API_KEY=""
if [[ "${voice_yn,,}" == "y" ]]; then
    OPENAI_API_KEY=$(ask_secret OPENAI_API_KEY "OpenAI API key")
fi

printf "  Configure Hetzner S3 backups? [y/N]: " >&2; read -r backup_yn
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
WEBHOOK_SECRET=${WEBHOOK_SECRET}
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

# ── Step 5: Start the stack ───────────────────────────────────────────────────
step "Starting services on VPS"

COMPOSE_CMD="sudo docker compose"

if [ -n "$OPENAI_API_KEY" ]; then
    rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD --profile voice up -d --build"
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

# ── Step 7: Configure OpenClaw webhook secret ─────────────────────────────────
step "Configuring OpenClaw webhook secret"

if [ -n "$WEBHOOK_SECRET" ]; then
    if rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD exec -T openclaw openclaw config set channels.telegram.webhookSecret '$WEBHOOK_SECRET'" 2>/dev/null; then
        rsh "cd '$REMOTE_DIR' && $COMPOSE_CMD restart openclaw" 2>/dev/null || true
        ok "Webhook secret configured (openclaw restarted to re-register webhook)"
    else
        warn "Could not set webhook secret — run manually: docker compose exec openclaw openclaw config set channels.telegram.webhookSecret <secret>"
    fi
else
    warn "WEBHOOK_SECRET not set — webhook is unauthenticated"
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
