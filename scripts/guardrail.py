#!/usr/bin/env python3
"""
OpenClaw execution guardrail.

Observes structured JSON logs from OpenClaw and enforces per-session limits.
Abort mechanism: kill -TERM <openclaw_pid> — kills ALL sessions (Phase 1 limitation).
No per-session abort available (openclaw session abort does not exist).

Actual log format (discovered 2026-03-02):
  All events: {"type": "log", "time": "<ISO-8601>", "level": "...", "subsystem": "...", "message": "...", "raw": "..."}
  Session ID: embedded in message as sessionId=<value> — regex-extracted, NOT a top-level field
  Session register: subsystem="diagnostic", message contains "run registered:"
  Session clear:    subsystem="diagnostic", message contains "run cleared:"
  LLM turn start:   subsystem="agent/embedded", message contains "embedded run start:"
  LLM turn done:    subsystem="agent/embedded", message contains "embedded run done:"
  Tool calls:       NOT OBSERVED in log sample — MAX_TOOL_CALLS not enforced (TODO)
"""

import os
import sys
import json
import re
import select
import time
import signal
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

# ── Log format constants ──────────────────────────────────────────────────────
# All events that matter have type="log"
LOG_TYPE = "log"
TYPE_FIELD = "type"
SUBSYSTEM_FIELD = "subsystem"
MESSAGE_FIELD = "message"
TIMESTAMP_FIELD = "time"  # ISO-8601 string

# Session ID regex — extracted from message string
SESSION_ID_RE = re.compile(r'sessionId=(\S+)')

# Event classifiers (subsystem + message content)
SUBSYSTEM_DIAGNOSTIC = "diagnostic"
SUBSYSTEM_AGENT = "agent/embedded"

MSG_SESSION_REGISTER = "run registered:"
MSG_SESSION_CLEAR = "run cleared:"
MSG_LLM_START = "embedded run start:"
MSG_LLM_DONE = "embedded run done:"

# ── Config ────────────────────────────────────────────────────────────────────
KILL_SWITCH_PATH = os.getenv("KILL_SWITCH_PATH", "/home/node/.openclaw/GUARDRAIL_DISABLE")


def parse_timestamp(ts_str: str) -> float:
    """Parse ISO-8601 timestamp string to unix float."""
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00')).timestamp()
    except (ValueError, AttributeError):
        return time.time()


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
        self.max_tool_calls = int(os.getenv("MAX_TOOL_CALLS", "50"))   # not currently enforced (tool events not observed)
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
        except FileNotFoundError:
            print("[guardrail] ERROR: pgrep not found — PID detection disabled", flush=True)
            return None
        except Exception as e:
            print(f"[guardrail] WARNING: pgrep failed: {e}", flush=True)
            return None

    # ── Kill switch ───────────────────────────────────────────────────────────

    def check_kill_switch(self):
        if os.path.exists(KILL_SWITCH_PATH):
            print(f"[guardrail] KILL SWITCH active ({KILL_SWITCH_PATH}) — terminating OpenClaw", flush=True)
            self.kill_openclaw()
            sys.exit(0)

    # ── Abort ─────────────────────────────────────────────────────────────────

    def kill_openclaw(self):
        """Kill the OpenClaw process. Drops ALL active sessions."""
        pid = self.openclaw_pid or self.find_openclaw_pid()
        if not pid:
            print("[guardrail] Cannot find OpenClaw PID — cannot abort", flush=True)
            return

        print(f"[guardrail] Sending SIGTERM to pid={pid}", flush=True)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        time.sleep(10)

        try:
            os.kill(pid, signal.SIGKILL)
            print(f"[guardrail] Sent SIGKILL to pid={pid} (did not exit after SIGTERM)", flush=True)
        except ProcessLookupError:
            pass

    # ── Limit checking ────────────────────────────────────────────────────────

    def check_limits(self, session: SessionState, now: float) -> Optional[str]:
        elapsed = now - session.start_time

        if elapsed > self.max_session_seconds:
            return f"session time limit ({elapsed:.0f}s > {self.max_session_seconds}s)"

        if session.llm_count >= self.max_llm_calls:
            return f"llm call limit ({session.llm_count} >= {self.max_llm_calls})"

        # Idle check: only if last_event_time is meaningfully in the past relative to now
        idle = now - session.last_event_time
        if idle > self.max_idle_seconds:
            return f"idle timeout ({idle:.0f}s > {self.max_idle_seconds}s)"

        return None

    # ── Session ID extraction ─────────────────────────────────────────────────

    @staticmethod
    def extract_session_id(message: str) -> Optional[str]:
        m = SESSION_ID_RE.search(message)
        return m.group(1) if m else None

    # ── Event processing ──────────────────────────────────────────────────────

    def process_event(self, event: dict):
        # Only process log-type events
        if event.get(TYPE_FIELD) != LOG_TYPE:
            return

        subsystem = event.get(SUBSYSTEM_FIELD, "")
        message = event.get(MESSAGE_FIELD, "")

        session_id = self.extract_session_id(message)
        if not session_id:
            return

        # Use wall-clock time for all session tracking so that limit checks
        # and prune_sessions() operate consistently against real time.
        wall_now = time.time()

        # Session end — clean up and return
        if subsystem == SUBSYSTEM_DIAGNOSTIC and MSG_SESSION_CLEAR in message:
            self.sessions.pop(session_id, None)
            return

        # Session register — create state
        if subsystem == SUBSYSTEM_DIAGNOSTIC and MSG_SESSION_REGISTER in message:
            if session_id not in self.sessions:
                self.sessions[session_id] = SessionState(
                    session_id=session_id,
                    start_time=wall_now,
                    last_event_time=wall_now,
                )
            return

        # LLM events — ensure session exists, increment counter
        if subsystem == SUBSYSTEM_AGENT and MSG_LLM_START in message:
            if session_id not in self.sessions:
                self.sessions[session_id] = SessionState(
                    session_id=session_id,
                    start_time=wall_now,
                    last_event_time=wall_now,
                )
            session = self.sessions[session_id]
            session.last_event_time = wall_now
            session.llm_count += 1

            violation = self.check_limits(session, wall_now)
            if violation:
                print(f"[guardrail] VIOLATION session={session_id}: {violation}", flush=True)
                self.sessions.pop(session_id, None)
                self.kill_openclaw()  # blocks event loop for up to 10s (SIGTERM wait)
            return

        if subsystem == SUBSYSTEM_AGENT and MSG_LLM_DONE in message:
            session = self.sessions.get(session_id)
            if session:
                session.last_event_time = wall_now
            return

    # ── Pruning ───────────────────────────────────────────────────────────────

    def prune_sessions(self, now: float):
        """Remove sessions with no events for 2× idle window."""
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
                if limit >= 2 ** 62:
                    return
            except Exception:
                return
        except Exception:
            return

        pct = (current / limit) * 100
        if pct > self.max_memory_pct:
            print(f"[guardrail] MEMORY THRESHOLD {pct:.1f}% > {self.max_memory_pct}% — terminating", flush=True)
            self.kill_openclaw()  # blocks event loop for up to 10s (SIGTERM wait)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        print("[guardrail] Starting", flush=True)
        print(
            f"[guardrail] Limits: "
            f"session={self.max_session_seconds}s "
            f"llm={self.max_llm_calls} "
            f"idle={self.max_idle_seconds}s "
            f"memory={self.max_memory_pct}% "
            f"tool_calls={self.max_tool_calls} (not enforced — tool events not observed)",
            flush=True,
        )
        self.openclaw_pid = self.find_openclaw_pid()

        try:
            proc = subprocess.Popen(
                ["openclaw", "logs", "--follow", "--json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            print("[guardrail] ERROR: 'openclaw' not found on PATH — cannot start log monitor", flush=True)
            raise

        last_watchdog = time.time()

        while True:
            now = time.time()

            self.check_kill_switch()

            if now - last_watchdog > 5:
                self.openclaw_pid = self.find_openclaw_pid()
                self.check_memory()
                self.prune_sessions(now)
                last_watchdog = now

            # Wait up to 5s for a log line so the watchdog runs on idle systems.
            ready, _, _ = select.select([proc.stdout], [], [], 5.0)
            if not ready:
                continue

            line = proc.stdout.readline()
            if not line:
                break  # EOF — subprocess exited

            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
                self.process_event(event)
            except json.JSONDecodeError:
                pass


if __name__ == "__main__":
    Guardrail().run()
