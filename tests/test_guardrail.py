"""
Unit tests for scripts/guardrail.py

Log format note: OpenClaw logs are JSONL with these fields:
  type="log", time=ISO8601, level, subsystem, message, raw
Session IDs are embedded in the message as sessionId=<value> (regex-extracted).
Tool events use runId=<value> instead; the runId→sessionId mapping is built from LLM start events.
Events are identified by subsystem + message content, not a type field.
"""
import subprocess
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

def make_log_event(subsystem, message, session_id=None, level="debug"):
    """Build a realistic OpenClaw log event."""
    msg = message
    if session_id and "sessionId=" not in message:
        msg = f"{message} sessionId={session_id}"
    return {
        "type": "log",
        "time": "2026-03-02T23:39:48.579Z",
        "level": level,
        "subsystem": subsystem,
        "message": msg,
        "raw": msg,
    }

def make_session_register(session_id="s1"):
    return make_log_event("diagnostic", f"run registered: sessionId={session_id}", session_id=None)

def make_session_clear(session_id="s1"):
    return make_log_event("diagnostic", f"run cleared: sessionId={session_id}", session_id=None)

def make_llm_start(session_id="s1", run_id="abc"):
    return make_log_event("agent/embedded", f"embedded run start: runId={run_id} sessionId={session_id} provider=openai model=gpt-4", session_id=None)

def make_llm_done(session_id="s1", run_id="abc"):
    return make_log_event("agent/embedded", f"embedded run done: runId={run_id} sessionId={session_id}", session_id=None)

def make_tool_start(run_id="abc", tool_name="bash"):
    """Tool events have runId but NOT sessionId."""
    return make_log_event("agent/embedded", f"embedded run tool start: runId={run_id} tool={tool_name} toolCallId=tc1", session_id=None)

# ── check_limits ─────────────────────────────────────────────────────────────

def test_no_violation_under_limits():
    g = Guardrail()
    session, now = make_session(llm_count=3)
    assert g.check_limits(session, now) is None

def test_llm_call_violation():
    g = Guardrail()
    session, now = make_session(llm_count=g.max_llm_calls)
    result = g.check_limits(session, now)
    assert result is not None
    assert "llm" in result.lower()

def test_llm_call_no_violation_one_under_limit():
    g = Guardrail()
    session, now = make_session(llm_count=g.max_llm_calls - 1)
    assert g.check_limits(session, now) is None

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

def test_session_register_creates_session():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw') as mock_kill:
        g.process_event(make_session_register("abc"))
        assert "abc" in g.sessions
        mock_kill.assert_not_called()

def test_llm_start_increments_llm_count():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw'):
        g.process_event(make_session_register("abc"))
        g.process_event(make_llm_start("abc"))
        g.process_event(make_llm_start("abc"))
        assert g.sessions["abc"].llm_count == 2

def test_session_clear_removes_session():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw'):
        g.process_event(make_session_register("abc"))
        assert "abc" in g.sessions
        g.process_event(make_session_clear("abc"))
        assert "abc" not in g.sessions

def test_violation_calls_kill_openclaw():
    g = Guardrail()
    g.max_llm_calls = 2
    with patch.object(g, 'kill_openclaw') as mock_kill:
        g.process_event(make_session_register("abc"))
        g.process_event(make_llm_start("abc"))  # count=1, no violation
        g.process_event(make_llm_done("abc"))
        g.process_event(make_llm_start("abc"))  # count=2 >= max=2, triggers violation
        mock_kill.assert_called_once()

def test_non_log_events_ignored():
    """Events with type != 'log' must be silently ignored."""
    g = Guardrail()
    with patch.object(g, 'kill_openclaw') as mock_kill:
        g.process_event({"type": "metric", "subsystem": "diagnostic", "message": "run registered: sessionId=abc"})
        assert "abc" not in g.sessions
        mock_kill.assert_not_called()

def test_event_without_session_id_ignored():
    """Diagnostic events that don't contain sessionId= in message must be silently ignored."""
    g = Guardrail()
    with patch.object(g, 'kill_openclaw') as mock_kill:
        g.process_event({"type": "log", "subsystem": "diagnostic", "message": "run registered: no session here", "time": "2026-03-02T23:39:48.579Z"})
        assert len(g.sessions) == 0

# ── tool call tracking ────────────────────────────────────────────────────────

def test_tool_call_violation():
    g = Guardrail()
    g.max_tool_calls = 3
    with patch.object(g, 'kill_openclaw') as mock_kill:
        g.process_event(make_session_register("abc"))
        g.process_event(make_llm_start("abc", run_id="r1"))
        g.process_event(make_tool_start("r1"))   # tool_count=1
        g.process_event(make_tool_start("r1"))   # tool_count=2
        mock_kill.assert_not_called()
        g.process_event(make_tool_start("r1"))   # tool_count=3 >= max=3, violation
        mock_kill.assert_called_once()

def test_tool_count_increments():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw'):
        g.process_event(make_session_register("abc"))
        g.process_event(make_llm_start("abc", run_id="r1"))
        g.process_event(make_tool_start("r1"))
        g.process_event(make_tool_start("r1"))
        assert g.sessions["abc"].tool_count == 2

def test_tool_call_no_violation_one_under_limit():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw') as mock_kill:
        g.process_event(make_session_register("abc"))
        g.process_event(make_llm_start("abc", run_id="r1"))
        for _ in range(g.max_tool_calls - 1):
            g.process_event(make_tool_start("r1"))
        mock_kill.assert_not_called()
        assert g.sessions["abc"].tool_count == g.max_tool_calls - 1

def test_tool_call_unknown_run_id_ignored():
    """Tool events with an unknown runId (no prior LLM start) must be silently ignored."""
    g = Guardrail()
    with patch.object(g, 'kill_openclaw') as mock_kill:
        g.process_event(make_tool_start("unknown-run"))
        assert len(g.sessions) == 0
        mock_kill.assert_not_called()

def test_runid_mapping_cleaned_up_on_llm_done():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw'):
        g.process_event(make_session_register("abc"))
        g.process_event(make_llm_start("abc", run_id="r1"))
        assert "r1" in g.runid_to_session
        g.process_event(make_llm_done("abc", run_id="r1"))
        assert "r1" not in g.runid_to_session

def test_runid_mapping_cleaned_up_on_session_clear():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw'):
        g.process_event(make_session_register("abc"))
        g.process_event(make_llm_start("abc", run_id="r1"))
        assert "r1" in g.runid_to_session
        g.process_event(make_session_clear("abc"))
        assert "r1" not in g.runid_to_session
        assert "abc" not in g.sessions

def test_tool_check_limits_violation():
    g = Guardrail()
    session, now = make_session(tool_count=g.max_tool_calls)
    result = g.check_limits(session, now)
    assert result is not None
    assert "tool" in result.lower()

def test_tool_check_limits_no_violation_one_under():
    g = Guardrail()
    session, now = make_session(tool_count=g.max_tool_calls - 1)
    assert g.check_limits(session, now) is None

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
        g.process_event(make_session_register("old"))
        g.sessions["old"].last_event_time = time.time() - (g.max_idle_seconds * 3)
        g.prune_sessions(time.time())
        assert "old" not in g.sessions

def test_prune_keeps_active_sessions():
    g = Guardrail()
    with patch.object(g, 'kill_openclaw'):
        g.process_event(make_session_register("active"))
        g.prune_sessions(time.time())
        assert "active" in g.sessions


# ── log subprocess restart ────────────────────────────────────────────────────

def test_restart_log_proc_if_stale_when_too_old():
    """Subprocess older than max age is terminated and replaced with a fresh one."""
    g = Guardrail()
    g.max_log_proc_seconds = 10

    old_proc = MagicMock()
    new_proc = MagicMock()
    new_proc.stdout = MagicMock()
    old_started = time.time() - 100  # well past the 10s limit

    with patch.object(g, '_start_log_proc', return_value=(new_proc, time.time())) as mock_start:
        returned_proc, returned_started = g._restart_log_proc_if_stale(old_proc, old_started)

    old_proc.terminate.assert_called_once()
    mock_start.assert_called_once()
    assert returned_proc is new_proc


def test_restart_log_proc_if_stale_when_fresh():
    """Subprocess within max age is left untouched."""
    g = Guardrail()
    g.max_log_proc_seconds = 1800

    fresh_proc = MagicMock()
    fresh_started = time.time()

    with patch.object(g, '_start_log_proc') as mock_start:
        returned_proc, returned_started = g._restart_log_proc_if_stale(fresh_proc, fresh_started)

    mock_start.assert_not_called()
    assert returned_proc is fresh_proc
    assert returned_started == fresh_started


def test_restart_log_proc_force_kills_if_terminate_hangs():
    """If the old subprocess ignores SIGTERM, SIGKILL is sent."""
    g = Guardrail()
    g.max_log_proc_seconds = 1

    old_proc = MagicMock()
    old_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="openclaw", timeout=5), None]
    new_proc = MagicMock()
    new_proc.stdout = MagicMock()
    old_started = time.time() - 100

    with patch.object(g, '_start_log_proc', return_value=(new_proc, time.time())):
        g._restart_log_proc_if_stale(old_proc, old_started)

    old_proc.kill.assert_called_once()
