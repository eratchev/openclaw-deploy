#!/bin/sh
set -e

# ── First-boot bootstrap ──────────────────────────────────────────────────────
# Generate minimal openclaw.json from env vars if no config exists.
# This eliminates the local OpenClaw install prerequisite.
CONFIG_FILE="/home/node/.openclaw/openclaw.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "[entrypoint] No config found — bootstrapping from .env..."

    # Verify required env vars
    for var in TELEGRAM_TOKEN DOMAIN; do
        eval "val=\$$var"
        if [ -z "$val" ]; then
            echo "[entrypoint] ERROR: $var is not set. Cannot bootstrap config."
            exit 1
        fi
    done

    # Generate webhook secret (stored in config only — not written back to .env)
    WEBHOOK_SECRET=$(openssl rand -hex 32)

    openclaw config set channels.telegram.botToken  "${TELEGRAM_TOKEN}"
    openclaw config set channels.telegram.webhookSecret "${WEBHOOK_SECRET}"
    openclaw config set channels.telegram.webhookUrl "https://${DOMAIN}/telegram-webhook"
    openclaw config set channels.telegram.webhookHost "0.0.0.0"

    # Configure Anthropic LLM provider if key is present
    # NOTE: OpenClaw may auto-detect ANTHROPIC_API_KEY from env — || true handles that
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        openclaw config set agents.main.provider anthropic || true
    fi

    echo "[entrypoint] Bootstrap complete. Starting gateway..."
fi

# ── Guardrail supervisor ──────────────────────────────────────────────────────
echo "[entrypoint] Starting guardrail supervisor..."

# Supervised restart loop — guardrail must never silently disappear
while true; do
  code=0
  python3 /home/node/guardrail.py || code=$?
  echo "[entrypoint] guardrail exited (code ${code}), restarting in 5s..."
  sleep 5
done &

echo "[entrypoint] Starting OpenClaw Gateway..."
exec openclaw gateway --port 18789
