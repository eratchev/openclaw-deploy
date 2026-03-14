#!/bin/bash
# One-shot Gmail setup. Run locally on your Mac.
# Usage: bash scripts/setup-gmail.sh user@host path/to/client_secret.json
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
python3 "$REPO_DIR/services/mail-proxy/scripts/auth_setup.py" \
    --client-secret "$CLIENT_SECRET" \
    --out "$TMPDIR_LOCAL/token.json"
ok "Token received"

# ── Step 3: Encrypt token ─────────────────────────────────────────────────────
step "Encrypting token"
cd "$REPO_DIR"
python3 services/mail-proxy/scripts/encrypt_token.py \
    --token "$TMPDIR_LOCAL/token.json" \
    --key "$KEY" \
    --out "$TMPDIR_LOCAL/gmail_token.enc"
ok "Token encrypted"

# ── Step 4: Copy token to VPS ─────────────────────────────────────────────────
step "Copying gmail_token.enc to VPS"
scp "$TMPDIR_LOCAL/gmail_token.enc" "$HOST:/tmp/gmail_token.enc"
ssh "$HOST" "sudo cp /tmp/gmail_token.enc /var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data/gmail_token.enc \
    && sudo chown 1000:1000 /var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data/gmail_token.enc \
    && rm -f /tmp/gmail_token.enc"
ok "Token deployed to VPS volume"

# ── Step 5: Update .env on VPS ───────────────────────────────────────────────
step "Updating GMAIL_TOKEN_ENCRYPTION_KEY in .env"
# Note: double-quoted SSH string expands $KEY locally before sending to remote shell.
# Single-quoted echo inside ensures the key value (which may contain special chars) is safe.
ssh "$HOST" "sed -i '/^GMAIL_TOKEN_ENCRYPTION_KEY=/d' ~/openclaw-deploy/.env && echo 'GMAIL_TOKEN_ENCRYPTION_KEY=$KEY' >> ~/openclaw-deploy/.env"
ok "Key written to .env"

# ── Step 6: Register gmail CLI on exec approvals allowlist ────────────────────
step "Registering gmail CLI on exec approvals allowlist"
ssh "$HOST" "cd ~/openclaw-deploy && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add '/home/node/.openclaw/bin/gmail' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add 'gmail' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add 'gmail *' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw config set tools.exec.safeBins '[\"gcal\",\"date\",\"ai\",\"gmail\"]' && \
    sudo docker compose restart openclaw"
ok "gmail CLI registered on allowlist"

# ── Step 7: Start mail-proxy (or restart if already running) ──────────────────
step "Starting mail-proxy"
ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose --profile mail up -d --build mail-proxy"
ok "mail-proxy started"

echo ""
echo -e "${BOLD}Gmail setup complete.${NC}"
echo "  Run 'make doctor' to verify."
