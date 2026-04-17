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

    # ── Morning cron ────────────────────────────────────────────────────────────
    # --announce + --to: deliver the agent's final summary to the Telegram chat.
    # Without --announce the output stays in the isolated session and is never sent.
    # || true: job persists in volume across restarts; guard prevents set -e from
    # halting bootstrap if job already exists on a volume restored from backup
    openclaw cron add \
        --name "Morning briefing" \
        --cron "0 9 * * *" \
        --tz "America/Los_Angeles" \
        --session isolated \
        --announce \
        --model "anthropic/claude-haiku-4-5-20251001" \
        --thinking low \
        --timeout-seconds 480 \
        --channel telegram \
        ${HEARTBEAT_TO:+--to "${HEARTBEAT_TO}"} \
        --message "Read MEMORY_GUIDE.md for tool documentation. Then run the morning briefing: check today full calendar schedule for every gcal account listed in MEMORY_GUIDE.md, and check unread emails from overnight for every gmail account listed in MEMORY_GUIDE.md (use gmail list --limit 10 per account). Compose a concise summary — events today with times, any email action items from all accounts — and send it to Evgueni via Telegram." \
        || true

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
