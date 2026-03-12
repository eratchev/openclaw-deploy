#!/bin/bash
# One-shot Google Calendar setup. Run locally on your Mac.
# Usage: bash scripts/setup-gcal.sh user@host path/to/client_secret.json
set -euo pipefail

HOST="${1:-}"
CLIENT_SECRET="${2:-}"
CLIENT_SECRET="${CLIENT_SECRET/#\~/$HOME}"

if [ -z "$HOST" ] || [ -z "$CLIENT_SECRET" ]; then
    echo "Usage: $0 user@host path/to/client_secret.json"
    exit 1
fi

if [ ! -f "$CLIENT_SECRET" ]; then
    echo "Error: client_secret.json not found at $CLIENT_SECRET"
    exit 1
fi

BOLD='\033[1m'; GREEN='\033[0;32m'; NC='\033[0m'
step() { echo -e "\n${BOLD}▶ $1${NC}"; }
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMPDIR_LOCAL=$(mktemp -d)
trap 'rm -rf "$TMPDIR_LOCAL"' EXIT

# ── Step 1: Generate encryption key ──────────────────────────────────────────
step "Generating Fernet encryption key"
KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
ok "Key generated"

# ── Step 2: OAuth browser flow ────────────────────────────────────────────────
step "Authenticating with Google (browser will open)"
python3 "$REPO_DIR/services/calendar-proxy/scripts/auth_setup.py" \
    --client-secret "$CLIENT_SECRET" \
    --out "$TMPDIR_LOCAL/token.json"
ok "Token received"

# ── Step 3: Encrypt token ─────────────────────────────────────────────────────
step "Encrypting token"
cd "$REPO_DIR"
python3 services/calendar-proxy/scripts/encrypt_token.py \
    --token "$TMPDIR_LOCAL/token.json" \
    --key "$KEY" \
    --out "$TMPDIR_LOCAL/gcal_token.enc"
ok "Token encrypted"

# ── Step 4: Copy token to VPS ─────────────────────────────────────────────────
step "Copying gcal_token.enc to VPS"
scp "$TMPDIR_LOCAL/gcal_token.enc" "$HOST:/tmp/gcal_token.enc"
ssh "$HOST" "sudo cp /tmp/gcal_token.enc /var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data/gcal_token.enc \
    && sudo chown 1000:1000 /var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data/gcal_token.enc \
    && rm -f /tmp/gcal_token.enc"
ok "Token deployed to VPS volume"

# ── Step 5: Update .env on VPS ───────────────────────────────────────────────
step "Updating GCAL_TOKEN_ENCRYPTION_KEY in .env"
ssh "$HOST" "
    sed -i '/^GCAL_TOKEN_ENCRYPTION_KEY=/d' ~/openclaw-deploy/.env
    echo 'GCAL_TOKEN_ENCRYPTION_KEY=${KEY}' >> ~/openclaw-deploy/.env
"
ok "Key written to .env"

# ── Step 6: Restart calendar-proxy ───────────────────────────────────────────
step "Restarting calendar-proxy"
ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose --profile calendar up -d --force-recreate calendar-proxy"
ok "calendar-proxy restarted"

echo ""
echo -e "${BOLD}Google Calendar setup complete.${NC}"
echo "  Run 'make doctor' to verify."
