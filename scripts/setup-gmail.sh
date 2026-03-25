#!/bin/bash
# Multi-account Gmail setup. Run locally on your Mac.
# Usage:
#   bash scripts/setup-gmail.sh user@host path/to/client_secret.json [account_label]
#
# With no account_label: migrates existing single-account setup to 'personal'
# With account_label:    runs OAuth flow for that account (e.g. ACCOUNT=jobs)
set -euo pipefail

HOST="${1:-}"
CLIENT_SECRET="${2:-}"
CLIENT_SECRET="${CLIENT_SECRET/#\~/$HOME}"
ACCOUNT="${3:-}"

if [ -z "$HOST" ] || [ -z "$CLIENT_SECRET" ]; then
    echo "Usage: $0 user@host path/to/client_secret.json [account_label]"
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

# ── Migration mode (no ACCOUNT arg) ──────────────────────────────────────────
if [ -z "$ACCOUNT" ]; then
    step "Migration mode: renaming existing single-account setup to 'personal'"
    # Rename token file on VPS
    ssh "$HOST" "
        DATA=/var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data
        if [ -f \"\$DATA/gmail_token.enc\" ]; then
            sudo mv \"\$DATA/gmail_token.enc\" \"\$DATA/gmail_token.personal.enc\"
            sudo chown 1000:1000 \"\$DATA/gmail_token.personal.enc\"
            echo 'Token file renamed'
        else
            echo 'No legacy gmail_token.enc found — already migrated?'
        fi
    "
    # Rename env var in .env
    ssh "$HOST" "
        cd ~/openclaw-deploy
        if grep -q '^GMAIL_TOKEN_ENCRYPTION_KEY=' .env; then
            KEY=\$(grep '^GMAIL_TOKEN_ENCRYPTION_KEY=' .env | cut -d= -f2-)
            sed -i '/^GMAIL_TOKEN_ENCRYPTION_KEY=/d' .env
            sed -i '/^GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL=/d' .env
            echo \"GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL=\$KEY\" >> .env
            echo 'Env var renamed'
        else
            echo 'GMAIL_TOKEN_ENCRYPTION_KEY not found — already migrated?'
        fi
        # Add GMAIL_ACCOUNTS=personal if not already set
        if ! grep -q '^GMAIL_ACCOUNTS=' .env; then
            echo 'GMAIL_ACCOUNTS=personal' >> .env
            echo 'GMAIL_ACCOUNTS set'
        fi
    "
    ok "Migration complete (personal)"
    step "Restarting mail-proxy"
    ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose --profile mail up -d --force-recreate mail-proxy"
    ok "mail-proxy restarted"
    step "Updating MEMORY_GUIDE.md"
    python3 "$SCRIPT_DIR/update-memory-accounts.py" gmail personal
    scp "$REPO_DIR/workspace/MEMORY_GUIDE.md" "$HOST:/tmp/MEMORY_GUIDE.md"
    ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose cp /tmp/MEMORY_GUIDE.md openclaw:/home/node/.openclaw/workspace/MEMORY_GUIDE.md && rm -f /tmp/MEMORY_GUIDE.md"
    ok "MEMORY_GUIDE.md deployed"
    echo ""
    echo -e "${BOLD}Migration complete. Run 'make doctor' to verify.${NC}"
    exit 0
fi

LABEL_UPPER=$(echo "$ACCOUNT" | tr '[:lower:]' '[:upper:]')

# ── New account OAuth flow ────────────────────────────────────────────────────
step "Generating Fernet encryption key for account '$ACCOUNT'"
KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
ok "Key generated"

step "Authenticating with Google for account '$ACCOUNT' (browser will open)"
python3 "$REPO_DIR/services/mail-proxy/scripts/auth_setup.py" \
    --client-secret "$CLIENT_SECRET" \
    --out "$TMPDIR_LOCAL/token.json"
ok "Token received"

step "Encrypting token"
cd "$REPO_DIR"
python3 services/mail-proxy/scripts/encrypt_token.py \
    --token "$TMPDIR_LOCAL/token.json" \
    --key "$KEY" \
    --out "$TMPDIR_LOCAL/gmail_token.${ACCOUNT}.enc"
ok "Token encrypted"

step "Copying gmail_token.${ACCOUNT}.enc to VPS"
scp "$TMPDIR_LOCAL/gmail_token.${ACCOUNT}.enc" "$HOST:/tmp/gmail_token.${ACCOUNT}.enc"
ssh "$HOST" "
    sudo cp /tmp/gmail_token.${ACCOUNT}.enc \
        /var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data/gmail_token.${ACCOUNT}.enc
    sudo chown 1000:1000 \
        /var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data/gmail_token.${ACCOUNT}.enc
    rm -f /tmp/gmail_token.${ACCOUNT}.enc
"
ok "Token deployed to VPS volume"

step "Updating .env on VPS"
ssh "$HOST" "
    cd ~/openclaw-deploy
    # Write/overwrite the per-label encryption key
    sed -i '/^GMAIL_TOKEN_ENCRYPTION_KEY_${LABEL_UPPER}=/d' .env
    echo 'GMAIL_TOKEN_ENCRYPTION_KEY_${LABEL_UPPER}=${KEY}' >> .env

    # Add label to GMAIL_ACCOUNTS (idempotent)
    if grep -q '^GMAIL_ACCOUNTS=' .env; then
        if ! grep -qE '^GMAIL_ACCOUNTS=.*\b${ACCOUNT}\b' .env; then
            sed -i 's/^GMAIL_ACCOUNTS=\(.*\)/GMAIL_ACCOUNTS=\1,${ACCOUNT}/' .env
        fi
    else
        echo 'GMAIL_ACCOUNTS=${ACCOUNT}' >> .env
    fi
"
ok "Key written to .env, '$ACCOUNT' added to GMAIL_ACCOUNTS"

step "Pulling latest code on VPS"
ssh "$HOST" "cd ~/openclaw-deploy && git pull --ff-only"
ok "Code updated"

step "Restarting mail-proxy"
ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose --profile mail up -d --build mail-proxy"
ok "mail-proxy restarted"

step "Updating MEMORY_GUIDE.md"
new_accounts=$(ssh "$HOST" "grep '^GMAIL_ACCOUNTS=' ~/openclaw-deploy/.env | cut -d= -f2-" || echo "$ACCOUNT")
python3 "$SCRIPT_DIR/update-memory-accounts.py" gmail "$new_accounts"
scp "$REPO_DIR/workspace/MEMORY_GUIDE.md" "$HOST:/tmp/MEMORY_GUIDE.md"
ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose cp /tmp/MEMORY_GUIDE.md openclaw:/home/node/.openclaw/workspace/MEMORY_GUIDE.md && rm -f /tmp/MEMORY_GUIDE.md"
ok "MEMORY_GUIDE.md deployed"

echo ""
echo -e "${BOLD}Gmail setup complete for account '$ACCOUNT'.${NC}"
echo "  Run 'make doctor' to verify."
