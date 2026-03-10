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

check_service openclaw
check_service caddy
check_service redis
check_service voice-proxy  true
check_service calendar-proxy true

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
