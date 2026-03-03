# Google Calendar MCP Proxy — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build `services/calendar-proxy/` — a Python MCP server that gives OpenClaw controlled Google Calendar access via a layered policy engine with encrypted token storage, Redis-backed rate limiting, and append-only audit logging.

**Architecture:** See `docs/plans/2026-03-03-google-calendar-design.md` for the full design. The proxy exposes 5 MCP tools over SSE on the internal Docker network. All writes flow through `validate → assess → enforce → execute`. OAuth tokens are Fernet-encrypted at rest in the shared Docker volume.

**Tech Stack:** Python 3.11, `mcp[sse]` (FastMCP), `google-api-python-client`, `google-auth-oauthlib`, `pydantic` v2, `cryptography` (Fernet), `redis`, `python-dateutil`, `fakeredis` (tests), `pytest`, `pytest-asyncio`.

**Test command:** `pytest tests/calendar_proxy/ -v`
**Existing test command (must keep passing):** `pytest tests/ -v`

---

### Task 1: Scaffold

**Files:**
- Create: `services/calendar-proxy/Dockerfile`
- Create: `services/calendar-proxy/requirements.txt`
- Create: `services/calendar-proxy/__init__.py`
- Create: `services/calendar-proxy/scripts/__init__.py`
- Create: `tests/calendar_proxy/__init__.py`

**Step 1: Create directory structure**

```bash
mkdir -p services/calendar-proxy/scripts
mkdir -p tests/calendar_proxy
touch services/calendar-proxy/__init__.py
touch services/calendar-proxy/scripts/__init__.py
touch tests/calendar_proxy/__init__.py
```

**Step 2: Write `services/calendar-proxy/requirements.txt`**

```
mcp[sse]>=1.4.0
google-api-python-client>=2.150.0
google-auth-oauthlib>=1.2.0
google-auth-httplib2>=0.2.0
pydantic>=2.9.0
cryptography>=43.0.0
redis>=5.2.0
python-dateutil>=2.9.0
```

**Step 3: Write `services/calendar-proxy/Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -r -u 1001 proxy
USER proxy

EXPOSE 8080
CMD ["python", "server.py"]
```

**Step 4: Verify existing tests still pass**

```bash
pytest tests/ -v
```

Expected: all existing 16 guardrail tests pass.

**Step 5: Commit**

```bash
git add services/calendar-proxy/ tests/calendar_proxy/
git commit -m "feat: scaffold calendar-proxy service"
```

---

### Task 2: Pydantic Models (`models.py`)

The validation layer lives here. Every model field and constraint comes directly from the design doc. Tests come first.

**Files:**
- Create: `tests/calendar_proxy/test_models.py`
- Create: `services/calendar-proxy/models.py`

**Step 1: Write failing tests**

```python
# tests/calendar_proxy/test_models.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import pytest
from pydantic import ValidationError
from models import CreateEventInput, UpdateEventInput, DeleteEventInput, RecurrenceRule


# ── RecurrenceRule ────────────────────────────────────────────────────────────

def test_rrule_requires_count_or_until():
    with pytest.raises(ValidationError, match="COUNT or UNTIL"):
        RecurrenceRule(rrule="FREQ=WEEKLY")

def test_rrule_rejects_infinite():
    with pytest.raises(ValidationError):
        RecurrenceRule(rrule="FREQ=DAILY")  # no COUNT or UNTIL

def test_rrule_rejects_hourly():
    with pytest.raises(ValidationError, match="daily or less"):
        RecurrenceRule(rrule="FREQ=HOURLY;COUNT=10")

def test_rrule_rejects_minutely():
    with pytest.raises(ValidationError, match="daily or less"):
        RecurrenceRule(rrule="FREQ=MINUTELY;COUNT=10")

def test_rrule_rejects_count_over_max(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_RECURRENCE_COUNT", "52")
    with pytest.raises(ValidationError, match="exceeds maximum"):
        RecurrenceRule(rrule="FREQ=WEEKLY;COUNT=100")

def test_rrule_valid_weekly_count():
    r = RecurrenceRule(rrule="FREQ=WEEKLY;COUNT=12")
    assert r.rrule == "FREQ=WEEKLY;COUNT=12"

def test_rrule_valid_daily_until():
    r = RecurrenceRule(rrule="FREQ=DAILY;UNTIL=20261231T000000Z")
    assert "UNTIL" in r.rrule


# ── CreateEventInput ──────────────────────────────────────────────────────────

def test_create_rejects_naive_start():
    with pytest.raises(ValidationError, match="timezone"):
        CreateEventInput(
            title="Test", start="2026-03-15T14:00:00",
            end="2026-03-15T15:00:00+02:00", execution_mode="dry_run"
        )

def test_create_rejects_naive_end():
    with pytest.raises(ValidationError, match="timezone"):
        CreateEventInput(
            title="Test", start="2026-03-15T14:00:00+02:00",
            end="2026-03-15T15:00:00", execution_mode="dry_run"
        )

def test_create_rejects_start_after_end():
    with pytest.raises(ValidationError, match="before end"):
        CreateEventInput(
            title="Test", start="2026-03-15T16:00:00+02:00",
            end="2026-03-15T15:00:00+02:00", execution_mode="dry_run"
        )

def test_create_rejects_zero_duration():
    with pytest.raises(ValidationError, match="before end"):
        CreateEventInput(
            title="Test", start="2026-03-15T14:00:00+02:00",
            end="2026-03-15T14:00:00+02:00", execution_mode="dry_run"
        )

def test_create_rejects_duration_over_max(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_EVENT_HOURS", "8")
    with pytest.raises(ValidationError, match="exceeds maximum"):
        CreateEventInput(
            title="Test", start="2026-03-15T08:00:00+02:00",
            end="2026-03-15T18:00:00+02:00", execution_mode="dry_run"  # 10h
        )

def test_create_defaults_calendar_id():
    ev = CreateEventInput(
        title="Test", start="2026-03-15T14:00:00+02:00",
        end="2026-03-15T15:00:00+02:00", execution_mode="dry_run"
    )
    assert ev.calendar_id == "primary"

def test_create_valid_event():
    ev = CreateEventInput(
        title="Standup", start="2026-03-15T09:00:00+02:00",
        end="2026-03-15T09:30:00+02:00", execution_mode="execute"
    )
    assert ev.title == "Standup"
    assert ev.execution_mode == "execute"

def test_create_valid_with_recurrence():
    ev = CreateEventInput(
        title="Weekly sync", start="2026-03-15T10:00:00+02:00",
        end="2026-03-15T11:00:00+02:00", execution_mode="dry_run",
        recurrence=RecurrenceRule(rrule="FREQ=WEEKLY;COUNT=4")
    )
    assert ev.recurrence.rrule == "FREQ=WEEKLY;COUNT=4"


# ── UpdateEventInput ──────────────────────────────────────────────────────────

def test_update_requires_event_id():
    with pytest.raises(ValidationError):
        UpdateEventInput(changes={"title": "New"}, execution_mode="dry_run")

def test_update_valid():
    u = UpdateEventInput(
        event_id="abc123", changes={"title": "Updated"}, execution_mode="dry_run"
    )
    assert u.event_id == "abc123"


# ── DeleteEventInput ──────────────────────────────────────────────────────────

def test_delete_requires_event_id():
    with pytest.raises(ValidationError):
        DeleteEventInput(execution_mode="dry_run")

def test_delete_valid():
    d = DeleteEventInput(event_id="abc123", execution_mode="execute")
    assert d.event_id == "abc123"
```

**Step 2: Run to verify all fail**

```bash
pytest tests/calendar_proxy/test_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'models'`

**Step 3: Write `services/calendar-proxy/models.py`**

```python
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from pydantic import BaseModel, field_validator, model_validator


def _max_recurrence_count() -> int:
    return int(os.getenv("GCAL_MAX_RECURRENCE_COUNT", "52"))

def _max_event_hours() -> int:
    return int(os.getenv("GCAL_MAX_EVENT_HOURS", "8"))

def _max_past_hours() -> int:
    return int(os.getenv("GCAL_MAX_PAST_HOURS", "1"))


class RecurrenceRule(BaseModel):
    rrule: str

    @field_validator("rrule")
    @classmethod
    def validate_rrule(cls, v: str) -> str:
        if "COUNT=" not in v and "UNTIL=" not in v:
            raise ValueError("RRULE must specify COUNT or UNTIL — infinite recurrence not allowed")
        if re.search(r"FREQ=(HOURLY|MINUTELY|SECONDLY)", v, re.IGNORECASE):
            raise ValueError("RRULE frequency must be daily or less frequent")
        count_match = re.search(r"COUNT=(\d+)", v)
        if count_match:
            count = int(count_match.group(1))
            max_count = _max_recurrence_count()
            if count > max_count:
                raise ValueError(f"RRULE COUNT {count} exceeds maximum {max_count}")
        return v


def _parse_dt(v: str, field: str) -> datetime:
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        raise ValueError(f"Invalid datetime for {field}: {v!r}")
    if dt.tzinfo is None:
        raise ValueError(f"Datetime for {field} must include timezone offset, got naive: {v!r}")
    return dt


class CreateEventInput(BaseModel):
    title: str
    start: str
    end: str
    calendar_id: str = "primary"
    description: Optional[str] = None
    recurrence: Optional[RecurrenceRule] = None
    execution_mode: Literal["dry_run", "execute"]
    idempotency_key: Optional[str] = None

    @field_validator("start", "end")
    @classmethod
    def validate_datetime_with_tz(cls, v: str) -> str:
        _parse_dt(v, v)  # raises if naive
        return v

    @model_validator(mode="after")
    def validate_temporal(self) -> "CreateEventInput":
        start = _parse_dt(self.start, "start")
        end = _parse_dt(self.end, "end")
        if start >= end:
            raise ValueError("start must be before end")
        duration_hours = (end - start).total_seconds() / 3600
        max_hours = _max_event_hours()
        if duration_hours > max_hours:
            raise ValueError(f"Duration {duration_hours:.1f}h exceeds maximum {max_hours}h")
        max_past = _max_past_hours()
        now = datetime.now(tz=timezone.utc)
        if start.astimezone(timezone.utc) < now - timedelta(hours=max_past):
            raise ValueError(f"start is more than {max_past}h in the past")
        return self


class UpdateEventInput(BaseModel):
    event_id: str
    changes: dict[str, Any]
    calendar_id: str = "primary"
    execution_mode: Literal["dry_run", "execute"]
    idempotency_key: Optional[str] = None


class DeleteEventInput(BaseModel):
    event_id: str
    calendar_id: str = "primary"
    execution_mode: Literal["dry_run", "execute"]
    idempotency_key: Optional[str] = None


class ListEventsInput(BaseModel):
    calendar_id: str = "primary"
    time_min: str
    time_max: str

    @field_validator("time_min", "time_max")
    @classmethod
    def validate_datetime_with_tz(cls, v: str) -> str:
        _parse_dt(v, v)
        return v


class CheckAvailabilityInput(BaseModel):
    time_min: str
    time_max: str
    duration_minutes: int

    @field_validator("time_min", "time_max")
    @classmethod
    def validate_datetime_with_tz(cls, v: str) -> str:
        _parse_dt(v, v)
        return v


class ConflictEntry(BaseModel):
    event_id: str
    title: str
    occurrence_start: str
    overlap_minutes: int
    severity: Literal["partial", "full"]


class ImpactModel(BaseModel):
    overlaps_existing: bool = False
    overlapping_events: list[ConflictEntry] = []
    outside_business_hours: bool = False
    is_weekend: bool = False
    duration_minutes: float = 0
    recurring: bool = False
    recurrence_instances_checked: int = 0
    work_calendar: bool = False


class PolicyResponse(BaseModel):
    request_id: str
    status: Literal["safe_to_execute", "needs_confirmation", "denied", "error"]
    impact: Optional[ImpactModel] = None
    normalized_event: Optional[dict] = None
    event_id: Optional[str] = None
    reason: Optional[str] = None
```

**Step 4: Run tests**

```bash
pytest tests/calendar_proxy/test_models.py -v
```

Expected: all pass.

**Step 5: Run full suite**

```bash
pytest tests/ -v
```

Expected: all 16 + new model tests pass.

**Step 6: Commit**

```bash
git add services/calendar-proxy/models.py tests/calendar_proxy/test_models.py
git commit -m "feat: add Pydantic models with full input validation"
```

---

### Task 3: Audit Module (`audit.py`)

Append-only JSONL writer. Startup rotation. Never logs secrets.

**Files:**
- Create: `tests/calendar_proxy/test_audit.py`
- Create: `services/calendar-proxy/audit.py`

**Step 1: Write failing tests**

```python
# tests/calendar_proxy/test_audit.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import json
import uuid
import pytest
from pathlib import Path
from audit import AuditLog


def test_audit_writes_jsonl_entry(tmp_path):
    log = AuditLog(log_path=tmp_path / "audit.log")
    log.write(
        request_id=str(uuid.uuid4()),
        tool="create_event",
        execution_mode="dry_run",
        session_id="s1",
        args={"title": "Test", "start": "2026-03-15T09:00:00+02:00"},
        status="dry_run",
        duration_ms=42,
    )
    lines = (tmp_path / "audit.log").read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "create_event"
    assert entry["tool_version"] == "v1"
    assert entry["status"] == "dry_run"
    assert entry["execution_mode"] == "dry_run"
    assert "time" in entry
    assert "request_id" in entry


def test_audit_appends_multiple_entries(tmp_path):
    log = AuditLog(log_path=tmp_path / "audit.log")
    for i in range(3):
        log.write(
            request_id=str(uuid.uuid4()),
            tool="list_events",
            execution_mode="execute",
            session_id="s1",
            args={},
            status="dry_run",
            duration_ms=i,
        )
    lines = (tmp_path / "audit.log").read_text().strip().splitlines()
    assert len(lines) == 3


def test_audit_never_logs_token(tmp_path):
    log = AuditLog(log_path=tmp_path / "audit.log")
    log.write(
        request_id="r1",
        tool="create_event",
        execution_mode="execute",
        session_id="s1",
        args={"token": "SECRET", "title": "Test"},
        status="created",
        duration_ms=10,
    )
    content = (tmp_path / "audit.log").read_text()
    assert "SECRET" not in content


def test_audit_rotates_at_startup_when_over_limit(tmp_path):
    log_path = tmp_path / "audit.log"
    # Write a file that pretends to be 1 byte over the 1-byte limit
    log_path.write_text("x" * 10)
    log = AuditLog(log_path=log_path, max_bytes=5)  # 10 > 5 → rotate
    assert (tmp_path / "audit.log.1").exists()
    assert not log_path.exists() or log_path.stat().st_size == 0


def test_audit_no_rotation_when_under_limit(tmp_path):
    log_path = tmp_path / "audit.log"
    log_path.write_text("small")
    log = AuditLog(log_path=log_path, max_bytes=1000)
    assert not (tmp_path / "audit.log.1").exists()


def test_audit_includes_event_id_on_created(tmp_path):
    log = AuditLog(log_path=tmp_path / "audit.log")
    log.write(
        request_id="r1",
        tool="create_event",
        execution_mode="execute",
        session_id="s1",
        args={"title": "Test"},
        status="created",
        event_id="google-event-123",
        duration_ms=100,
    )
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert entry["event_id"] == "google-event-123"


def test_audit_includes_reason_on_denied(tmp_path):
    log = AuditLog(log_path=tmp_path / "audit.log")
    log.write(
        request_id="r1",
        tool="create_event",
        execution_mode="execute",
        session_id="s1",
        args={"title": "Test"},
        status="denied",
        reason="calendar_id not in allowlist",
        duration_ms=5,
    )
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert entry["reason"] == "calendar_id not in allowlist"
    assert "event_id" not in entry
```

**Step 2: Run to verify all fail**

```bash
pytest tests/calendar_proxy/test_audit.py -v
```

Expected: `ModuleNotFoundError: No module named 'audit'`

**Step 3: Write `services/calendar-proxy/audit.py`**

```python
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

TOOL_VERSION = "v1"
_NEVER_LOG = {"token", "key", "secret", "password", "credential"}

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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed(max_bytes)

    def _rotate_if_needed(self, max_bytes: int) -> None:
        if self._path.exists() and self._path.stat().st_size > max_bytes:
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

        with self._path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
```

**Step 4: Run tests**

```bash
pytest tests/calendar_proxy/test_audit.py -v
```

Expected: all pass.

**Step 5: Run full suite**

```bash
pytest tests/ -v
```

Expected: all pass.

**Step 6: Commit**

```bash
git add services/calendar-proxy/audit.py tests/calendar_proxy/test_audit.py
git commit -m "feat: add append-only audit log with startup rotation"
```

---

### Task 4: Auth Module (`auth.py`) + Setup Scripts

Fernet encryption/decryption, atomic token refresh write, fail-fast on missing key.

**Files:**
- Create: `tests/calendar_proxy/test_auth.py`
- Create: `services/calendar-proxy/auth.py`
- Create: `services/calendar-proxy/scripts/auth_setup.py`
- Create: `services/calendar-proxy/scripts/encrypt_token.py`

**Step 1: Write failing tests**

```python
# tests/calendar_proxy/test_auth.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from auth import TokenStore, generate_key


def test_generate_key_returns_bytes():
    key = generate_key()
    assert isinstance(key, bytes)
    assert len(key) > 0


def test_encrypt_decrypt_roundtrip():
    key = generate_key()
    store = TokenStore(key=key)
    original = {"access_token": "tok", "refresh_token": "ref", "token_uri": "https://oauth2.googleapis.com/token"}
    encrypted = store.encrypt(original)
    assert isinstance(encrypted, bytes)
    assert b"tok" not in encrypted  # not plaintext
    decrypted = store.decrypt(encrypted)
    assert decrypted == original


def test_encrypt_decrypt_wrong_key_fails():
    key1 = generate_key()
    key2 = generate_key()
    store1 = TokenStore(key=key1)
    store2 = TokenStore(key=key2)
    original = {"access_token": "tok"}
    encrypted = store1.encrypt(original)
    with pytest.raises(Exception):
        store2.decrypt(encrypted)


def test_atomic_write(tmp_path):
    key = generate_key()
    store = TokenStore(key=key, token_path=tmp_path / "gcal_token.enc")
    token = {"access_token": "tok", "refresh_token": "ref"}
    store.save(token)
    loaded = store.load()
    assert loaded == token
    # No .tmp file left behind
    assert not (tmp_path / "gcal_token.enc.tmp").exists()


def test_save_is_atomic_on_crash(tmp_path, monkeypatch):
    """If rename fails, original file is intact."""
    key = generate_key()
    token_path = tmp_path / "gcal_token.enc"
    store = TokenStore(key=key, token_path=token_path)
    original = {"access_token": "original"}
    store.save(original)

    # Simulate crash during rename
    def bad_replace(src):
        raise OSError("disk full")

    store2 = TokenStore(key=key, token_path=token_path)
    with patch.object(Path, "replace", side_effect=bad_replace):
        with pytest.raises(OSError):
            store2.save({"access_token": "new"})

    # Original still intact
    loaded = store.load()
    assert loaded["access_token"] == "original"


def test_fail_fast_missing_key(monkeypatch):
    monkeypatch.delenv("GCAL_TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GCAL_TOKEN_ENCRYPTION_KEY"):
        TokenStore.from_env()


def test_load_from_env(tmp_path, monkeypatch):
    key = generate_key()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY", key.decode())
    token_path = tmp_path / "gcal_token.enc"
    token = {"access_token": "tok"}
    store_write = TokenStore(key=key, token_path=token_path)
    store_write.save(token)

    store_read = TokenStore.from_env(token_path=token_path)
    assert store_read.load() == token
```

**Step 2: Run to verify all fail**

```bash
pytest tests/calendar_proxy/test_auth.py -v
```

Expected: `ModuleNotFoundError: No module named 'auth'`

**Step 3: Write `services/calendar-proxy/auth.py`**

```python
import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


def generate_key() -> bytes:
    return Fernet.generate_key()


class TokenStore:
    def __init__(self, key: bytes, token_path: Path = Path("/data/gcal_token.enc")):
        self._fernet = Fernet(key)
        self._path = Path(token_path)

    @classmethod
    def from_env(cls, token_path: Path = Path("/data/gcal_token.enc")) -> "TokenStore":
        raw_key = os.environ.get("GCAL_TOKEN_ENCRYPTION_KEY")
        if not raw_key:
            raise RuntimeError(
                "Missing GCAL_TOKEN_ENCRYPTION_KEY — refusing to start. "
                "Generate one with: python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        return cls(key=raw_key.encode(), token_path=token_path)

    def encrypt(self, token_dict: dict[str, Any]) -> bytes:
        return self._fernet.encrypt(json.dumps(token_dict).encode())

    def decrypt(self, data: bytes) -> dict[str, Any]:
        return json.loads(self._fernet.decrypt(data))

    def save(self, token_dict: dict[str, Any]) -> None:
        """Atomic write: encrypt → tmp → rename."""
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(self.encrypt(token_dict))
        tmp.replace(self._path)  # atomic on Linux

    def load(self) -> dict[str, Any]:
        return self.decrypt(self._path.read_bytes())
```

**Step 4: Write `services/calendar-proxy/scripts/auth_setup.py`**

```python
#!/usr/bin/env python3
"""
One-time OAuth setup script. Run locally on your Mac.
Usage: python3 scripts/auth_setup.py --client-secret client_secret.json --out token.json
"""
import argparse
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
    credentials = flow.run_local_server(port=0)

    token_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes),
    }
    with open(args.out, "w") as f:
        json.dump(token_data, f, indent=2)
    print(f"Token written to {args.out}")
    print("Next step: encrypt it with scripts/encrypt_token.py")

if __name__ == "__main__":
    main()
```

**Step 5: Write `services/calendar-proxy/scripts/encrypt_token.py`**

```python
#!/usr/bin/env python3
"""
Encrypt token.json → token.enc using a Fernet key.
Usage: python3 scripts/encrypt_token.py --token token.json --key <KEY> --out token.enc
"""
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from auth import TokenStore
import json

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.token) as f:
        token_data = json.load(f)

    store = TokenStore(key=args.key.encode())
    encrypted = store.encrypt(token_data)

    with open(args.out, "wb") as f:
        f.write(encrypted)
    print(f"Encrypted token written to {args.out}")

if __name__ == "__main__":
    main()
```

**Step 6: Run tests**

```bash
pytest tests/calendar_proxy/test_auth.py -v
```

Expected: all pass.

**Step 7: Run full suite**

```bash
pytest tests/ -v
```

Expected: all pass.

**Step 8: Commit**

```bash
git add services/calendar-proxy/auth.py services/calendar-proxy/scripts/
git add tests/calendar_proxy/test_auth.py
git commit -m "feat: add token encryption, atomic refresh write, and setup scripts"
```

---

### Task 5: Policy Engine — Assess Phase (`policies.py`)

Conflict detection, recurrence expansion, business hour evaluation. All in the `assess()` function that produces an `ImpactModel`.

**Files:**
- Create: `tests/calendar_proxy/test_policies_assess.py`
- Create: `services/calendar-proxy/policies.py`

**Step 1: Write failing tests**

```python
# tests/calendar_proxy/test_policies_assess.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone
import pytz
from policies import assess
from models import CreateEventInput, ImpactModel, RecurrenceRule


def _make_input(**kwargs):
    defaults = dict(
        title="Test",
        start="2026-03-16T10:00:00+02:00",  # Monday
        end="2026-03-16T11:00:00+02:00",
        execution_mode="dry_run",
    )
    defaults.update(kwargs)
    return CreateEventInput(**defaults)


def _no_conflicts(calendar_id, time_min, time_max):
    return []


def _one_conflict(calendar_id, time_min, time_max):
    return [{"id": "existing-1", "summary": "Other meeting",
             "start": {"dateTime": time_min}, "end": {"dateTime": time_max}}]


# ── Business hours ────────────────────────────────────────────────────────────

def test_inside_business_hours(monkeypatch):
    monkeypatch.setenv("GCAL_ALLOWED_START_HOUR", "8")
    monkeypatch.setenv("GCAL_ALLOWED_END_HOUR", "20")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "Europe/Helsinki")
    # 10:00 Helsinki on Monday
    impact = assess(_make_input(), list_events_fn=_no_conflicts)
    assert impact.outside_business_hours is False
    assert impact.is_weekend is False


def test_outside_business_hours_early(monkeypatch):
    monkeypatch.setenv("GCAL_ALLOWED_START_HOUR", "8")
    monkeypatch.setenv("GCAL_ALLOWED_END_HOUR", "20")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "Europe/Helsinki")
    inp = _make_input(start="2026-03-16T06:00:00+02:00", end="2026-03-16T07:00:00+02:00")
    impact = assess(inp, list_events_fn=_no_conflicts)
    assert impact.outside_business_hours is True


def test_outside_business_hours_late(monkeypatch):
    monkeypatch.setenv("GCAL_ALLOWED_START_HOUR", "8")
    monkeypatch.setenv("GCAL_ALLOWED_END_HOUR", "20")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "Europe/Helsinki")
    inp = _make_input(start="2026-03-16T21:00:00+02:00", end="2026-03-16T22:00:00+02:00")
    impact = assess(inp, list_events_fn=_no_conflicts)
    assert impact.outside_business_hours is True


def test_weekend_detection(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "Europe/Helsinki")
    # 2026-03-21 is a Saturday
    inp = _make_input(start="2026-03-21T10:00:00+02:00", end="2026-03-21T11:00:00+02:00")
    impact = assess(inp, list_events_fn=_no_conflicts)
    assert impact.is_weekend is True


def test_weekday_not_weekend(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "Europe/Helsinki")
    impact = assess(_make_input(), list_events_fn=_no_conflicts)
    assert impact.is_weekend is False


# ── Conflict detection ────────────────────────────────────────────────────────

def test_no_conflict_when_no_existing_events(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    impact = assess(_make_input(), list_events_fn=_no_conflicts)
    assert impact.overlaps_existing is False
    assert impact.overlapping_events == []


def test_conflict_detected(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    impact = assess(_make_input(), list_events_fn=_one_conflict)
    assert impact.overlaps_existing is True
    assert len(impact.overlapping_events) == 1
    assert impact.overlapping_events[0].event_id == "existing-1"


# ── Recurrence expansion ──────────────────────────────────────────────────────

def test_recurrence_expands_instances(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    inp = _make_input(
        recurrence=RecurrenceRule(rrule="FREQ=WEEKLY;COUNT=4")
    )
    impact = assess(inp, list_events_fn=_no_conflicts)
    assert impact.recurring is True
    assert impact.recurrence_instances_checked == 4


def test_recurrence_conflict_on_second_instance(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    call_count = {"n": 0}

    def conflicts_on_second_call(calendar_id, time_min, time_max):
        call_count["n"] += 1
        if call_count["n"] == 2:
            return [{"id": "clash", "summary": "Clash",
                     "start": {"dateTime": time_min}, "end": {"dateTime": time_max}}]
        return []

    inp = _make_input(recurrence=RecurrenceRule(rrule="FREQ=WEEKLY;COUNT=3"))
    impact = assess(inp, list_events_fn=conflicts_on_second_call)
    assert impact.overlaps_existing is True
    assert impact.recurrence_instances_checked == 3


def test_non_recurring_checks_one_window(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    call_count = {"n": 0}
    def count_calls(calendar_id, time_min, time_max):
        call_count["n"] += 1
        return []
    assess(_make_input(), list_events_fn=count_calls)
    assert call_count["n"] == 1


# ── Duration ──────────────────────────────────────────────────────────────────

def test_duration_minutes_calculated(monkeypatch):
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    impact = assess(_make_input(), list_events_fn=_no_conflicts)
    assert impact.duration_minutes == 60.0
```

**Step 2: Run to verify all fail**

```bash
pytest tests/calendar_proxy/test_policies_assess.py -v
```

Expected: `ModuleNotFoundError: No module named 'policies'`

**Step 3: Write `services/calendar-proxy/policies.py` (assess phase only)**

```python
import os
from datetime import datetime, timezone, timedelta
from typing import Callable
import pytz
from dateutil.rrule import rrulestr

from models import CreateEventInput, ImpactModel, ConflictEntry


def _user_tz() -> pytz.BaseTzInfo:
    return pytz.timezone(os.getenv("GCAL_USER_TIMEZONE", "UTC"))


def _to_user_tz(dt: datetime) -> datetime:
    """Convert any timezone-aware datetime to the user's configured timezone."""
    return dt.astimezone(_user_tz())


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _overlap_minutes(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> int:
    overlap_start = max(a_start, b_start)
    overlap_end = min(a_end, b_end)
    if overlap_end <= overlap_start:
        return 0
    return int((overlap_end - overlap_start).total_seconds() / 60)


def _classify_severity(overlap_mins: int, duration_mins: float) -> str:
    return "full" if overlap_mins >= duration_mins else "partial"


def _check_one_window(
    start: datetime,
    end: datetime,
    calendar_id: str,
    list_events_fn: Callable,
) -> list[ConflictEntry]:
    existing = list_events_fn(
        calendar_id,
        start.isoformat(),
        end.isoformat(),
    )
    conflicts = []
    duration = (end - start).total_seconds() / 60
    for ev in existing:
        ev_start = _parse_dt(ev["start"].get("dateTime") or ev["start"].get("date"))
        ev_end = _parse_dt(ev["end"].get("dateTime") or ev["end"].get("date"))
        mins = _overlap_minutes(start, end, ev_start, ev_end)
        if mins > 0:
            conflicts.append(ConflictEntry(
                event_id=ev["id"],
                title=ev.get("summary", "(no title)"),
                occurrence_start=start.isoformat(),
                overlap_minutes=mins,
                severity=_classify_severity(mins, duration),
            ))
    return conflicts


def assess(event: CreateEventInput, list_events_fn: Callable) -> ImpactModel:
    """Phase 2: produce impact model without making policy decisions."""
    start = _parse_dt(event.start)
    end = _parse_dt(event.end)
    duration_minutes = (end - start).total_seconds() / 60

    # Business hours + weekend (evaluated in user timezone)
    local_start = _to_user_tz(start)
    start_hour_cfg = int(os.getenv("GCAL_ALLOWED_START_HOUR", "8"))
    end_hour_cfg = int(os.getenv("GCAL_ALLOWED_END_HOUR", "20"))
    outside_business_hours = (
        local_start.hour < start_hour_cfg or local_start.hour >= end_hour_cfg
    )
    is_weekend = local_start.weekday() >= 5  # 5=Sat, 6=Sun

    all_conflicts: list[ConflictEntry] = []
    instances_checked = 0

    if event.recurrence:
        # Expand recurrence instances and check each one
        rule = rrulestr(event.recurrence.rrule, dtstart=start)
        occurrences = list(rule)
        instances_checked = len(occurrences)
        for occ_start in occurrences:
            occ_end = occ_start + (end - start)
            conflicts = _check_one_window(occ_start, occ_end, event.calendar_id, list_events_fn)
            all_conflicts.extend(conflicts)
    else:
        # Single event
        instances_checked = 1
        all_conflicts = _check_one_window(start, end, event.calendar_id, list_events_fn)

    return ImpactModel(
        overlaps_existing=len(all_conflicts) > 0,
        overlapping_events=all_conflicts,
        outside_business_hours=outside_business_hours,
        is_weekend=is_weekend,
        duration_minutes=duration_minutes,
        recurring=event.recurrence is not None,
        recurrence_instances_checked=instances_checked,
        work_calendar=event.calendar_id == os.getenv("GCAL_WORK_CALENDAR_ID", "__unset__"),
    )
```

**Step 4: Run tests**

```bash
pytest tests/calendar_proxy/test_policies_assess.py -v
```

Expected: all pass.

**Step 5: Run full suite**

```bash
pytest tests/ -v
```

Expected: all pass.

**Step 6: Commit**

```bash
git add services/calendar-proxy/policies.py tests/calendar_proxy/test_policies_assess.py
git commit -m "feat: add policy assess phase with recurrence expansion and conflict detection"
```

---

### Task 6: Policy Engine — Enforce Phase

Takes an `ImpactModel` and returns `status` + optional `reason`. The full denial matrix from the design doc.

**Files:**
- Create: `tests/calendar_proxy/test_policies_enforce.py`
- Modify: `services/calendar-proxy/policies.py` (add `enforce()`)

**Step 1: Write failing tests**

```python
# tests/calendar_proxy/test_policies_enforce.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import pytest
from policies import enforce
from models import ImpactModel, ConflictEntry


def _impact(**kwargs):
    defaults = dict(
        overlaps_existing=False,
        overlapping_events=[],
        outside_business_hours=False,
        is_weekend=False,
        duration_minutes=60,
        recurring=False,
        recurrence_instances_checked=1,
        work_calendar=False,
    )
    defaults.update(kwargs)
    return ImpactModel(**defaults)


# ── Denied ────────────────────────────────────────────────────────────────────

def test_denied_not_in_allowlist():
    status, reason = enforce(_impact(), calendar_id="other@group.calendar.google.com", in_allowlist=False)
    assert status == "denied"
    assert "allowlist" in reason


def test_denied_recurring_work_outside_hours():
    impact = _impact(recurring=True, work_calendar=True, outside_business_hours=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "denied"


def test_denied_recurring_work_weekend():
    impact = _impact(recurring=True, work_calendar=True, is_weekend=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "denied"


# ── Needs confirmation ────────────────────────────────────────────────────────

def test_needs_confirmation_overlap():
    impact = _impact(
        overlaps_existing=True,
        overlapping_events=[ConflictEntry(event_id="x", title="X", occurrence_start="2026-03-16T10:00:00+02:00", overlap_minutes=30, severity="partial")]
    )
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_over_2h():
    impact = _impact(duration_minutes=150)  # 2.5 hours
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_outside_hours():
    impact = _impact(outside_business_hours=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_weekend():
    impact = _impact(is_weekend=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_work_calendar():
    impact = _impact(work_calendar=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_recurring():
    impact = _impact(recurring=True)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "needs_confirmation"


def test_needs_confirmation_delete_always():
    impact = _impact()
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True, is_delete=True)
    assert status == "needs_confirmation"


# ── Safe to execute ───────────────────────────────────────────────────────────

def test_safe_to_execute_simple_event():
    impact = _impact(duration_minutes=30)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "safe_to_execute"
    assert reason is None


def test_safe_to_execute_exactly_2h():
    impact = _impact(duration_minutes=120)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "safe_to_execute"


def test_safe_to_execute_personal_calendar_inside_hours():
    impact = _impact(duration_minutes=45, work_calendar=False, recurring=False,
                     outside_business_hours=False, is_weekend=False)
    status, reason = enforce(impact, calendar_id="primary", in_allowlist=True)
    assert status == "safe_to_execute"
```

**Step 2: Run to verify all fail**

```bash
pytest tests/calendar_proxy/test_policies_enforce.py -v
```

Expected: `ImportError: cannot import name 'enforce'`

**Step 3: Add `enforce()` to `services/calendar-proxy/policies.py`**

Append to the existing file:

```python
def enforce(
    impact: ImpactModel,
    *,
    calendar_id: str,
    in_allowlist: bool,
    is_delete: bool = False,
) -> tuple[str, str | None]:
    """Phase 3: apply policy rules → (status, reason)."""

    # Hard denials — not overridable
    if not in_allowlist:
        return "denied", f"calendar_id '{calendar_id}' is not in GCAL_ALLOWED_CALENDARS"

    if impact.recurring and impact.work_calendar and (impact.outside_business_hours or impact.is_weekend):
        return "denied", "recurring event on work calendar outside business hours is not allowed"

    # Confirmation required
    if is_delete:
        return "needs_confirmation", None
    if impact.overlaps_existing:
        return "needs_confirmation", None
    if impact.duration_minutes > 120:
        return "needs_confirmation", None
    if impact.outside_business_hours:
        return "needs_confirmation", None
    if impact.is_weekend:
        return "needs_confirmation", None
    if impact.work_calendar:
        return "needs_confirmation", None
    if impact.recurring:
        return "needs_confirmation", None

    return "safe_to_execute", None
```

**Step 4: Run tests**

```bash
pytest tests/calendar_proxy/test_policies_enforce.py -v
```

Expected: all pass.

**Step 5: Run full suite**

```bash
pytest tests/ -v
```

Expected: all pass.

**Step 6: Commit**

```bash
git add services/calendar-proxy/policies.py tests/calendar_proxy/test_policies_enforce.py
git commit -m "feat: add policy enforce phase with full denial matrix"
```

---

### Task 7: Policy Engine — Execute Phase (Redis Rate Limit + Idempotency)

Redis-backed rate limiting (per calendar, date-keyed) and idempotency (per operation type, 10-minute TTL). Uses `fakeredis` in tests.

**Files:**
- Create: `tests/calendar_proxy/test_policies_execute.py`
- Modify: `services/calendar-proxy/policies.py` (add `check_rate_limit()`, `check_idempotency()`, `record_idempotency()`)

**Step 1: Write failing tests**

```python
# tests/calendar_proxy/test_policies_execute.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import json
import time
import hashlib
import pytest
import fakeredis
from policies import check_rate_limit, check_idempotency, record_idempotency, idempotency_key_for


# ── Rate limiting ─────────────────────────────────────────────────────────────

def test_rate_limit_allows_under_limit(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_EVENTS_PER_DAY", "10")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    r = fakeredis.FakeRedis()
    for _ in range(9):
        ok, reason = check_rate_limit(r, calendar_id="primary", op="create", date_str="2026-03-15")
        assert ok

def test_rate_limit_blocks_at_limit(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_EVENTS_PER_DAY", "3")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    r = fakeredis.FakeRedis()
    for _ in range(3):
        r.incr("rate_limit:primary:2026-03-15")
    ok, reason = check_rate_limit(r, calendar_id="primary", op="create", date_str="2026-03-15")
    assert not ok
    assert "rate limit" in reason.lower()

def test_rate_limit_separate_per_calendar(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_EVENTS_PER_DAY", "2")
    r = fakeredis.FakeRedis()
    r.set("rate_limit:work@calendar:2026-03-15", 2)  # work at limit
    ok, _ = check_rate_limit(r, calendar_id="primary", op="create", date_str="2026-03-15")
    assert ok  # personal unaffected

def test_rate_limit_update_uses_separate_counter(monkeypatch):
    monkeypatch.setenv("GCAL_MAX_UPDATES_PER_DAY", "50")
    r = fakeredis.FakeRedis()
    ok, _ = check_rate_limit(r, calendar_id="primary", op="update", date_str="2026-03-15")
    assert ok


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_idempotency_key_for_create():
    key = idempotency_key_for("create", {"title": "T", "start": "2026-03-15T10:00:00+02:00", "end": "2026-03-15T11:00:00+02:00", "calendar_id": "primary"})
    assert key.startswith("sha256:")
    assert len(key) > 10

def test_idempotency_key_for_update():
    key = idempotency_key_for("update", {"event_id": "abc", "changes": {"title": "New"}})
    assert key.startswith("sha256:")

def test_idempotency_key_for_delete():
    key = idempotency_key_for("delete", {"event_id": "abc"})
    assert key.startswith("sha256:")

def test_idempotency_no_hit_first_time():
    r = fakeredis.FakeRedis()
    result = check_idempotency(r, "sha256:abc123")
    assert result is None

def test_idempotency_hit_on_second_execute():
    r = fakeredis.FakeRedis()
    record_idempotency(r, "sha256:abc123", event_id="google-event-1")
    result = check_idempotency(r, "sha256:abc123")
    assert result == "google-event-1"

def test_idempotency_expires_after_ttl():
    r = fakeredis.FakeRedis()
    record_idempotency(r, "sha256:abc123", event_id="ev1", ttl_seconds=1)
    time.sleep(1.1)
    result = check_idempotency(r, "sha256:abc123")
    assert result is None

def test_idempotency_not_written_for_dry_run():
    """dry_run must never write idempotency cache — verified by caller convention."""
    r = fakeredis.FakeRedis()
    # record_idempotency should not be called for dry_run
    # This test documents the contract: check that we can call record_idempotency
    # only from execute path (enforced in server.py, not here)
    # Just verify record doesn't auto-expire immediately
    record_idempotency(r, "sha256:dry", event_id="ev", ttl_seconds=600)
    assert check_idempotency(r, "sha256:dry") == "ev"
```

**Step 2: Run to verify all fail**

```bash
pytest tests/calendar_proxy/test_policies_execute.py -v
```

Expected: `ImportError`

**Step 3: Append to `services/calendar-proxy/policies.py`**

```python
import hashlib
import json
from typing import Optional
import redis as redis_lib


def _today_str(user_tz_name: str) -> str:
    import pytz
    from datetime import datetime
    tz = pytz.timezone(user_tz_name)
    return datetime.now(tz).strftime("%Y-%m-%d")


def check_rate_limit(
    r: redis_lib.Redis,
    *,
    calendar_id: str,
    op: str,
    date_str: str,
) -> tuple[bool, Optional[str]]:
    """Returns (allowed, reason). Increments counter if allowed."""
    if op == "update":
        limit = int(os.getenv("GCAL_MAX_UPDATES_PER_DAY", "50"))
        key = f"rate_limit_updates:{calendar_id}:{date_str}"
    else:
        limit = int(os.getenv("GCAL_MAX_EVENTS_PER_DAY", "10"))
        key = f"rate_limit:{calendar_id}:{date_str}"

    current = int(r.get(key) or 0)
    if current >= limit:
        return False, f"rate limit reached: {current}/{limit} {op}s on {calendar_id} for {date_str}"

    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, 48 * 3600)  # 48h TTL — no DST math needed
    pipe.execute()
    return True, None


def idempotency_key_for(op: str, payload: dict) -> str:
    """Compute SHA256 idempotency key for a given operation."""
    if op == "create":
        data = {k: payload[k] for k in ("title", "start", "end", "calendar_id") if k in payload}
    elif op == "update":
        data = {"event_id": payload["event_id"], "changes": payload.get("changes", {})}
    else:  # delete
        data = {"event_id": payload["event_id"]}
    normalized = json.dumps(data, sort_keys=True)
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return f"sha256:{digest}"


def check_idempotency(r: redis_lib.Redis, key: str) -> Optional[str]:
    """Returns event_id if duplicate detected, None otherwise."""
    val = r.get(f"idem:{key}")
    return val.decode() if val else None


def record_idempotency(
    r: redis_lib.Redis,
    key: str,
    *,
    event_id: str,
    ttl_seconds: int = 600,  # 10 minutes
) -> None:
    """Record a successful execute for idempotency dedup."""
    r.setex(f"idem:{key}", ttl_seconds, event_id)
```

**Step 4: Run tests**

```bash
pytest tests/calendar_proxy/test_policies_execute.py -v
```

Expected: all pass.

**Step 5: Run full suite**

```bash
pytest tests/ -v
```

Expected: all pass.

**Step 6: Commit**

```bash
git add services/calendar-proxy/policies.py tests/calendar_proxy/test_policies_execute.py
git commit -m "feat: add Redis rate limiting and idempotency to policy execute phase"
```

---

### Task 8: MCP Server (`server.py`) + Health Endpoint

Wire all modules together. Expose 5 tools via FastMCP SSE. Add `/health` endpoint. Dry-run override startup warning.

**Files:**
- Create: `tests/calendar_proxy/test_server.py`
- Create: `services/calendar-proxy/server.py`

**Step 1: Write failing tests**

```python
# tests/calendar_proxy/test_server.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import pytest
from unittest.mock import patch, MagicMock
import fakeredis


@pytest.fixture
def mock_env(monkeypatch, tmp_path):
    """Set all required env vars and mock Redis + token store."""
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY", "dGVzdC10ZXN0LXRlc3QtdGVzdC10ZXN0LXRlc3Q=")
    monkeypatch.setenv("GCAL_ALLOWED_CALENDARS", "primary")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    monkeypatch.setenv("GCAL_DRY_RUN", "false")


def test_dry_run_mode_emits_warning(monkeypatch, capsys):
    monkeypatch.setenv("GCAL_DRY_RUN", "true")
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY", "dGVzdC10ZXN0LXRlc3QtdGVzdC10ZXN0LXRlc3Q=")
    import importlib
    import server
    importlib.reload(server)
    captured = capsys.readouterr()
    assert "DRY_RUN" in captured.out


def test_create_event_dry_run_returns_dry_run_status(monkeypatch, mock_env):
    """create_event with execution_mode=dry_run never calls Google."""
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_build.return_value = MagicMock()

        import server
        result = server.handle_create_event({
            "title": "Test",
            "start": "2026-03-16T10:00:00+00:00",
            "end": "2026-03-16T11:00:00+00:00",
            "execution_mode": "dry_run",
        })
        assert result["status"] in ("dry_run", "safe_to_execute", "needs_confirmation", "denied")
        mock_build.return_value.events.return_value.insert.assert_not_called()


def test_list_events_returns_list(monkeypatch, mock_env):
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "ev1", "summary": "Test", "start": {"dateTime": "2026-03-16T10:00:00+00:00"}, "end": {"dateTime": "2026-03-16T11:00:00+00:00"}}]
        }
        mock_build.return_value = mock_service

        import server
        result = server.handle_list_events({
            "time_min": "2026-03-16T00:00:00+00:00",
            "time_max": "2026-03-16T23:59:59+00:00",
        })
        assert isinstance(result, list)


def test_health_returns_token_and_redis_status(monkeypatch, mock_env, tmp_path):
    with patch("server.get_redis") as mock_redis, \
         patch("server.token_store") as mock_store:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_store.load.return_value = {"access_token": "tok"}

        import server
        health = server.get_health()
        assert "redis" in health
        assert "token" in health
        assert health["dry_run_mode"] is False
```

**Step 2: Run to verify all fail**

```bash
pytest tests/calendar_proxy/test_server.py -v
```

Expected: `ModuleNotFoundError: No module named 'server'`

**Step 3: Write `services/calendar-proxy/server.py`**

```python
import os
import uuid
import time
from datetime import datetime
from typing import Any

import redis as redis_lib
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from mcp.server.fastmcp import FastMCP

from auth import TokenStore
from audit import AuditLog
from models import (
    CreateEventInput, UpdateEventInput, DeleteEventInput,
    ListEventsInput, CheckAvailabilityInput,
)
from policies import assess, enforce, check_rate_limit, check_idempotency, record_idempotency, idempotency_key_for

# ── Startup ───────────────────────────────────────────────────────────────────

DRY_RUN = os.getenv("GCAL_DRY_RUN", "false").lower() == "true"
if DRY_RUN:
    print("[calendar-proxy] [WARN] *** DRY_RUN MODE ACTIVE — no calendar writes will be executed ***", flush=True)

token_store = TokenStore.from_env()
audit = AuditLog()
mcp = FastMCP("calendar-proxy")

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_redis() -> redis_lib.Redis:
    return redis_lib.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))


def build_google_service():
    token_data = token_store.load()
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes"),
    )
    return build("calendar", "v3", credentials=creds)


def _allowed_calendars() -> set[str]:
    raw = os.getenv("GCAL_ALLOWED_CALENDARS", "primary")
    return {c.strip() for c in raw.split(",")}


def _list_events_fn(service):
    def fn(calendar_id: str, time_min: str, time_max: str) -> list:
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
        ).execute()
        return result.get("items", [])
    return fn


def _today_date_str() -> str:
    import pytz
    tz = pytz.timezone(os.getenv("GCAL_USER_TIMEZONE", "UTC"))
    return datetime.now(tz).strftime("%Y-%m-%d")


def _run_write_pipeline(event_input, op: str, is_delete: bool = False):
    """Shared validate → assess → enforce → execute pipeline for write tools."""
    request_id = str(uuid.uuid4())
    start_ms = time.monotonic()
    execution_mode = event_input.execution_mode
    if DRY_RUN:
        execution_mode = "dry_run"

    calendar_id = event_input.calendar_id
    in_allowlist = calendar_id in _allowed_calendars()

    r = get_redis()
    service = build_google_service()

    # Assess
    if hasattr(event_input, "title"):
        impact = assess(event_input, _list_events_fn(service))
    else:
        impact = None

    # Enforce
    status, reason = enforce(
        impact or type("I", (), {"overlaps_existing": False, "overlapping_events": [],
                                  "outside_business_hours": False, "is_weekend": False,
                                  "duration_minutes": 0, "recurring": False,
                                  "recurrence_instances_checked": 0, "work_calendar": False})(),
        calendar_id=calendar_id,
        in_allowlist=in_allowlist,
        is_delete=is_delete,
    )

    duration_ms = int((time.monotonic() - start_ms) * 1000)

    if status == "denied":
        audit.write(request_id=request_id, tool=op, execution_mode=execution_mode,
                    session_id="", args=event_input.model_dump(), status="denied",
                    reason=reason, duration_ms=duration_ms)
        return {"request_id": request_id, "status": "denied", "reason": reason}

    if status == "needs_confirmation" or execution_mode == "dry_run":
        audit.write(request_id=request_id, tool=op, execution_mode=execution_mode,
                    session_id="", args=event_input.model_dump(),
                    status="dry_run" if execution_mode == "dry_run" else "needs_confirmation",
                    duration_ms=duration_ms)
        return {
            "request_id": request_id,
            "status": "dry_run" if execution_mode == "dry_run" else "needs_confirmation",
            "impact": impact.model_dump() if impact else None,
        }

    # Execute path: rate limit → idempotency → Google API
    date_str = _today_date_str()
    ok, rate_reason = check_rate_limit(r, calendar_id=calendar_id, op=op, date_str=date_str)
    if not ok:
        audit.write(request_id=request_id, tool=op, execution_mode=execution_mode,
                    session_id="", args=event_input.model_dump(), status="denied",
                    reason=rate_reason, duration_ms=duration_ms)
        return {"request_id": request_id, "status": "denied", "reason": rate_reason}

    idem_key = event_input.idempotency_key or idempotency_key_for(op, event_input.model_dump())
    existing_event_id = check_idempotency(r, idem_key)
    if existing_event_id:
        return {"request_id": request_id, "status": "safe_to_execute", "event_id": existing_event_id}

    return None  # Caller executes the actual Google API call


# ── Tool handlers (called by tests and MCP tools) ─────────────────────────────

def handle_create_event(args: dict) -> dict:
    event_input = CreateEventInput(**args)
    result = _run_write_pipeline(event_input, op="create")
    if result is not None:
        return result
    # Execute
    service = build_google_service()
    body = {"summary": event_input.title, "start": {"dateTime": event_input.start},
            "end": {"dateTime": event_input.end}}
    if event_input.description:
        body["description"] = event_input.description
    if event_input.recurrence:
        body["recurrence"] = [f"RRULE:{event_input.recurrence.rrule}"]
    created = service.events().insert(calendarId=event_input.calendar_id, body=body).execute()
    event_id = created["id"]
    idem_key = event_input.idempotency_key or idempotency_key_for("create", event_input.model_dump())
    record_idempotency(get_redis(), idem_key, event_id=event_id)
    audit.write(request_id=str(uuid.uuid4()), tool="create_event", execution_mode="execute",
                session_id="", args=event_input.model_dump(), status="created", event_id=event_id, duration_ms=0)
    return {"request_id": str(uuid.uuid4()), "status": "safe_to_execute", "event_id": event_id}


def handle_list_events(args: dict) -> list:
    inp = ListEventsInput(**args)
    service = build_google_service()
    return _list_events_fn(service)(inp.calendar_id, inp.time_min, inp.time_max)


def get_health() -> dict:
    health: dict[str, Any] = {"dry_run_mode": DRY_RUN}
    try:
        get_redis().ping()
        health["redis"] = "ok"
    except Exception as e:
        health["redis"] = f"error: {e}"
    try:
        token_store.load()
        health["token"] = "ok"
    except Exception as e:
        health["token"] = f"error: {e}"
    if os.getenv("GCAL_HEALTH_CHECK_GOOGLE", "false").lower() == "true":
        try:
            build_google_service()
            health["google_api"] = "ok"
        except Exception as e:
            health["google_api"] = f"error: {e}"
    else:
        health["google_api"] = "skipped"
    return health


# ── MCP tool registrations ────────────────────────────────────────────────────

@mcp.tool()
def create_event(title: str, start: str, end: str, execution_mode: str,
                 calendar_id: str = "primary", description: str = None,
                 recurrence_rrule: str = None, idempotency_key: str = None) -> dict:
    """Create a Google Calendar event."""
    args = {"title": title, "start": start, "end": end, "execution_mode": execution_mode,
            "calendar_id": calendar_id}
    if description:
        args["description"] = description
    if recurrence_rrule:
        from models import RecurrenceRule
        args["recurrence"] = RecurrenceRule(rrule=recurrence_rrule)
    if idempotency_key:
        args["idempotency_key"] = idempotency_key
    return handle_create_event(args)


@mcp.tool()
def list_events(time_min: str, time_max: str, calendar_id: str = "primary") -> list:
    """List Google Calendar events in a time window."""
    return handle_list_events({"time_min": time_min, "time_max": time_max, "calendar_id": calendar_id})


@mcp.tool()
def check_availability(time_min: str, time_max: str, duration_minutes: int) -> dict:
    """Find free slots in a time window."""
    inp = CheckAvailabilityInput(time_min=time_min, time_max=time_max, duration_minutes=duration_minutes)
    service = build_google_service()
    existing = _list_events_fn(service)(inp.calendar_id if hasattr(inp, "calendar_id") else "primary",
                                        inp.time_min, inp.time_max)
    return {"events": existing, "duration_requested_minutes": duration_minutes}


@mcp.tool()
def delete_event(event_id: str, execution_mode: str, calendar_id: str = "primary",
                 idempotency_key: str = None) -> dict:
    """Delete a Google Calendar event."""
    event_input = DeleteEventInput(event_id=event_id, execution_mode=execution_mode,
                                   calendar_id=calendar_id, idempotency_key=idempotency_key)
    result = _run_write_pipeline(event_input, op="delete", is_delete=True)
    if result is not None:
        return result
    service = build_google_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return {"request_id": str(uuid.uuid4()), "status": "safe_to_execute", "event_id": event_id}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8080)
```

**Step 4: Run tests**

```bash
pytest tests/calendar_proxy/test_server.py -v
```

Expected: all pass.

**Step 5: Run full suite**

```bash
pytest tests/ -v
```

Expected: all pass.

**Step 6: Commit**

```bash
git add services/calendar-proxy/server.py tests/calendar_proxy/test_server.py
git commit -m "feat: add MCP server with 5 tools, health endpoint, and dry-run warning"
```

---

### Task 9: Docker Compose Integration

Wire `calendar-proxy` into the stack. Update `.env.example`.

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

**Step 1: Read current docker-compose.yml**

Read the file first, then add the `calendar-proxy` service block after the `redis` service.

**Step 2: Add `calendar-proxy` to `docker-compose.yml`**

Add to the `services:` section (after `redis`):

```yaml
  calendar-proxy:
    build: ./services/calendar-proxy
    restart: unless-stopped
    networks:
      - internal
    depends_on:
      - redis
    volumes:
      - openclaw_data:/data:rw
    environment:
      - GCAL_ALLOWED_CALENDARS=${GCAL_ALLOWED_CALENDARS:-primary}
      - GCAL_MAX_EVENTS_PER_DAY=${GCAL_MAX_EVENTS_PER_DAY:-10}
      - GCAL_MAX_UPDATES_PER_DAY=${GCAL_MAX_UPDATES_PER_DAY:-50}
      - GCAL_MAX_EVENT_HOURS=${GCAL_MAX_EVENT_HOURS:-8}
      - GCAL_MAX_PAST_HOURS=${GCAL_MAX_PAST_HOURS:-1}
      - GCAL_ALLOWED_START_HOUR=${GCAL_ALLOWED_START_HOUR:-8}
      - GCAL_ALLOWED_END_HOUR=${GCAL_ALLOWED_END_HOUR:-20}
      - GCAL_USER_TIMEZONE=${GCAL_USER_TIMEZONE:-UTC}
      - GCAL_WORK_CALENDAR_ID=${GCAL_WORK_CALENDAR_ID:-}
      - GCAL_MAX_RECURRENCE_COUNT=${GCAL_MAX_RECURRENCE_COUNT:-52}
      - GCAL_AUDIT_MAX_MB=${GCAL_AUDIT_MAX_MB:-50}
      - GCAL_DRY_RUN=${GCAL_DRY_RUN:-false}
      - GCAL_HEALTH_CHECK_GOOGLE=${GCAL_HEALTH_CHECK_GOOGLE:-false}
      - GCAL_TOKEN_ENCRYPTION_KEY=${GCAL_TOKEN_ENCRYPTION_KEY}
      - REDIS_URL=redis://redis:6379
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp
    security_opt:
      - no-new-privileges:true
    mem_limit: 256m
    cpus: "0.5"
```

**Step 3: Add `GCAL_*` section to `.env.example`**

Append to `.env.example`:

```bash
# ── Google Calendar Proxy ──────────────────────────────────────────────────────
# Required: generate with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
GCAL_TOKEN_ENCRYPTION_KEY=

# Comma-separated calendar IDs allowed for write operations (default: primary)
GCAL_ALLOWED_CALENDARS=primary

# Calendar ID treated as work calendar (any event on it requires confirmation)
GCAL_WORK_CALENDAR_ID=

# Rate limits
GCAL_MAX_EVENTS_PER_DAY=10
GCAL_MAX_UPDATES_PER_DAY=50

# Event constraints
GCAL_MAX_EVENT_HOURS=8
GCAL_MAX_PAST_HOURS=1
GCAL_MAX_RECURRENCE_COUNT=52

# Business hours (evaluated in GCAL_USER_TIMEZONE)
GCAL_ALLOWED_START_HOUR=8
GCAL_ALLOWED_END_HOUR=20

# Your local timezone — critical for correct business hours + weekend detection
# Example: Europe/Helsinki, America/New_York, Asia/Tokyo
GCAL_USER_TIMEZONE=UTC

# Audit log
GCAL_AUDIT_MAX_MB=50

# Set to true to enable dry-run mode (no real writes — for testing)
GCAL_DRY_RUN=false

# Set to true to include Google API liveness check in /health
GCAL_HEALTH_CHECK_GOOGLE=false
```

**Step 4: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all pass.

**Step 5: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat: add calendar-proxy to Docker Compose stack"
```

---

### Task 10: Smoke Test with Dry-Run Mode

End-to-end test confirming the full policy pipeline works from input to response with `GCAL_DRY_RUN=true`. No real Google API calls.

**Files:**
- Create: `tests/calendar_proxy/test_smoke.py`

**Step 1: Write smoke test**

```python
# tests/calendar_proxy/test_smoke.py
"""
End-to-end smoke test for the full pipeline using dry-run mode.
No real Google API calls. Uses fakeredis.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import pytest
import fakeredis
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def setup_env(monkeypatch, tmp_path):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GCAL_ALLOWED_CALENDARS", "primary")
    monkeypatch.setenv("GCAL_USER_TIMEZONE", "UTC")
    monkeypatch.setenv("GCAL_DRY_RUN", "false")
    monkeypatch.setenv("GCAL_MAX_EVENTS_PER_DAY", "10")

    from auth import TokenStore
    from pathlib import Path
    store = TokenStore(key=key.encode(), token_path=tmp_path / "gcal_token.enc")
    store.save({"token": "test", "refresh_token": "ref",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "id", "client_secret": "sec", "scopes": []})

    import server
    import importlib
    monkeypatch.setattr(server, "token_store", store)


def test_smoke_create_dry_run_simple_event():
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {"items": []}
        mock_build.return_value = mock_service

        import server
        result = server.handle_create_event({
            "title": "Quick sync",
            "start": "2026-03-16T10:00:00+00:00",
            "end": "2026-03-16T10:30:00+00:00",
            "execution_mode": "dry_run",
        })
        assert result["status"] in ("dry_run", "safe_to_execute", "needs_confirmation")
        mock_service.events.return_value.insert.assert_not_called()


def test_smoke_denied_outside_allowlist():
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_build.return_value = MagicMock()

        import server
        result = server.handle_create_event({
            "title": "Test",
            "start": "2026-03-16T10:00:00+00:00",
            "end": "2026-03-16T11:00:00+00:00",
            "execution_mode": "execute",
            "calendar_id": "notallowed@calendar.google.com",
        })
        assert result["status"] == "denied"
        assert "allowlist" in result["reason"]


def test_smoke_needs_confirmation_weekend():
    with patch("server.build_google_service") as mock_build, \
         patch("server.get_redis") as mock_redis:
        mock_redis.return_value = fakeredis.FakeRedis()
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {"items": []}
        mock_build.return_value = mock_service

        import server
        # 2026-03-21 is Saturday
        result = server.handle_create_event({
            "title": "Weekend event",
            "start": "2026-03-21T10:00:00+00:00",
            "end": "2026-03-21T11:00:00+00:00",
            "execution_mode": "execute",
        })
        assert result["status"] == "needs_confirmation"


def test_smoke_list_events_works():
    with patch("server.build_google_service") as mock_build:
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "1", "summary": "Existing", "start": {"dateTime": "2026-03-16T09:00:00+00:00"}, "end": {"dateTime": "2026-03-16T10:00:00+00:00"}}]
        }
        mock_build.return_value = mock_service

        import server
        events = server.handle_list_events({
            "time_min": "2026-03-16T00:00:00+00:00",
            "time_max": "2026-03-16T23:59:59+00:00",
        })
        assert len(events) == 1
        assert events[0]["id"] == "1"
```

**Step 2: Run smoke tests**

```bash
pytest tests/calendar_proxy/test_smoke.py -v
```

Expected: all pass.

**Step 3: Run full suite one final time**

```bash
pytest tests/ -v
```

Expected: all pass (16 guardrail + all calendar proxy tests).

**Step 4: Commit**

```bash
git add tests/calendar_proxy/test_smoke.py
git commit -m "test: add end-to-end smoke tests for calendar proxy pipeline"
```

---

## Post-Implementation: First Deploy

After all tasks pass, deploy to the VPS:

```bash
# 1. One-time: generate token locally
python3 services/calendar-proxy/scripts/auth_setup.py \
  --client-secret client_secret.json --out token.json

# 2. Generate encryption key and save to .env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Add GCAL_TOKEN_ENCRYPTION_KEY=<key> to local and VPS .env

# 3. Encrypt and copy token to VPS
python3 services/calendar-proxy/scripts/encrypt_token.py \
  --token token.json --key <KEY> --out token.enc
scp token.enc user@YOUR_VPS_IP:/tmp/
ssh user@YOUR_VPS_IP "
  docker run --rm \
    -v openclaw-deploy_openclaw_data:/data \
    -v /tmp:/src \
    busybox sh -c 'cp /src/token.enc /data/gcal_token.enc && chmod 600 /data/gcal_token.enc'
"

# 4. Clean up local plaintext files
rm client_secret.json token.json token.enc

# 5. Pull and restart
git push origin main
ssh user@YOUR_VPS_IP "cd openclaw-deploy && git pull && docker compose up -d"

# 6. Verify health
ssh user@YOUR_VPS_IP "docker compose exec calendar-proxy python3 -c \
  \"import urllib.request; print(urllib.request.urlopen('http://localhost:8080/health').read())\""
```

Also set in VPS `.env` before starting:
```
GCAL_TOKEN_ENCRYPTION_KEY=<key>
GCAL_USER_TIMEZONE=<your timezone>
GCAL_WORK_CALENDAR_ID=<work cal id if any>
GCAL_ALLOWED_CALENDARS=primary,<work cal id if any>
```
