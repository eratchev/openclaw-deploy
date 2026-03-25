#!/bin/bash
# Multi-account Google Calendar setup. Run locally on your Mac.
# Usage:
#   bash scripts/setup-gcal.sh user@host path/to/client_secret.json [account_label]
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
        if [ -f \"\$DATA/gcal_token.enc\" ]; then
            sudo mv \"\$DATA/gcal_token.enc\" \"\$DATA/gcal_token.personal.enc\"
            sudo chown 1000:1000 \"\$DATA/gcal_token.personal.enc\"
            echo 'Token file renamed'
        else
            echo 'No legacy gcal_token.enc found — already migrated?'
        fi
    "
    # Rename env var in .env
    ssh "$HOST" "
        cd ~/openclaw-deploy
        if grep -q '^GCAL_TOKEN_ENCRYPTION_KEY=' .env; then
            KEY=\$(grep '^GCAL_TOKEN_ENCRYPTION_KEY=' .env | cut -d= -f2-)
            sed -i '/^GCAL_TOKEN_ENCRYPTION_KEY=/d' .env
            sed -i '/^GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL=/d' .env
            echo \"GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL=\$KEY\" >> .env
            echo 'Env var renamed'
        else
            echo 'GCAL_TOKEN_ENCRYPTION_KEY not found — already migrated?'
        fi
        # Add GCAL_ACCOUNTS=personal if not already set
        if ! grep -q '^GCAL_ACCOUNTS=' .env; then
            echo 'GCAL_ACCOUNTS=personal' >> .env
            echo 'GCAL_ACCOUNTS set'
        fi
    "
    ok "Migration complete (personal)"
    step "Restarting calendar-proxy"
    ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose --profile calendar up -d --force-recreate calendar-proxy"
    ok "calendar-proxy restarted"
    step "Updating MEMORY_GUIDE.md"
    python3 "$SCRIPT_DIR/update-memory-accounts.py" gcal personal
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
python3 "$REPO_DIR/services/calendar-proxy/scripts/auth_setup.py" \
    --client-secret "$CLIENT_SECRET" \
    --out "$TMPDIR_LOCAL/token.json"
ok "Token received"

step "Encrypting token"
cd "$REPO_DIR"
python3 services/calendar-proxy/scripts/encrypt_token.py \
    --token "$TMPDIR_LOCAL/token.json" \
    --key "$KEY" \
    --out "$TMPDIR_LOCAL/gcal_token.${ACCOUNT}.enc"
ok "Token encrypted"

step "Copying gcal_token.${ACCOUNT}.enc to VPS"
scp "$TMPDIR_LOCAL/gcal_token.${ACCOUNT}.enc" "$HOST:/tmp/gcal_token.${ACCOUNT}.enc"
ssh "$HOST" "
    sudo cp /tmp/gcal_token.${ACCOUNT}.enc \
        /var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data/gcal_token.${ACCOUNT}.enc
    sudo chown 1000:1000 \
        /var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data/gcal_token.${ACCOUNT}.enc
    rm -f /tmp/gcal_token.${ACCOUNT}.enc
"
ok "Token deployed to VPS volume"

step "Updating .env on VPS"
ssh "$HOST" "
    cd ~/openclaw-deploy
    # Write/overwrite the per-label encryption key
    sed -i '/^GCAL_TOKEN_ENCRYPTION_KEY_${LABEL_UPPER}=/d' .env
    echo 'GCAL_TOKEN_ENCRYPTION_KEY_${LABEL_UPPER}=${KEY}' >> .env

    # Add label to GCAL_ACCOUNTS (idempotent)
    if grep -q '^GCAL_ACCOUNTS=' .env; then
        if ! grep -qE '^GCAL_ACCOUNTS=.*\b${ACCOUNT}\b' .env; then
            sed -i 's/^GCAL_ACCOUNTS=\(.*\)/GCAL_ACCOUNTS=\1,${ACCOUNT}/' .env
        fi
    else
        echo 'GCAL_ACCOUNTS=${ACCOUNT}' >> .env
    fi
"
ok "Key written to .env, '$ACCOUNT' added to GCAL_ACCOUNTS"

step "Pulling latest code on VPS"
ssh "$HOST" "cd ~/openclaw-deploy && git pull --ff-only"
ok "Code updated"

step "Restarting calendar-proxy"
ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose --profile calendar up -d --build calendar-proxy"
ok "calendar-proxy restarted"

step "Updating MEMORY_GUIDE.md"
new_accounts=$(ssh "$HOST" "grep '^GCAL_ACCOUNTS=' ~/openclaw-deploy/.env | cut -d= -f2-" || echo "$ACCOUNT")
python3 "$SCRIPT_DIR/update-memory-accounts.py" gcal "$new_accounts"
scp "$REPO_DIR/workspace/MEMORY_GUIDE.md" "$HOST:/tmp/MEMORY_GUIDE.md"
ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose cp /tmp/MEMORY_GUIDE.md openclaw:/home/node/.openclaw/workspace/MEMORY_GUIDE.md && rm -f /tmp/MEMORY_GUIDE.md"
ok "MEMORY_GUIDE.md deployed"

echo ""
echo -e "${BOLD}Google Calendar setup complete for account '$ACCOUNT'.${NC}"
echo "  Run 'make doctor' to verify."
