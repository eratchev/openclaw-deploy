# Heartbeat and Cron Design

**Goal:** Make the OpenClaw agent proactive — ambient monitoring throughout the day via heartbeat, plus a guaranteed daily morning briefing via cron.

---

## Problem

The agent is currently fully reactive: it only acts when a message arrives. Two complementary mechanisms fix this:

1. **Heartbeat** — polls every 30 minutes during active hours, checks for urgent emails and upcoming events, sends a Telegram message only when something needs attention.
2. **Morning cron** — fires at 9 AM PT daily in an isolated session, always delivers a concise briefing: today's calendar + overnight important emails.

---

## Architecture

No new services. Two changes to existing files:

- **`workspace/HEARTBEAT.md`** — new file the agent reads during every heartbeat poll.
- **`entrypoint.sh`** — 8 lines added to the first-boot block: 6 for heartbeat config, 1 cron registration command.

State tracking uses an existing pattern: `memory/heartbeat-state.json` (agent-owned, persists in the `openclaw_data` volume).

---

## Component 1: `workspace/HEARTBEAT.md`

```markdown
# HEARTBEAT

Background ambient check. Keep it fast and quiet.

## Rules

- Only reach out if something genuinely needs attention.
- If it is past 22:00 or before 09:00 PT, reply `HEARTBEAT_OK` unless urgent.
- Check `memory/heartbeat-state.json` before notifying — do not repeat notifications.
- Update `memory/heartbeat-state.json` after every run.

## Checks (run in order)

### 1. Urgent email
- `gmail list --limit 5`
- Notify if a new important email arrived since `lastNotifiedEmailTime`.
- Important = from a real person, time-sensitive, or requires action. Skip newsletters and FYIs.
- If notifying: sender, subject, one-line summary.

### 2. Upcoming event (next 2 hours)
- Check calendar for events starting in the next 2 hours.
- Notify if there is an event not in `notifiedEventIds`.
- If notifying: title, start time, attendees if any.

### 3. Nothing to flag → reply `HEARTBEAT_OK`

## State file: `memory/heartbeat-state.json`
{
  "lastNotifiedEmailTime": <unix timestamp>,
  "notifiedEventIds": ["<event-id>", ...]
}
Update after each run. Keep `notifiedEventIds` to the last 20 entries.
```

### Design notes

- **State tracking prevents spam.** Without `lastNotifiedEmailTime` and `notifiedEventIds`, the same email or event would trigger a notification on every 30-minute cycle until dismissed.
- **Calendar-proxy 15-minute reminders remain.** The heartbeat's 2-hour calendar check is a preparation notice, not a replacement for the existing last-call reminder.
- **`notifiedEventIds` cap at 20.** The agent trims the list to the 20 most recent entries after each update, preventing unbounded growth.
- **Token footprint.** HEARTBEAT.md is intentionally small. The agent reads its full workspace context (AGENTS.md, MEMORY_GUIDE.md) so it has tool docs available.

---

## Component 2: `entrypoint.sh` additions

Both additions go inside the existing `if [ ! -f "$CONFIG_FILE" ]; then` first-boot block. They run once on first container startup; the config and cron job persist in the `openclaw_data` volume across restarts.

### Heartbeat configuration

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
| `activeHours.start` | `"09:00"` | No noise before 9 AM |
| `activeHours.end` | `"22:00"` | No noise after 10 PM |
| `activeHours.timezone` | `"America/Los_Angeles"` | Pacific Time with DST |

### Morning cron job

```sh
openclaw cron add \
    --name "Morning briefing" \
    --cron "0 9 * * * America/Los_Angeles" \
    --session isolated \
    --message "Morning briefing: check today's full calendar schedule (gcal list for today) and important unread emails from overnight (gmail list --limit 10). Compose a concise summary — events today with times, any email action items — and send it to Evgueni via Telegram."
```

| Option | Value | Reason |
|--------|-------|--------|
| `--cron` | `"0 9 * * * America/Los_Angeles"` | 9 AM PT daily; 6-field format handles DST |
| `--session isolated` | — | Fresh context; does not pollute main conversation history |
| `--message` | briefing prompt | Tells the agent what to do; it reads MEMORY_GUIDE.md for tool syntax |

The agent in the isolated session reads `AGENTS.md` and `MEMORY_GUIDE.md` at session start, so it has full access to `gcal` and `gmail` tool documentation without any additional configuration.

---

## Interaction with Existing Features

| Existing feature | Interaction |
|-----------------|-------------|
| Calendar-proxy 15-min reminders | Heartbeat adds a 2-hour heads-up; the two complement each other |
| Gmail poller importance scoring | Heartbeat does its own quick check; mail-proxy scoring is a separate signal |
| guardrail session limits | Heartbeat and cron sessions count toward their respective session limits independently |
| `memory/heartbeat-state.json` | Agent-owned; lives in `openclaw_data` volume; already documented in `AGENTS.md` |

---

## What Changes

| File | Change |
|------|--------|
| `workspace/HEARTBEAT.md` | **Create** — heartbeat checklist |
| `entrypoint.sh` | **Modify** — add 8 lines to first-boot block |

No new services. No new dependencies. No schema changes.

---

## Out of Scope

- End-of-day cron (not requested)
- Multiple heartbeat targets (single personal assistant, one channel is sufficient)
- Heartbeat model override (default model is appropriate; no need for a separate fast model)
- ClawHub skills or Composio integration (separate future decision)
