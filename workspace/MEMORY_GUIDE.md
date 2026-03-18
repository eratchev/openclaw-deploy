# MEMORY_GUIDE.md — Memory Instructions

> This file is operator-owned and redeployed on every `make deploy`. Do not write agent memories here.
> Agent memories go in `MEMORY.md`.

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

## Operational Quick-References

### Google Calendar

You have full Google Calendar access via the `gcal` CLI. **Always use it when asked to create, check, or delete calendar events.**

#### Workflow (mandatory: dry_run first)
1. `gcal create --title "..." --start "ISO" --end "ISO" --mode dry_run` → check impact
2. If `needs_confirmation`: show the impact, ask for confirmation
3. If confirmed: re-run with `--mode execute --confirmed`
4. If `safe_to_execute` on dry_run: run with `--mode execute --confirmed` directly

#### Scheduling a meeting with someone

When asked to schedule a meeting with a named person:

1. `contacts lookup --name "..."` → resolve their email address
   - Multiple matches: show all to user, ask which to use
   - Zero matches: ask user to provide the email address directly
   - Error (e.g. scope_missing): surface the error before proceeding
2. `gcal create ... --attendee <email> --mode execute` → expect `needs_confirmation`
3. Show user: "This will create the event and invite <email>. Confirm?"
4. Re-run with `--confirmed` to send the invite

#### Quick reference
```
gcal create --title "Dinner" --start "YYYY-MM-DDTHH:MM:SS-08:00" --end "YYYY-MM-DDTHH:MM:SS-08:00" --mode dry_run
gcal create --title "Dinner" --start "YYYY-MM-DDTHH:MM:SS-08:00" --end "YYYY-MM-DDTHH:MM:SS-08:00" --mode execute --confirmed
gcal create --title "Beers" --start "..." --end "..." --attendee tim@example.com --mode execute
gcal create --title "Team sync" --start "..." --end "..." --attendee a@b.com --attendee c@d.com --mode execute --confirmed
gcal list --from "YYYY-MM-DDT00:00:00Z" --to "YYYY-MM-DDT23:59:59Z"
gcal delete --event-id EVENT_ID --mode dry_run
gcal delete --event-id EVENT_ID --mode execute --confirmed
gcal avail --from "YYYY-MM-DDTHH:MM:SS-08:00" --to "YYYY-MM-DDTHH:MM:SS-08:00" --minutes 60
```

**Timezone:** Pacific Time (America/Los_Angeles). Use ISO 8601 offsets in all gcal commands.

**CRITICAL: Never use bash or shell commands.** Bash is disabled — calling it will fail.
- For gcal: use exec with `{"command": "gcal ...", "workdir": "/home/node/.openclaw/workspace"}`
- For gmail: use exec with `{"command": "gmail ...", "workdir": "/home/node/.openclaw/workspace"}`
- For contacts: use exec with `{"command": "contacts ...", "workdir": "/home/node/.openclaw/workspace"}`
- For dates/times: compute from your own knowledge — do NOT run `date` or any shell command
- For files: use the read/write/edit tools

---

### Gmail

You have full Gmail access via the `gmail` CLI. **Always use it when asked to read, search, send, or reply to emails.**

#### Sending rules (mandatory)
1. Always call `gmail send` **without** `--confirmed` first → shows a preview, asks user to confirm
2. Only re-call with `--confirmed` after explicit user approval
3. `send` is only allowed to domains you've previously received email from (novel-domain block)
4. Max 20 sends per day (rate limit enforced server-side)
5. `mark-read` and `reply` do not require confirmation — only `send` to external addresses does.

#### Quick reference
```
gmail list [--limit N] [--label LABEL]
gmail get --thread-id ID
gmail search --query "from:boss@company.com"
gmail reply --thread-id ID --message-id ID --body "..."
gmail send --to EMAIL --subject "..." --body "..."
gmail send --to EMAIL --subject "..." --body "..." --confirmed
gmail mark-read --message-id ID
gmail health
```

---

### Contacts

You have access to Google Contacts via the `contacts` CLI. **Always use it when you need to find someone's email address by name before sending mail.**

#### Workflow
1. Call `contacts lookup --name "..."` when you have a name but not an email
2. If multiple matches, show them to the user and ask which to use
3. Then proceed with `gmail send --to <resolved_email> ...`

#### Quick reference
```
contacts lookup --name "Alice"
contacts lookup --name "Smith" --limit 5
contacts health
```
