import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_REDACTED_FIELDS = {"subject", "body", "snippet", "text", "content"}

_DEFAULT_LOG_PATH = Path("/data/gmail-audit.log")
_DEFAULT_MAX_BYTES = int(os.getenv("GMAIL_AUDIT_MAX_MB", "50")) * 1024 * 1024


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
        operation: str,
        message_id: Optional[str],
        from_addr: Optional[str],
        status: str,
        reason: Optional[str] = None,
        duration_ms: int = 0,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        entry: dict[str, Any] = {
            "time": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "request_id": request_id,
            "operation": operation,
            "status": status,
            "duration_ms": duration_ms,
        }
        if message_id is not None:
            entry["message_id"] = message_id
        if from_addr is not None:
            entry["from_addr"] = from_addr
        if reason is not None:
            entry["reason"] = reason
        # extra fields: include only safe keys (no content)
        if extra:
            for k, v in extra.items():
                if k.lower() not in _REDACTED_FIELDS:
                    entry[k] = v

        self._rotate_if_needed()
        with self._path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
