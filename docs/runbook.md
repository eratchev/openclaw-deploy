# OpenClaw Deploy — Ops Runbook

Practical reference for deploying, operating, and recovering the openclaw-deploy stack.

---

## 0. VPS Requirements

### Minimum spec

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| RAM      | 2 GB    | 4 GB        |
| Disk     | 20 GB   | 40 GB       |
| Swap     | **2 GB** (required on 2 GB hosts) | 4 GB |

The OpenClaw gateway is a Node.js process. On a 2 GB host it is safe, but **only if swap is configured** — without swap, a transient allocation spike will trigger a kernel OOM death spiral.

### Node.js heap cap

`docker-compose.yml` passes `NODE_OPTIONS=--max-old-space-size=512` to the openclaw container by default, capping V8 heap at 512 MB. This is the right value for a 2 GB host. On a 4 GB host you can raise it in `.env`:

```bash
# .env on VPS (optional override)
NODE_OPTIONS=--max-old-space-size=1024
```

### Add swap on a 2 GB VPS (one-time setup)

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Verify: `free -h` — Swap line should show 2.0 GB total.

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
4. Auto-generate `REDIS_PASSWORD` and `WEBHOOK_SECRET` (random 32-byte hex each).
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
# SSH into VPS first:
ssh $(cat .deploy | cut -d= -f2)

# Then on the VPS:
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

**Recovery (run on the VPS):**

```bash
# The volume name is derived from the repo directory name.
# Find it first:
docker volume ls | grep openclaw_data

# Remove the kill-switch file (substitute your actual volume name if different):
VOLUME=$(docker volume ls -q | grep openclaw_data)
docker run --rm -v "$VOLUME":/data busybox rm -f /data/GUARDRAIL_DISABLE

# Restart OpenClaw:
make restart
```

(`make restart` runs `docker compose restart openclaw`.)

---

## 7. Rollback to a Previous Image

**On your VPS** (`ssh $(cat .deploy | cut -d= -f2)`):

```bash
# On VPS: list available images with digests:
docker image ls ghcr.io/openclaw/openclaw --digests
```

**Locally:** pin the desired digest in `docker-compose.yml`:

```yaml
# Locally: services.openclaw.image — replace :latest with the digest
image: ghcr.io/openclaw/openclaw@sha256:<digest>
```

**Locally:** deploy the pinned image:

```bash
# Locally: push the change and redeploy
make deploy
```

Or, **on your VPS** if editing directly:

```bash
# On VPS: apply without touching other services
docker compose up -d --no-deps openclaw
make doctor
```

To return to latest: revert the image line to `:latest` and run `make update` (locally).

---

## 8. Restore from Backup

Backups are `.tar.gz` archives of the `openclaw-deploy_openclaw_data` volume created by `make backup` (local) or `make backup-remote` (S3).

**Run these commands on the VPS:** `ssh user@YOUR_VPS_IP`

```bash
# On VPS: 1. Stop OpenClaw to avoid data corruption:
docker compose stop openclaw

# On VPS: 2. Find your volume name (depends on repo directory name):
VOLUME=$(docker volume ls -q | grep openclaw_data)

# On VPS: 3. Restore the archive into the volume:
docker run --rm \
  -v "$VOLUME":/data \
  -v /path/to/backup:/backup:ro \
  busybox tar xzf /backup/openclaw-data-YYYYMMDD-HHMMSS.tar.gz -C /data

# On VPS: 4. Start OpenClaw:
docker compose up -d --no-deps openclaw

# On VPS: 5. Verify (or run `make doctor` locally):
docker compose ps
```

For S3 backups: download the archive on the VPS with `aws s3 cp s3://<bucket>/<key> /tmp/restore.tar.gz` before step 3.

---

## 9. Troubleshooting

### OOM / memory death spiral (kswapd at 100%, all processes in D state)

**Symptoms:** `top` shows `kswapd0` at 100% CPU, most processes in `D` (uninterruptible sleep) state, `docker compose down` hangs.

**Cause:** The VPS ran out of RAM and has no swap, or the Node.js gateway exceeded `--max-old-space-size` and triggered a restart loop where each startup attempt allocates more RAM than is available.

**Recovery:**

```bash
# 1. Force-stop the stuck containers
docker kill $(docker ps -q)
# If that hangs too:
sudo systemctl stop docker && sudo systemctl start docker

# 2. Add swap if missing (see section 0)
free -h    # confirm Swap row is non-zero before continuing
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile \
  && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 3. Pull latest config and restart
git pull && docker compose up -d
```

**Diagnosis — identify the leaking process:**

```bash
# Sort by RSS descending
ps aux --sort=-%mem | head -10
```

Key processes and normal RSS on a 2 GB host:

| Process | Normal RSS | Alarm if > |
|---------|-----------|------------|
| `openclaw-gatewa` | 400–500 MB | 700 MB |
| `openclaw-logs` | 50–300 MB | 500 MB |
| `python` (guardrail) | 40–70 MB | 150 MB |

`openclaw-logs` (`openclaw logs --follow --json`) has a known memory leak. The guardrail automatically restarts it every 30 minutes (`MAX_LOG_PROC_SECONDS=1800`). To restart it immediately:

```bash
# On VPS: find and kill the log subprocess — guardrail restarts it within 5s
pgrep -a node | grep 'openclaw logs'   # get PID
kill <pid>
```

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
