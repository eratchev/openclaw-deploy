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

# Install aws CLI if not present (required by backup-cron.sh)
if ! command -v aws &>/dev/null; then
  echo "[install-backup-cron] Installing aws CLI..."
  apt-get install -y -q awscli
fi

# Install an immutable snapshot to /usr/local/sbin so the cron job
# is not affected by future 'git pull' updates to the repo.
# Rewrite the REPO= line to the absolute path so the script works
# from /usr/local/sbin (where dirname "$0" would resolve to /usr/local).
tmpfile=$(mktemp)
sed "s|^REPO=.*|REPO=\"${REPO}\"|" "$SCRIPT" > "$tmpfile"
install -m 0700 "$tmpfile" "$INSTALL_PATH"
rm -f "$tmpfile"

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
