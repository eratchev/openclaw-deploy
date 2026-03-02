# Upgrade Path

## Standard Upgrade

Always back up before upgrading.

**Step 1 — Back up `/data`:**

```bash
make backup
```

This creates a timestamped archive of the `/data` volume before any changes. Keep the backup until you have verified the upgraded stack is working correctly.

**Step 2 — Pull and restart:**

```bash
make update
```

This pulls the latest OpenClaw image and restarts the stack. Equivalent to:

```bash
docker compose pull openclaw
docker compose up -d --no-deps openclaw
```

**Step 3 — Verify after upgrade:**

```bash
make logs    # watch for errors in the first 60 seconds after restart
make status  # confirm all containers are running
```

Check that the guardrail is running:

```bash
docker compose exec openclaw ps aux | grep guardrail
```

Send a test message to your bot and confirm it responds normally.

## Rollback

If the upgraded image is broken, roll back to a specific known-good tag by editing `docker-compose.yml` to pin the image tag, then run:

```bash
make up
```

To find available tags: browse to the OpenClaw packages page at `https://ghcr.io/openclaw/openclaw` and look for a stable or versioned tag that predates the broken release.

Pin the image tag in `docker-compose.yml` until the issue is resolved upstream:

```yaml
image: ghcr.io/openclaw/openclaw:<stable-tag>
```

## If You Suspect Compromise

If you suspect the container was compromised before or during the backup window, treat `/data` backups made after the suspected compromise point as potentially tainted.

Do not restore from a tainted backup without:

1. Rotating all secrets — Telegram bot token, WhatsApp credentials, LLM API keys, `REDIS_PASSWORD`. Assume everything in `.env` and everything the container could have read from `/data` is compromised.
2. Reviewing the contents of the backup archive before restoring — look for unexpected files, modified skill files, or injected configuration.
3. Starting fresh from the pre-compromise backup if one exists, or from an empty `/data` if none does.

A compromised `/data` volume can persist malicious skills or configuration that survive a container image upgrade. Rotating secrets and auditing `/data` contents is not optional after a suspected compromise.
