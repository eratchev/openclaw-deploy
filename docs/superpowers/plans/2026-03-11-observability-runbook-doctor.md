# Observability, Incident Runbook, and Doctor Hardening

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Telegram alerts for guardrail kills and backup failures, harden `make doctor` with system sanity checks, and document secret rotation and compromise response procedures.

**Architecture:** Three independent tracks — (1) observability: lightweight in-process Telegram alerts via stdlib `urllib.request` in the guardrail and `curl` in the backup script; (2) runbook: two new sections added to `docs/runbook.md`; (3) doctor: new "System" section in `scripts/doctor.sh` covering swap, NODE_OPTIONS, and a crontab grep fix. All alerting is opt-in via `ALERT_TELEGRAM_CHAT_ID` in `.env` — missing var = silently skipped, no errors.

**Tech Stack:** Python 3 (stdlib only), bash, curl, Telegram Bot API

---

## File Map

| File | Change |
|---|---|
| `scripts/guardrail.py` | Add `_alert(msg)` method; add `reason` param to `kill_openclaw()`; call `_alert` on violations and memory kill |
| `tests/test_guardrail.py` | Tests for `_alert()` behavior and kill reason propagation |
| `scripts/backup-cron.sh` | Add `alert()` bash function; add failure-only trap using sentinel variable |
| `scripts/doctor.sh` | Fix crontab grep; add `ALERT_TELEGRAM_CHAT_ID` optional check; new System section |
| `docs/runbook.md` | Add Section 11 (secret rotation) and Section 12 (compromise response) |

---

## Task 1: Guardrail Kill Alerts

**Files:**
- Modify: `scripts/guardrail.py`
- Modify: `tests/test_guardrail.py`

### Step 1: Write failing tests

Add to the bottom of `tests/test_guardrail.py`:

```python
# ── observability alerts ──────────────────────────────────────────────────────

def test_alert_sends_telegram_when_configured():
    """_alert() calls urlopen when both ALERT_TELEGRAM_CHAT_ID and TELEGRAM_TOKEN are set."""
    g = Guardrail()
    with patch.dict(os.environ, {"ALERT_TELEGRAM_CHAT_ID": "123456", "TELEGRAM_TOKEN": "tok:abc"}), \
         patch("urllib.request.urlopen") as mock_urlopen:
        g._alert("test message")
    mock_urlopen.assert_called_once()


def test_alert_skips_when_chat_id_missing():
    """_alert() is a no-op when ALERT_TELEGRAM_CHAT_ID is not set."""
    g = Guardrail()
    with patch.dict(os.environ, {"TELEGRAM_TOKEN": "tok:abc"}, clear=True), \
         patch("urllib.request.urlopen") as mock_urlopen:
        g._alert("test message")
    mock_urlopen.assert_not_called()


def test_alert_skips_when_token_missing():
    """_alert() is a no-op when TELEGRAM_TOKEN is not set."""
    g = Guardrail()
    with patch.dict(os.environ, {"ALERT_TELEGRAM_CHAT_ID": "123456"}, clear=True), \
         patch("urllib.request.urlopen") as mock_urlopen:
        g._alert("test message")
    mock_urlopen.assert_not_called()


def test_alert_does_not_raise_on_network_error():
    """_alert() logs a warning and continues if the HTTP call fails."""
    g = Guardrail()
    with patch.dict(os.environ, {"ALERT_TELEGRAM_CHAT_ID": "123456", "TELEGRAM_TOKEN": "tok:abc"}), \
         patch("urllib.request.urlopen", side_effect=Exception("network error")):
        g._alert("test message")  # must not raise


def test_kill_openclaw_alerts_with_reason():
    """kill_openclaw(reason) sends an alert containing the reason string."""
    g = Guardrail()
    g.openclaw_pid = 99999
    with patch("os.kill"), patch("time.sleep"), \
         patch.object(g, "_alert") as mock_alert:
        g.kill_openclaw("llm call limit (5 >= 5)")
    mock_alert.assert_called_once()
    assert "llm call limit" in mock_alert.call_args[0][0]


def test_kill_openclaw_no_alert_without_reason():
    """kill_openclaw() with no reason (kill-switch path) sends no alert."""
    g = Guardrail()
    g.openclaw_pid = 99999
    with patch("os.kill"), patch("time.sleep"), \
         patch.object(g, "_alert") as mock_alert:
        g.kill_openclaw()
    mock_alert.assert_not_called()
```

Also add `import os` to the test file imports if not already present (check — it uses `os.path` via `tmp_path` but `os.environ` may not be imported at module level; add `import os` after `import subprocess`).

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/evgueni/repos/personal/openclaw-deploy
.venv/bin/python -m pytest tests/test_guardrail.py -k "alert or kill_openclaw" -v
```

Expected: 6 FAIL (methods/params don't exist yet).

- [ ] **Step 3: Add `import os` and `import urllib.request` / `import urllib.parse` to guardrail.py**

They are already imported implicitly via stdlib but add them explicitly at the top of `scripts/guardrail.py` after the existing imports:

```python
import urllib.parse
import urllib.request
```

- [ ] **Step 4: Add `_alert()` method to `Guardrail` class**

Add after `check_memory()` and before `_start_log_proc()`:

```python
# ── Alerting ──────────────────────────────────────────────────────────────────

def _alert(self, message: str):
    """Send a Telegram message to ALERT_TELEGRAM_CHAT_ID. Silent no-op if not configured."""
    chat_id = os.getenv("ALERT_TELEGRAM_CHAT_ID", "")
    token = os.getenv("TELEGRAM_TOKEN", "")
    if not chat_id or not token:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[guardrail] WARNING: alert failed: {e}", flush=True)
```

- [ ] **Step 5: Add `reason` parameter to `kill_openclaw()` and call `_alert()`**

Replace the existing `kill_openclaw()` signature and first line:

Old:
```python
def kill_openclaw(self):
    """Kill the OpenClaw process. Drops ALL active sessions."""
    pid = self.openclaw_pid or self.find_openclaw_pid()
```

New:
```python
def kill_openclaw(self, reason: str = ""):
    """Kill the OpenClaw process. Drops ALL active sessions."""
    if reason:
        self._alert(f"🚨 OpenClaw guardrail killed gateway\nReason: {reason}")
    pid = self.openclaw_pid or self.find_openclaw_pid()
```

- [ ] **Step 6: Pass `reason` at each violation call site**

In `process_event()` — LLM violation (line ~246):
```python
# Old:
self.kill_openclaw()
# New (immediately after the print statement for the LLM violation):
self.kill_openclaw(violation)
```

In `process_event()` — tool violation (line ~200):
```python
# Old:
self.kill_openclaw()
# New:
self.kill_openclaw(violation)
```

In `check_memory()` (line ~301):
```python
# Old:
self.kill_openclaw()
# New:
self.kill_openclaw(f"memory {pct:.1f}% > {self.max_memory_pct}%")
```

`check_kill_switch()` keeps `self.kill_openclaw()` with no reason — no alert on deliberate kill.

- [ ] **Step 7: Run all guardrail tests**

```bash
.venv/bin/python -m pytest tests/test_guardrail.py -v
```

Expected: all 33 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add scripts/guardrail.py tests/test_guardrail.py
git commit -m "feat: alert to Telegram on guardrail kill (configurable via ALERT_TELEGRAM_CHAT_ID)"
```

---

## Task 2: Backup Failure Alerts

**Files:**
- Modify: `scripts/backup-cron.sh`

No automated tests for shell scripts. Manual verification steps provided.

- [ ] **Step 1: Add `ALERT_TELEGRAM_CHAT_ID` to the parse_env block**

After `BACKUP_RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-7}"` (line 28), add:

```bash
ALERT_TELEGRAM_CHAT_ID="$(parse_env ALERT_TELEGRAM_CHAT_ID)"
TELEGRAM_TOKEN="$(parse_env TELEGRAM_TOKEN)"
```

- [ ] **Step 2: Add `alert()` function after the parse block**

Add immediately before the `VOLUME=...` line:

```bash
# Send a Telegram alert. Silent no-op if credentials are not configured.
alert() {
    local msg="$1"
    [[ -z "${ALERT_TELEGRAM_CHAT_ID:-}" || -z "${TELEGRAM_TOKEN:-}" ]] && return 0
    curl -sf "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${ALERT_TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${msg}" \
        > /dev/null 2>&1 || true
}
```

- [ ] **Step 3: Add failure sentinel and trap**

Replace the existing trap line:
```bash
trap 'rm -f "$TMPFILE"' EXIT
```

With:
```bash
_backup_ok=false
trap 'rm -f "$TMPFILE"; [[ "$_backup_ok" == "true" ]] || alert "🚨 OpenClaw backup FAILED at $(hostname) $(date -u +%Y-%m-%dT%H:%M:%SZ)"' EXIT
```

- [ ] **Step 4: Set sentinel to true at end of script**

Replace the final `echo "[backup] Done."` line with:

```bash
_backup_ok=true
echo "[backup] Done."
```

- [ ] **Step 5: Manual smoke test**

Verify the alert function handles missing credentials gracefully:

```bash
# Test: no credentials — should be silent
(
  ALERT_TELEGRAM_CHAT_ID=""
  TELEGRAM_TOKEN=""
  alert() {
    [[ -z "${ALERT_TELEGRAM_CHAT_ID:-}" || -z "${TELEGRAM_TOKEN:-}" ]] && return 0
    echo "SENT: $1"
  }
  alert "test"
)
# Expected: no output
```

- [ ] **Step 6: Commit**

```bash
git add scripts/backup-cron.sh
git commit -m "feat: Telegram alert on backup failure (configurable via ALERT_TELEGRAM_CHAT_ID)"
```

---

## Task 3: Doctor Sanity Checks

**Files:**
- Modify: `scripts/doctor.sh`

- [ ] **Step 1: Add `ALERT_TELEGRAM_CHAT_ID` to optional .env checks**

After the existing `check_optional WEBHOOK_SECRET` line (line 75):

```bash
check_optional ALERT_TELEGRAM_CHAT_ID "ALERT_TELEGRAM_CHAT_ID" "guardrail/backup alerts disabled  →  set to your Telegram chat ID (message @userinfobot to find it)"
```

- [ ] **Step 2: Fix the crontab grep**

`install-backup-cron.sh` installs the script to `/usr/local/sbin/openclaw-backup`, not `backup-cron.sh`. Fix the check on line 161:

Old:
```bash
if sudo crontab -l 2>/dev/null | grep -q "backup-cron.sh"; then
```

New:
```bash
if sudo crontab -l 2>/dev/null | grep -q "openclaw-backup"; then
```

- [ ] **Step 3: Add System section at the end, before the Summary block**

Insert before the `# ── Summary` comment (line 168):

```bash
# ── System ─────────────────────────────────────────────────────────────────────

echo ""
echo " System"

# Swap
swap_total=$(free -m | awk '/^Swap:/ {print $2}')
if [[ "${swap_total:-0}" -gt 0 ]]; then
    pass "Swap  ${swap_total}MB configured"
else
    warn "Swap  none — add 2GB swapfile on 2GB hosts (see docs/runbook.md section 0)"
fi

# NODE_OPTIONS (V8 heap cap)
if sudo docker compose exec -T openclaw printenv NODE_OPTIONS 2>/dev/null | grep -q "max-old-space"; then
    node_opts=$(sudo docker compose exec -T openclaw printenv NODE_OPTIONS 2>/dev/null || true)
    pass "NODE_OPTIONS  ${node_opts}"
else
    warn "NODE_OPTIONS  not set — V8 heap unbounded (OOM risk on 2GB hosts; set --max-old-space-size=768 in .env)"
fi
```

- [ ] **Step 4: Run doctor locally to verify no syntax errors**

```bash
bash -n scripts/doctor.sh
```

Expected: no output (syntax OK).

- [ ] **Step 5: Verify the new checks appear in output**

On the VPS or locally (if Docker is running):

```bash
make doctor
```

Expected additions to output:
```
 System
 ✅ Swap  2048MB configured
 ✅ NODE_OPTIONS  --max-old-space-size=768

 .env
 ...
 ⚪ ALERT_TELEGRAM_CHAT_ID — not set (guardrail/backup alerts disabled → ...)
```

- [ ] **Step 6: Commit**

```bash
git add scripts/doctor.sh
git commit -m "feat: add system sanity checks to make doctor (swap, NODE_OPTIONS, alert config); fix crontab grep"
```

---

## Task 4: Incident Runbook

**Files:**
- Modify: `docs/runbook.md`

Docs only — no tests required.

- [ ] **Step 1: Add Section 11 (Secret Rotation)**

Append after the existing Section 10 (Google Calendar Setup), before any trailing content:

```markdown
---

## 11. Secret Rotation

Rotate secrets one at a time to avoid simultaneous downtime.

### ANTHROPIC_API_KEY

1. Generate a new key at console.anthropic.com.
2. Update `.env` on the VPS: `nano .env` → replace `ANTHROPIC_API_KEY=...`
3. `docker compose up -d --no-deps openclaw` — picks up the new key on restart.
4. Revoke the old key at console.anthropic.com.
5. `make doctor` to confirm healthy.

### TELEGRAM_TOKEN

Telegram tokens cannot be rotated without a full re-registration:

1. Message @BotFather → `/mybots` → select bot → `API Token` → `Revoke current token`.
2. BotFather issues a new token.
3. Update `.env` on the VPS: replace `TELEGRAM_TOKEN=...`
4. Re-register the webhook with the new token:
   ```bash
   docker compose exec openclaw openclaw config set channels.telegram.botToken "<new token>"
   docker compose exec openclaw openclaw config set channels.telegram.webhookUrl "https://${DOMAIN}/telegram-webhook"
   docker compose restart openclaw
   ```
5. `make doctor` — confirm Telegram webhook shows ✅.

### REDIS_PASSWORD

Changing the Redis password requires a coordinated restart:

```bash
# On VPS:
NEW_PASS=$(openssl rand -hex 32)

# 1. Update .env
sed -i "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=${NEW_PASS}/" .env

# 2. Restart everything together (redis must start with new password,
#    openclaw must authenticate with it simultaneously)
docker compose down && docker compose up -d

make doctor
```

### WEBHOOK_SECRET

```bash
# On VPS:
NEW_SECRET=$(openssl rand -hex 32)

# 1. Update .env
sed -i "s/^WEBHOOK_SECRET=.*/WEBHOOK_SECRET=${NEW_SECRET}/" .env

# 2. Update openclaw config so Telegram registers the new secret
docker compose exec openclaw openclaw config set channels.telegram.webhookSecret "$NEW_SECRET"

# 3. Restart affected services
docker compose up -d --no-deps voice-proxy
docker compose restart openclaw

make doctor
```

### OPENAI_API_KEY

```bash
# On VPS: update .env, restart voice-proxy
sed -i "s/^OPENAI_API_KEY=.*/OPENAI_API_KEY=<new key>/" .env
docker compose up -d --no-deps voice-proxy
```

Revoke the old key at platform.openai.com.

---

## 12. Compromise Response

If you suspect the bot or VPS has been compromised:

### Step 1 — Contain immediately

```bash
make kill-switch    # stops the bot within 5 seconds
```

### Step 2 — Revoke all credentials at source

Do this before rotating `.env` — invalidate at the provider so the leaked key cannot be used even if not yet rotated locally:

| Credential | Where to revoke |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `TELEGRAM_TOKEN` | @BotFather → Revoke |
| `OPENAI_API_KEY` | platform.openai.com → API Keys |
| S3 access key | Hetzner Console → Object Storage → Access Keys |

### Step 3 — Assess scope

```bash
# Check for unexpected SSH logins
sudo last | head -20

# Check for unexpected processes
sudo ps aux | grep -v "docker\|containerd\|openclaw\|redis\|caddy\|sshd\|systemd\|root"

# Check Docker for unexpected containers or images
docker ps -a
docker images
```

### Step 4 — Wipe and redeploy (if server is compromised)

If the VPS itself may be compromised (not just a leaked API key):

1. Snapshot the data volume first:
   ```bash
   make backup-remote
   ```
2. Destroy and rebuild the VPS from scratch at Hetzner Console.
3. Run `make deploy HOST=user@new-vps-ip` from your local machine.
4. Restore data from backup (see Section 8).
5. Set all new credentials in `.env`.

### Step 5 — Post-incident

- Rotate all secrets (see Section 11).
- Remove the kill switch: `docker run --rm -v $(docker volume ls -q | grep openclaw_data):/data busybox rm -f /data/GUARDRAIL_DISABLE`
- `make restart && make doctor`
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbook.md
git commit -m "docs: add secret rotation (section 11) and compromise response (section 12) to runbook"
```

---

## Final Verification

- [ ] Run full test suite:

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all tests pass, no regressions.

- [ ] Push:

```bash
git push
```

- [ ] Deploy to VPS and verify doctor output:

```bash
git pull && docker compose up -d
make doctor
```

Expected: new System section visible with swap + NODE_OPTIONS checks, and `ALERT_TELEGRAM_CHAT_ID` in the .env section.

---

## Enabling Alerts (post-deploy)

To activate Telegram alerts, add to `.env` on the VPS:

```bash
ALERT_TELEGRAM_CHAT_ID=<your chat ID>
```

To find your chat ID: message [@userinfobot](https://t.me/userinfobot) on Telegram — it replies with your numeric ID.

Then restart the container to pick it up:

```bash
docker compose up -d
```

No code changes needed — alerting is already wired; `ALERT_TELEGRAM_CHAT_ID` being set is the only switch.
