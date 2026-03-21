# Skill Binary Setup Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `make setup-skills` so users can opt-in install OpenClaw skill binaries (gh, jq, rg, spogo) into the container in one step, consistent with existing `setup-gcal` / `setup-gmail` patterns.

**Architecture:** A new `scripts/setup-skills.sh` script downloads static Linux amd64 binaries from GitHub releases onto the VPS, copies them into the openclaw container via `docker cp`, registers each on the exec approvals allowlist, and merges them into `tools.exec.safeBins`. A `make setup-skills` target calls it. `doctor.sh` grows a new "Skills" section that shows which skill binaries are present. The `summarize` skill (macOS-only via brew, no Linux binary) is explicitly excluded with a note.

**Tech Stack:** bash, curl, docker compose, openclaw CLI (approvals, config), python3 (JSON merge for safeBins)

---

## Skill → Binary Map

| Skill | Bins needed | Linux binary source |
|---|---|---|
| `github` | `gh` | GitHub CLI releases tarball (`cli/cli`) |
| `session-logs` | `jq`, `rg` | jqlang/jq direct binary; BurntSushi/ripgrep musl tarball |
| `spotify-player` | `spogo` | steipete/spogo tarball (Go binary; uses Spotify Web API cookies) |
| `summarize` | `summarize` | **Not available on Linux** — brew-only; skip with note |

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `scripts/setup-skills.sh` | **Create** | Downloads binaries on VPS, installs into container, updates approvals + safeBins |
| `Makefile` | **Modify** | Add `setup-skills` target + update `.PHONY` |
| `scripts/doctor.sh` | **Modify** | Add "Skills" section reporting which skill bins are installed |
| `scripts/setup.sh` | **Modify** | Add optional skills prompt to deploy wizard + summary line |

No new Python tests — consistent with existing pattern (no unit tests for shell scripts). Verification is done via `make doctor` output.

---

## Chunk 1: setup-skills.sh + Makefile target

### Task 1: Create `scripts/setup-skills.sh`

**Files:**
- Create: `scripts/setup-skills.sh`

- [ ] **Step 1: Create the script**

```bash
#!/bin/bash
# Install OpenClaw skill CLIs into the openclaw container.
# Downloads static Linux amd64 binaries from GitHub releases onto the VPS,
# then docker-cp them into the container. Safe to re-run (idempotent).
#
# Usage:
#   bash scripts/setup-skills.sh user@host
#   bash scripts/setup-skills.sh user@host github session-logs
#   bash scripts/setup-skills.sh user@host github session-logs spotify-player
#
# Supported skills: github  session-logs  spotify-player
# NOT supported:    summarize (macOS-only brew package, no Linux binary available)
set -euo pipefail

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "Usage: $0 user@host [skill1 skill2 ...]"
    echo "Skills: github session-logs spotify-player"
    exit 1
fi

# Remaining args are skill names; default to all Linux-available skills.
# Must check $# BEFORE shift to distinguish "no skills given" from "user gave empty string".
shift
if [ "$#" -eq 0 ]; then
    SKILLS=(github session-logs spotify-player)
else
    SKILLS=("$@")
fi

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step()  { echo -e "\n${BOLD}▶ $1${NC}"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
skip()  { echo -e "  ${YELLOW}–${NC} $1"; }
fail()  { echo -e "  ${RED}✗${NC} $1"; exit 1; }

COMPOSE="sudo docker compose -f ~/openclaw-deploy/docker-compose.yml"
BIN_DIR="/home/node/.openclaw/bin"

# ── Helpers ────────────────────────────────────────────────────────────────────

# Fetch latest GitHub release download URL matching a pattern (run on VPS)
latest_gh_asset() {
    local repo="$1" pattern="$2"
    ssh "$HOST" "curl -fsSL 'https://api.github.com/repos/${repo}/releases/latest' \
        | python3 -c \"import sys,json; assets=json.load(sys.stdin)['assets']; \
          match=[a['browser_download_url'] for a in assets if '${pattern}' in a['name']]; \
          print(match[0] if match else '')\""
}

# Ensure the bin directory exists inside the container
ensure_bin_dir() {
    ssh "$HOST" "$COMPOSE exec -T openclaw mkdir -p $BIN_DIR"
}

# Install a single binary into the container from a VPS path
install_bin() {
    local name="$1" vps_path="$2"
    ssh "$HOST" "$COMPOSE cp '$vps_path' openclaw:$BIN_DIR/$name \
        && $COMPOSE exec -T openclaw chmod +x $BIN_DIR/$name"
    ok "$name installed at $BIN_DIR/$name"
}

# Register a binary on the exec approvals allowlist (|| true: idempotent)
register_approvals() {
    local name="$1"
    ssh "$HOST" "$COMPOSE exec -T openclaw openclaw approvals allowlist add '$BIN_DIR/$name' --agent main --gateway 2>/dev/null || true
        $COMPOSE exec -T openclaw openclaw approvals allowlist add '$name' --agent main --gateway 2>/dev/null || true
        $COMPOSE exec -T openclaw openclaw approvals allowlist add '$name *' --agent main --gateway 2>/dev/null || true"
    ok "$name registered on approvals allowlist"
}

# Download tarball on VPS into an isolated temp dir, extract a named binary, install it
# Usage: install_tarball_bin DISPLAY_NAME BINARY_NAME TARBALL_URL
install_tarball_bin() {
    local display="$1" name="$2" url="$3"
    ssh "$HOST" "
        TMPD=\$(mktemp -d)
        trap 'rm -rf \"\$TMPD\"' EXIT
        curl -fsSL '$url' | tar -xz -C \"\$TMPD\"
        BIN=\$(find \"\$TMPD\" -name '$name' -type f | head -1)
        [ -n \"\$BIN\" ] || { echo 'Binary $name not found in tarball'; exit 1; }
        cp \"\$BIN\" \"\$TMPD/$name\"
        chmod +x \"\$TMPD/$name\"
        sudo docker compose -f ~/openclaw-deploy/docker-compose.yml cp \"\$TMPD/$name\" openclaw:$BIN_DIR/$name
        sudo docker compose -f ~/openclaw-deploy/docker-compose.yml exec -T openclaw chmod +x $BIN_DIR/$name
    "
    ok "$display installed at $BIN_DIR/$name"
}

# ── Skill installers ───────────────────────────────────────────────────────────

install_github() {
    step "Installing github skill → gh (GitHub CLI)"
    local url
    url=$(latest_gh_asset "cli/cli" "linux_amd64.tar.gz")
    [ -n "$url" ] || fail "Could not find gh release for linux_amd64"
    install_tarball_bin "gh" "gh" "$url"
    register_approvals "gh"
}

install_session_logs() {
    step "Installing session-logs skill → jq"
    local jq_url="https://github.com/jqlang/jq/releases/latest/download/jq-linux-amd64"
    ssh "$HOST" "
        TMPD=\$(mktemp -d)
        trap 'rm -rf \"\$TMPD\"' EXIT
        curl -fsSL '$jq_url' -o \"\$TMPD/jq\"
        chmod +x \"\$TMPD/jq\"
        sudo docker compose -f ~/openclaw-deploy/docker-compose.yml cp \"\$TMPD/jq\" openclaw:$BIN_DIR/jq
        sudo docker compose -f ~/openclaw-deploy/docker-compose.yml exec -T openclaw chmod +x $BIN_DIR/jq
    "
    ok "jq installed at $BIN_DIR/jq"
    register_approvals "jq"

    step "Installing session-logs skill → rg (ripgrep)"
    local rg_url
    rg_url=$(latest_gh_asset "BurntSushi/ripgrep" "x86_64-unknown-linux-musl.tar.gz")
    [ -n "$rg_url" ] || fail "Could not find rg release for x86_64-unknown-linux-musl"
    install_tarball_bin "rg" "rg" "$rg_url"
    register_approvals "rg"
}

install_spotify_player() {
    step "Installing spotify-player skill → spotify_player"
    local url
    url=$(latest_gh_asset "aome510/spotify-player" "x86_64-unknown-linux-gnu.tar.gz")
    [ -n "$url" ] || fail "Could not find spotify_player release for x86_64-unknown-linux-gnu"
    install_tarball_bin "spotify_player" "spotify_player" "$url"
    register_approvals "spotify_player"
}

# ── Main ───────────────────────────────────────────────────────────────────────

ensure_bin_dir

NEW_BINS=()

for skill in "${SKILLS[@]}"; do
    case "$skill" in
        github)         install_github;         NEW_BINS+=(gh) ;;
        session-logs)   install_session_logs;   NEW_BINS+=(jq rg) ;;
        spotify-player) install_spotify_player; NEW_BINS+=(spotify_player) ;;
        summarize)
            skip "summarize — not available on Linux (macOS brew-only package)"
            ;;
        *)
            skip "$skill — unknown skill, skipping"
            ;;
    esac
done

# ── Update tools.exec.safeBins (merge, don't overwrite) ───────────────────────
# Uses a temp file to pass JSON data to python3, avoiding fragile shell string interpolation.

if [ "${#NEW_BINS[@]}" -gt 0 ]; then
    step "Merging new bins into tools.exec.safeBins"

    # Fetch current safeBins JSON from the container
    CURRENT_JSON=$(ssh "$HOST" "$COMPOSE exec -T openclaw openclaw config get tools.exec.safeBins 2>/dev/null || echo '[]'")

    # Build new-bins list and merge locally using a temp file to pass CURRENT_JSON safely
    TMPJSON=$(mktemp)
    trap 'rm -f "$TMPJSON"' EXIT
    printf '%s' "$CURRENT_JSON" > "$TMPJSON"

    NEW_BINS_PY="[$(printf "'%s'," "${NEW_BINS[@]}" | sed 's/,$//')]"
    MERGED=$(python3 - <<PYEOF
import json
with open("$TMPJSON") as f:
    raw = f.read().strip()
current = json.loads(raw) if raw else []
new = $NEW_BINS_PY
merged = sorted(set(current + new))
print(json.dumps(merged))
PYEOF
)
    ssh "$HOST" "$COMPOSE exec -T openclaw openclaw config set tools.exec.safeBins '$MERGED'"
    ok "safeBins updated: $MERGED"

    step "Restarting openclaw to apply config"
    ssh "$HOST" "cd ~/openclaw-deploy && $COMPOSE restart openclaw"
    ok "openclaw restarted"
fi

echo ""
echo -e "${BOLD}Skill setup complete.${NC}"
echo "  Run 'make doctor' to verify."
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/setup-skills.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/setup-skills.sh
git commit -m "feat: add setup-skills.sh for installing skill CLIs into container"
```

---

### Task 2: Add `make setup-skills` target to Makefile

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Add the target**

In `Makefile`, after the `setup-gmail` target, add:

```makefile
# Install OpenClaw skill CLIs into the container (run once after deploy)
# Opt-in per skill. Usage: make setup-skills [SKILLS="github session-logs spotify-player"]
# Supported: github  session-logs  spotify-player
# Not on Linux: summarize (macOS brew-only)
setup-skills:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	@bash scripts/setup-skills.sh "$(HOST)" $(SKILLS)
```

Also update the `.PHONY` line to include `setup-skills`:

Current `.PHONY`:
```
.PHONY: up up-calendar up-voice up-mail down logs logs-all restart status backup backup-remote update test kill-switch setup-approvals setup-heartbeat setup-egress setup-inbound setup-gcal setup-gmail deploy-workspace deploy deploy-clis doctor pair-whatsapp
```

New `.PHONY` (add `setup-skills` at the end):
```
.PHONY: up up-calendar up-voice up-mail down logs logs-all restart status backup backup-remote update test kill-switch setup-approvals setup-heartbeat setup-egress setup-inbound setup-gcal setup-gmail setup-skills deploy-workspace deploy deploy-clis doctor pair-whatsapp
```

- [ ] **Step 2: Verify the target appears in `make help` equivalent**

```bash
grep -n 'setup-skills' Makefile
```

Expected: 2 lines (`.PHONY` and target definition).

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "feat: add setup-skills Makefile target"
```

---

## Chunk 2: doctor.sh Skills section

### Task 3: Add Skills section to `scripts/doctor.sh`

**Files:**
- Modify: `scripts/doctor.sh`

- [ ] **Step 1: Add the Skills section**

Insert a new section in `doctor.sh` between the "Gmail" section and the "Backups" section. The check looks for each skill binary inside the container via `docker compose exec`.

```bash
# ── Skills ─────────────────────────────────────────────────────────────────────

echo ""
echo " Skills"

check_skill_bin() {
    local bin="$1" skill="$2"
    if sudo docker compose exec -T openclaw test -f "$BIN_DIR/$bin" 2>/dev/null; then
        pass "$skill  ($bin installed)"
    else
        skip "$skill  ($bin not installed  →  run: make setup-skills SKILLS=$skill)"
    fi
}

BIN_DIR="/home/node/.openclaw/bin"
check_skill_bin "gh"            "github"
check_skill_bin "jq"            "session-logs (jq)"
check_skill_bin "rg"            "session-logs (rg)"
check_skill_bin "spotify_player" "spotify-player"
skip "summarize  (not available on Linux — macOS only)"
```

Exact insertion point: after the closing `fi` of the Gmail section (around line 171) and before the `# ── Backups ──` header.

- [ ] **Step 2: Run a syntax check**

```bash
bash -n scripts/doctor.sh
```

Expected: no output (no syntax errors).

- [ ] **Step 3: Commit**

```bash
git add scripts/doctor.sh
git commit -m "feat: add Skills section to doctor.sh"
```

---

## Chunk 3: deploy wizard integration (setup.sh)

### Task 4: Add optional skills prompt to `scripts/setup.sh`

**Files:**
- Modify: `scripts/setup.sh`

The deploy wizard already prompts for voice, backups, alerts, and WhatsApp at the end. Skills fit the same pattern.

- [ ] **Step 1: Add the skills prompt**

`$SCRIPT_DIR` is already defined earlier in `setup.sh` (around line 248) — reuse it directly.

Insert after the closing `fi` of the WhatsApp pairing block (Step 11) and before the `# ── Summary ──` block:

```bash
# ── Step 12: Install skill binaries (optional) ───────────────────────────────
skills_installed=false
echo ""
printf "  Install OpenClaw skill binaries (github, session-logs, spotify-player)? [y/N]: " >&2
read -r skills_yn
if [[ "${skills_yn,,}" == "y" ]]; then
    step "Installing skill binaries"
    if bash "$SCRIPT_DIR/setup-skills.sh" "$HOST"; then
        ok "Skill binaries installed"
        skills_installed=true
    else
        warn "Skill install had errors — run: make setup-skills"
    fi
fi
```

- [ ] **Step 2: Add skills line to the deploy summary**

Exact insertion point: after the closing `fi` of the Calendar block and before the blank line + `make doctor` hint. The Calendar block ends with:

```bash
else
    echo -e "  ${YELLOW}⚪${NC} Calendar    Google Calendar not set up — see docs/runbook.md §10"
fi
echo ""
echo "  Health check:  make doctor"
```

Insert the skills lines between `fi` and `echo ""`:

```bash
if $skills_installed; then
    echo -e "  ${GREEN}✅${NC} Skills      github, session-logs, spotify-player installed"
else
    echo -e "  ${YELLOW}⚪${NC} Skills      not installed — run: make setup-skills"
fi
echo ""
echo "  Health check:  make doctor"
```

- [ ] **Step 3: Verify syntax and diff**

```bash
bash -n scripts/setup.sh
git diff scripts/setup.sh
```

`bash -n` catches syntax errors only. Review the diff to confirm both insertions landed in the right place (Step 12 block after WhatsApp, skills summary after Calendar `fi`).

- [ ] **Step 4: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat: add optional skill binary install prompt to deploy wizard"
```

---

## Chunk 4: Final verification

- [ ] **Step 1: Run all tests locally**

```bash
make test
```

Expected: all tests pass (this is Python-only; shell scripts are not unit-tested per project convention).

- [ ] **Step 2: Verify setup-skills.sh syntax**

```bash
bash -n scripts/setup-skills.sh
```

Expected: no output.

- [ ] **Step 3: Verify Makefile**

```bash
grep -E '(setup-skills|\.PHONY)' Makefile
```

Expected: `setup-skills` appears in `.PHONY` and as a target.

- [ ] **Step 4: Commit (if not already done)**

All three previous tasks each committed their own changes. No extra commit needed.

---

## Usage After Deploy

```bash
# Install all available skills
make setup-skills

# Install specific skills only
make setup-skills SKILLS="github session-logs"

# Check which skills are installed
make doctor
```

**Note:** `summarize` skill is not available on Linux. Only macOS brew is supported by steipete. Skip it or contribute a Linux binary upstream.
