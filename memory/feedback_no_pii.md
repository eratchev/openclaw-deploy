---
name: No PII or sensitive info in commits
description: Never commit PII or sensitive values to git
type: feedback
---

Never commit PII or sensitive information to git. This includes:

- IP addresses (VPS IPs, server addresses)
- Telegram/WhatsApp chat IDs or user IDs
- Usernames combined with hostnames (e.g. `user@1.2.3.4`)
- Bot usernames if they identify the user
- Any other personally identifiable or targeting information

**Why:** The repo is public. Sensitive values committed to history require destructive history rewrites (force push) to clean up.

**How to apply:** Before every commit, scan for IPs, numeric IDs, and user-specific identifiers. Use placeholders (`YOUR_VPS_IP`, `YOUR_TELEGRAM_CHAT_ID`) in docs and examples. Keep real values in `.env` (gitignored) or VPS config volumes only.
