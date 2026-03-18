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
