#!/bin/bash
# Update CLI binaries in the openclaw container without re-running OAuth.
# Safe to run at any time — skips binaries that haven't been installed yet.
# Usage: bash scripts/deploy-clis.sh user@host
set -euo pipefail

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "Usage: $0 user@host"
    exit 1
fi

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${BOLD}▶ $1${NC}"; }
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
skip() { echo -e "  ${YELLOW}–${NC} $1 (not installed — skipping)"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE="sudo docker compose -f ~/openclaw-deploy/docker-compose.yml"

install_cli() {
    local name="$1"
    local src="$2"
    local dest="/home/node/.openclaw/bin/$name"

    if ! [ -f "$src" ]; then
        skip "$name (source not found at $src)"
        return
    fi

    # Only update if already installed — preserves approvals allowlist setup.
    # If the binary isn't there, setup-gmail / setup-gcal hasn't been run yet.
    if ssh "$HOST" "$COMPOSE exec -T openclaw test -f $dest" 2>/dev/null; then
        scp "$src" "$HOST:/tmp/$name"
        ssh "$HOST" "$COMPOSE cp /tmp/$name openclaw:$dest \
            && $COMPOSE exec -T openclaw chmod +x $dest \
            && rm -f /tmp/$name"
        ok "$name"
    else
        skip "$name"
    fi
}

step "Updating CLI binaries in openclaw container"
install_cli "gmail"    "$REPO_DIR/services/mail-proxy/scripts/gmail"
install_cli "contacts" "$REPO_DIR/services/mail-proxy/scripts/contacts"
install_cli "gcal"     "$REPO_DIR/services/calendar-proxy/scripts/gcal"

echo ""
echo -e "${BOLD}CLI update complete.${NC}"
