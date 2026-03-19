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

    # ── Heartbeat ──────────────────────────────────────────────────────────────
    # Runs every 30 min during active hours; agent reads HEARTBEAT.md for checklist
    openclaw config set agents.defaults.heartbeat.every "30m"
    openclaw config set agents.defaults.heartbeat.target "last"
    openclaw config set agents.defaults.heartbeat.directPolicy "allow"
    openclaw config set agents.defaults.heartbeat.activeHours.start "09:00"
    openclaw config set agents.defaults.heartbeat.activeHours.end "22:00"
    openclaw config set agents.defaults.heartbeat.activeHours.timezone "America/Los_Angeles"
    # Explicit Telegram delivery target (Telegram chat ID) — set HEARTBEAT_TO in .env
    if [ -n "${HEARTBEAT_TO:-}" ]; then
        openclaw config set agents.defaults.heartbeat.to "${HEARTBEAT_TO}"
    fi

    # ── Morning cron ────────────────────────────────────────────────────────────
    # || true: job persists in volume across restarts; guard prevents set -e from
    # halting bootstrap if job already exists on a volume restored from backup
    openclaw cron add \
        --name "Morning briefing" \
        --cron "0 9 * * *" \
        --tz "America/Los_Angeles" \
        --session isolated \
        --message "Read MEMORY_GUIDE.md for tool documentation. Then run the morning briefing: check today's full calendar schedule (gcal list for today) and important unread emails from overnight (gmail list --limit 10). Compose a concise summary — events today with times, any email action items — and send it to Evgueni via Telegram." \
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
