#!/bin/sh
set -e

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
