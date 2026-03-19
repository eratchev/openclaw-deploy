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

**Prerequisites:**
- A VPS running Ubuntu 24.04 (Hetzner CX22 ~$5/mo works well)
- A domain pointing at the VPS IP
- SSH key access: `ssh-copy-id user@<your-vps>`
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- An [Anthropic API key](https://console.anthropic.com)

**Deploy:**

```bash
git clone https://github.com/eratchev/openclaw-deploy.git
cd openclaw-deploy
make deploy HOST=user@<your-vps>
```

The wizard provisions the VPS, configures everything interactively, and starts the stack. When it finishes, send a message to your bot.

**Add WhatsApp (optional):**

```bash
make pair-whatsapp
```

Renders a QR code in your terminal. Scan with WhatsApp on your phone.

**Check health:**

```bash
make doctor
```

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
make setup-gcal CLIENT_SECRET=path/to/client_secret.json
```

This generates a Fernet encryption key, runs the Google OAuth browser flow, encrypts the token, copies it to the VPS, updates `.env`, and restarts the calendar-proxy. Requires `client_secret.json` from Google Cloud Console (see below).

**Additional `.env` vars (add via `make deploy` or manually):**

```bash
GCAL_USER_TIMEZONE=America/Los_Angeles  # your local timezone
GCAL_ALLOWED_CALENDARS=primary          # comma-separated calendar IDs
GCAL_WORK_CALENDAR_ID=                  # optional — requires confirmation for any write
```

Then `make up-calendar` to start the base stack **plus** the calendar-proxy container. (Plain `make up` skips `calendar-proxy` — it only starts when explicitly requested via the `calendar` profile.)

**One-time exec approvals setup** (run after first deploy):

```bash
make setup-approvals
```

This configures the `gcal` and `date` binaries on the exec allowlist so the agent can call them without interactive approval.

See [docs/calendar-proxy.md](docs/calendar-proxy.md) for tuning, health checks, and troubleshooting.

## Gmail Integration *(optional)*

OpenClaw can read, search, and reply to Gmail, and proactively notifies you via Telegram when important emails arrive (scored by Claude AI).

**One-time setup (local machine):**

```bash
make setup-gmail CLIENT_SECRET=path/to/client_secret.json
```

This generates a Fernet encryption key, runs the Google OAuth browser flow (requesting `gmail.readonly`, `gmail.send`, `gmail.modify`), encrypts the token, copies it to the VPS, updates `.env`, registers the `gmail` CLI on the exec approvals allowlist, and starts the service.

Requires `client_secret.json` from Google Cloud Console (same project as Calendar if using both — see below).

**Start:**

```bash
make up-mail
```

**Available agent commands:**

| Command | Description |
|---|---|
| `gmail list` | Show unread inbox (up to 10) |
| `gmail get --thread-id ID` | Fetch full thread |
| `gmail search --query "..."` | Gmail query syntax |
| `gmail reply --thread-id ID --message-id ID --body "..."` | Reply to thread |
| `gmail send --to EMAIL --subject "..." --body "..." --confirmed` | Send new email |
| `gmail mark-read --message-id ID` | Mark as read |

**Proactive notifications:**

When new emails arrive, the agent scores them for importance using Claude and sends a Telegram summary for anything scoring ≥ 7 (configurable via `GMAIL_IMPORTANCE_THRESHOLD`). Requires `ALERT_TELEGRAM_CHAT_ID` in `.env`.

**Re-auth (if token expires):**

```bash
make setup-gmail CLIENT_SECRET=path/to/client_secret.json
```

Safe to re-run — generates a fresh key and token.

See [docs/superpowers/specs/2026-03-13-gmail-integration-design.md](docs/superpowers/specs/2026-03-13-gmail-integration-design.md) for architecture details.

### Getting `client_secret.json`

Both Calendar and Gmail integrations use the same Google Cloud OAuth flow:

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a project (or reuse one).
2. Enable the API(s) you need: **APIs & Services → Library**
   - For Calendar: enable **Google Calendar API**
   - For Gmail: enable **Gmail API**
3. Create credentials: **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: anything (e.g. `openclaw`)
4. Download the JSON — that is your `client_secret.json`.
5. Add your Google account as a test user: **OAuth consent screen → Test users → Add**.

You can reuse the same project and the same `client_secret.json` for both Calendar and Gmail.

### Voice Transcription *(optional)*

Automatically transcribes Telegram voice notes via OpenAI Whisper so you can speak to OpenClaw hands-free.

**Setup:**

1. Add to `.env`:
   ```bash
   OPENAI_API_KEY=sk-...
   TELEGRAM_TOKEN=<your bot token>   # same token as channels.telegram.botToken in openclaw.json
   ```

2. Configure OpenClaw for webhook mode (required — voice-proxy intercepts incoming webhook POSTs; long-polling cannot be intercepted):
   ```bash
   # Set secret before URL or validation fails
   docker compose exec openclaw openclaw config set channels.telegram.webhookSecret <random-32-char-hex>
   docker compose exec openclaw openclaw config set channels.telegram.webhookUrl https://<your-domain>/telegram-webhook
   docker compose exec openclaw openclaw config set channels.telegram.webhookHost 0.0.0.0
   ```

3. `make up-voice`

4. Send a voice note to your bot — it should reply as if you typed the text.

**Cost:** ~$0.006/min (OpenAI Whisper). Negligible for personal use.
**Rate limit:** 10 voice messages/minute per chat (configurable via `VOICE_RATE_LIMIT_PER_MIN`).

**Troubleshooting:** Check `docker compose logs voice-proxy`. Each voice request logs `status=ok|error|no_api_key|rate_limited|size_exceeded`.

## Brave Search *(optional)*

The agent can search the web using the Brave Search API. Get a free API key at [brave.com/search/api](https://brave.com/search/api), then configure it in the running container:

```bash
docker compose exec openclaw openclaw config set tools.web.search.apiKey <YOUR_KEY>
docker compose exec openclaw openclaw config set tools.web.search.provider brave
docker compose exec openclaw openclaw config set tools.web.search.maxResults 5
docker compose restart openclaw
```

## OpenClaw Skills *(optional)*

OpenClaw ships with bundled skills that unlock additional capabilities. Some require external CLI binaries to be installed in the container. Install them in one step:

```bash
make setup-skills                                    # all supported skills
make setup-skills SKILLS="github session-logs"       # specific skills only
```

Supported skills and their binaries:

| Skill | Binary | Works out of the box? |
|---|---|---|
| `session-logs` | `jq`, `rg` | ✅ Yes |
| `github` | `gh` | Needs `gh auth login` |
| `spotify-player` | `spotify_player` | Needs Spotify app + OAuth |
| `summarize` | `summarize` | ❌ Not available on Linux |

---

### GitHub skill

After installing, authenticate `gh` inside the container:

```bash
make ssh   # or: ssh user@your-vps
sudo docker compose exec -it openclaw gh auth login
```

Follow the prompts. Once authenticated, ask the bot things like "do I have any open PRs?" or "what's the status of my latest CI run?"

---

### Spotify skill

Requires a Spotify Premium account and a Spotify developer app.

**Step 1: Create a Spotify app**

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and create an app
2. In app settings, add redirect URI: `http://127.0.0.1:8080` (use `127.0.0.1`, not `localhost` — Spotify rejects the latter)
3. Copy your **Client ID** and **Client Secret**

**Step 2: Write the config into the container**

The container filesystem is read-only — config must go inside the persistent volume at `/home/node/.openclaw/`:

```bash
ssh user@your-vps "sudo docker compose -f ~/openclaw-deploy/docker-compose.yml exec -T openclaw bash -c '
  mkdir -p /home/node/.openclaw/spotify-player
  cat > /home/node/.openclaw/spotify-player/app.toml << EOF
[app]
client_id = \"YOUR_CLIENT_ID\"
client_secret = \"YOUR_CLIENT_SECRET\"
EOF
'"
```

**Step 3: Authenticate via SSH port forwarding**

The container has no browser, so tunnel port 8080 through SSH so the OAuth redirect reaches your local machine:

```bash
ssh -L 8080:localhost:8080 user@your-vps \
  "sudo docker compose -f ~/openclaw-deploy/docker-compose.yml exec -it openclaw \
  /home/node/.openclaw/bin/spotify_player"
```

`spotify_player` will print an auth URL. Open it in your local browser, approve the permissions, and the redirect to `127.0.0.1:8080` travels back through the tunnel to complete auth. The token is saved to the persistent volume — you only need to do this once.

After that, ask the bot: "play some jazz", "skip this song", "what's playing?"

---

## Agent Workspace *(optional)*

The `workspace/` directory contains the agent's instruction files. These are copied into the container at runtime and control agent behaviour:

- `AGENTS.md` — injected into every system prompt (always active, all sessions)
- `SOUL.md` — agent identity and personality
- `POLICY.md` — safety rules, authority model, guardrails
- `OPERATIONS.md` — execution model and tool usage
- `USER.md` — who the agent is helping (preferences, context)
- `COMMANDS.md` — global commands available in all sessions including groups
- `MEMORY_GUIDE.md` — memory instructions and tool quick-references (operator-owned, redeployed on every `make deploy`)
- `MEMORY.md` — agent-owned long-term memory, loaded in direct/DM sessions only; never overwritten after first deploy

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

**Cause:** The `/data` volume is empty. On a fresh deploy this is handled automatically — the entrypoint bootstraps `openclaw.json` from `.env` on first start.

**Fix:** If bootstrap did not run (e.g. the container started before `.env` was written), ensure `.env` has `TELEGRAM_TOKEN` and `DOMAIN` set, then restart:
```bash
make doctor  # confirms .env vars
sudo docker compose restart openclaw
```

### Bootstrap fails with `config set` error on first start

**Symptom:** `[entrypoint] ERROR: TELEGRAM_TOKEN is not set`

**Cause:** `.env` is missing a required variable. The bootstrap runs before the gateway and fails fast.

**Fix:** Verify `.env` has `TELEGRAM_TOKEN`, `DOMAIN`, and `ANTHROPIC_API_KEY` set, then restart:
```bash
make doctor  # shows which vars are missing
sudo docker compose restart openclaw
```
