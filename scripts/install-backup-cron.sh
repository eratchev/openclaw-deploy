#!/usr/bin/env bash
# Install the daily backup cron job for root.
# Run once as root: sudo bash scripts/install-backup-cron.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO/scripts/backup-cron.sh"
INSTALL_PATH="/usr/local/sbin/openclaw-backup"
CRON_ENTRY="0 3 * * * $INSTALL_PATH >> /var/log/openclaw-backup.log 2>&1"
CRON_MARKER="openclaw-backup"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi

# Install an immutable snapshot to /usr/local/sbin so the cron job
# is not affected by future 'git pull' updates to the repo.
install -m 0700 "$SCRIPT" "$INSTALL_PATH"

# Install into root crontab (idempotent — remove existing entry first)
(
  { crontab -l 2>/dev/null || true; } | { grep -v "$CRON_MARKER" || true; }
  echo "# $CRON_MARKER"
  echo "$CRON_ENTRY"
) | crontab -

echo "[install-backup-cron] Script installed to $INSTALL_PATH"
echo "[install-backup-cron] Cron job installed (runs daily at 03:00 UTC):"
echo "  $CRON_ENTRY"
echo "[install-backup-cron] Logs will go to /var/log/openclaw-backup.log"
echo "[install-backup-cron] Test with: sudo $INSTALL_PATH"
