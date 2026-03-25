#!/bin/bash
# Push latest code to VPS and rebuild affected services.
# Non-interactive — safe to run after every git push.
# Usage: bash scripts/push.sh user@host
set -euo pipefail

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "Usage: $0 user@host"
    exit 1
fi

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${BOLD}▶ $1${NC}"; }
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }

COMPOSE="sudo docker compose"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── 1. Pull latest code ───────────────────────────────────────────────────────
step "Pulling latest code on VPS"
ssh "$HOST" "
    cd ~/openclaw-deploy
    git stash --quiet 2>/dev/null || true
    git pull --ff-only
    git stash pop --quiet 2>/dev/null || true
"
ok "Code up to date"

# ── 2. Rebuild running optional services ─────────────────────────────────────
step "Rebuilding running optional services"
running_svcs=$(ssh "$HOST" "cd ~/openclaw-deploy && $COMPOSE ps --format '{{.Service}}' 2>/dev/null" || echo "")
for svc_info in "mail-proxy:mail" "calendar-proxy:calendar" "voice-proxy:voice"; do
    svc="${svc_info%%:*}"
    profile="${svc_info##*:}"
    if echo "$running_svcs" | grep -qx "$svc"; then
        ssh "$HOST" "cd ~/openclaw-deploy && $COMPOSE --profile $profile up -d --build $svc"
        ok "Rebuilt $svc"
    fi
done

# ── 3. Update CLI binaries in openclaw ───────────────────────────────────────
step "Updating CLI binaries"
bash "$SCRIPT_DIR/deploy-clis.sh" "$HOST"

# ── 4. Deploy workspace files ─────────────────────────────────────────────────
step "Deploying workspace files"
scp workspace/*.md "$HOST:/tmp/"
for f in workspace/*.md; do
    fname=$(basename "$f")
    if [ "$fname" = "MEMORY.md" ]; then
        if ssh "$HOST" "cd ~/openclaw-deploy && $COMPOSE exec -T openclaw test -f /home/node/.openclaw/workspace/MEMORY.md" 2>/dev/null; then
            ssh "$HOST" "rm -f /tmp/MEMORY.md"
            ok "MEMORY.md preserved (agent-owned)"
        else
            ssh "$HOST" "cd ~/openclaw-deploy && $COMPOSE cp /tmp/MEMORY.md openclaw:/home/node/.openclaw/workspace/MEMORY.md && rm -f /tmp/MEMORY.md"
            ok "MEMORY.md seeded"
        fi
    else
        ssh "$HOST" "cd ~/openclaw-deploy && $COMPOSE cp /tmp/$fname openclaw:/home/node/.openclaw/workspace/$fname && rm -f /tmp/$fname"
    fi
done
ok "Workspace files deployed"

echo ""
echo -e "${BOLD}Push complete. Run 'make doctor' to verify.${NC}"
