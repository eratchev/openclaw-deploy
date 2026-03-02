# OpenClaw Deploy — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A hardened, internet-facing OpenClaw deployment on a single VPS with a log-driven execution guardrail — publishable as an open-source template.

**Architecture:** OpenClaw Gateway daemon runs in a hardened Docker container (official image, UID 1000, cap_drop ALL, read_only rootfs). Caddy terminates TLS. Two Docker networks enforce that Redis is never reachable from the internet. A Python guardrail process runs in the same container, observes structured JSON logs, and kills the process on session limit violations.

**Tech Stack:** Docker Compose, Caddy, OpenClaw (`ghcr.io/openclaw/openclaw`), Redis 7, Python 3.11 (guardrail), UFW, Fail2ban, pytest

**Design doc:** `docs/plans/2026-03-02-openclaw-deploy-design.md`

---

## Critical Prerequisites

Before any implementation, verify two things locally (requires a running OpenClaw instance):

**A) Log format discovery** — Run `openclaw logs --follow --json` and capture 10–20 lines. You need exact field names for: session identifier, event type, tool invocations, LLM invocations, session start/end. Save a sample to `docs/log-samples.jsonl`. The guardrail parser depends entirely on this.

**B) OpenClaw config path** — Confirm OpenClaw's config and workspace live at `/home/node/.openclaw` inside the container (since the `node` user's home is `/home/node`). Run `docker run --rm ghcr.io/openclaw/openclaw:latest sh -c "echo ~node"` to verify.

Do not write the guardrail parser (Task 6+) until you have real log samples.

---

## Task 1: Repo Scaffold

**Files:**
- Create: `README.md` (placeholder)
- Create: `.gitignore`
- Create: `.env.example`
- Create: `docs/.gitkeep`

**Step 1: Initialize git**

```bash
cd /Users/evgueni/repos/personal/openclaw-deploy
git init
```

**Step 2: Create `.gitignore`**

```
.env
*.log
__pycache__/
.pytest_cache/
*.pyc
.DS_Store
```

**Step 3: Create `.env.example`**

```bash
# === OpenClaw ===
# Telegram bot token from @BotFather
TELEGRAM_TOKEN=

# WhatsApp credentials (set via openclaw onboard, stored in /data)
# No token needed here — WhatsApp session is initialized interactively

# === LLM ===
# Anthropic API key (recommended: claude-opus-4-6)
ANTHROPIC_API_KEY=

# OpenAI API key (optional fallback)
OPENAI_API_KEY=

# === Redis ===
# Generate with: openssl rand -hex 32
REDIS_PASSWORD=

# === Guardrail ===
MAX_SESSION_SECONDS=300
MAX_TOOL_CALLS=50
MAX_LLM_CALLS=30
MAX_IDLE_SECONDS=60
MAX_MEMORY_PCT=90

# === Caddy ===
# Your domain name (must point to this VPS)
DOMAIN=assistant.yourdomain.com
```

**Step 4: Create placeholder README**

```bash
echo "# openclaw-deploy\n\nSetup instructions coming soon." > README.md
```

**Step 5: Commit**

```bash
git add .gitignore .env.example README.md
git commit -m "feat: repo scaffold with .gitignore and .env.example"
```

---

## Task 2: docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

**Step 1: Write docker-compose.yml**

```yaml
version: "3.8"

networks:
  ingress:
    driver: bridge
  internal:
    driver: bridge
    internal: true

volumes:
  openclaw_data:
  redis_data:

services:
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    networks:
      - ingress
    depends_on:
      - openclaw

  openclaw:
    image: ghcr.io/openclaw/openclaw:latest
    entrypoint: ["/entrypoint.sh"]
    user: "1000:1000"
    read_only: true
    tmpfs:
      - /tmp
    volumes:
      - openclaw_data:/home/node/.openclaw
      - ./entrypoint.sh:/entrypoint.sh:ro
      - ./scripts/guardrail.py:/app/guardrail.py:ro
    env_file:
      - .env
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
      - seccomp:default
    mem_limit: 2g
    cpus: 1.5
    pids_limit: 256
    restart: unless-stopped
    networks:
      - ingress
      - internal
    depends_on:
      - redis

  redis:
    image: redis:7-alpine
    command: >
      redis-server
      --requirepass ${REDIS_PASSWORD}
      --bind 0.0.0.0
      --protected-mode yes
      --save 60 1
    restart: unless-stopped
    volumes:
      - redis_data:/data
    networks:
      - internal
    mem_limit: 256m
    cpus: 0.5

volumes:
  caddy_data:
  caddy_config:
```

**Step 2: Verify network isolation is explicit**

Check the compose file — confirm:
- `caddy` has only `ingress` (no `internal`)
- `redis` has only `internal` (no `ingress`)
- `openclaw` has both

**Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add hardened docker-compose with network segmentation"
```

---

## Task 3: Caddyfile

**Files:**
- Create: `Caddyfile`

**Step 1: Write Caddyfile**

```caddyfile
{$DOMAIN} {
    reverse_proxy openclaw:18789
}
```

Notes:
- Port `18789` is OpenClaw Gateway's default WebSocket/HTTP port
- Caddy auto-provisions TLS via ACME — no cert config needed
- `{$DOMAIN}` reads from the `DOMAIN` env var (set in `.env`)

**Step 2: Verify port**

Run `docker run --rm ghcr.io/openclaw/openclaw:latest openclaw gateway --help 2>&1 | grep port` to confirm `18789` is the default. If different, update the Caddyfile.

**Step 3: Commit**

```bash
git add Caddyfile
git commit -m "feat: add Caddy reverse proxy config with auto-TLS"
```

---

## Task 4: entrypoint.sh

**Files:**
- Create: `entrypoint.sh`

**Step 1: Write entrypoint.sh**

```bash
#!/bin/sh
set -e

echo "[entrypoint] Starting guardrail supervisor..."

# Supervised restart loop — guardrail must never silently disappear
while true; do
  python3 /app/guardrail.py
  echo "[entrypoint] guardrail exited (code $?), restarting in 5s..."
  sleep 5
done &

echo "[entrypoint] Starting OpenClaw Gateway..."
exec openclaw gateway --port 18789
```

**Step 2: Make it executable locally (for inspection)**

```bash
chmod +x entrypoint.sh
```

Note: the `:ro` mount in compose means the container sees it read-only. `chmod` here is for local clarity; the exec bit must be set before the image builds or the entrypoint must be invoked explicitly. Since we're using `entrypoint: ["/entrypoint.sh"]` in compose, Docker calls it via sh if the bit isn't set — add an explicit shebang check:

If the container errors on `permission denied`, change compose to:
```yaml
entrypoint: ["sh", "/entrypoint.sh"]
```

**Step 3: Commit**

```bash
git add entrypoint.sh
git commit -m "feat: add entrypoint with supervised guardrail restart loop"
```

---

## Task 5: Discover OpenClaw Log Format

**Files:**
- Create: `docs/log-samples.jsonl` (gitignored — may contain personal data)
- Update: `.gitignore`

This task is research. Do not skip it. The guardrail parser is useless without knowing the real field names.

**Step 1: Add log samples to .gitignore**

```
docs/log-samples.jsonl
```

**Step 2: Capture live log output**

On a running OpenClaw instance (local or dev VPS):

```bash
openclaw logs --follow --json 2>&1 | head -50 > docs/log-samples.jsonl
```

Trigger a few interactions: send a message, invoke a tool, let a session complete.

**Step 3: Document what you find**

Open `docs/log-samples.jsonl` and answer these questions — write answers as comments at the top of `scripts/guardrail.py`:

```python
# Log format discovery (run: openclaw logs --follow --json)
# Session ID field: ???         e.g. "session_id", "sid", "session"
# Event type field: ???         e.g. "type", "event", "kind"
# Tool invocation type value: ??? e.g. "tool.call", "tool_use", "tool"
# LLM invocation type value: ???  e.g. "llm.call", "model.request", "agent"
# Session start type value: ???   e.g. "session.start", "session_start"
# Session end type value: ???     e.g. "session.end", "session.complete"
# Timestamp field: ???            e.g. "ts", "timestamp", "time"
#
# Sample event (redact personal data):
# {"type": "...", "session_id": "...", ...}
```

**Step 4: Update .gitignore and commit**

```bash
git add .gitignore
git commit -m "chore: gitignore log samples (may contain personal data)"
```

---

## Task 6: guardrail.py — Tests First

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_guardrail.py`
- Create: `requirements-dev.txt`

**Step 1: Create requirements-dev.txt**

```
pytest>=8.0
```

**Step 2: Install**

```bash
pip install -r requirements-dev.txt
```

**Step 3: Write failing tests**

Use the field names discovered in Task 5. Replace `SESSION_FIELD`, `TYPE_FIELD`, `TOOL_TYPE`, `LLM_TYPE`, `END_TYPE` with actual values.

```python
# tests/test_guardrail.py
import time
import pytest
from unittest.mock import patch, MagicMock
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from scripts.guardrail import Guardrail, SessionState

# ── Helpers ──────────────────────────────────────────────────────────────────

def make_session(tool_count=0, llm_count=0, age_seconds=0, idle_seconds=0):
    now = time.time()
    s = SessionState(
        session_id="test-session",
        start_time=now - age_seconds,
        tool_count=tool_count,
        llm_count=llm_count,
        last_event_time=now - idle_seconds if idle_seconds else now - age_seconds + 1,
    )
    return s, now

def make_event(session_id="s1", event_type="log", **kwargs):
    """Build a minimal log event using the field names from Task 5."""
    # TODO: replace "type" and "session_id" with actual field names from log discovery
    return {"type": event_type, "session_id": session_id, "ts": time.time(), **kwargs}

# ── check_limits ─────────────────────────────────────────────────────────────

def test_no_violation_under_limits():
    g = Guardrail()
    session, now = make_session(tool_count=5, llm_count=3)
    assert g.check_limits(session, now) is None

def test_tool_call_violation():
    g = Guardrail()
    session, now = make_session(tool_count=g.max_tool_calls + 1)
    result = g.check_limits(session, now)
    assert result is not None
    assert "tool" in result.lower()

def test_llm_call_violation():
    g = Guardrail()
    session, now = make_session(llm_count=g.max_llm_calls + 1)
    result = g.check_limits(session, now)
    assert result is not None
    assert "llm" in result.lower()

def test_session_time_violation():
    g = Guardrail()
    session, now = make_session(age_seconds=g.max_session_seconds + 1)
    result = g.check_limits(session, now)
    assert result is not None
    assert "time" in result.lower()

def test_idle_timeout_violation():
    g = Guardrail()
    session, now = make_session(age_seconds=30, idle_seconds=g.max_idle_seconds + 1)
    result = g.check_limits(session, now)
    assert result is not None
    assert "idle" in result.lower()

def test_no_idle_violation_at_session_start():
    """New session with no prior events should not trigger idle timeout."""
    g = Guardrail()
    session, now = make_session(age_seconds=0, idle_seconds=0)
    assert g.check_limits(session, now) is None

# ── process_event ─────────────────────────────────────────────────────────────

def test_process_event_creates_new_session():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw') as mock_kill:
        g.process_event(make_event(session_id="abc", event_type="tool.call"))
        assert "abc" in g.sessions
        mock_kill.assert_not_called()

def test_process_event_increments_tool_count():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw'):
        g.process_event(make_event(session_id="abc", event_type="tool.call"))
        g.process_event(make_event(session_id="abc", event_type="tool.call"))
        assert g.sessions["abc"].tool_count == 2

def test_process_event_increments_llm_count():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw'):
        g.process_event(make_event(session_id="abc", event_type="llm.call"))
        assert g.sessions["abc"].llm_count == 1

def test_process_event_completion_removes_session():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw'):
        g.process_event(make_event(session_id="abc", event_type="tool.call"))
        assert "abc" in g.sessions
        g.process_event(make_event(session_id="abc", event_type="session.complete"))
        assert "abc" not in g.sessions

def test_violation_calls_kill_openclaw():
    g = Guardrail()
    g.max_tool_calls = 2
    with patch.object(g, 'kill_openclaw') as mock_kill:
        g.process_event(make_event(session_id="abc", event_type="tool.call"))
        g.process_event(make_event(session_id="abc", event_type="tool.call"))
        g.process_event(make_event(session_id="abc", event_type="tool.call"))  # triggers violation
        mock_kill.assert_called_once()

# ── kill switch ───────────────────────────────────────────────────────────────

def test_kill_switch_triggers_when_file_exists(tmp_path):
    g = Guardrail()
    kill_switch = tmp_path / "GUARDRAIL_DISABLE"
    kill_switch.touch()
    with patch.object(g, 'kill_openclaw') as mock_kill, \
         patch('sys.exit') as mock_exit, \
         patch('scripts.guardrail.KILL_SWITCH_PATH', str(kill_switch)):
        g.check_kill_switch()
        mock_kill.assert_called_once()
        mock_exit.assert_called_once_with(0)

def test_kill_switch_no_trigger_when_file_absent(tmp_path):
    g = Guardrail()
    absent_path = str(tmp_path / "GUARDRAIL_DISABLE")
    with patch.object(g, 'kill_openclaw') as mock_kill, \
         patch('scripts.guardrail.KILL_SWITCH_PATH', absent_path):
        g.check_kill_switch()
        mock_kill.assert_not_called()

# ── prune_sessions ────────────────────────────────────────────────────────────

def test_prune_removes_stale_sessions():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw'):
        g.process_event(make_event(session_id="old", event_type="tool.call"))
        g.sessions["old"].last_event_time = time.time() - (g.max_idle_seconds * 3)
        g.prune_sessions(time.time())
        assert "old" not in g.sessions

def test_prune_keeps_active_sessions():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw'):
        g.process_event(make_event(session_id="active", event_type="tool.call"))
        g.prune_sessions(time.time())
        assert "active" in g.sessions
```

**Step 4: Run tests — expect all to fail**

```bash
pytest tests/test_guardrail.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'scripts.guardrail'` — correct, implementation doesn't exist yet.

**Step 5: Create empty module so import works**

```bash
touch scripts/__init__.py
```

**Step 6: Run tests again — expect import errors on Guardrail**

```bash
pytest tests/test_guardrail.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'Guardrail'`

**Step 7: Commit tests**

```bash
git add tests/ scripts/__init__.py requirements-dev.txt
git commit -m "test: add guardrail unit tests (all failing)"
```

---

## Task 7: guardrail.py — Implementation

**Files:**
- Create: `scripts/guardrail.py`

Update the `TYPE_FIELD`, `SESSION_FIELD`, `TOOL_TYPES`, `LLM_TYPES`, `END_TYPES` constants using the values discovered in Task 5.

**Step 1: Write scripts/guardrail.py**

```python
#!/usr/bin/env python3
"""
OpenClaw execution guardrail.

Observes structured JSON logs from OpenClaw and enforces per-session limits.
Abort mechanism: kill -TERM <openclaw_pid> — kills ALL sessions (Phase 1 limitation).
No per-session abort available (openclaw session abort does not exist).

Log format fields (verify with: openclaw logs --follow --json):
  Session ID field:          "session_id"     ← UPDATE from Task 5
  Event type field:          "type"           ← UPDATE from Task 5
  Tool invocation type:      "tool.call"      ← UPDATE from Task 5
  LLM invocation type:       "llm.call"       ← UPDATE from Task 5
  Session end type:          "session.complete" ← UPDATE from Task 5
  Timestamp field:           "ts"             ← UPDATE from Task 5
"""

import os
import sys
import json
import time
import signal
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Optional

# ── Log format constants (update from Task 5 discovery) ──────────────────────
SESSION_FIELD = "session_id"
TYPE_FIELD = "type"
TOOL_TYPES = {"tool.call", "tool_use", "tool"}       # all observed tool event types
LLM_TYPES = {"llm.call", "model.request", "agent"}   # all observed LLM event types
END_TYPES = {"session.complete", "session.end", "session_end"}
TIMESTAMP_FIELD = "ts"

# ── Config ────────────────────────────────────────────────────────────────────
KILL_SWITCH_PATH = os.getenv("KILL_SWITCH_PATH", "/home/node/.openclaw/GUARDRAIL_DISABLE")


# ── Data ──────────────────────────────────────────────────────────────────────
@dataclass
class SessionState:
    session_id: str
    start_time: float
    tool_count: int = 0
    llm_count: int = 0
    last_event_time: float = field(default_factory=time.time)


# ── Guardrail ─────────────────────────────────────────────────────────────────
class Guardrail:
    def __init__(self):
        self.sessions: Dict[str, SessionState] = {}
        self.openclaw_pid: Optional[int] = None

        # Limits (configurable via env)
        self.max_session_seconds = int(os.getenv("MAX_SESSION_SECONDS", "300"))
        self.max_tool_calls = int(os.getenv("MAX_TOOL_CALLS", "50"))
        self.max_llm_calls = int(os.getenv("MAX_LLM_CALLS", "30"))
        self.max_idle_seconds = int(os.getenv("MAX_IDLE_SECONDS", "60"))
        self.max_memory_pct = float(os.getenv("MAX_MEMORY_PCT", "90"))

    # ── PID detection ─────────────────────────────────────────────────────────

    def find_openclaw_pid(self) -> Optional[int]:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "openclaw gateway"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                pids = result.stdout.strip().split("\n")
                return int(pids[0])
        except Exception:
            pass
        return None

    # ── Kill switch ───────────────────────────────────────────────────────────

    def check_kill_switch(self):
        if os.path.exists(KILL_SWITCH_PATH):
            print(f"[guardrail] KILL SWITCH active ({KILL_SWITCH_PATH}) — terminating OpenClaw", flush=True)
            self.kill_openclaw()
            sys.exit(0)  # Do not restart

    # ── Abort ─────────────────────────────────────────────────────────────────

    def kill_openclaw(self):
        """
        Kill the OpenClaw process. Drops ALL active sessions.
        Docker restart policy will bring the container back up.
        """
        pid = self.openclaw_pid or self.find_openclaw_pid()
        if not pid:
            print("[guardrail] Cannot find OpenClaw PID — cannot abort", flush=True)
            return

        print(f"[guardrail] Sending SIGTERM to pid={pid}", flush=True)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return  # Already dead

        time.sleep(10)

        try:
            os.kill(pid, signal.SIGKILL)
            print(f"[guardrail] Sent SIGKILL to pid={pid} (did not exit after SIGTERM)", flush=True)
        except ProcessLookupError:
            pass  # Exited cleanly after SIGTERM

    # ── Limit checking ────────────────────────────────────────────────────────

    def check_limits(self, session: SessionState, now: float) -> Optional[str]:
        elapsed = now - session.start_time

        if elapsed > self.max_session_seconds:
            return f"session time limit ({elapsed:.0f}s > {self.max_session_seconds}s)"

        if session.tool_count > self.max_tool_calls:
            return f"tool call limit ({session.tool_count} > {self.max_tool_calls})"

        if session.llm_count > self.max_llm_calls:
            return f"llm call limit ({session.llm_count} > {self.max_llm_calls})"

        # Idle check: only applies once a session has had at least one event
        idle = now - session.last_event_time
        if session.last_event_time > session.start_time and idle > self.max_idle_seconds:
            return f"idle timeout ({idle:.0f}s > {self.max_idle_seconds}s)"

        return None

    # ── Event processing ──────────────────────────────────────────────────────

    def process_event(self, event: dict):
        session_id = event.get(SESSION_FIELD)
        event_type = event.get(TYPE_FIELD, "")
        ts = event.get(TIMESTAMP_FIELD) or time.time()
        now = float(ts) if isinstance(ts, (int, float)) else time.time()

        if not session_id or not event_type:
            return

        # End event — clean up and return
        if event_type in END_TYPES:
            self.sessions.pop(session_id, None)
            return

        # Create session state if new
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState(
                session_id=session_id,
                start_time=now,
                last_event_time=now,
            )

        session = self.sessions[session_id]
        session.last_event_time = now

        if event_type in TOOL_TYPES:
            session.tool_count += 1
        elif event_type in LLM_TYPES:
            session.llm_count += 1

        violation = self.check_limits(session, now)
        if violation:
            print(f"[guardrail] VIOLATION session={session_id}: {violation}", flush=True)
            self.sessions.pop(session_id, None)
            self.kill_openclaw()

    # ── Pruning ───────────────────────────────────────────────────────────────

    def prune_sessions(self, now: float):
        """Remove sessions with no events for 2× idle window (likely dead without a clean end event)."""
        cutoff = self.max_idle_seconds * 2
        stale = [sid for sid, s in self.sessions.items()
                 if now - s.last_event_time > cutoff]
        for sid in stale:
            print(f"[guardrail] Pruning stale session={sid}", flush=True)
            del self.sessions[sid]

    # ── Memory watchdog ───────────────────────────────────────────────────────

    def check_memory(self):
        """Read container memory usage from cgroups (v2 then v1 fallback)."""
        try:
            with open("/sys/fs/cgroup/memory.current") as f:
                current = int(f.read().strip())
            with open("/sys/fs/cgroup/memory.max") as f:
                raw = f.read().strip()
                if raw == "max":
                    return
                limit = int(raw)
        except FileNotFoundError:
            try:
                with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as f:
                    current = int(f.read().strip())
                with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as f:
                    limit = int(f.read().strip())
                if limit >= 2 ** 62:  # unlimited sentinel
                    return
            except Exception:
                return
        except Exception:
            return

        pct = (current / limit) * 100
        if pct > self.max_memory_pct:
            print(f"[guardrail] MEMORY THRESHOLD {pct:.1f}% > {self.max_memory_pct}% — terminating", flush=True)
            self.kill_openclaw()

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        print("[guardrail] Starting", flush=True)
        self.openclaw_pid = self.find_openclaw_pid()

        proc = subprocess.Popen(
            ["openclaw", "logs", "--follow", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        last_watchdog = time.time()

        for line in proc.stdout:
            now = time.time()

            self.check_kill_switch()

            if now - last_watchdog > 5:
                self.openclaw_pid = self.find_openclaw_pid()
                self.check_memory()
                self.prune_sessions(now)
                last_watchdog = now

            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
                self.process_event(event)
            except json.JSONDecodeError:
                pass  # Non-JSON line (startup messages etc.) — ignore


if __name__ == "__main__":
    Guardrail().run()
```

**Step 2: Run all tests**

```bash
pytest tests/test_guardrail.py -v
```

Expected: all tests pass. If `make_event` field names don't match the implementation constants, update `make_event` in the test file to match what you discovered in Task 5.

**Step 3: Run with specific test cases to verify edge cases**

```bash
pytest tests/test_guardrail.py -v -k "violation"
pytest tests/test_guardrail.py -v -k "kill_switch"
```

Both groups should pass.

**Step 4: Commit**

```bash
git add scripts/guardrail.py scripts/__init__.py
git commit -m "feat: add log-driven execution guardrail with session limits and process kill"
```

---

## Task 8: provision.sh

**Files:**
- Create: `scripts/provision.sh`

**Step 1: Write scripts/provision.sh**

```bash
#!/usr/bin/env bash
# OpenClaw VPS provisioning script
# Run once as root on a fresh Ubuntu LTS VPS.
# Idempotent — safe to run multiple times.
set -euo pipefail

echo "[provision] Starting VPS hardening..."

# ── System updates ────────────────────────────────────────────────────────────
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q \
  ufw fail2ban unattended-upgrades curl git python3 \
  apt-transport-https ca-certificates gnupg

# ── Unattended security upgrades ──────────────────────────────────────────────
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades

# ── SSH hardening ─────────────────────────────────────────────────────────────
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
systemctl reload ssh

# ── UFW inbound rules ─────────────────────────────────────────────────────────
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
ufw allow 443/tcp comment "HTTPS (Caddy)"
ufw --force enable
echo "[provision] UFW inbound rules applied."

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

# ── Fail2ban ──────────────────────────────────────────────────────────────────
systemctl enable --now fail2ban
echo "[provision] Fail2ban enabled."

# ── Docker install ────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
  echo "[provision] Docker installed."
else
  echo "[provision] Docker already installed."
fi

# ── /data volume permissions ─────────────────────────────────────────────────
# Ensure openclaw_data volume is owned by UID 1000 (node user).
# Run this after `docker compose up` has created the volume.
echo "[provision] To fix /data permissions after first compose up, run:"
echo "  docker run --rm -v openclaw-deploy_openclaw_data:/home/node/.openclaw busybox chown -R 1000:1000 /home/node/.openclaw"

echo "[provision] Done. Reboot recommended before starting services."
```

**Step 2: Make executable**

```bash
chmod +x scripts/provision.sh
```

**Step 3: Commit**

```bash
git add scripts/provision.sh
git commit -m "feat: add idempotent VPS provisioning script with UFW and fail2ban"
```

---

## Task 9: Makefile

**Files:**
- Create: `Makefile`

**Step 1: Write Makefile**

```makefile
.PHONY: up down logs restart backup update status test

# Start all services
up:
	docker compose up -d

# Stop all services
down:
	docker compose down

# Follow OpenClaw logs
logs:
	docker compose logs -f openclaw

# Follow all logs
logs-all:
	docker compose logs -f

# Restart OpenClaw only
restart:
	docker compose restart openclaw

# Show container resource usage
status:
	docker stats --no-stream

# Backup /data volume to ./backups/
backup:
	mkdir -p backups
	docker run --rm \
		-v openclaw-deploy_openclaw_data:/source:ro \
		-v $(PWD)/backups:/backup \
		busybox tar czf /backup/openclaw-data-$(shell date +%Y%m%d-%H%M%S).tar.gz -C /source .
	@echo "Backup saved to ./backups/"

# Pull latest image and restart
update:
	docker compose pull openclaw
	docker compose up -d --no-deps openclaw
	@echo "OpenClaw updated. Check logs: make logs"

# Run guardrail unit tests
test:
	pytest tests/ -v

# Trigger manual kill switch
kill-switch:
	@echo "Activating kill switch..."
	docker compose exec openclaw touch /home/node/.openclaw/GUARDRAIL_DISABLE
	@echo "Kill switch activated. OpenClaw will terminate within 5s."
	@echo "To resume: docker compose exec openclaw rm /home/node/.openclaw/GUARDRAIL_DISABLE && make up"
```

**Step 2: Verify make commands parse correctly**

```bash
make --dry-run up
make --dry-run backup
```

Expected: commands printed, no errors.

**Step 3: Commit**

```bash
git add Makefile
git commit -m "feat: add Makefile with up/down/logs/backup/update/test/kill-switch targets"
```

---

## Task 10: Documentation

**Files:**
- Modify: `README.md`
- Create: `docs/architecture.md`
- Create: `docs/threat-model.md`
- Create: `docs/security-checklist.md`
- Create: `docs/execution-guardrails.md`
- Create: `docs/upgrade-path.md`

**Step 1: Write docs/architecture.md**

Copy the ASCII diagram from the design doc. Add:
- Network table (ingress/internal matrix)
- Description of each service's role
- Why the two-network separation matters

**Step 2: Write docs/threat-model.md**

Must include these sections explicitly:

```markdown
## What This Deployment Protects Against
## What It Does NOT Protect Against
## Known Gaps
### Gap 1 — Outbound Egress Unrestricted (Phase 1)
### Gap 2 — /data Compromise
> Container compromise = /data compromise. If OpenClaw is exploited via
> a malicious skill, prompt injection, or tool abuse, an attacker can
> write arbitrary files to /data. These files persist across restarts.
### Gap 3 — No Skill/Runtime Isolation
### Gap 4 — No Per-Session Abort
> Guardrail kills the entire process, dropping all active sessions.
## Assumptions
## Deployment Risks
```

**Step 3: Write docs/security-checklist.md**

Pre-launch checklist format:

```markdown
- [ ] `.env` is NOT committed to git (`git status` shows nothing)
- [ ] `REDIS_PASSWORD` is set and non-empty
- [ ] `DOMAIN` points to this VPS in DNS
- [ ] VPS: only ports 22 and 443 open (`ufw status`)
- [ ] SSH: password auth disabled (`grep PasswordAuthentication /etc/ssh/sshd_config`)
- [ ] Fail2ban running (`systemctl status fail2ban`)
- [ ] OpenClaw container running as UID 1000 (`docker compose exec openclaw id`)
- [ ] Container caps dropped (`docker inspect openclaw-deploy-openclaw-1 | grep CapDrop`)
- [ ] Redis not reachable from host (`nc -zv localhost 6379` should fail)
- [ ] Resource limits in effect (`docker stats` shows mem limit)
- [ ] Guardrail running (`docker compose exec openclaw ps aux | grep guardrail`)
- [ ] /data owned by 1000:1000 (`docker compose exec openclaw ls -la /home/node/`)
- [ ] Telegram webhook responding (send a message to your bot)
- [ ] WhatsApp connected (check openclaw status)
- [ ] Logs flowing (`make logs` shows activity)
```

**Step 4: Write docs/execution-guardrails.md**

Include:
- What the guardrail monitors and why
- All limit env vars with defaults and guidance for tuning
- Abort behavior (process-level, all sessions drop)
- How to disable temporarily (`GUARDRAIL_DISABLE` file)
- How to tune limits for your usage pattern
- Known limitations (no per-session abort, loop not triggering mem limits)

**Step 5: Write docs/upgrade-path.md**

Include:
- How to update OpenClaw (`make update`)
- How to back up `/data` before upgrading (`make backup`)
- How to verify the upgrade worked
- Rollback procedure (re-pull previous image tag)
- Warning: treat backups as tainted if compromise suspected

**Step 6: Write README.md**

Structure:
```markdown
# openclaw-deploy

> Hardened single-VPS deployment of OpenClaw with execution guardrails.

## What This Is
## What This Is NOT
## Prerequisites
## Quickstart (5 steps)
## Security Model (link to docs/threat-model.md)
## Guardrails (link to docs/execution-guardrails.md)
## Upgrading (link to docs/upgrade-path.md)
## Pre-launch Checklist (link to docs/security-checklist.md)
```

**Step 7: Commit all docs**

```bash
git add README.md docs/
git commit -m "docs: add architecture, threat model, security checklist, guardrails, upgrade path"
```

---

## Task 11: End-to-End Verification

**No files.** Manual verification against the success criteria from the design doc.

**Step 1: Start the stack**

```bash
make up
docker compose ps  # all containers should be "Up"
```

**Step 2: Verify network isolation**

```bash
# Redis should NOT be reachable from the caddy container
docker compose exec caddy sh -c "nc -zv redis 6379" 2>&1
# Expected: connection refused or name not resolved
```

**Step 3: Verify OpenClaw runs non-root**

```bash
docker compose exec openclaw id
# Expected: uid=1000(node) gid=1000(node)
```

**Step 4: Verify resource limits are enforced**

```bash
docker stats --no-stream
# Expected: MEM LIMIT shows 2GiB for openclaw, not 0B
```

**Step 5: Verify Redis requires auth**

```bash
docker compose exec redis redis-cli ping
# Expected: NOAUTH Authentication required
```

**Step 6: Verify guardrail is running**

```bash
docker compose exec openclaw ps aux | grep guardrail
# Expected: guardrail.py process visible
```

**Step 7: Verify /data permissions**

```bash
docker compose exec openclaw ls -la /home/node/
# Expected: .openclaw directory owned by 1000:1000
```

**Step 8: Run unit tests**

```bash
make test
# Expected: all tests pass
```

**Step 9: Test kill switch**

```bash
make kill-switch
# Wait 10 seconds
docker compose ps
# Expected: openclaw restarted (Docker restart policy)
```

**Step 10: Send a test message via Telegram**

Configure Telegram, send a message to your bot, verify response.

**Step 11: Final commit**

```bash
git add -p  # review any remaining changes
git commit -m "chore: end-to-end verification complete"
```

---

## Implementation Order

```
Task 1  → Task 2 → Task 3 → Task 4     # Foundation
Task 5                                   # REQUIRED before Task 6/7
Task 6  → Task 7                        # Guardrail (TDD)
Task 8  → Task 9                        # Ops
Task 10                                  # Docs
Task 11                                  # Verify
```

Tasks 8, 9, 10 are independent of each other after Task 7 completes.

---

## Open Questions for Task 5

Answers to these determine the exact constants in `guardrail.py`:

1. What is the field name for session ID in log events?
2. What is the field name for event type?
3. What string values indicate tool invocation events?
4. What string values indicate LLM invocation events?
5. What string values indicate session end/completion?
6. What is the timestamp field name and format (unix float, ISO string)?
