---
name: Project status
description: Current feature completion status and deployment state
type: project
---

Features shipped to origin/main as of 2026-03-21:
- Gmail integration (mail-proxy) — complete
- Google Calendar proxy — complete
- Contacts lookup — complete
- Calendar reminders — complete
- Self-updating memory — complete
- Attendee management (gcal --attendee flag, invite via Google Calendar API) — complete
- Heartbeat + morning cron — complete
- Spotify skill (spogo) — complete; uses browser cookies (sp_dc + sp_t), PATH fix in docker-compose

**Why:** Tracking feature completion so future sessions know what's done vs. remaining.

**How to apply:** When user asks about project status or what features remain, check this file first.

261 tests passing as of attendee management completion (pre-heartbeat). Guardrail tests added for memory grace period (now 37 tests).

## Heartbeat / cron deployment state (as of 2026-03-19)

- Heartbeat running every 30 min, 9 AM–10 PM PT. Silent when nothing urgent (`ok-token`/`silent:true`). Sends Telegram alert only when urgent email or upcoming event found.
- `heartbeat.to` set to user's Telegram chat ID in VPS config volume — needed so delivery routes to Telegram DM rather than the main agent session. Find it via `cat /home/node/.openclaw/agents/main/sessions/sessions.json | python3 -c "import json,sys; [print(k) for k in json.load(sys.stdin) if 'telegram:direct' in k]"` on the VPS.
- Morning cron registered on VPS (job ID `d5154f27`), fires daily at 9 AM PT.
- HEARTBEAT.md deployed to container workspace.
- Guardrail has 120s memory grace period to avoid false kills on restart.
- `HEARTBEAT_TO=<telegram-chat-id>` should be added to `.env` on VPS for future fresh deploys.
- WhatsApp is logged out (401 loop) — harmless, not actively used.
