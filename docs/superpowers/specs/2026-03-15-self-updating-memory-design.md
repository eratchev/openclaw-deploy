# Self-Updating Agent Memory Design

**Goal:** Allow the OpenClaw agent to accumulate and persist its own memories across sessions and redeployments without manual editing.

**Problem:** `MEMORY.md` is currently overwritten on every `make deploy` — the deploy loop copies all `workspace/*.md` into the container with no guard. Any content the agent has written is wiped on redeploy.

---

## Two-File Split

The current `MEMORY.md` serves two purposes: instructions (how to use memory) and actual stored memories. These have different owners and different deploy semantics.

| File | Owner | Deployed? | Contains |
|---|---|---|---|
| `MEMORY_GUIDE.md` | Git / operator | Always overwritten | Instructions, compression rules, gcal/gmail quick-ref |
| `MEMORY.md` | Agent | Seeded once, never overwritten | Accumulated memories — preferences, workflows, decisions, context |

**First deploy:** `MEMORY.md` absent in container → copy seed from git. Agent populates it over time.

**Subsequent deploys:** `MEMORY.md` already present → skip container copy; delete from VPS `/tmp/`. Agent's content is preserved.

**Reset:** `sudo docker compose exec openclaw rm /home/node/.openclaw/workspace/MEMORY.md` — next deploy re-seeds.

---

## Memory Update Behavior

The agent already has instructions in AGENTS.md to maintain MEMORY.md. No behavior change is needed there. This design simply ensures that writes survive redeployment.

- **Reactive:** User says "remember this" → agent writes immediately
- **Proactive:** During heartbeats, agent reviews the session and writes durable knowledge — preferences expressed, recurring workflows, decisions made, important context

---

## Changes

### File creation order

Files must be created in this order to avoid a deploy that silently skips `MEMORY_GUIDE.md` (since `workspace/*.md` glob picks it up automatically once it exists — no glob change needed):

1. Create `workspace/MEMORY_GUIDE.md` (new file — glob picks it up automatically)
2. Modify `workspace/MEMORY.md` (strip to seed)
3. Update `workspace/AGENTS.md`
4. Update `scripts/setup.sh`

### `workspace/MEMORY_GUIDE.md` (new)

Contains all content currently in `workspace/MEMORY.md`: instructions on what to store, compression rules, example good/bad entries, and the gcal/gmail operational quick-reference sections under `## Memory`. This is a rename of existing content — nothing new is added.

### `workspace/MEMORY.md` (stripped to seed)

The current `workspace/MEMORY.md` contains only instructions and quick-references — no real agent memories have been written to it yet. Stripping it is safe.

The new seed contains only a header and a pointer to `MEMORY_GUIDE.md`. The second `>` line in the warning block is **new** (not in the current file) and must be added explicitly:

```markdown
# MEMORY.md — Long-Term Memory

> ⚠️ Load in main/DM sessions only. Never in group chats — contains personal context.
> See MEMORY_GUIDE.md for instructions on what and how to store.

---

## Memory

_Nothing yet._
```

### `workspace/AGENTS.md` (updated)

Two changes:

**1. Every Session block:** `MEMORY_GUIDE.md` added as an unconditional step (all sessions), since it contains operational quick-references that apply everywhere. `MEMORY.md` stays main-session only. Existing steps 6–7 shift to 7–8.

Before (lines 25–26):
```
6. Read `memory/YYYY-MM-DD.md` (today + yesterday) for recent context
7. **If in MAIN SESSION** (direct chat with your human): Also read `MEMORY.md`
```

After:
```
6. Read `MEMORY_GUIDE.md` — operational tools and memory instructions
7. Read `memory/YYYY-MM-DD.md` (today + yesterday) for recent context
8. **If in MAIN SESSION** (direct chat with your human): Also read `MEMORY.md`
```

**2. Memory section:** Updated to explain the two-file model — `MEMORY_GUIDE.md` is operator-owned (always fresh on deploy), `MEMORY.md` is agent-owned (persists across deploys).

### `scripts/setup.sh` (deploy loop)

The batch `scp workspace/*.md "$HOST:/tmp/"` runs unconditionally for all files including `MEMORY.md` (so the seed is available on first deploy). The guard is on the `docker compose cp` into the container — not on the SCP. The `/tmp/MEMORY.md` cleanup runs in both branches (preserve and seed) to avoid stale files on the VPS.

`docker compose exec` requires the container to be running. By step 8 the container has been started (step 5) and health-waited (step 6). The existence check is wrapped in `if` because `setup.sh` runs under `set -euo pipefail` — a bare `test -f` returning non-zero on first deploy would abort the script.

Replace the existing Step 8 block (currently lines 281–288 of `setup.sh`):

```bash
step "Deploying workspace files"
scp workspace/*.md "$HOST:/tmp/"
for f in workspace/*.md; do
    fname=$(basename "$f")
    if [ "$fname" = "MEMORY.md" ]; then
        # Guard is on the container copy, not the SCP — seed already in /tmp/.
        # `if` wrapper required: set -euo pipefail would abort on non-zero from test -f.
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

---

## Out of Scope

- Automated summarization or compression of MEMORY.md (agent handles this per AGENTS.md instructions)
- Backup of MEMORY.md to S3 or git (container volume is on the VPS; existing backup infrastructure covers it)
- Multiple memory files or namespaced memory
- Memory search or indexing
