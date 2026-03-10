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
