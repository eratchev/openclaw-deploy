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

# Source .env — set -a exports vars so subprocesses (docker compose) inherit them
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
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

check_service() {
    local name=$1 optional=${2:-false}
    local status
    status=$(sudo docker compose ps --format '{{.Name}} {{.Status}}' 2>/dev/null | grep "^openclaw-deploy-${name}-" || echo "")
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
check_optional WEBHOOK_SECRET    "WEBHOOK_SECRET"   "Telegram webhook unauthenticated"
check_optional ALERT_TELEGRAM_CHAT_ID "ALERT_TELEGRAM_CHAT_ID" "guardrail/backup alerts disabled  →  set to your Telegram chat ID (message @userinfobot to find it)"

# ── Services ───────────────────────────────────────────────────────────────────

echo ""
echo " Services"

check_service openclaw
check_service caddy
check_service redis
check_service voice-proxy  true
check_service calendar-proxy true
check_service mail-proxy true

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
        webhook_err_date=$(echo "$webhook_info" | python3 -c "import sys,json; print(json.load(sys.stdin)['result'].get('last_error_date',0))" 2>/dev/null)
        now=$(date +%s)
        if [ -n "$webhook_err" ] && [ "$((now - webhook_err_date))" -lt 600 ]; then
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

# ── Google Calendar ────────────────────────────────────────────────────────────

echo ""
echo " Google Calendar"

if [ -n "${GCAL_ACCOUNTS:-}" ]; then
    if [ -n "${GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL:-}${GCAL_TOKEN_ENCRYPTION_KEY_JOBS:-}" ] || \
       [ -n "${GCAL_TOKEN_ENCRYPTION_KEY:-}" ]; then
        pass "GCAL_TOKEN_ENCRYPTION_KEY_*  set"
    else
        warn "No GCAL_TOKEN_ENCRYPTION_KEY_* vars found — run: make setup-gcal ACCOUNT=personal CLIENT_SECRET=..."
    fi
    IFS=',' read -ra _gcal_accounts <<< "$GCAL_ACCOUNTS"
    for _acct in "${_gcal_accounts[@]}"; do
        _acct=$(echo "$_acct" | tr -d ' ')
        if sudo docker compose --profile calendar exec -T calendar-proxy test -f "/data/gcal_token.${_acct}.enc" 2>/dev/null; then
            pass "gcal:${_acct}  token present"
        else
            warn "gcal:${_acct}  token missing → run: make setup-gcal ACCOUNT=${_acct} CLIENT_SECRET=..."
        fi
    done
    cal_health=$(sudo docker compose --profile calendar exec -T calendar-proxy python3 -c \
        "import urllib.request; import json; r=urllib.request.urlopen('http://localhost:8080/health',timeout=3); print(json.load(r)['configured'])" \
        2>/dev/null || echo "")
    if [ "$cal_health" = "True" ]; then
        pass "calendar-proxy  /health → configured"
    elif sudo docker compose ps --format '{{.Name}}' 2>/dev/null | grep -q "calendar-proxy"; then
        warn "calendar-proxy  running but /health unreachable"
    else
        skip "calendar-proxy  not started → run: make up-calendar"
    fi
else
    # Legacy single-account check
    if sudo docker compose exec -T openclaw test -f /home/node/.openclaw/gcal_token.enc 2>/dev/null; then
        pass "gcal_token.enc  present (legacy)"
    else
        skip "Google Calendar  not configured → run: make setup-gcal CLIENT_SECRET=..."
    fi
fi

# ── Gmail ──────────────────────────────────────────────────────────────────────

echo ""
echo " Gmail"

if [ -n "${GMAIL_ACCOUNTS:-}" ]; then
    if [ -n "${GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL:-}${GMAIL_TOKEN_ENCRYPTION_KEY_JOBS:-}" ] || \
       [ -n "${GMAIL_TOKEN_ENCRYPTION_KEY:-}" ]; then
        pass "GMAIL_TOKEN_ENCRYPTION_KEY_*  set"
    else
        warn "No GMAIL_TOKEN_ENCRYPTION_KEY_* vars found — run: make setup-gmail ACCOUNT=personal CLIENT_SECRET=..."
    fi
    IFS=',' read -ra _gmail_accounts <<< "$GMAIL_ACCOUNTS"
    for _acct in "${_gmail_accounts[@]}"; do
        _acct=$(echo "$_acct" | tr -d ' ')
        if sudo docker compose --profile mail exec -T mail-proxy test -f "/data/gmail_token.${_acct}.enc" 2>/dev/null; then
            pass "gmail:${_acct}  token present"
        else
            warn "gmail:${_acct}  token missing → run: make setup-gmail ACCOUNT=${_acct} CLIENT_SECRET=..."
        fi
    done
    mail_health=$(sudo docker compose --profile mail exec -T mail-proxy python3 -c \
        "import urllib.request; import json; r=urllib.request.urlopen('http://localhost:8091/health',timeout=3); print(json.load(r)['configured'])" \
        2>/dev/null || echo "")
    if [ "$mail_health" = "True" ]; then
        pass "mail-proxy  /health → configured"
    elif sudo docker compose ps --format '{{.Name}}' 2>/dev/null | grep -q "mail-proxy"; then
        warn "mail-proxy  running but /health unreachable"
    else
        skip "mail-proxy  not started → run: make up-mail"
    fi
else
    # Legacy single-account check
    if [ -n "${GMAIL_TOKEN_ENCRYPTION_KEY:-}" ]; then
        warn "GMAIL_TOKEN_ENCRYPTION_KEY set but GMAIL_ACCOUNTS not configured → run: make setup-gmail CLIENT_SECRET=... to migrate"
    else
        skip "Gmail  not configured → run: make setup-gmail CLIENT_SECRET=..."
    fi
fi

# ── Skills ─────────────────────────────────────────────────────────────────────

echo ""
echo " Skills"

check_skill_bin() {
    local bin="$1" skill="$2"
    if sudo docker compose exec -T openclaw test -f "$BIN_DIR/$bin" 2>/dev/null; then
        pass "$skill  ($bin installed)"
    else
        skip "$skill  ($bin not installed  →  run: make setup-skills SKILLS=$skill)"
    fi
}

BIN_DIR="/home/node/.openclaw/bin"
check_skill_bin "gh"             "github"
check_skill_bin "jq"             "session-logs (jq)"
check_skill_bin "rg"             "session-logs (rg)"
check_skill_bin "spogo"          "spotify-player"
skip "summarize  (not available on Linux — macOS only)"

# ── Backups ────────────────────────────────────────────────────────────────────

echo ""
echo " Backups"

if [ -n "${BACKUP_S3_BUCKET:-}" ] && [ -n "${BACKUP_S3_ACCESS_KEY:-}" ]; then
    pass "S3 credentials  configured"
else
    warn "S3 credentials  not set — daily backups disabled"
fi

if sudo crontab -l 2>/dev/null | grep -q "openclaw-backup"; then
    pass "Cron  backup job installed"
else
    warn "Cron  not installed  →  run: sudo bash scripts/install-backup-cron.sh"
fi

# ── System ─────────────────────────────────────────────────────────────────────

echo ""
echo " System"

# Swap
swap_total=$(free -m | awk '/^Swap:/ {print $2}')
if [[ "${swap_total:-0}" -gt 0 ]]; then
    pass "Swap  ${swap_total}MB configured"
else
    warn "Swap  none — add 2GB swapfile on 2GB hosts (see docs/runbook.md section 0)"
fi

# NODE_OPTIONS (V8 heap cap)
node_opts=$(sudo docker compose exec -T openclaw printenv NODE_OPTIONS 2>/dev/null || true)
if echo "$node_opts" | grep -q "max-old-space"; then
    pass "NODE_OPTIONS  ${node_opts}"
else
    warn "NODE_OPTIONS  not set — V8 heap unbounded (OOM risk on 2GB hosts; set NODE_OPTIONS=--max-old-space-size=768 in .env)"
fi

# ── Inbound ────────────────────────────────────────────────────────────────────

echo ""
echo " Inbound"

input_policy=$(sudo iptables -L INPUT -n 2>/dev/null | awk 'NR==1{gsub(/[()]/, "", $NF); print $NF}')
if [ "${input_policy:-}" = "DROP" ]; then
    input_rules=$(sudo iptables -L INPUT -n 2>/dev/null)
    if echo "$input_rules" | grep -q "dpt:22" && echo "$input_rules" | grep -q "dpt:443"; then
        pass "Inbound firewall  active (policy DROP, SSH/443 open)"
    else
        warn "Inbound policy is DROP but SSH(22) or HTTPS(443) not found — check iptables -L INPUT"
    fi
else
    warn "Inbound firewall  INPUT policy is ACCEPT — run: make setup-inbound"
fi

# ── Egress ─────────────────────────────────────────────────────────────────────

echo ""
echo " Egress"

if sudo iptables -L OPENCLAW_EGRESS -n &>/dev/null; then
    if sudo iptables -L DOCKER-USER -n 2>/dev/null | grep -q "OPENCLAW_EGRESS"; then
        pass "Egress allowlist  active (HTTPS/DNS/NTP only)"
    else
        warn "Egress chain exists but not hooked into DOCKER-USER — run: make setup-egress"
    fi
else
    warn "Egress allowlist  not configured — run: make setup-egress"
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
