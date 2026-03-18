# Heartbeat and Cron Design

**Goal:** Make the OpenClaw agent proactive — ambient monitoring throughout the day via heartbeat, plus a guaranteed daily morning briefing via cron.

---

## Problem

The agent is currently fully reactive: it only acts when a message arrives. Two complementary mechanisms fix this:

1. **Heartbeat** — polls every 30 minutes during active hours (9 AM–10 PM PT), checks for urgent emails and upcoming events, sends a Telegram message only when something needs attention.
2. **Morning cron** — fires at 9 AM PT daily in an isolated session, always delivers a concise briefing: today's calendar + overnight important emails.

---

## Architecture

No new services. Two changes to existing files:

- **`workspace/HEARTBEAT.md`** — new file the agent reads during every heartbeat poll.
- **`entrypoint.sh`** — 7 commands added to the first-boot block: 6 for heartbeat config, 1 cron registration.

State tracking uses an existing pattern: `memory/heartbeat-state.json` (agent-owned, persists in the `openclaw_data` volume). The schema extends the one already documented in `AGENTS.md`.

---

## Component 1: `workspace/HEARTBEAT.md`

`HEARTBEAT.md` is **operator-owned** and redeployed fresh on every `make deploy`, exactly like `MEMORY_GUIDE.md`. The agent must not modify it.

The platform's `activeHours` config (`09:00–22:00 PT`) suppresses all heartbeat polls outside those hours at the infrastructure level. No quiet-hours logic is needed inside `HEARTBEAT.md` — the platform handles it.

Content:

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

### Design notes

- **Schema extends `AGENTS.md`.** `lastChecks.email/calendar/weather` are already defined in `AGENTS.md` (lines 166–175). `notifiedThreadIds` and `notifiedEventIds` are additive new fields. The implementation plan must update `AGENTS.md` to document these new fields.
- **ID-based deduplication, not timestamp comparison.** Using thread/event IDs rather than arrival timestamps avoids the need for a `--since` flag or timestamp parsing. `gmail list --limit 5` returns the 5 most recent threads; any thread ID not yet in `notifiedThreadIds` is treated as new.
- **FIFO trim at 20.** "Most recently added" means insertion order: when the list exceeds 20 entries, drop from index 0. This is deterministic and requires no sorting. Known limitation: an unread thread that persists in `gmail list` results across more heartbeat cycles than the buffer covers will eventually re-trigger a notification once its ID is trimmed out. In a typical low-volume inbox (≤1–2 important emails per heartbeat cycle) the 20-entry buffer covers many days; this is acceptable for a personal-assistant use case.
- **Calendar-proxy 15-minute reminders remain.** The heartbeat's 2-hour calendar check is a preparation notice, not a replacement for the existing last-call reminder.
- **Token footprint.** `HEARTBEAT.md` is intentionally small. The agent reads its full workspace context (AGENTS.md, MEMORY_GUIDE.md) so it has tool docs available; `lightContext` is left at its default (`false`).

---

## Component 2: `entrypoint.sh` additions

Both additions go inside the existing `if [ ! -f "$CONFIG_FILE" ]; then` first-boot block. They run once on first container startup; the config and cron job persist in the `openclaw_data` volume across restarts.

### Heartbeat configuration (6 commands)

```sh
openclaw config set agents.defaults.heartbeat.every "30m"
openclaw config set agents.defaults.heartbeat.target "last"
openclaw config set agents.defaults.heartbeat.directPolicy "allow"
openclaw config set agents.defaults.heartbeat.activeHours.start "09:00"
openclaw config set agents.defaults.heartbeat.activeHours.end "22:00"
openclaw config set agents.defaults.heartbeat.activeHours.timezone "America/Los_Angeles"
```

| Key | Value | Reason |
|-----|-------|--------|
| `every` | `"30m"` | Balances responsiveness with token cost |
| `target` | `"last"` | Delivers to the most recently active chat |
| `directPolicy` | `"allow"` | Allows delivery to DMs (personal assistant use case) |
| `activeHours.start` | `"09:00"` | No polls before 9 AM |
| `activeHours.end` | `"22:00"` | No polls after 10 PM |
| `activeHours.timezone` | `"America/Los_Angeles"` | Pacific Time with DST |

`activeHours` is a hard suppression: no heartbeat polls fire outside these hours. The agent never runs outside 9 AM–10 PM PT.

### Morning cron job (1 command)

```sh
openclaw cron add \
    --name "Morning briefing" \
    --cron "0 9 * * * America/Los_Angeles" \
    --session isolated \
    --message "Read MEMORY_GUIDE.md for tool documentation. Then run the morning briefing: check today's full calendar schedule (gcal list for today) and important unread emails from overnight (gmail list --limit 10). Compose a concise summary — events today with times, any email action items — and send it to Evgueni via Telegram."
```

| Option | Value | Reason |
|--------|-------|--------|
| `--cron` | `"0 9 * * * America/Los_Angeles"` | 9 AM PT daily; 6-field format handles DST automatically |
| `--session isolated` | — | Fresh context; does not pollute main conversation history |
| `--message` | briefing prompt | Instructs the agent to read MEMORY_GUIDE.md first for `gcal`/`gmail` tool syntax |

The `--message` prompt explicitly instructs `Read MEMORY_GUIDE.md` first. This ensures tool documentation is available regardless of whether the isolated session auto-injects workspace context.

---

## What Changes

| File | Change |
|------|--------|
| `workspace/HEARTBEAT.md` | **Create** — heartbeat checklist (operator-owned) |
| `entrypoint.sh` | **Modify** — add 7 commands to first-boot block |
| `workspace/AGENTS.md` | **Modify** — (1) update `memory/heartbeat-state.json` schema docs to include `notifiedThreadIds` and `notifiedEventIds`; (2) remove the sentence granting the agent permission to edit `HEARTBEAT.md` (currently: "You are free to edit `HEARTBEAT.md` with a short checklist or reminders") — this contradicts the operator-owned constraint |
| `workspace/MEMORY_GUIDE.md` | No change |

---

## Interaction with Existing Features

| Existing feature | Interaction |
|-----------------|-------------|
| Calendar-proxy 15-min reminders | Heartbeat adds a 2-hour heads-up; the two complement each other |
| Gmail poller importance scoring | Heartbeat does its own quick check; mail-proxy scoring is a separate signal |
| guardrail session limits | Heartbeat and cron sessions count toward their respective session limits independently |
| `memory/heartbeat-state.json` | Agent-owned; lives in `openclaw_data` volume; schema extended from AGENTS.md definition |

---

## Out of Scope

- End-of-day cron (not requested)
- Multiple heartbeat targets (single personal assistant, one channel is sufficient)
- Heartbeat model override (default model is appropriate)
- `lightContext` for cron (explicit `Read MEMORY_GUIDE.md` instruction is simpler and more reliable)
- ClawHub skills or Composio integration (separate future decision)
