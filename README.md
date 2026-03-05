# openclaw-deploy

> Hardened single-VPS deployment of [OpenClaw](https://github.com/openclaw/openclaw) with execution guardrails. Personal assistant + publishable open-source template.
>
> **Repo:** https://github.com/eratchev/openclaw-deploy

## What This Is

One VPS. One Docker Compose. Hardened container, log-driven execution guardrail, Redis session store, TLS via Caddy. Telegram, WhatsApp, Google Calendar, and Brave Search are all optional integrations — the base stack runs without any of them.

Out of the box you get:

- TLS termination via Caddy with automatic Let's Encrypt certificates
- OpenClaw Gateway running as a non-root user with all Linux capabilities dropped, read-only filesystem, and resource limits enforced
- Redis session store isolated to an internal Docker network — unreachable from the internet
- A Python execution guardrail that kills runaway LLM sessions before they burn tokens or abuse tools
- VPS hardening via `scripts/provision.sh` (UFW, SSH key-only auth, Fail2ban, unattended security upgrades)
- Automated daily backups of the `/data` volume to Hetzner Object Storage with configurable retention
- A `Makefile` with commands for bring-up, teardown, logs, backup, and upgrade

## What This Is NOT

- Not multi-tenant
- Not Kubernetes
- Not a managed SaaS
- Not hardened for enterprise (see threat model)

## Prerequisites

- A VPS (Hetzner CX22 or equivalent, ~$5-7/month)
- Ubuntu 24.04 LTS
- A domain name pointing to the VPS
- OpenClaw already set up locally (you need to onboard channels before deploying)
- Docker + Docker Compose (installed by provision.sh)
- **SSH public key loaded on the VPS** — `scripts/provision.sh` disables password authentication. Run `ssh-copy-id user@<your-vps>` before provisioning or you will be locked out.

## Quickstart

1. Clone this repo on your VPS
2. Run `sudo bash scripts/provision.sh`
3. Copy `.env.example` to `.env` and fill in your values
4. Copy your local OpenClaw config to the VPS data volume:
   ```bash
   # On your local machine
   rsync -av ~/.openclaw/ user@<your-vps>:/tmp/openclaw-config/

   # Then on the VPS (from inside the repo directory)
   docker run --rm \
     -v /tmp/openclaw-config:/src \
     -v "$(basename $(pwd))_openclaw_data":/dest \
     busybox sh -c 'cp -r /src/. /dest/ && chown -R 1000:1000 /dest'
   ```
5. `make up`
6. Fix `/data` permissions (the OpenClaw container runs as UID 1000):
   ```bash
   docker run --rm -v "$(basename $(pwd))_openclaw_data":/data busybox chown -R 1000:1000 /data
   ```
   Run this from inside the repo directory. The volume name is `<repo-dir-name>_openclaw_data`.
7. Fix the device platform pin — OpenClaw config created on macOS has a `darwin` platform binding that the Linux container rejects. Run this once after first deploy:
   ```bash
   docker compose exec openclaw node -e "
   const fs = require('fs');
   const p = '/home/node/.openclaw/devices/paired.json';
   const d = JSON.parse(fs.readFileSync(p, 'utf8'));
   for (const id of Object.keys(d)) {
     if (d[id].clientId === 'cli') d[id].platform = 'linux';
   }
   fs.writeFileSync(p, JSON.stringify(d, null, 2));
   fs.writeFileSync('/home/node/.openclaw/devices/pending.json', '{}');
   console.log('done');
   "
   docker compose restart openclaw
   ```
   Without this fix the guardrail exits immediately and `openclaw logs` returns `pairing required`.
8. Run through `docs/security-checklist.md`

## Security Model

This deployment shifts OpenClaw's execution risk to containment. OpenClaw can execute arbitrary code via skills and tools — the hardening around it prevents that from compromising the host.

See [docs/threat-model.md](docs/threat-model.md) for the full threat model including known gaps. Phase 1 ships with outbound egress unrestricted — read it before deploying.

## Execution Guardrails

A Python watchdog runs inside the container and kills OpenClaw if sessions exceed configurable limits (tool calls, LLM calls, session time, idle timeout). Because OpenClaw has no per-session abort API, a violation kills all sessions — the container restarts automatically.

See [docs/execution-guardrails.md](docs/execution-guardrails.md) for limits and tuning.

## Backups

The `/data` volume (OpenClaw config, credentials, session history) is backed up daily to Hetzner Object Storage. Backups older than `BACKUP_RETAIN_DAYS` (default: 7) are pruned automatically.

**Setup (one-time):**

1. Create a bucket in [Hetzner Object Storage](https://console.hetzner.com) and generate S3 credentials
2. Add the `BACKUP_S3_*` vars to `.env` (see `.env.example`)
3. Install the cron job: `sudo bash scripts/install-backup-cron.sh`

**Manual backup:** `make backup-remote`

Backups run daily at 03:00 UTC. Logs go to `/var/log/openclaw-backup.log`.

## Google Calendar Integration *(optional)*

OpenClaw can read and write your Google Calendar via an MCP proxy that runs on the internal Docker network. All writes go through a policy engine (conflict detection, business hours, rate limits) before touching the Google API.

**One-time setup (local machine):**

```bash
# 1. Generate an encryption key and save it
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# → Add GCAL_TOKEN_ENCRYPTION_KEY=<key> to .env (local and VPS)

# 2. Authenticate with Google (requires client_secret.json from Google Cloud Console)
python3 services/calendar-proxy/scripts/auth_setup.py \
  --client-secret client_secret.json --out token.json

# 3. Encrypt the token and copy it to the VPS
python3 services/calendar-proxy/scripts/encrypt_token.py \
  --token token.json --key <KEY> --out token.enc
scp token.enc user@<your-vps>:/tmp/

ssh user@<your-vps> "
  docker run --rm \
    -v openclaw-deploy_openclaw_data:/data \
    -v /tmp:/src \
    busybox sh -c 'cp /src/token.enc /data/gcal_token.enc && chmod 600 /data/gcal_token.enc'
"

# 4. Clean up plaintext files
rm client_secret.json token.json token.enc
```

**VPS `.env` additions required:**

```bash
GCAL_TOKEN_ENCRYPTION_KEY=<key>
GCAL_USER_TIMEZONE=Europe/Helsinki   # your local timezone
GCAL_ALLOWED_CALENDARS=primary       # comma-separated calendar IDs
GCAL_WORK_CALENDAR_ID=               # optional — requires confirmation for any write
```

Then `make up-calendar` to start the base stack **plus** the calendar-proxy container. (Plain `make up` skips `calendar-proxy` — it only starts when explicitly requested via the `calendar` profile.)

**One-time exec approvals setup** (run after first deploy):

```bash
make setup-approvals
```

This configures the `gcal` and `date` binaries on the exec allowlist so the agent can call them without interactive approval.

See [docs/calendar-proxy.md](docs/calendar-proxy.md) for tuning, health checks, and troubleshooting.

### Voice Transcription *(optional)*

Automatically transcribes Telegram voice notes via OpenAI Whisper so you can speak to OpenClaw hands-free.

**Setup:**
1. Add `OPENAI_API_KEY=sk-...` to `.env`
2. `make up-voice`
3. Send a voice note to your bot — it should reply as if you typed the text

**Cost:** ~$0.006/min (OpenAI Whisper). Negligible for personal use.
**Rate limit:** 10 voice messages/minute per chat (configurable via `VOICE_RATE_LIMIT_PER_MIN`).

## Brave Search *(optional)*

The agent can search the web using the Brave Search API. Get a free API key at [brave.com/search/api](https://brave.com/search/api), then configure it in the running container:

```bash
docker compose exec openclaw openclaw config set tools.web.search.apiKey <YOUR_KEY>
docker compose exec openclaw openclaw config set tools.web.search.provider brave
docker compose exec openclaw openclaw config set tools.web.search.maxResults 5
docker compose restart openclaw
```

## Agent Workspace *(optional)*

The `workspace/` directory contains the agent's instruction files. These are copied into the container at runtime and control agent behaviour:

- `AGENTS.md` — injected into every system prompt (always active, all sessions)
- `MEMORY.md` — loaded in direct/DM sessions only (personal context, not shared in groups)
- `COMMANDS.md` — global commands available in all sessions including groups

Edit the files locally, then deploy:

```bash
make deploy-workspace
```

> **Telegram groups:** The bot only responds when @mentioned (e.g. `@YourBotName ai update`). This is controlled by `channels.telegram.groupPolicy: open` — change it to `disabled` to block group messages entirely, or configure per-group `requireMention: false` to allow unprefixed commands.

## Upgrading

`make backup-remote && make update`

See [docs/upgrade-path.md](docs/upgrade-path.md).

## Pre-launch Checklist

See [docs/security-checklist.md](docs/security-checklist.md). Run through it before going live.

## Troubleshooting

### Guardrail exits immediately / `pairing required`

**Symptom:** `[entrypoint] guardrail exited (code 0), restarting in 5s...` loops indefinitely. Running `docker compose exec openclaw openclaw logs --json` returns `gateway closed (1008): pairing required`.

**Cause:** OpenClaw config was created on macOS. The gateway pins the CLI device to `darwin` and rejects connections from the Linux container.

**Fix:**
```bash
docker compose exec openclaw node -e "
const fs = require('fs');
const p = '/home/node/.openclaw/devices/paired.json';
const d = JSON.parse(fs.readFileSync(p, 'utf8'));
for (const id of Object.keys(d)) {
  if (d[id].clientId === 'cli') d[id].platform = 'linux';
}
fs.writeFileSync(p, JSON.stringify(d, null, 2));
fs.writeFileSync('/home/node/.openclaw/devices/pending.json', '{}');
console.log('done');
"
docker compose restart openclaw
```

### Caddy fails with `unrecognized global option: reverse_proxy`

**Cause:** The `DOMAIN` variable is not visible to Caddy, so `{$DOMAIN}` expands to an empty string and Caddy interprets the site block as the global options block.

**Fix:** Ensure `env_file: - .env` is present under the `caddy` service in `docker-compose.yml` (already included in this repo).

### OpenClaw reports `Missing config`

**Cause:** The `/data` volume is empty — OpenClaw config was not copied from your local machine before starting the stack.

**Fix:** Stop the stack, copy your local `~/.openclaw` into the volume (see Quickstart step 4), then bring the stack back up.
