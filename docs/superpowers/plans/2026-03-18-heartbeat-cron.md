# Heartbeat and Cron Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the OpenClaw agent proactive — heartbeat polls every 30 min (9 AM–10 PM PT) for urgent emails/events, plus a 9 AM daily cron briefing.

**Architecture:** Create `workspace/HEARTBEAT.md` with the ambient check checklist; update `workspace/AGENTS.md` to fix the schema docs and remove a contradictory edit-permission sentence; add 7 commands to `entrypoint.sh`'s first-boot block for new deployments; add a `setup-heartbeat` Makefile target to configure the existing deployment without a full redeploy.

**Tech Stack:** POSIX shell (`entrypoint.sh`), Markdown workspace files, OpenClaw CLI (`openclaw config set`, `openclaw cron add`), GNU Make.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `workspace/HEARTBEAT.md` | Create | Heartbeat checklist the agent reads every 30 min |
| `workspace/AGENTS.md` | Modify | Fix `heartbeat-state.json` schema; remove agent edit-permission for HEARTBEAT.md |
| `entrypoint.sh` | Modify | Configure heartbeat + register cron job on first boot (new deploys) |
| `Makefile` | Modify | Add `setup-heartbeat` target for existing deployment |

No new services. No new dependencies. No Python code — nothing to unit-test. Each task verifies via shell commands or file inspection.

---

## Chunk 1: Workspace Files and Entrypoint

### Task 1: Create `workspace/HEARTBEAT.md`

**Files:**
- Create: `workspace/HEARTBEAT.md`

- [ ] **Step 1: Create the file with exact content from spec**

```markdown
# HEARTBEAT

> Operator-owned. Do not modify — redeployed on make deploy.

Background ambient check. Keep it fast and quiet.

## Rules

- Only reach out if something genuinely needs attention.
- Check `memory/heartbeat-state.json` before notifying — do not repeat notifications.
- Update `memory/heartbeat-state.json` after every run.

## Checks (run in order)

### 1. Urgent email
- `gmail list --limit 5`
- Notify if any thread ID is not in `notifiedThreadIds` AND the email is important.
- Important = from a real person, time-sensitive, or requires action. Skip newsletters and FYIs.
- If notifying: sender, subject, one-line summary.
- Add notified thread IDs to `notifiedThreadIds`.

### 2. Upcoming event (next 2 hours)
- Check calendar for events starting in the next 2 hours.
- Notify if there is an event whose ID is not in `notifiedEventIds`.
- If notifying: title, start time, attendees if any.
- Add notified event IDs to `notifiedEventIds`.

### 3. Nothing to flag → reply `HEARTBEAT_OK`

## State file: `memory/heartbeat-state.json`
{
  "lastChecks": {
    "email": <unix timestamp>,
    "calendar": <unix timestamp>,
    "weather": null
  },
  "notifiedThreadIds": ["<thread-id>", ...],
  "notifiedEventIds": ["<event-id>", ...]
}
Keep `notifiedThreadIds` and `notifiedEventIds` to the 20 most recently added entries
(drop from the front of the array when the list exceeds 20 — insertion-order FIFO).
```

- [ ] **Step 2: Verify the file exists and looks correct**

```bash
cat workspace/HEARTBEAT.md
```

Expected: full content above, no truncation.

- [ ] **Step 3: Commit**

```bash
git add workspace/HEARTBEAT.md
git commit -m "feat(workspace): add HEARTBEAT.md checklist for ambient monitoring"
```

---

### Task 2: Update `workspace/AGENTS.md`

Two targeted edits. Read the file first to confirm exact line content before editing.

**Files:**
- Modify: `workspace/AGENTS.md` (lines ~137 and ~165–175)

- [ ] **Step 1: Remove the agent edit-permission sentence for HEARTBEAT.md**

Find and remove this exact block (currently around lines 136–137 — the blank line before the sentence plus the sentence itself):

```
\n
You are free to edit `HEARTBEAT.md` with a short checklist or reminders. Keep it small to limit token burn.
```

Delete both the blank line and the sentence. The surrounding content stays.

After edit, the section should read (exactly one blank line between the prompt and the heading):

```markdown
Default heartbeat prompt:
`Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK.`

### Heartbeat vs Cron: When to Use Each
```

- [ ] **Step 2: Update the `heartbeat-state.json` schema block**

Find the current schema block (around lines 166–175):

```json
{
  "lastChecks": {
    "email": 1703275200,
    "calendar": 1703260800,
    "weather": null
  }
}
```

Replace with the extended schema that adds the two deduplication arrays:

```json
{
  "lastChecks": {
    "email": 1703275200,
    "calendar": 1703260800,
    "weather": null
  },
  "notifiedThreadIds": ["<thread-id>", ...],
  "notifiedEventIds": ["<event-id>", ...]
}
```

Keep the `**Track your checks**` heading above it. Add the note as a plain paragraph between the closing ` ``` ` of the schema block and the `**When to reach out:**` heading that follows it:

```
`notifiedThreadIds` and `notifiedEventIds` prevent repeat notifications. Keep each list to the 20 most recently added entries (drop from front when over 20).
```

- [ ] **Step 3: Verify both edits are correct**

```bash
grep -n "free to edit" workspace/AGENTS.md
```

Expected: no output (the sentence is gone).

```bash
grep -n "notifiedThreadIds" workspace/AGENTS.md
```

Expected: one or two lines showing the schema and/or the note.

- [ ] **Step 4: Commit**

```bash
git add workspace/AGENTS.md
git commit -m "fix(workspace): remove HEARTBEAT.md edit permission, extend heartbeat-state schema"
```

---

### Task 3: Update `entrypoint.sh` — first-boot block

**Files:**
- Modify: `entrypoint.sh`

The first-boot block already ends with:

```sh
    echo "[entrypoint] Bootstrap complete. Starting gateway..."
fi
```

Add the 7 new commands inside the `if` block, just before the closing `echo "[entrypoint] Bootstrap complete..."` line.

- [ ] **Step 1: Add heartbeat config commands and cron job to entrypoint.sh**

The block to insert (before the closing `echo`):

```sh
    # ── Heartbeat ──────────────────────────────────────────────────────────────
    # Runs every 30 min during active hours; agent reads HEARTBEAT.md for checklist
    openclaw config set agents.defaults.heartbeat.every "30m"
    openclaw config set agents.defaults.heartbeat.target "last"
    openclaw config set agents.defaults.heartbeat.directPolicy "allow"
    openclaw config set agents.defaults.heartbeat.activeHours.start "09:00"
    openclaw config set agents.defaults.heartbeat.activeHours.end "22:00"
    openclaw config set agents.defaults.heartbeat.activeHours.timezone "America/Los_Angeles"

    # ── Morning cron ────────────────────────────────────────────────────────────
    # || true: job persists in volume across restarts; guard prevents set -e from
    # halting bootstrap if job already exists on a volume restored from backup
    openclaw cron add \
        --name "Morning briefing" \
        --cron "0 9 * * * America/Los_Angeles" \
        --session isolated \
        --message "Read MEMORY_GUIDE.md for tool documentation. Then run the morning briefing: check today's full calendar schedule (gcal list for today) and important unread emails from overnight (gmail list --limit 10). Compose a concise summary — events today with times, any email action items — and send it to Evgueni via Telegram." \
        || true
```

After the edit, the full first-boot block should look like:

```sh
if [ ! -f "$CONFIG_FILE" ]; then
    echo "[entrypoint] No config found — bootstrapping from .env..."

    for var in TELEGRAM_TOKEN DOMAIN; do
        eval "val=\$$var"
        if [ -z "$val" ]; then
            echo "[entrypoint] ERROR: $var is not set. Cannot bootstrap config."
            exit 1
        fi
    done

    WEBHOOK_SECRET=$(openssl rand -hex 32)

    openclaw config set channels.telegram.botToken  "${TELEGRAM_TOKEN}"
    openclaw config set channels.telegram.webhookSecret "${WEBHOOK_SECRET}"
    openclaw config set channels.telegram.webhookUrl "https://${DOMAIN}/telegram-webhook"
    openclaw config set channels.telegram.webhookHost "0.0.0.0"

    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        openclaw config set agents.main.provider anthropic || true
    fi

    # ── Heartbeat ──────────────────────────────────────────────────────────────
    openclaw config set agents.defaults.heartbeat.every "30m"
    openclaw config set agents.defaults.heartbeat.target "last"
    openclaw config set agents.defaults.heartbeat.directPolicy "allow"
    openclaw config set agents.defaults.heartbeat.activeHours.start "09:00"
    openclaw config set agents.defaults.heartbeat.activeHours.end "22:00"
    openclaw config set agents.defaults.heartbeat.activeHours.timezone "America/Los_Angeles"

    # ── Morning cron ────────────────────────────────────────────────────────────
    # || true: job persists in volume across restarts; guard prevents set -e from
    # halting bootstrap if job already exists on a volume restored from backup
    openclaw cron add \
        --name "Morning briefing" \
        --cron "0 9 * * * America/Los_Angeles" \
        --session isolated \
        --message "Read MEMORY_GUIDE.md for tool documentation. Then run the morning briefing: check today's full calendar schedule (gcal list for today) and important unread emails from overnight (gmail list --limit 10). Compose a concise summary — events today with times, any email action items — and send it to Evgueni via Telegram." \
        || true

    echo "[entrypoint] Bootstrap complete. Starting gateway..."
fi
```

- [ ] **Step 2: Verify shell syntax is valid**

```bash
sh -n entrypoint.sh
```

Expected: no output (no syntax errors).

- [ ] **Step 3: Commit**

```bash
git add entrypoint.sh
git commit -m "feat(entrypoint): configure heartbeat and register morning cron on first boot"
```

---

### Task 4: Add `setup-heartbeat` Makefile target

The first-boot block in `entrypoint.sh` only runs when the config file does not exist. The existing VPS already has a config, so the heartbeat and cron must be applied once manually. Add a `make setup-heartbeat` target consistent with the existing `setup-approvals` pattern.

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Add `setup-heartbeat` to the .PHONY line and add the target**

Add `setup-heartbeat` to the `.PHONY` list at the top of the Makefile (it already has `setup-approvals setup-egress setup-inbound setup-gcal setup-gmail`).

Then add the target after `setup-approvals` (around line 73). The full target:

```makefile
# Configure heartbeat and morning cron (run once on existing deployment, or after reset)
setup-heartbeat:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	ssh "$(HOST)" "cd ~/openclaw-deploy && \
	  sudo docker compose exec -T openclaw openclaw config set agents.defaults.heartbeat.every '30m' && \
	  sudo docker compose exec -T openclaw openclaw config set agents.defaults.heartbeat.target 'last' && \
	  sudo docker compose exec -T openclaw openclaw config set agents.defaults.heartbeat.directPolicy 'allow' && \
	  sudo docker compose exec -T openclaw openclaw config set agents.defaults.heartbeat.activeHours.start '09:00' && \
	  sudo docker compose exec -T openclaw openclaw config set agents.defaults.heartbeat.activeHours.end '22:00' && \
	  sudo docker compose exec -T openclaw openclaw config set agents.defaults.heartbeat.activeHours.timezone 'America/Los_Angeles' && \
	  sudo docker compose exec -T openclaw openclaw cron add \
	    --name 'Morning briefing' \
	    --cron '0 9 * * * America/Los_Angeles' \
	    --session isolated \
	    --message 'Read MEMORY_GUIDE.md for tool documentation. Then run the morning briefing: check today full calendar schedule (gcal list for today) and important unread emails from overnight (gmail list --limit 10). Compose a concise summary — events today with times, any email action items — and send it to Evgueni via Telegram.' || true && \
	  echo 'Heartbeat and cron configured.'"
```

Notes:
- `-T` on every `docker compose exec` is required for non-interactive SSH execution (no TTY available). Omitting it causes the command to hang or fail.
- The `--message` text is intentionally slightly different from the `entrypoint.sh` version: the apostrophe in "today's" is removed to avoid breaking the outer single-quoted SSH string. Both messages are semantically identical; the only difference is "today's" → "today".

- [ ] **Step 2: Verify Makefile syntax**

```bash
make --dry-run setup-heartbeat HOST=dummy 2>&1 | head -5
```

Expected: SSH command printed, no `make` syntax error.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "feat(make): add setup-heartbeat target for existing deployments"
```

---

## Chunk 2: Deploy and Verify

### Task 5: Deploy workspace files and configure OpenClaw

- [ ] **Step 1: Push workspace files to VPS**

```bash
make deploy-workspace HOST=<your-vps>
```

Expected output: `Deployed AGENTS.md`, `Deployed HEARTBEAT.md`, plus other `.md` files. Confirms `HEARTBEAT.md` now exists in the container and the updated `AGENTS.md` is live.

- [ ] **Step 2: Run setup-heartbeat**

```bash
make setup-heartbeat HOST=<your-vps>
```

Expected: 6 config set commands succeed, cron add succeeds, final line: `Heartbeat and cron configured.`

If `openclaw cron add` fails with "job already exists", it means this was run before — safe to ignore.

- [ ] **Step 3: Verify heartbeat config**

```bash
ssh <your-vps> "cd ~/openclaw-deploy && sudo docker compose exec -T openclaw openclaw config get agents.defaults.heartbeat"
```

Expected output contains:
```
every: 30m
target: last
directPolicy: allow
activeHours.start: 09:00
activeHours.end: 22:00
activeHours.timezone: America/Los_Angeles
```

- [ ] **Step 4: Verify morning cron job**

```bash
ssh <your-vps> "cd ~/openclaw-deploy && sudo docker compose exec -T openclaw openclaw cron list"
```

Expected: a row showing `Morning briefing` with schedule `0 9 * * * America/Los_Angeles` and a job ID (e.g. `abc123`). Example output:

```
ID       NAME               SCHEDULE                        NEXT RUN
abc123   Morning briefing   0 9 * * * America/Los_Angeles   2026-03-19 09:00:00
```

Note the `ID` column — you will need this value for the smoke test's manual trigger step.

- [ ] **Step 5: Verify HEARTBEAT.md in container**

```bash
ssh <your-vps> "cd ~/openclaw-deploy && sudo docker compose exec -T openclaw cat /home/node/.openclaw/workspace/HEARTBEAT.md"
```

Expected: full HEARTBEAT.md content including `> Operator-owned. Do not modify`.

- [ ] **Step 6: Commit verification notes (optional)**

If any issues were found and fixed during verification, commit those fixes. Otherwise no commit needed.

---

## Post-Deploy Smoke Test

Wait up to 30 minutes after `make setup-heartbeat`. The agent should send a `HEARTBEAT_OK` message (or a proactive alert if there's urgent email or an upcoming event). If nothing arrives within 35 minutes during active hours (9 AM–10 PM PT), check:

```bash
ssh <your-vps> "cd ~/openclaw-deploy && sudo docker compose logs openclaw --tail 100 | grep -i heartbeat"
```

For the morning cron: wait until the next 9 AM PT. The agent will send a Telegram briefing. To force an early test, use the job ID from Step 4 above:

```bash
ssh <your-vps> "cd ~/openclaw-deploy && sudo docker compose exec -T openclaw openclaw cron run <job-id>"
```
