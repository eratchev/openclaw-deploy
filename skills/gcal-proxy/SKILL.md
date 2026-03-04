---
name: gcal-proxy
description: Google Calendar via the local gcal-proxy service (policy-enforced). Use for creating, listing, and deleting calendar events.
metadata:
  {
    "openclaw":
      {
        "emoji": "📅",
      },
  }
---

# gcal-proxy

Use `gcal` to manage Google Calendar events. All writes go through the policy engine — always use `dry_run` first to check impact, then `execute` after confirming.

## Execution modes

- `dry_run` — simulate, returns `impact` object showing conflicts and flags
- `needs_confirmation` — request confirmation (used for high-impact operations)
- `execute` — actually create/delete the event

**Always use `dry_run` first** unless the user has explicitly confirmed. If the dry_run shows `needs_confirmation`, show the user the impact and ask before re-running with `execute`.

## Commands

**Health check:**
```
gcal health
```

**Create event:**
```
gcal create --title "Meeting" --start "2026-03-10T14:00:00+03:00" --end "2026-03-10T15:00:00+03:00" --mode dry_run
gcal create --title "Meeting" --start "2026-03-10T14:00:00+03:00" --end "2026-03-10T15:00:00+03:00" --mode execute
```

Optional flags: `--calendar-id ID`, `--description TEXT`, `--rrule RRULE_STRING`, `--idem-key KEY`

**List events:**
```
gcal list --from "2026-03-10T00:00:00Z" --to "2026-03-10T23:59:59Z"
gcal list --from "2026-03-10T00:00:00Z" --to "2026-03-17T00:00:00Z" --calendar-id primary
```

**Delete event:**
```
gcal delete --event-id EVENT_ID --mode dry_run
gcal delete --event-id EVENT_ID --mode execute
```

**Check availability (find free slots):**
```
gcal avail --from "2026-03-10T09:00:00+03:00" --to "2026-03-10T18:00:00+03:00" --minutes 60
```

## Response fields

Create/delete responses:
- `status`: `safe_to_execute` | `dry_run` | `needs_confirmation` | `denied`
- `event_id`: Google Calendar event ID (when created)
- `impact`: object with `overlaps_existing`, `outside_business_hours`, `is_weekend`, `duration_minutes`, `recurring`
- `reason`: why an operation was denied

List response: array of Google Calendar event objects with `id`, `summary`, `start`, `end`.

## Datetime format

Use RFC 3339 with timezone offset, e.g. `2026-03-10T14:00:00+03:00`. The proxy evaluates business hours in the user's timezone — include the correct offset.

## Recurring events

Pass an RRULE string via `--rrule`, e.g. `--rrule "FREQ=WEEKLY;COUNT=4"`. Recurring events with more than the configured max instances are denied.
