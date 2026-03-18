import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

TOOL_VERSION = "v1"
_NEVER_LOG = {"token", "key", "secret", "password", "credential", "attendees"}

_DEFAULT_LOG_PATH = Path("/data/calendar-audit.log")
_DEFAULT_MAX_BYTES = int(os.getenv("GCAL_AUDIT_MAX_MB", "50")) * 1024 * 1024


def _scrub_args(args: dict) -> dict:
    """Remove any key whose name looks like a secret."""
    return {k: v for k, v in args.items() if not any(s in k.lower() for s in _NEVER_LOG)}


class AuditLog:
    def __init__(
        self,
        log_path: Path = _DEFAULT_LOG_PATH,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ):
        self._path = Path(log_path)
        self._max_bytes = max_bytes
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()

    def _rotate_if_needed(self) -> None:
        if self._path.exists() and self._path.stat().st_size > self._max_bytes:
            rotated = self._path.with_suffix(self._path.suffix + ".1")
            self._path.rename(rotated)

    def write(
        self,
        *,
        request_id: str,
        tool: str,
        execution_mode: str,
        session_id: str,
        args: dict[str, Any],
        status: str,
        event_id: Optional[str] = None,
        reason: Optional[str] = None,
        duration_ms: int = 0,
        request_hash: Optional[str] = None,
    ) -> None:
        entry: dict[str, Any] = {
            "time": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "request_id": request_id,
            "tool": tool,
            "tool_version": TOOL_VERSION,
            "execution_mode": execution_mode,
            "session_id": session_id,
            "args": _scrub_args(args),
            "status": status,
            "duration_ms": duration_ms,
        }
        if request_hash:
            entry["request_hash"] = request_hash
        if event_id is not None:
            entry["event_id"] = event_id
        if reason is not None:
            entry["reason"] = reason

        self._rotate_if_needed()
        with self._path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
