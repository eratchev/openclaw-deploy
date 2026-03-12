# MEMORY.md — Long-Term Memory

> ⚠️ Load in main/DM sessions only. Never in group chats — contains personal context.

Memory is long-term context. Use it to retain information that remains useful across sessions.

---

## What To Store

Persist:

- important user preferences
- recurring workflows
- system architecture
- stable project knowledge
- key decisions
- lessons learned

---

## What Not To Store

Do not persist:

- transient chat details
- raw transcripts
- speculative thoughts
- low-signal observations

---

## Compression

When memory grows, compress during heartbeats:

- preserve conclusions
- remove redundant information
- keep the minimal representation that retains full meaning

---

## Memory Updates

When new durable knowledge appears:

1. summarize it
2. store the minimal representation
3. link it to existing context if relevant

---

## Example Good Memory Entry

User preference:
Evgueni prefers concise responses with structured explanations and clear action steps.

---

## Example Bad Memory Entry

"Evgueni asked about dinner options at 7:32 PM."

---

## Memory

### Google Calendar

You have full Google Calendar access via the `gcal` CLI. **Always use it when asked to create, check, or delete calendar events.**

#### Workflow (mandatory: dry_run first)
1. `gcal create --title "..." --start "ISO" --end "ISO" --mode dry_run` → check impact
2. If `needs_confirmation`: show the impact, ask for confirmation
3. If confirmed: re-run with `--mode execute --confirmed`
4. If `safe_to_execute` on dry_run: run with `--mode execute --confirmed` directly

#### Quick reference
```
gcal create --title "Dinner" --start "2026-03-04T20:00:00-08:00" --end "2026-03-04T21:00:00-08:00" --mode dry_run
gcal create --title "Dinner" --start "2026-03-04T20:00:00-08:00" --end "2026-03-04T21:00:00-08:00" --mode execute --confirmed
gcal list --from "2026-03-04T00:00:00Z" --to "2026-03-04T23:59:59Z"
gcal delete --event-id EVENT_ID --mode dry_run
gcal delete --event-id EVENT_ID --mode execute --confirmed
gcal avail --from "2026-03-04T09:00:00-08:00" --to "2026-03-04T18:00:00-08:00" --minutes 60
```

**Timezone:** Pacific Time (America/Los_Angeles). Use ISO 8601 offsets in all gcal commands.

**CRITICAL: Never use bash or shell commands.** Bash is disabled — calling it will fail.
- For gcal: use exec with `{"command": "gcal ...", "workdir": "/home/node/.openclaw/workspace"}`
- For dates/times: compute from your own knowledge — do NOT run `date` or any shell command
- For files: use the read/write/edit tools
