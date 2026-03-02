#!/bin/sh
set -e

echo "[entrypoint] Starting guardrail supervisor..."

# Supervised restart loop — guardrail must never silently disappear
while true; do
  python3 /home/node/guardrail.py || code=$?
  echo "[entrypoint] guardrail exited (code ${code:-0}), restarting in 5s..."
  unset code
  sleep 5
done &

echo "[entrypoint] Starting OpenClaw Gateway..."
exec openclaw gateway --port 18789
