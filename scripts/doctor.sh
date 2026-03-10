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
