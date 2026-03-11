#!/usr/bin/env bash
# Daily backup cron script.
# Backs up the openclaw_data volume to Hetzner Object Storage (S3-compatible).
# Installed by: sudo bash scripts/install-backup-cron.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[backup] ERROR: $ENV_FILE not found" >&2
  exit 1
fi

# Safe .env parser — reads one variable at a time using grep/cut.
# Unlike 'source', this never executes .env content as shell code.
parse_env() {
  local var=$1
  grep "^${var}=" "$ENV_FILE" | head -1 | cut -d= -f2-
}

BACKUP_S3_BUCKET="$(parse_env BACKUP_S3_BUCKET)"
BACKUP_S3_ENDPOINT="$(parse_env BACKUP_S3_ENDPOINT)"
BACKUP_S3_ACCESS_KEY="$(parse_env BACKUP_S3_ACCESS_KEY)"
BACKUP_S3_SECRET_KEY="$(parse_env BACKUP_S3_SECRET_KEY)"
BACKUP_S3_REGION="$(parse_env BACKUP_S3_REGION)"
BACKUP_RETAIN_DAYS="$(parse_env BACKUP_RETAIN_DAYS)"
BACKUP_RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-7}"
ALERT_TELEGRAM_CHAT_ID="$(parse_env ALERT_TELEGRAM_CHAT_ID)"
TELEGRAM_TOKEN="$(parse_env TELEGRAM_TOKEN)"

: "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET not set in .env}"
: "${BACKUP_S3_ENDPOINT:?BACKUP_S3_ENDPOINT not set in .env}"
: "${BACKUP_S3_ACCESS_KEY:?BACKUP_S3_ACCESS_KEY not set in .env}"
: "${BACKUP_S3_SECRET_KEY:?BACKUP_S3_SECRET_KEY not set in .env}"

# Send a Telegram alert. Silent no-op if credentials are not configured.
alert() {
    local msg="$1"
    [[ -z "${ALERT_TELEGRAM_CHAT_ID:-}" || -z "${TELEGRAM_TOKEN:-}" ]] && return 0
    curl -sf "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${ALERT_TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${msg}" \
        > /dev/null 2>&1 || true
}

VOLUME="$(basename "$REPO")_openclaw_data"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
TMPFILE="/tmp/openclaw-backup-${TIMESTAMP}.tar.gz"
S3_KEY="openclaw-data-${TIMESTAMP}.tar.gz"
_backup_ok=false
trap 'rm -f "$TMPFILE"; [[ "$_backup_ok" == "true" ]] || alert "🚨 OpenClaw backup FAILED at $(hostname) $(date -u +%Y-%m-%dT%H:%M:%SZ)"' EXIT

# ── Create backup ─────────────────────────────────────────────────────────────
echo "[backup] Creating backup of volume ${VOLUME}..."
docker run --rm \
  -v "${VOLUME}:/source:ro" \
  -v /tmp:/out \
  busybox tar czf "/out/openclaw-backup-${TIMESTAMP}.tar.gz" -C /source .

# ── Upload to S3 ──────────────────────────────────────────────────────────────
echo "[backup] Uploading ${S3_KEY} to s3://${BACKUP_S3_BUCKET}/..."
AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY" \
AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_KEY" \
  aws s3 cp "$TMPFILE" "s3://${BACKUP_S3_BUCKET}/${S3_KEY}" \
    --endpoint-url "$BACKUP_S3_ENDPOINT" \
    --region "${BACKUP_S3_REGION:-fsn1}" \
    --no-progress

# ── Remove local temp file ────────────────────────────────────────────────────
rm -f "$TMPFILE"
echo "[backup] Uploaded. Local temp file removed."

# ── Prune old backups from S3 ─────────────────────────────────────────────────
CUTOFF="$(date -u -d "-${BACKUP_RETAIN_DAYS} days" +%Y-%m-%d)"
echo "[backup] Pruning backups older than ${BACKUP_RETAIN_DAYS} days (before ${CUTOFF})..."

AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY" \
AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_KEY" \
  aws s3 ls "s3://${BACKUP_S3_BUCKET}/" \
    --endpoint-url "$BACKUP_S3_ENDPOINT" \
    --region "${BACKUP_S3_REGION:-fsn1}" | \
  while read -r obj_date _time _size obj_key; do
    if [[ -n "$obj_key" && "$obj_date" < "$CUTOFF" ]]; then
      echo "[backup] Deleting old backup: ${obj_key} (${obj_date})"
      AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY" \
      AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_KEY" \
        aws s3 rm "s3://${BACKUP_S3_BUCKET}/${obj_key}" \
          --endpoint-url "$BACKUP_S3_ENDPOINT" \
          --region "${BACKUP_S3_REGION:-fsn1}"
    fi
  done

_backup_ok=true
echo "[backup] Done."
