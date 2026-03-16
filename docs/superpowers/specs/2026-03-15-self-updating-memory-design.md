# Self-Updating Agent Memory Design

**Goal:** Allow the OpenClaw agent to accumulate and persist its own memories across sessions and redeployments without manual editing.

**Problem:** `MEMORY.md` is currently overwritten on every `make deploy`, wiping any content the agent has written. The agent has file write tools and AGENTS.md already instructs it to maintain MEMORY.md — but the deploy pipeline undermines this.

---

## Two-File Split

The current `MEMORY.md` serves two purposes: instructions (how to use memory) and actual stored memories. These have different owners and different deploy semantics.

| File | Owner | Deployed? | Contains |
|---|---|---|---|
| `MEMORY_GUIDE.md` | Git / operator | Always overwritten | Instructions, compression rules, gcal/gmail quick-ref |
| `MEMORY.md` | Agent | Seeded once, never overwritten | Accumulated memories — preferences, workflows, decisions, context |

**First deploy:** `MEMORY.md` does not exist in the container → copy from git (minimal seed with empty `## Memory` section). Agent populates it over time.

**Subsequent deploys:** `MEMORY.md` already exists → skip. Agent's content is preserved.

**Reset:** `docker compose exec openclaw rm /home/node/.openclaw/workspace/MEMORY.md` — next deploy re-seeds a clean copy.

---

## Memory Update Behavior

The agent already has instructions in AGENTS.md to maintain MEMORY.md. No behavior change is needed there. This design simply ensures that writes survive redeployment.

Updates happen in two ways:

- **Reactive:** User says "remember this" → agent writes immediately
- **Proactive:** During heartbeats, agent reviews the session and writes durable knowledge — preferences expressed, recurring workflows, decisions made, important context

---

## Changes

### `workspace/MEMORY.md` (stripped to seed)

Minimal seed file. Contains only the `## Memory` section header with a placeholder. The agent fills it in over time.

```markdown
# MEMORY.md — Long-Term Memory

> ⚠️ Load in main/DM sessions only. Never in group chats — contains personal context.
> See MEMORY_GUIDE.md for instructions on what and how to store.

---

## Memory

<!-- Agent writes here. Do not edit manually — this file is agent-owned. -->
```

### `workspace/MEMORY_GUIDE.md` (new — current MEMORY.md content)

Contains everything currently in `workspace/MEMORY.md`: instructions on what to store, compression rules, example good/bad entries, and the gcal/gmail operational quick-reference sections.

### `workspace/AGENTS.md` (updated)

In the **Every Session** section, step 7 changes from:

```
7. If in MAIN SESSION: Also read MEMORY.md
```

to:

```
7. If in MAIN SESSION: Also read MEMORY_GUIDE.md and MEMORY.md
```

The Memory section explanation is updated to clarify: MEMORY_GUIDE.md = instructions (operator-owned, always fresh), MEMORY.md = your memories (you own this, survives redeploys).

### `scripts/setup.sh` (deploy loop)

The workspace deploy step (Step 8) changes from a simple loop that copies all `.md` files to one that treats `MEMORY.md` specially:

```bash
for f in workspace/*.md; do
    fname=$(basename "$f")
    if [ "$fname" = "MEMORY.md" ]; then
        # Only seed if not already present — agent owns this file after first deploy
        if rsh "cd $REMOTE_DIR && $COMPOSE_CMD exec -T openclaw test -f /home/node/.openclaw/workspace/MEMORY.md" 2>/dev/null; then
            ok "MEMORY.md preserved (agent-owned)"
        else
            scp "$f" "$HOST:/tmp/$fname"
            rsh "cd $REMOTE_DIR && $COMPOSE_CMD cp /tmp/$fname openclaw:/home/node/.openclaw/workspace/$fname && rm -f /tmp/$fname"
            ok "MEMORY.md seeded (first deploy)"
        fi
    else
        scp "$f" "$HOST:/tmp/$fname"
        rsh "cd $REMOTE_DIR && $COMPOSE_CMD cp /tmp/$fname openclaw:/home/node/.openclaw/workspace/$fname && rm -f /tmp/$fname"
    fi
done
```

---

## Out of Scope

- Automated summarization or compression of MEMORY.md (agent does this itself per AGENTS.md instructions)
- Backup of MEMORY.md to S3 or git (the container volume is already on the VPS; existing backup infrastructure covers it)
- Multiple memory files or namespaced memory
- Memory search or indexing
