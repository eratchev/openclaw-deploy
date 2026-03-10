# Setup Automation Design
**Date:** 2026-03-10
**Status:** Approved

## Problem

openclaw-deploy currently requires ~8 manual steps before a user can send their first message: SSH in, run `provision.sh`, edit `.env`, install OpenClaw locally, configure channels, rsync config to the VPS, fix a volume permission, fix a platform pin. This is too much surface area for someone discovering the project cold on GitHub.

**Target audience:** Broader AI/self-hosting community — people who find this on GitHub and want a hardened personal AI assistant on a VPS, with no prior OpenClaw experience.

**Primary pain point:** Initial setup complexity.

## Goals

1. Reduce first-message time from ~8 manual steps to 2 (`make deploy HOST=user@vps.com`, send a message)
2. Eliminate the local OpenClaw installation prerequisite entirely
3. Make WhatsApp pairing possible without a local OpenClaw install
4. Give users a `make doctor` command that tells them exactly what's wrong at any point

## Non-Goals

- Multi-tenant or multi-user support
- Web-based setup UI
- Kubernetes or cloud-managed deployment

---

## Component 1: `make deploy` — one-command remote setup

A `scripts/setup.sh` invoked via `make deploy HOST=user@x.x.x.x`. Runs entirely from the user's local machine over SSH. No agent or daemon required on the VPS beforehand.

### Flow

1. **SSH preflight** — verify key-based access works. Fail fast with a clear message if not.
2. **Provision** — SSH in, install Docker + git if missing, run `scripts/provision.sh` (idempotent).
3. **Clone or pull** — clone repo to `/home/$USER/openclaw-deploy`, or `git pull` if already present.
4. **Interactive `.env` wizard** — prompt for each required var with a one-line description. Auto-generate `REDIS_PASSWORD`. Skip optional sections unless the user opts in. Detect an existing `.env` and only ask for missing vars. Write `.env` on the VPS only (never on the local machine).
5. **Stack start** — `docker compose up -d` (or the appropriate profile based on what was configured).
6. **Health wait** — poll container health endpoints for up to 60 seconds, print live status.
7. **Summary** — print what's running, what's skipped, and what to do next (pair WhatsApp, configure backups).

### Prerequisites (user-facing)

- SSH key access to a fresh Ubuntu 24.04 VPS
- A domain pointing at the VPS IP
- A Telegram bot token from @BotFather
- An Anthropic API key

No local OpenClaw installation required.

### `.deploy` file

`make deploy` writes a `.deploy` file (gitignored) to the local repo root with `HOST=user@x.x.x.x`. Subsequent `make` targets (`make doctor`, `make pair-whatsapp`, `make logs`) read `HOST` from this file so the user doesn't have to pass it every time.

---

## Component 2: First-boot config bootstrap

Eliminates the local OpenClaw prerequisite by generating the minimum working config from `.env` on first boot.

### Mechanism

`entrypoint.sh` checks for `/home/node/.openclaw/openclaw.json` before starting the gateway. If missing, it runs a bootstrap sequence of `openclaw config set` commands:

```
TELEGRAM_TOKEN         → channels.telegram.botToken
DOMAIN                 → channels.telegram.webhookUrl (https://$DOMAIN/telegram-webhook)
                       → channels.telegram.webhookSecret (auto-generated, stored in .env)
                       → channels.telegram.webhookHost = 0.0.0.0
ANTHROPIC_API_KEY      → model provider config
REDIS_PASSWORD         → session store connection string
```

After bootstrap, the gateway starts normally. Telegram is fully operational.

### Device pairing unknown

OpenClaw generates device pairing credentials (`devices/paired.json`) on first run. It's unknown whether `openclaw gateway` can start cold without pre-existing device state. This must be validated during implementation — if cold start fails, the bootstrap may also need to pre-generate a device entry or pair the CLI device non-interactively.

### Idempotency

The bootstrap is skipped if `openclaw.json` already exists. Safe to restart the container without re-running bootstrap.

---

## Component 3: WhatsApp pairing via SSH

WhatsApp requires an interactive QR code scan — this cannot be automated. The flow is surfaced as a single make target after deploy.

### Flow

1. `make deploy` summary screen shows: `⚪ WhatsApp — not paired → run: make pair-whatsapp`
2. `make pair-whatsapp` SSHes into the VPS and runs:
   ```bash
   docker compose exec -it openclaw openclaw configure --section whatsapp
   ```
3. QR code renders in the user's terminal. User scans with their phone.
4. OpenClaw restarts the WhatsApp provider automatically on successful pairing.

Re-running `make pair-whatsapp` is safe — `openclaw configure` handles already-paired gracefully.

---

## Component 4: `make doctor`

A `scripts/doctor.sh` that runs on the VPS and prints a structured health report. Callable any time — during setup, after an upgrade, or when debugging.

### Checks

| Category | Check | Method |
|----------|-------|--------|
| `.env` | Required vars set (DOMAIN, TELEGRAM_TOKEN, ANTHROPIC_API_KEY, REDIS_PASSWORD) | `grep` |
| `.env` | Optional vars present (BACKUP_S3_*, OPENAI_API_KEY) | `grep` — warns if missing |
| Services | All enabled containers running and healthy | `docker compose ps` |
| Connectivity | Telegram webhook registered and error-free | `getWebhookInfo` API call |
| Connectivity | Redis reachable and authenticated | `redis-cli -a $PASS ping` |
| Connectivity | Guardrail process running | `pgrep` inside openclaw container |
| Channels | Telegram — last update timestamp | `getWebhookInfo` |
| Channels | WhatsApp — paired state | openclaw config read |
| Backups | S3 credentials set | `.env` check |
| Backups | Cron installed | `crontab -l` check |

### Output format

Human-readable with ✅ / ⚠️ / ❌ / ⚪ indicators. Warnings for optional missing config, errors for required failures. Actionable next-step hint when warnings/errors are found.

### Exit codes

- `0` — all required checks pass (warnings allowed)
- `1` — one or more required checks failed

Allows use in post-deploy scripts and CI.

---

## Summary

| Component | Effort | Impact |
|-----------|--------|--------|
| `make deploy` wizard | High | Eliminates all manual SSH steps |
| First-boot bootstrap | Medium | Eliminates local OpenClaw prerequisite |
| WhatsApp pairing | Low | Completes zero-local-install story |
| `make doctor` | Medium | Turns silent failures into actionable output |

The device pairing unknown in Component 2 is the highest implementation risk and should be validated first.
