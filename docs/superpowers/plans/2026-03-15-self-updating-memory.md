# Self-Updating Agent Memory Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split workspace MEMORY.md into an operator-owned guide file and an agent-owned memory file, and guard the deploy script so the agent's accumulated memories survive every `make deploy`.

**Architecture:** Create `workspace/MEMORY_GUIDE.md` with all current MEMORY.md instructional content. Strip `workspace/MEMORY.md` to a minimal seed. Update AGENTS.md to read both files. Update `scripts/setup.sh` to skip copying `MEMORY.md` into the container if it already exists.

**Tech Stack:** Bash (setup.sh), Markdown (workspace files). No new services or dependencies.

---

## Chunk 1: Workspace files

### Task 1: Create MEMORY_GUIDE.md and strip MEMORY.md to seed

No unit tests apply — these are markdown files read by the agent at runtime. Verification is visual inspection of file contents.

**Files:**
- Create: `workspace/MEMORY_GUIDE.md`
- Modify: `workspace/MEMORY.md`

**Background:** `workspace/MEMORY.md` currently has two roles: instructions (What To Store, Compression, examples) and operational quick-references (gcal/gmail) under `## Memory`. All of this moves to `MEMORY_GUIDE.md`. `MEMORY.md` becomes a minimal seed the agent owns.

- [ ] **Step 1: Create `workspace/MEMORY_GUIDE.md`**

Create the file with the full content of the current `workspace/MEMORY.md`, with one addition: a header note explaining this file is operator-owned.

```markdown
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
- For gmail: use exec with `{"command": "gmail ...", "workdir": "/home/node/.openclaw/workspace"}`
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

- For gmail: use exec with `{"command": "gmail ...", "workdir": "/home/node/.openclaw/workspace"}`
```

- [ ] **Step 2: Verify `workspace/MEMORY_GUIDE.md` looks correct**

```bash
cat workspace/MEMORY_GUIDE.md
```

Expected: full instructional content + gcal/gmail quick-refs. No agent memory entries.

- [ ] **Step 3: Overwrite `workspace/MEMORY.md` with the seed**

Replace the entire file with the content below. Note: the warning block intentionally has **two** `>` lines — the second line (`See MEMORY_GUIDE.md...`) is new and does not exist in the current file.

```markdown
# MEMORY.md — Long-Term Memory

> ⚠️ Load in main/DM sessions only. Never in group chats — contains personal context.
> See MEMORY_GUIDE.md for instructions on what and how to store.

---

## Memory

_Nothing yet._
```

- [ ] **Step 4: Verify the seed is correct**

```bash
cat workspace/MEMORY.md
```

Expected: no instructional content, no gcal/gmail sections — only the header, warning block, `## Memory` heading, and `_Nothing yet._`.

- [ ] **Step 5: Commit**

```bash
git add workspace/MEMORY_GUIDE.md workspace/MEMORY.md
git commit -m "feat(workspace): split MEMORY.md into MEMORY_GUIDE.md (operator) and MEMORY.md (agent seed)"
```

---

### Task 2: Update AGENTS.md to read both files

**Files:**
- Modify: `workspace/AGENTS.md:25-26`

The Every Session block currently reads `MEMORY.md` conditionally in main sessions only. `MEMORY_GUIDE.md` must be read unconditionally in all sessions (it contains gcal/gmail operational refs that apply everywhere). `MEMORY.md` stays main-session only.

- [ ] **Step 1: Edit `workspace/AGENTS.md` lines 25–26**

This replaces 2 lines with 3 lines (inserting one new line). Use the exact `old_string` below to avoid ambiguity:

old_string (lines 25–26, exactly):
```
6. Read `memory/YYYY-MM-DD.md` (today + yesterday) for recent context
7. **If in MAIN SESSION** (direct chat with your human): Also read `MEMORY.md`
```

new_string (3 lines — old step 6 becomes step 7, old step 7 becomes step 8):
```
6. Read `MEMORY_GUIDE.md` — operational tools and memory instructions
7. Read `memory/YYYY-MM-DD.md` (today + yesterday) for recent context
8. **If in MAIN SESSION** (direct chat with your human): Also read `MEMORY.md`
```

The line immediately after (`Don't ask permission. Just do it.`) is not touched.

- [ ] **Step 2: Update the `## Memory` prose block in AGENTS.md**

The `## Memory` section (around line 33) only lists two files. Add `MEMORY_GUIDE.md` so the file is internally consistent with the new two-file model:

old_string:
```
You wake up fresh each session. These files are your continuity:

- **Daily notes:** `memory/YYYY-MM-DD.md` (create `memory/` if needed) — raw logs of what happened
- **Long-term:** `MEMORY.md` — your curated memories, like a human's long-term memory
```

new_string:
```
You wake up fresh each session. These files are your continuity:

- **Instructions & tools:** `MEMORY_GUIDE.md` — operational quick-refs and memory rules (operator-owned, always fresh)
- **Daily notes:** `memory/YYYY-MM-DD.md` (create `memory/` if needed) — raw logs of what happened
- **Long-term:** `MEMORY.md` — your curated memories, like a human's long-term memory (agent-owned, persists across deploys)
```

- [ ] **Step 3: Update the `### 🧠 MEMORY.md` section in AGENTS.md**

Use the exact `old_string` below as the anchor (lines 39–41 of AGENTS.md):

old_string:
```
### 🧠 MEMORY.md - Your Long-Term Memory

- **ONLY load in main session** (direct chats with your human)
```

new_string (inserts one paragraph between the heading and the bullet list):
```
### 🧠 MEMORY.md - Your Long-Term Memory

`MEMORY_GUIDE.md` contains instructions and tool quick-references — it is redeployed fresh on every `make deploy` and should never contain agent memories. `MEMORY.md` is yours — it persists across deploys and is the only place to write memories.

- **ONLY load in main session** (direct chats with your human)
```

- [ ] **Step 4: Verify the Every Session block looks correct**

```bash
grep -A 18 "Every Session" workspace/AGENTS.md
```

Expected: step 6 is `Read MEMORY_GUIDE.md`, step 7 is `Read memory/YYYY-MM-DD.md`, step 8 is `If in MAIN SESSION`, followed by `Don't ask permission. Just do it.`

- [ ] **Step 5: Commit**

```bash
git add workspace/AGENTS.md
git commit -m "feat(workspace): update AGENTS.md to load MEMORY_GUIDE.md in all sessions"
```

---

## Chunk 2: Deploy script

### Task 3: Guard MEMORY.md in setup.sh deploy loop

**Files:**
- Modify: `scripts/setup.sh:281-288`

The current Step 8 (lines 281–288) copies all `workspace/*.md` into the container unconditionally. Replace it so `MEMORY.md` is only copied on first deploy (when absent), and preserved on all subsequent deploys.

Key constraints:
- `setup.sh` runs under `set -euo pipefail` (line 2) — the existence check must be in an `if` statement or the script aborts when the file is absent on first deploy
- `COMPOSE_CMD` is set to `"sudo docker compose"` at line 222
- `docker compose exec` requires the container to be running — it will be by step 8 (started at step 5, health-waited at step 6)
- Chain `cp && rm` in one `rsh` call (same pattern as the rest of the loop) to avoid leaving stale files on VPS `/tmp/`

- [ ] **Step 1: Replace the Step 8 block in `scripts/setup.sh`**

Find and replace lines 281–288:

Old:
```bash
# ── Step 8: Deploy workspace files ───────────────────────────────────────────
step "Deploying workspace files"
scp workspace/*.md "$HOST:/tmp/"
for f in workspace/*.md; do
    fname=$(basename "$f")
    rsh "cd $REMOTE_DIR && $COMPOSE_CMD cp /tmp/$fname openclaw:/home/node/.openclaw/workspace/$fname && rm -f /tmp/$fname"
done
ok "Workspace files deployed"
```

New:
```bash
# ── Step 8: Deploy workspace files ───────────────────────────────────────────
step "Deploying workspace files"
scp workspace/*.md "$HOST:/tmp/"
for f in workspace/*.md; do
    fname=$(basename "$f")
    if [ "$fname" = "MEMORY.md" ]; then
        # Agent owns MEMORY.md after first deploy — only seed if absent.
        # Wrapped in `if` due to set -euo pipefail: test -f returns non-zero when file is absent.
        if rsh "cd $REMOTE_DIR && $COMPOSE_CMD exec -T openclaw test -f /home/node/.openclaw/workspace/MEMORY.md" 2>/dev/null; then
            rsh "rm -f /tmp/MEMORY.md"
            ok "MEMORY.md preserved (agent-owned)"
        else
            rsh "cd $REMOTE_DIR && $COMPOSE_CMD cp /tmp/MEMORY.md openclaw:/home/node/.openclaw/workspace/MEMORY.md && rm -f /tmp/MEMORY.md"
            ok "MEMORY.md seeded (first deploy)"
        fi
    else
        rsh "cd $REMOTE_DIR && $COMPOSE_CMD cp /tmp/$fname openclaw:/home/node/.openclaw/workspace/$fname && rm -f /tmp/$fname"
    fi
done
ok "Workspace files deployed"
```

- [ ] **Step 2: Verify the diff looks right**

```bash
git diff scripts/setup.sh
```

Expected: only the Step 8 block changed; no other lines modified.

- [ ] **Step 3: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat(deploy): preserve agent-owned MEMORY.md across redeploys"
```

- [ ] **Step 4: Push**

```bash
git push
```

---

## Manual Verification

After pushing, run `make deploy` on your VPS. Watch the Step 8 output:

- **First deploy after this change:** You should see `MEMORY.md seeded (first deploy)` and `MEMORY_GUIDE.md` copied normally.
- **Subsequent deploys:** You should see `MEMORY.md preserved (agent-owned)`.

To confirm the agent reads both files, check a fresh conversation — the agent should still know the gcal/gmail quick-references (from `MEMORY_GUIDE.md`) and should be able to write to `MEMORY.md` without those writes being wiped.
