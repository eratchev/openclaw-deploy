---
name: Post-change workflow requirements
description: After every code change: update docs, run tests, deploy and verify on VPS
type: feedback
---

After every code change, always do all three steps before considering work done:

1. **Update docs** — check `docs/runbook.md` and any other relevant docs for stale references (make targets, defaults, process names, RSS numbers, etc.) and update them.

2. **Run tests** — run `make test` locally to confirm nothing is broken.

3. **Deploy and verify on VPS** — push to git, pull on VPS, restart affected services, and confirm the change works end-to-end (e.g. run the CLI command, check `make doctor`, check `docker stats`).

**Why:** User was repeatedly having to ask for these steps. They should be automatic, not prompted.

**How to apply:** At the end of every task — even small fixes — do all three before saying "done".
