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
    # Copy all three scripts provision.sh needs at runtime
    scp scripts/provision.sh scripts/inbound.sh scripts/egress.sh "$HOST:/tmp/"
    rsh "sudo bash /tmp/provision.sh"
    ok "Provision complete"
}
ok "Docker available"

# ── Step 2b: Apply inbound firewall rules ─────────────────────────────────────
step "Applying inbound firewall rules"
if scp scripts/inbound.sh "$HOST:/tmp/inbound.sh" && rsh "sudo bash /tmp/inbound.sh"; then
    ok "Inbound firewall active (SSH/HTTP/HTTPS only)"
else
    warn "Inbound setup failed — run: make setup-inbound"
fi

# ── Step 2c: Apply container egress allowlist ─────────────────────────────────
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
REMOTE_DIR="~/openclaw-deploy"

rsh "
if [ -d $REMOTE_DIR/.git ]; then
    cd $REMOTE_DIR && git pull --ff-only
else
    git clone '$REPO_URL' $REMOTE_DIR
fi
"
ok "Repository up to date at ~/openclaw-deploy"

# ── Step 4: .env wizard ───────────────────────────────────────────────────────
step "Configuring .env on VPS"

# Fetch existing .env from VPS (empty string if not present)
existing_env=$(rsh "cat $REMOTE_DIR/.env 2>/dev/null || echo ''" )

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
# Initialise optional vars from existing .env so re-runs preserve config when
# the user skips a section (answers N). The if-blocks below overwrite on Y.
OPENAI_API_KEY=$(get_existing OPENAI_API_KEY)
BACKUP_S3_ENDPOINT=$(get_existing BACKUP_S3_ENDPOINT)
BACKUP_S3_BUCKET=$(get_existing BACKUP_S3_BUCKET)
BACKUP_S3_ACCESS_KEY=$(get_existing BACKUP_S3_ACCESS_KEY)
BACKUP_S3_SECRET_KEY=$(get_existing BACKUP_S3_SECRET_KEY)
BACKUP_S3_REGION=$(get_existing BACKUP_S3_REGION); BACKUP_S3_REGION=${BACKUP_S3_REGION:-hel1}
BACKUP_RETAIN_DAYS=$(get_existing BACKUP_RETAIN_DAYS); BACKUP_RETAIN_DAYS=${BACKUP_RETAIN_DAYS:-7}
ALERT_TELEGRAM_CHAT_ID=$(get_existing ALERT_TELEGRAM_CHAT_ID)
TELEGRAM_ALLOWED_USER_IDS=$(get_existing TELEGRAM_ALLOWED_USER_IDS)
GMAIL_TOKEN_ENCRYPTION_KEY=$(get_existing GMAIL_TOKEN_ENCRYPTION_KEY)

[ -n "$OPENAI_API_KEY" ]          && _voice_hint=" [currently enabled]"   || _voice_hint=""
[ -n "$BACKUP_S3_BUCKET" ]        && _backup_hint=" [currently: $BACKUP_S3_BUCKET]" || _backup_hint=""
[ -n "$ALERT_TELEGRAM_CHAT_ID" ]  && _alerts_hint=" [currently: chat $ALERT_TELEGRAM_CHAT_ID]" || _alerts_hint=""

printf "  Enable voice transcription (requires OpenAI key)?%s [y/N]: " "$_voice_hint" >&2; read -r voice_yn
if [[ "${voice_yn,,}" == "y" ]]; then
    OPENAI_API_KEY=$(ask_secret OPENAI_API_KEY "OpenAI API key")
fi

printf "  Configure Hetzner S3 backups?%s [y/N]: " "$_backup_hint" >&2; read -r backup_yn
if [[ "${backup_yn,,}" == "y" ]]; then
    BACKUP_S3_ENDPOINT=$(ask BACKUP_S3_ENDPOINT "S3 endpoint" "https://hel1.your-objectstorage.com")
    BACKUP_S3_BUCKET=$(ask   BACKUP_S3_BUCKET   "S3 bucket name")
    BACKUP_S3_ACCESS_KEY=$(ask_secret BACKUP_S3_ACCESS_KEY "S3 access key")
    BACKUP_S3_SECRET_KEY=$(ask_secret BACKUP_S3_SECRET_KEY "S3 secret key")
    BACKUP_S3_REGION=$(ask   BACKUP_S3_REGION   "S3 region" "hel1")
    BACKUP_RETAIN_DAYS=$(ask BACKUP_RETAIN_DAYS "Backup retention (days)" "7")
fi

printf "  Enable Telegram alerts (guardrail kills, backup failures)?%s [y/N]: " "$_alerts_hint" >&2; read -r alerts_yn
if [[ "${alerts_yn,,}" == "y" ]]; then
    echo "  Find your chat ID: message @userinfobot on Telegram" >&2
    ALERT_TELEGRAM_CHAT_ID=$(ask ALERT_TELEGRAM_CHAT_ID "Your Telegram chat ID")
fi

_allowlist_default="${TELEGRAM_ALLOWED_USER_IDS:-${ALERT_TELEGRAM_CHAT_ID}}"
[ -n "$TELEGRAM_ALLOWED_USER_IDS" ] && _allowlist_hint=" [currently: $TELEGRAM_ALLOWED_USER_IDS]" || _allowlist_hint=""
printf "  Restrict bot to specific Telegram user IDs? (recommended)?%s [Y/n]: " "$_allowlist_hint" >&2; read -r allowlist_yn
if [[ "${allowlist_yn,,}" != "n" ]]; then
    echo "  Comma-separated user IDs. For a personal bot, your chat ID = your user ID." >&2
    TELEGRAM_ALLOWED_USER_IDS=$(ask TELEGRAM_ALLOWED_USER_IDS "Allowed Telegram user IDs" "$_allowlist_default")
fi


# Validate required vars — fail before writing if any are empty
if [ -z "$DOMAIN" ] || [ -z "$TELEGRAM_TOKEN" ] || [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "" >&2
    [ -z "$DOMAIN" ]            && echo "  ERROR: Domain name is required" >&2
    [ -z "$TELEGRAM_TOKEN" ]    && echo "  ERROR: Telegram bot token is required" >&2
    [ -z "$ANTHROPIC_API_KEY" ] && echo "  ERROR: Anthropic API key is required" >&2
    die "Required vars missing — re-run make deploy and provide values"
fi

# Write .env to VPS
step "Writing .env to VPS"

rsh "cat > $REMOTE_DIR/.env" << EOF
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
ALERT_TELEGRAM_CHAT_ID=${ALERT_TELEGRAM_CHAT_ID}
TELEGRAM_ALLOWED_USER_IDS=${TELEGRAM_ALLOWED_USER_IDS}
GMAIL_TOKEN_ENCRYPTION_KEY=${GMAIL_TOKEN_ENCRYPTION_KEY}
EOF
ok ".env written"

# ── Step 5: Start the stack ───────────────────────────────────────────────────
step "Starting services on VPS"

COMPOSE_CMD="sudo docker compose"

rsh "cd $REMOTE_DIR && $COMPOSE_CMD up -d --force-recreate --build"
ok "Started stack"

# ── Step 5b: Rebuild running optional profile services ────────────────────────
step "Rebuilding running optional services"
running_svcs=$(rsh "cd $REMOTE_DIR && $COMPOSE_CMD ps --format '{{.Service}}' 2>/dev/null" || echo "")
for svc_info in "mail-proxy:mail" "calendar-proxy:calendar" "voice-proxy:voice"; do
    svc="${svc_info%%:*}"
    profile="${svc_info##*:}"
    if echo "$running_svcs" | grep -qx "$svc"; then
        if rsh "cd $REMOTE_DIR && $COMPOSE_CMD --profile $profile up -d --build $svc"; then
            ok "Rebuilt $svc"
        else
            warn "Failed to rebuild $svc — run: make up-$profile"
        fi
    fi
done

# ── Step 6: Health wait ───────────────────────────────────────────────────────
step "Waiting for services to become healthy (up to 90s)"

deadline=$(($(date +%s) + 90))
all_healthy=false
while [ "$(date +%s)" -lt "$deadline" ]; do
    status=$(rsh "cd $REMOTE_DIR && $COMPOSE_CMD ps --format '{{.Name}} {{.Health}}' 2>/dev/null" || echo "")
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
    if rsh "cd $REMOTE_DIR && $COMPOSE_CMD exec -T openclaw openclaw config set channels.telegram.webhookSecret '$WEBHOOK_SECRET'" 2>/dev/null; then
        rsh "cd $REMOTE_DIR && $COMPOSE_CMD restart openclaw" 2>/dev/null || true
        ok "Webhook secret configured (openclaw restarted to re-register webhook)"
    else
        warn "Could not set webhook secret — run manually: docker compose exec openclaw openclaw config set channels.telegram.webhookSecret <secret>"
    fi
else
    warn "WEBHOOK_SECRET not set — webhook is unauthenticated"
fi

# ── Step 8: Deploy workspace files ───────────────────────────────────────────
step "Deploying workspace files"
scp workspace/*.md "$HOST:/tmp/"
for f in workspace/*.md; do
    fname=$(basename "$f")
    rsh "cd $REMOTE_DIR && $COMPOSE_CMD cp /tmp/$fname openclaw:/home/node/.openclaw/workspace/$fname && rm -f /tmp/$fname"
done
ok "Workspace files deployed"

# ── Step 10: Backup cron ──────────────────────────────────────────────────────
backup_cron_ok=false
if [[ "${backup_yn,,}" == "y" ]]; then
    step "Installing backup cron"
    if rsh "sudo bash $REMOTE_DIR/scripts/install-backup-cron.sh"; then
        ok "Backup cron installed (daily 03:00 UTC → $BACKUP_S3_BUCKET)"
        backup_cron_ok=true
    else
        warn "Backup cron install failed — run on VPS: sudo bash scripts/install-backup-cron.sh"
    fi
fi

# ── Step 11: WhatsApp pairing (optional, interactive) ────────────────────────
whatsapp_paired=false
echo ""
printf "  Pair WhatsApp now? [y/N]: " >&2; read -r whatsapp_yn
if [[ "${whatsapp_yn,,}" == "y" ]]; then
    step "Pairing WhatsApp"
    echo "  Scan the QR code with WhatsApp → Linked Devices → Link a device"
    echo "  (press Ctrl+C to cancel)"
    if ssh -t "$HOST" "sudo docker compose -f $REMOTE_DIR/docker-compose.yml exec -it openclaw openclaw channels login --channel whatsapp"; then
        ok "WhatsApp paired"
        whatsapp_paired=true
    else
        warn "WhatsApp pairing cancelled or failed — run: make pair-whatsapp"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  openclaw-deploy is running${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${GREEN}✅${NC} Telegram    @your_bot — send it a message"
if $whatsapp_paired; then
    echo -e "  ${GREEN}✅${NC} WhatsApp    paired"
else
    echo -e "  ${YELLOW}⚪${NC} WhatsApp    not paired — run: make pair-whatsapp"
fi
cron_installed=$(rsh "sudo crontab -l 2>/dev/null | grep -c openclaw-backup || true")
if $backup_cron_ok || [ "${cron_installed:-0}" -gt 0 ]; then
    echo -e "  ${GREEN}✅${NC} Backups     cron active (daily 03:00 UTC)"
elif [ -n "$BACKUP_S3_BUCKET" ]; then
    echo -e "  ${YELLOW}⚠️ ${NC} Backups     S3 configured but cron not installed — run on VPS: sudo bash scripts/install-backup-cron.sh"
else
    echo -e "  ${YELLOW}⚪${NC} Backups     not configured (re-run make deploy to add S3 credentials)"
fi
if [ -n "$ALERT_TELEGRAM_CHAT_ID" ]; then
    echo -e "  ${GREEN}✅${NC} Alerts      Telegram alerts enabled (chat $ALERT_TELEGRAM_CHAT_ID)"
else
    echo -e "  ${YELLOW}⚪${NC} Alerts      disabled — re-run make deploy to enable"
fi
if rsh "sudo docker compose -f $REMOTE_DIR/docker-compose.yml exec -T openclaw test -f /home/node/.openclaw/gcal_token.enc" 2>/dev/null; then
    echo -e "  ${GREEN}✅${NC} Calendar    Google Calendar configured"
else
    echo -e "  ${YELLOW}⚪${NC} Calendar    Google Calendar not set up — see docs/runbook.md §10"
fi
echo ""
echo "  Health check:  make doctor"
echo "  Logs:          make logs"
echo "  Upgrade:       make update"
echo ""
echo -e "HOST=${HOST}" > .deploy
ok "Saved HOST to .deploy — future make targets will use it automatically"
