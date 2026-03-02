# Execution Guardrails

## What It Is

`scripts/guardrail.py` is a log-driven watchdog process that runs inside the OpenClaw container alongside the Gateway daemon. It consumes OpenClaw's structured JSON log stream and enforces per-session limits on tool calls, LLM calls, session duration, and idle time. It also monitors container memory usage as a backstop.

## Why It Exists

OpenClaw executes arbitrary tool calls and LLM API calls at the direction of the LLM. An infinite tool loop — or a session that keeps calling the LLM waiting for user input that never comes — will not trigger Docker's memory or PID limits. Those limits protect against memory exhaustion and fork bombs, not against an LLM repeatedly calling tools that complete successfully. The guardrail catches the patterns that container-level limits cannot: session-scoped runaway behavior that operates entirely within the container's resource envelope.

## Architecture

The guardrail is launched by `entrypoint.sh` as a supervised background process before the Gateway starts:

```
OpenClaw (Gateway daemon)
   │
   │  openclaw logs --json --follow
   ▼
/home/node/guardrail.py (background process, same container)
   │
   ├─ session state machine (NEW → ACTIVE → COMPLETED | ABORTED)
   ├─ limit enforcer
   └─ kill -TERM <openclaw_pid>  →  Docker restarts container
```

The guardrail observes the log stream — it does not intercept or modify requests. Its state is in-memory and per-session. If the guardrail crashes, the supervised restart loop in `entrypoint.sh` restarts it within 5 seconds. A guardrail crash does not crash OpenClaw.

## Limits

| Limit | Env var | Default | Description |
|-------|---------|---------|-------------|
| Max session duration | `MAX_SESSION_SECONDS` | 300 | Maximum wall-clock time per session from first event to completion |
| Max tool calls | `MAX_TOOL_CALLS` | 50 | Maximum tool invocations per session |
| Max LLM calls | `MAX_LLM_CALLS` | 30 | Maximum LLM API calls per session |
| Max idle time | `MAX_IDLE_SECONDS` | 60 | Maximum time with no log events before the session is considered stuck |
| Memory threshold | `MAX_MEMORY_PCT` | 90 | Container memory usage percentage that triggers a watchdog kill |

All limits are set in `.env` and injected as environment variables into the container.

## Abort Behavior

When any limit is exceeded, the guardrail sends `SIGTERM` to the OpenClaw process:

```bash
kill -TERM <openclaw_pid>   # graceful shutdown signal
# if still running after 10 seconds:
kill -KILL <openclaw_pid>   # force kill
```

Docker's `restart: unless-stopped` policy brings the container back up automatically. There is no per-session abort API in OpenClaw — the kill is process-level, which means all active sessions are dropped when any single session triggers a limit violation. See Known Limitations below.

## Kill Switch

The kill switch is used when you want to halt OpenClaw immediately and prevent automatic restart until you manually clear it.

**Activate:**

```bash
make kill-switch
# or manually:
touch /home/node/.openclaw/GUARDRAIL_DISABLE
```

If `/home/node/.openclaw/GUARDRAIL_DISABLE` exists when the guardrail starts, it terminates OpenClaw immediately and does not restart it. Docker will attempt to restart the container, the guardrail will run, see the file, and kill OpenClaw again — effectively holding the service down.

**Deactivate:**

Primary method (works even when container is cycling):

```bash
# Remove the file from the volume directly (works even when container is cycling):
docker run --rm -v <project>_openclaw_data:/data busybox rm -f /data/GUARDRAIL_DISABLE
make restart
```

Secondary method (only works if container is in a stable running state):

```bash
docker compose exec -T openclaw rm /home/node/.openclaw/GUARDRAIL_DISABLE
make restart
```

## Tuning

The defaults are conservative — they are appropriate for an active session with a human in the loop. For personal use with a single trusted user, the following values are reasonable:

```env
MAX_TOOL_CALLS=100
MAX_SESSION_SECONDS=600
MAX_LLM_CALLS=60
MAX_IDLE_SECONDS=120
```

For automated or unattended use, keep the defaults tight. The goal is to bound the blast radius of a runaway session, not to prevent legitimate work from completing.

## Known Limitations

**1. No per-session abort — one bad session kills all sessions.**
OpenClaw does not expose a session abort API. When the guardrail triggers, it kills the entire OpenClaw process. All users with active sessions at that moment will lose their session. The container restarts and resumes accepting connections within seconds, but in-progress work is lost. This is a Phase 1 limitation. There is no workaround without an OpenClaw-level API for per-session termination.

**2. Sequential LLM loops will not trigger memory or PID limits.**
An LLM that repeatedly calls tools in a loop — each call completing normally, consuming CPU and tokens — will not exhaust memory or fork new processes. Docker's built-in resource limits will not catch this pattern. Only `MAX_TOOL_CALLS`, `MAX_LLM_CALLS`, and `MAX_SESSION_SECONDS` will catch it. This is the primary reason the guardrail exists.

**3. Log format constants must be updated if OpenClaw changes its log schema.**
The guardrail parses OpenClaw's structured JSON log stream. If OpenClaw changes its log event field names or structure, the guardrail's event detection will silently stop working — it will not error, it will simply not see the events. After any OpenClaw upgrade, verify that `make logs` shows guardrail event detection is working. The relevant constants are at the top of `scripts/guardrail.py`.
