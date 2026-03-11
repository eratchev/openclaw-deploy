# OpenClaw Deploy — Ops Runbook

Practical reference for deploying, operating, and recovering the openclaw-deploy stack.

---

## 1. First-Time Deploy

Run from your local machine (requires SSH key access to the VPS):

```bash
make deploy HOST=user@x.x.x.x
```

The script will:
1. Install Docker on the VPS if missing.
2. Clone the repo to `~/openclaw-deploy` on the VPS.
3. Prompt for required values: `DOMAIN`, `TELEGRAM_TOKEN`, `ANTHROPIC_API_KEY`.
4. Auto-generate `REDIS_PASSWORD` (random 32-byte hex).
5. Optionally prompt for `OPENAI_API_KEY` (voice transcription) and S3 backup credentials.
6. Write `.env` to the VPS and start the stack.
7. Wait up to 60 s for services to become healthy.

After deploy:

```bash
make doctor        # run health checks
make logs          # follow OpenClaw logs
```

The HOST is saved to `.deploy` — subsequent `make` targets (doctor, pair-whatsapp, etc.) pick it up automatically. To override: `make <target> HOST=user@x.x.x.x`.

---

## 2. Daily Operations

All commands run from your local machine unless noted.

| Command             | What it does                                      |
|---------------------|---------------------------------------------------|
| `make logs`         | Follow OpenClaw container logs                    |
| `make logs-all`     | Follow all container logs                         |
| `make status`       | Show live CPU/memory for all containers           |
| `make doctor`       | Run health checks on the VPS (env, services, webhook, Redis, guardrail) |
| `make backup`       | Snapshot data volume to `./backups/` on VPS       |
| `make backup-remote`| Upload snapshot to Hetzner Object Storage (S3)    |

---

## 3. Update OpenClaw

Always back up before updating.

```bash
make backup-remote     # snapshot to S3 first
make update            # pull latest image, restart openclaw only
make doctor            # confirm healthy
```

`make update` runs `docker compose pull openclaw && docker compose up -d --no-deps openclaw`. It does not restart other services.

---

## 4. WhatsApp Pairing

```bash
make pair-whatsapp     # opens an SSH session and renders the QR code in your terminal
```

Scan the QR code with WhatsApp on your phone (Linked Devices → Link a Device).

**If the bot shows status 440 (session conflict):**

1. On your phone: Settings → Linked Devices → remove all entries named "OpenClaw" or "Node".
2. Re-run `make pair-whatsapp`.

---

## 5. Voice Transcription

Requires `OPENAI_API_KEY` in `.env` on the VPS.

**Enable:**

```bash
make up-voice          # builds voice-proxy, (re)starts caddy
```

**Disable:**

```bash
# SSH into VPS, then:
docker compose stop voice-proxy && docker compose rm -f voice-proxy
```

`make doctor` reports voice-proxy as optional (skip = not started, not an error).

---

## 6. Emergency Kill Switch

Stops the bot within 5 seconds without touching other services:

```bash
make kill-switch
```

This touches `/home/node/.openclaw/GUARDRAIL_DISABLE` inside the container. The guardrail detects the file and kills OpenClaw. Docker restarts the container, guardrail sees the file again, kills it again — holding the service down until the file is removed.

**Recovery:**

```bash
# Remove the kill-switch file from the volume:
docker run --rm -v openclaw-deploy_openclaw_data:/data busybox rm -f /data/GUARDRAIL_DISABLE
# Restart OpenClaw:
make restart
```

(`make restart` runs `docker compose restart openclaw`.)

---

## 7. Rollback to a Previous Image

```bash
# On VPS — list available images with digests:
docker image ls ghcr.io/openclaw/openclaw --digests
```

Pin the desired digest in `docker-compose.yml`:

```yaml
# services.openclaw.image — replace :latest with the digest
image: ghcr.io/openclaw/openclaw@sha256:<digest>
```

Apply without touching other services:

```bash
docker compose up -d --no-deps openclaw
make doctor
```

To return to latest: revert the image line to `:latest` and run `make update`.

---

## 8. Restore from Backup

Backups are `.tar.gz` archives of the `openclaw-deploy_openclaw_data` volume created by `make backup` (local) or `make backup-remote` (S3).

```bash
# 1. Stop OpenClaw to avoid data corruption:
docker compose stop openclaw

# 2. Restore the archive into the volume:
docker run --rm \
  -v openclaw-deploy_openclaw_data:/data \
  -v /path/to/backup:/backup:ro \
  busybox tar xzf /backup/openclaw-data-YYYYMMDD-HHMMSS.tar.gz -C /data

# 3. Start OpenClaw:
docker compose up -d --no-deps openclaw

# 4. Verify:
make doctor
```

For S3 backups: download the archive with `aws s3 cp s3://<bucket>/<key> /tmp/restore.tar.gz` before step 2.

---

## 9. Troubleshooting

### CPU spike above 100%

```bash
make status    # identify which container is spiking
```

If it is `openclaw`, check for a WhatsApp session conflict:

```bash
docker compose logs openclaw | grep "status=440"
```

If 440 errors appear, follow the WhatsApp pairing steps in section 4.

### `make doctor` reports services not healthy

Services have a `start_period` of 10–30 s. Wait 30–60 s after startup and re-run:

```bash
make doctor
```

If still unhealthy, check all logs:

```bash
make logs-all
```

Look for startup errors, missing env vars, or port conflicts.

### Telegram webhook not registered

```bash
# On VPS:
docker compose exec openclaw openclaw config set channels.telegram.webhookUrl "https://<DOMAIN>/telegram-webhook"
docker compose restart openclaw
make doctor    # confirm webhook now shows registered
```

The domain must match the `DOMAIN` value in `.env` and must be reachable over HTTPS (Caddy handles TLS automatically).

---

## 10. Google Calendar Setup

See the full setup guide: `docs/plans/2026-03-03-google-calendar.md`

High-level steps:
1. Authenticate locally on Mac: `python3 scripts/auth_setup.py`
2. Encrypt the token: `python3 scripts/encrypt_token.py`
3. Copy `gcal_token.enc` to the VPS volume and set ownership:
   ```bash
   sudo chown 1000:1000 /path/to/volume/gcal_token.enc
   ```
4. Set `GCAL_TOKEN_ENCRYPTION_KEY` in `.env` on the VPS.
5. Start the calendar proxy: `make up-calendar`
6. Configure exec approvals (once): `make setup-approvals`

`make doctor` reports `calendar-proxy` status (optional — skip if not started).
