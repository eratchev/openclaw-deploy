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

293 tests passing as of 2026-03-25 (multi-account Gmail + GCal deployed).

## Cost-reduction changes (2026-04-16)

- **Heartbeat disabled** — was costing ~$4/month on Sonnet; duplicated by mail-proxy importance alerts + calendar-proxy reminders. Removed from entrypoint.sh, openclaw.json, Makefile.
- **Morning briefing switched to Haiku** — `anthropic/claude-haiku-4-5-20251001` instead of Sonnet.
- **Interactive chat fallback chain**: `openai/gpt-5.1-codex` → `anthropic/claude-haiku-4-5-20251001` → `anthropic/claude-sonnet-4-6` (Haiku added before Sonnet to avoid costly Sonnet fallback when Codex fails).
- **Gmail poll interval** set to 900s in `.env.example` (was 180s).
- **Anthropic API key rotated** (2026-04-16) after potential exposure in logs. New key stored in both `.env` on VPS and `auth-profiles.json` on data volume. See `reference_api_key_rotation.md`.
- Estimated monthly cost after changes: ~$9/month (down from ~$50/month).
