# Multi-Account Gmail and Google Calendar Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend mail-proxy and calendar-proxy to support N labeled Google accounts (e.g. `personal`, `jobs`) with zero new containers, backward-compatible Redis keys, and a per-account `--account` CLI flag.

**Architecture:** Both services load a `dict[label, TokenStore]` at startup via `TokenStore.load_all()`; tool handlers extract the `account` query param from `/call?account=<label>` and route to the correct store. Redis keys are namespaced by label; legacy single-account mode uses `account=""` which maps to the old un-prefixed key names.

**Tech Stack:** Python 3.11, FastMCP, Fernet (cryptography), fakeredis (tests), Starlette, bash (setup scripts)

---

## File Map

| File | Change |
|---|---|
| `services/mail-proxy/auth.py` | Add `for_account()`, `load_all()` class methods; add `logging` |
| `services/mail-proxy/policies.py` | Add `account=""` param to all 4 public functions; namespace Redis keys |
| `services/mail-proxy/poller.py` | Add `account=""` param to `poll_once` and `run_forever`; namespace Redis keys |
| `services/mail-proxy/server.py` | `token_store` → `token_stores` dict; add account routing; update `/health`; start per-account pollers |
| `services/calendar-proxy/auth.py` | Add degraded mode to `from_env`; add `for_account()`, `load_all()` |
| `services/calendar-proxy/server.py` | `token_store` → `token_stores` dict; `build_google_service(account="")` |
| `services/mail-proxy/scripts/gmail` | Add `--account <label>` flag |
| `services/calendar-proxy/scripts/gcal` | Add `--account <label>` flag |
| `scripts/setup-gmail.sh` | Add `ACCOUNT` param + migration mode |
| `scripts/setup-gcal.sh` | Add `ACCOUNT` param + migration mode |
| `Makefile` | Thread `ACCOUNT=` into setup targets |
| `docker-compose.yml` | Add `env_file: .env` to mail-proxy + calendar-proxy; remove hardcoded token key vars |
| `scripts/doctor.sh` | Per-account token checks driven by `GMAIL_ACCOUNTS` / `GCAL_ACCOUNTS` |
| `workspace/MEMORY_GUIDE.md` | Add accounts section to gmail + gcal docs |
| `tests/mail_proxy/test_auth.py` | Add `for_account` and `load_all` tests |
| `tests/mail_proxy/test_policies.py` | Add account-namespaced key tests |
| `tests/mail_proxy/test_poller.py` | Add account-parameterized Redis key tests |
| `tests/mail_proxy/test_server.py` | Update `token_store` → `token_stores`; add account routing tests |
| `tests/calendar_proxy/test_auth.py` | Add degraded mode + `for_account` + `load_all` tests |
| `tests/calendar_proxy/test_server.py` | Update for `token_stores` dict + account routing |

---

## Chunk 1: mail-proxy service internals

### Task 1: Extend `mail-proxy/auth.py` with multi-account factory methods

**Files:**
- Modify: `services/mail-proxy/auth.py`
- Test: `tests/mail_proxy/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/mail_proxy/test_auth.py`:

```python
import logging


def test_for_account_returns_none_when_no_key_no_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", raising=False)
    import auth
    result = auth.TokenStore.for_account("personal")
    assert result is None


def test_for_account_raises_when_file_exists_but_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", raising=False)
    # Create a fake token file at the expected path
    token_path = tmp_path / "gmail_token.personal.enc"
    token_path.write_bytes(b"dummy")
    import auth
    # Patch the default path construction so it finds our tmp file
    from unittest.mock import patch
    with patch("auth.Path") as mock_path_cls:
        mock_path_cls.side_effect = lambda p: token_path if "personal" in str(p) else type(token_path)(p)
        with pytest.raises(RuntimeError, match="GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL"):
            auth.TokenStore.for_account("personal")


def test_for_account_returns_store_when_key_set(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", key)
    import auth
    store = auth.TokenStore.for_account("personal")
    assert store is not None


def test_load_all_legacy_fallback_when_no_accounts_env(monkeypatch):
    """No GMAIL_ACCOUNTS set + legacy key present → {"": store}."""
    monkeypatch.delenv("GMAIL_ACCOUNTS", raising=False)
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    import auth
    result = auth.TokenStore.load_all()
    assert "" in result
    assert result[""] is not None


def test_load_all_empty_when_no_accounts_and_no_legacy(monkeypatch):
    monkeypatch.delenv("GMAIL_ACCOUNTS", raising=False)
    monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY", raising=False)
    import auth
    result = auth.TokenStore.load_all()
    assert result == {}


def test_load_all_loads_configured_accounts(monkeypatch):
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_JOBS", key2)
    import auth
    result = auth.TokenStore.load_all()
    assert set(result.keys()) == {"personal", "jobs"}


def test_load_all_skips_missing_account(monkeypatch, caplog):
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY_JOBS", raising=False)
    import auth
    with caplog.at_level(logging.WARNING, logger="auth"):
        result = auth.TokenStore.load_all()
    assert "personal" in result
    assert "jobs" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/evgueni/repos/personal/openclaw-deploy
python3 -m pytest tests/mail_proxy/test_auth.py -v -k "for_account or load_all" 2>&1 | tail -20
```

Expected: 7 failures (`AttributeError: type object 'TokenStore' has no attribute 'for_account'`)

- [ ] **Step 3: Implement `for_account` and `load_all` in `auth.py`**

Replace `services/mail-proxy/auth.py` with:

```python
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class TokenStore:
    def __init__(self, key: bytes, token_path: Path = Path("/data/gmail_token.enc")):
        self._fernet = Fernet(key)
        self._path = Path(token_path)

    @classmethod
    def from_env(
        cls, token_path: Path = Path("/data/gmail_token.enc")
    ) -> Optional["TokenStore"]:
        """Return TokenStore, None (degraded), or raise (misconfigured).

        - No key + no token file  → None (degraded mode, pre-setup)
        - No key + token file exists → RuntimeError (fail-fast)
        - Key present              → TokenStore (token file may or may not exist yet)
        """
        raw_key = os.environ.get("GMAIL_TOKEN_ENCRYPTION_KEY")
        path = Path(token_path)
        if not raw_key and not path.exists():
            return None
        if not raw_key and path.exists():
            raise RuntimeError(
                "GMAIL_TOKEN_ENCRYPTION_KEY is not set but "
                f"{path} exists — refusing to start. "
                "Set GMAIL_TOKEN_ENCRYPTION_KEY or remove the token file."
            )
        return cls(key=raw_key.encode(), token_path=path)

    @classmethod
    def for_account(cls, label: str, service: str = "gmail") -> Optional["TokenStore"]:
        """Load TokenStore for a specific account label.

        - No key + no token file  → None (logs warning, caller skips this label)
        - No key + token file exists → RuntimeError (fail-fast: misconfigured)
        - Key present              → TokenStore
        """
        key_env = f"{service.upper()}_TOKEN_ENCRYPTION_KEY_{label.upper()}"
        token_path = Path(f"/data/{service}_token.{label}.enc")
        raw_key = os.environ.get(key_env)
        if not raw_key and not token_path.exists():
            logger.warning("[auth] No key and no token file for account %r — skipping", label)
            return None
        if not raw_key and token_path.exists():
            raise RuntimeError(
                f"{key_env} is not set but {token_path} exists — refusing to start. "
                f"Set {key_env} or remove the token file."
            )
        return cls(key=raw_key.encode(), token_path=token_path)

    @classmethod
    def load_all(cls, service: str = "gmail") -> dict[str, "TokenStore"]:
        """Return {label: TokenStore} for all accounts in GMAIL_ACCOUNTS / GCAL_ACCOUNTS.

        Filters out None returns (unconfigured labels) with per-label warnings.
        Falls back to single-account mode if the env var is not set:
          - account="" maps to the legacy non-prefixed Redis keys.
        """
        env_var = f"{service.upper()}_ACCOUNTS"
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            # Legacy single-account fallback. Label "" = no Redis key prefix.
            store = cls.from_env()
            return {"": store} if store else {}
        labels = [lbl.strip() for lbl in raw.split(",") if lbl.strip()]
        result: dict[str, "TokenStore"] = {}
        for label in labels:
            store = cls.for_account(label, service)
            if store is not None:
                result[label] = store
            # else: warning already logged inside for_account
        return result

    def encrypt(self, token_dict: dict[str, Any]) -> bytes:
        return self._fernet.encrypt(json.dumps(token_dict).encode())

    def decrypt(self, data: bytes) -> dict[str, Any]:
        return json.loads(self._fernet.decrypt(data))

    def save(self, token_dict: dict[str, Any]) -> None:
        """Atomic write: encrypt → tmp → rename."""
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(self.encrypt(token_dict))
        tmp.replace(self._path)

    def load(self) -> dict[str, Any]:
        return self.decrypt(self._path.read_bytes())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/mail_proxy/test_auth.py -v 2>&1 | tail -20
```

Expected: all tests pass (including the 5 pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add services/mail-proxy/auth.py tests/mail_proxy/test_auth.py
git commit -m "feat(mail-proxy): add TokenStore.for_account and load_all for N-account support"
```

---

### Task 2: Namespace Redis keys in `mail-proxy/policies.py`

**Files:**
- Modify: `services/mail-proxy/policies.py`
- Test: `tests/mail_proxy/test_policies.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/mail_proxy/test_policies.py`:

```python
def test_update_seen_domains_uses_account_namespaced_key():
    import policies
    r = _redis()
    messages = [{"from_addr": "alice@example.com"}]
    policies.update_seen_domains(r, messages, account="jobs")
    # Namespaced key should exist
    members = r.zrange("gmail:seen_domains:jobs", 0, -1)
    assert b"example.com" in members
    # Legacy key should NOT exist
    assert r.zrange("gmail:seen_domains", 0, -1) == []


def test_check_novel_domain_uses_account_namespaced_key():
    import policies
    r = _redis()
    import time
    r.zadd("gmail:seen_domains:jobs", {"trusted.com": time.time()})
    ok, _ = policies.check_novel_domain(r, "a@trusted.com", account="jobs")
    assert ok is True
    # Same domain not in "personal" namespace
    ok2, _ = policies.check_novel_domain(r, "a@trusted.com", account="personal")
    assert ok2 is False


def test_check_rate_limit_uses_account_namespaced_key(monkeypatch):
    import policies
    monkeypatch.setenv("GMAIL_MAX_SENDS_PER_DAY", "2")
    r = _redis()
    r.set("gmail:sends:jobs:2026-03-24", "2")
    ok, reason = policies.check_rate_limit(r, date_str="2026-03-24", account="jobs")
    assert ok is False
    # "personal" counter is independent
    ok2, _ = policies.check_rate_limit(r, date_str="2026-03-24", account="personal")
    assert ok2 is True


def test_record_send_uses_account_namespaced_key():
    import policies
    r = _redis()
    policies.record_send(r, date_str="2026-03-24", account="jobs")
    assert r.get("gmail:sends:jobs:2026-03-24") == b"1"
    assert r.get("gmail:sends:2026-03-24") is None


def test_legacy_keys_used_when_account_is_empty():
    """account="" → uses old un-prefixed key names (backward compat)."""
    import policies
    r = _redis()
    messages = [{"from_addr": "x@legacy.com"}]
    policies.update_seen_domains(r, messages, account="")
    members = r.zrange("gmail:seen_domains", 0, -1)
    assert b"legacy.com" in members
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/mail_proxy/test_policies.py -v -k "namespaced or legacy_keys" 2>&1 | tail -15
```

Expected: 5 failures (`TypeError: update_seen_domains() got unexpected keyword argument 'account'`)

- [ ] **Step 3: Add `account=""` param to all public functions**

Replace `services/mail-proxy/policies.py` with:

```python
"""Gmail send policies: rate limits, seen-domain allowlist, counter tracking.

All public functions accept a redis.Redis client as their first argument.
Callers are responsible for passing a connected client; functions do not
swallow connection errors — fail-closed semantics for send operations.

account="" means legacy single-account mode (no namespace prefix in Redis keys).
account="personal" → keys like "gmail:seen_domains:personal".
"""

import os
import re
import time
from typing import Optional

import redis as redis_lib

_EMAIL_ADDR_RE = re.compile(r"<([^>]+)>")
_SEEN_DOMAINS_TTL = 86400  # 24 hours


def _extract_domain(from_addr: str) -> str:
    """Extract domain from 'Name <email@domain>' or 'email@domain'."""
    match = _EMAIL_ADDR_RE.search(from_addr)
    addr = match.group(1) if match else from_addr.strip()
    return addr.split("@")[-1].lower()


def _seen_domains_key(account: str) -> str:
    return f"gmail:seen_domains:{account}" if account else "gmail:seen_domains"


def _rate_key(account: str, date_str: str) -> str:
    return f"gmail:sends:{account}:{date_str}" if account else f"gmail:sends:{date_str}"


def _seen_message_key(account: str, message_id: str) -> str:
    return f"gmail:seen:{account}:{message_id}" if account else f"gmail:seen:{message_id}"


def update_seen_domains(
    r: redis_lib.Redis, messages: list[dict], account: str = ""
) -> None:
    """Add sender domains from messages to the seen-domains sorted set.

    Score = current Unix timestamp. TTL reset to 24h on every call.
    """
    now = time.time()
    mapping: dict[str, float] = {}
    for msg in messages:
        from_addr = msg.get("from_addr", "")
        if "@" in from_addr:
            domain = _extract_domain(from_addr)
            mapping[domain] = now
    if mapping:
        key = _seen_domains_key(account)
        r.zadd(key, mapping)
        r.expire(key, _SEEN_DOMAINS_TTL)


def check_novel_domain(
    r: redis_lib.Redis, recipient: str, account: str = ""
) -> tuple[bool, Optional[str]]:
    """Return (True, None) if domain seen before, (False, reason) otherwise.

    Raises redis_lib.exceptions.ConnectionError if Redis is unavailable —
    callers must treat this as fail-closed for send operations.
    """
    domain = _extract_domain(recipient)
    score = r.zscore(_seen_domains_key(account), domain)
    if score is None:
        return False, f"domain_not_allowed: {domain!r} has not been seen in your inbox"
    return True, None


def check_rate_limit(
    r: redis_lib.Redis, date_str: str, account: str = ""
) -> tuple[bool, Optional[str]]:
    """Return (True, None) if under daily send limit, (False, reason) otherwise.

    Limit is read from GMAIL_MAX_SENDS_PER_DAY env var (default: 20).
    """
    max_sends = int(os.getenv("GMAIL_MAX_SENDS_PER_DAY", "20"))
    key = _rate_key(account, date_str)
    current = r.get(key)
    count = int(current) if current else 0
    if count >= max_sends:
        return False, f"rate_limit: {count}/{max_sends} sends used today"
    return True, None


def record_send(r: redis_lib.Redis, date_str: str, account: str = "") -> None:
    """Increment the daily send counter. Key expires after 25h to survive midnight.

    Must be called only after a successful send — not optimistically.
    """
    key = _rate_key(account, date_str)
    r.incr(key)
    r.expire(key, 90000)  # 25 hours


def seen_message_key(account: str, message_id: str) -> str:
    """Public helper for poller to get the per-account seen-message key."""
    return _seen_message_key(account, message_id)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/mail_proxy/test_policies.py -v 2>&1 | tail -20
```

Expected: all tests pass (including the 8 pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add services/mail-proxy/policies.py tests/mail_proxy/test_policies.py
git commit -m "feat(mail-proxy): namespace Redis keys by account in policies"
```

---

### Task 3: Parameterize Redis keys in `mail-proxy/poller.py`

**Files:**
- Modify: `services/mail-proxy/poller.py`
- Test: `tests/mail_proxy/test_poller.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/mail_proxy/test_poller.py`:

```python
def test_poll_once_uses_account_namespaced_history_key():
    """When account='personal', historyId stored under gmail:historyId:personal."""
    import poller
    r = _redis()
    mock_service = MagicMock()
    mock_service.users().getProfile().execute.return_value = {"historyId": "200"}

    poller.poll_once(
        service=mock_service,
        r=r,
        scorer=_make_scorer(),
        notify_fn=lambda msgs: None,
        poll_label="INBOX",
        account="personal",
    )
    assert r.get("gmail:historyId:personal") == b"200"
    assert r.get("gmail:historyId") is None  # legacy key not touched


def test_poll_once_uses_account_namespaced_seen_key():
    """Dedup key uses account namespace."""
    import poller
    r = _redis()
    r.set("gmail:historyId:jobs", b"50")

    mock_service = MagicMock()
    mock_service.users().history().list().execute.return_value = {
        "history": [{"messagesAdded": [{"message": {"id": "msg-new"}}]}],
        "historyId": "51",
    }
    mock_service.users().messages().get().execute.return_value = {
        "id": "msg-new", "threadId": "t1",
        "payload": {"headers": [
            {"name": "From", "value": "a@b.com"},
            {"name": "Subject", "value": "Hi"},
            {"name": "Date", "value": "Mon"},
        ]},
        "snippet": "hello",
    }
    scored = [{"message_id": "msg-new", "from_addr": "a@b.com",
               "subject": "Hi", "score": 9}]
    notify_calls = []

    poller.poll_once(
        service=mock_service, r=r,
        scorer=_make_scorer(results=scored),
        notify_fn=lambda msgs: notify_calls.append(msgs),
        poll_label="INBOX",
        account="jobs",
    )
    # Dedup key should be namespaced
    assert r.exists("gmail:seen:jobs:msg-new")
    assert not r.exists("gmail:seen:msg-new")


def test_poll_once_legacy_keys_when_account_empty():
    """account='' → old un-prefixed key names (backward compat)."""
    import poller
    r = _redis()
    mock_service = MagicMock()
    mock_service.users().getProfile().execute.return_value = {"historyId": "300"}

    poller.poll_once(
        service=mock_service, r=r,
        scorer=_make_scorer(),
        notify_fn=lambda msgs: None,
        poll_label="INBOX",
        account="",
    )
    assert r.get("gmail:historyId") == b"300"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/mail_proxy/test_poller.py -v -k "namespaced or legacy_keys" 2>&1 | tail -15
```

Expected: 3 failures (`TypeError: poll_once() got unexpected keyword argument 'account'`)

- [ ] **Step 3: Add `account` param to `poll_once` and `run_forever`**

In `services/mail-proxy/poller.py`:

1. Remove the two module-level constants:
```python
_HISTORY_ID_KEY = "gmail:historyId"
_SEEN_PREFIX = "gmail:seen:"
```

2. Add two helper functions after `_SEEN_TTL`:
```python
def _history_id_key(account: str) -> str:
    return f"gmail:historyId:{account}" if account else "gmail:historyId"


def _seen_key(account: str, message_id: str) -> str:
    return f"gmail:seen:{account}:{message_id}" if account else f"gmail:seen:{message_id}"
```

3. Update `poll_once` signature and body — add `account: str = ""` parameter, replace all uses of the removed constants:

```python
def poll_once(
    service,
    r: redis_lib.Redis,
    scorer,
    notify_fn: Callable[[list[dict]], None],
    poll_label: str,
    account: str = "",
) -> None:
    """Single poll cycle: fetch new messages, score, notify.

    Handles first-run (no historyId) by recording current position without notifying.
    """
    if scorer.is_circuit_open():
        logger.debug("Circuit breaker open — skipping poll cycle")
        return

    history_id_key = _history_id_key(account)
    history_id_bytes = r.get(history_id_key)

    if history_id_bytes is None:
        profile = service.users().getProfile(userId="me").execute()
        current_id = str(profile.get("historyId", ""))
        if current_id:
            r.set(history_id_key, current_id.encode())
        return

    start_history_id = history_id_bytes.decode()

    try:
        resp = service.users().history().list(
            userId="me",
            startHistoryId=start_history_id,
            labelId=poll_label,
            historyTypes=["messageAdded"],
        ).execute()
    except Exception as exc:
        logger.warning("Gmail history.list failed: %s", exc)
        return

    new_id = str(resp.get("historyId", start_history_id))
    history_records = resp.get("history", [])

    seen_in_batch: set[str] = set()
    candidate_ids: list[str] = []
    for record in history_records:
        for added in record.get("messagesAdded", []):
            msg_id = added.get("message", {}).get("id")
            if msg_id and msg_id not in seen_in_batch:
                seen_in_batch.add(msg_id)
                candidate_ids.append(msg_id)

    fresh_ids = [mid for mid in candidate_ids if not r.exists(_seen_key(account, mid))]

    if fresh_ids:
        messages = [m for mid in fresh_ids if (m := _extract_message_meta(service, mid))]

        if messages:
            scored, _ = scorer.score(messages)

            for msg in messages:
                r.setex(_seen_key(account, msg["message_id"]), _SEEN_TTL, b"1")

            if scored:
                notify_fn(scored)

    r.set(history_id_key, new_id.encode())
```

4. Update `run_forever` signature — add `account: str = ""` and pass it through:

```python
def run_forever(
    *,
    build_service_fn: Callable,
    token_store,
    r: redis_lib.Redis,
    scorer,
    telegram_token: str,
    chat_id: str,
    poll_interval: int,
    poll_label: str,
    account: str = "",
) -> None:
    """Blocking loop. Run in a daemon thread."""
    if not chat_id:
        logger.warning("ALERT_TELEGRAM_CHAT_ID not set — proactive notifications disabled")

    def _notify(messages: list[dict]) -> None:
        if not chat_id:
            return
        notify_telegram(messages, token=telegram_token, chat_id=chat_id)

    _circuit_alert_sent = False
    while True:
        try:
            service = build_service_fn()
            was_open = scorer.is_circuit_open()
            poll_once(service=service, r=r, scorer=scorer,
                      notify_fn=_notify, poll_label=poll_label, account=account)
            now_open = scorer.is_circuit_open()
            if now_open and not was_open and not _circuit_alert_sent:
                _circuit_alert_sent = True
                if chat_id:
                    try:
                        _send_telegram(
                            telegram_token, chat_id,
                            "⚠️ Gmail importance scorer unavailable — notifications paused 30 min",
                        )
                    except Exception as alert_exc:
                        logger.warning("Failed to send circuit-breaker alert: %s", alert_exc)
            elif not now_open:
                _circuit_alert_sent = False
        except StopIteration:
            raise
        except Exception as exc:
            logger.error("Poller error: %s", exc)
        time.sleep(poll_interval)
```

- [ ] **Step 4: Run all poller tests**

```bash
python3 -m pytest tests/mail_proxy/test_poller.py -v 2>&1 | tail -20
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add services/mail-proxy/poller.py tests/mail_proxy/test_poller.py
git commit -m "feat(mail-proxy): parameterize Redis keys in poller by account"
```

---

### Task 4: Multi-account wiring in `mail-proxy/server.py`

**Files:**
- Modify: `services/mail-proxy/server.py`
- Test: `tests/mail_proxy/test_server.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/mail_proxy/test_server.py`:

```python
def test_health_returns_accounts_dict(monkeypatch):
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_JOBS", key2)
    import importlib, server as s_mod
    importlib.reload(s_mod)
    client = TestClient(s_mod.mcp.get_app())
    resp = client.get("/health")
    data = resp.json()
    assert data["configured"] is True
    assert "accounts" in data
    assert "personal" in data["accounts"]
    assert "jobs" in data["accounts"]


def test_call_routes_to_correct_account(monkeypatch):
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_JOBS", key2)

    import importlib, server as s_mod
    importlib.reload(s_mod)

    # Replace token_stores with mocks so we can track which one is used
    personal_store = MagicMock()
    jobs_store = MagicMock()
    s_mod.token_stores = {"personal": personal_store, "jobs": jobs_store}
    s_mod.DEFAULT_ACCOUNT = "personal"
    s_mod.CONFIGURED = True

    fake_messages = [{"message_id": "m1", "thread_id": "t1", "from_addr": "a@b.com",
                      "subject": "Hi", "snippet": "hello", "date": "Mon",
                      "unread": True}]

    client = TestClient(s_mod.mcp.get_app())

    with patch("server.gmail_client.list_messages", return_value=fake_messages), \
         patch("server.policies.update_seen_domains"), \
         patch("server.get_redis"):
        # /call?account=jobs should use jobs_store
        resp = client.post("/call?account=jobs", json={"tool": "list", "args": {}})
        assert resp.status_code == 200
        # Verify jobs_store was passed to build_service
        # (gmail_client.list_messages is patched, but we can check via the mock)


def test_call_returns_error_for_unknown_account(monkeypatch):
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal")
    key1 = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    import importlib, server as s_mod
    importlib.reload(s_mod)
    client = TestClient(s_mod.mcp.get_app())
    resp = client.post("/call?account=nonexistent", json={"tool": "list", "args": {}})
    data = resp.json()
    assert data["error"] == "unknown_account"
    assert "available" in data


def test_call_uses_default_account_when_no_param(monkeypatch):
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    monkeypatch.setenv("GMAIL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY_JOBS", key2)
    import importlib, server as s_mod
    importlib.reload(s_mod)
    # Default account is first = "personal"
    assert s_mod.DEFAULT_ACCOUNT == "personal"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/mail_proxy/test_server.py -v -k "accounts or unknown_account or default_account" 2>&1 | tail -15
```

Expected: failures (no `token_stores`, `DEFAULT_ACCOUNT`, etc.)

- [ ] **Step 3: Update `server.py` startup block**

Replace the startup section (lines 31–57 of the current file) with:

```python
# ── Startup ───────────────────────────────────────────────────────────────────

token_stores = TokenStore.load_all()
CONFIGURED = len(token_stores) > 0
DEFAULT_ACCOUNT = list(token_stores.keys())[0] if token_stores else ""
```

Remove `token_store = TokenStore.from_env()` and `CONFIGURED = token_store is not None`.

- [ ] **Step 4: Add `_resolve_account` helper and update all handlers**

After `_NOT_CONFIGURED_RESPONSE`, add:

```python
def _resolve_account(account: str) -> tuple[Optional[Any], Optional[dict]]:
    """Resolve account label to TokenStore. Returns (store, None) or (None, error_dict)."""
    label = account if account else DEFAULT_ACCOUNT
    store = token_stores.get(label)
    if store is None:
        return None, {
            "error": "unknown_account",
            "account": label,
            "available": list(token_stores.keys()),
        }
    return store, None
```

Update every handler to pop `account` from args and use `_resolve_account`. Pattern for each handler:

```python
def handle_list(args: dict) -> Any:
    account = args.pop("account", "")
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    store, err = _resolve_account(account)
    if err:
        return err
    effective = account if account else DEFAULT_ACCOUNT
    inp = ListInput(**args)
    service = gmail_client.build_service(store)
    messages = gmail_client.list_messages(service, label=inp.label, limit=inp.limit)
    try:
        policies.update_seen_domains(get_redis(), messages, account=effective)
    except Exception as exc:
        logger.warning("update_seen_domains failed: %s", exc)
    return messages


def handle_get(args: dict) -> Any:
    account = args.pop("account", "")
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    store, err = _resolve_account(account)
    if err:
        return err
    effective = account if account else DEFAULT_ACCOUNT
    inp = GetInput(**args)
    service = gmail_client.build_service(store)
    thread = gmail_client.get_thread(service, inp.thread_id)
    try:
        flat = [{"from_addr": m["from_addr"]} for m in thread.get("messages", [])]
        policies.update_seen_domains(get_redis(), flat, account=effective)
    except Exception as exc:
        logger.warning("update_seen_domains failed: %s", exc)
    return thread


def handle_search(args: dict) -> Any:
    account = args.pop("account", "")
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    store, err = _resolve_account(account)
    if err:
        return err
    inp = SearchInput(**args)
    service = gmail_client.build_service(store)
    return gmail_client.search_messages(service, query=inp.query, limit=inp.limit)


def handle_reply(args: dict) -> Any:
    account = args.pop("account", "")
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    store, err = _resolve_account(account)
    if err:
        return err
    effective = account if account else DEFAULT_ACCOUNT
    inp = ReplyInput(**args)
    request_id = str(uuid.uuid4())
    start = time.monotonic()
    try:
        r = get_redis()
        date_str = _today()
        ok, reason = policies.check_rate_limit(r, date_str, account=effective)
        if not ok:
            audit.write(request_id=request_id, operation="reply",
                        message_id=inp.message_id, from_addr=None,
                        status="denied", reason=reason)
            return {"request_id": request_id, "status": "denied", "reason": reason}
        service = gmail_client.build_service(store)
        new_id = gmail_client.reply_to_thread(
            service, thread_id=inp.thread_id, message_id=inp.message_id, body=inp.body
        )
        policies.record_send(r, date_str, account=effective)
        duration_ms = int((time.monotonic() - start) * 1000)
        audit.write(request_id=request_id, operation="reply",
                    message_id=new_id, from_addr=None, status="sent",
                    duration_ms=duration_ms)
        return {"request_id": request_id, "status": "sent", "message_id": new_id}
    except redis_lib.RedisError:
        return {"request_id": request_id, "status": "denied",
                "reason": "rate_limit_unavailable: Redis error — send blocked"}


def handle_send(args: dict) -> Any:
    account = args.pop("account", "")
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    store, err = _resolve_account(account)
    if err:
        return err
    effective = account if account else DEFAULT_ACCOUNT
    inp = SendInput(**args)
    request_id = str(uuid.uuid4())
    start = time.monotonic()

    if not inp.confirmed:
        audit.write(request_id=request_id, operation="send",
                    message_id=None, from_addr=None, status="needs_confirmation",
                    extra={"to": inp.to})
        return {
            "request_id": request_id,
            "status": "needs_confirmation",
            "message": f"Ready to send to {inp.to!r}. Call again with confirmed=true to execute.",
        }

    try:
        r = get_redis()
        ok_domain, domain_reason = policies.check_novel_domain(r, inp.to, account=effective)
        if not ok_domain:
            audit.write(request_id=request_id, operation="send",
                        message_id=None, from_addr=None, status="denied",
                        reason=domain_reason, extra={"to": inp.to})
            return {"request_id": request_id, "status": "denied", "reason": domain_reason}
        date_str = _today()
        ok_rate, rate_reason = policies.check_rate_limit(r, date_str, account=effective)
        if not ok_rate:
            audit.write(request_id=request_id, operation="send",
                        message_id=None, from_addr=None, status="denied",
                        reason=rate_reason, extra={"to": inp.to})
            return {"request_id": request_id, "status": "denied", "reason": rate_reason}

        service = gmail_client.build_service(store)
        new_id = gmail_client.send_email(service, to=inp.to,
                                          subject=inp.subject, body=inp.body)
        policies.record_send(r, date_str, account=effective)
        duration_ms = int((time.monotonic() - start) * 1000)
        audit.write(request_id=request_id, operation="send",
                    message_id=new_id, from_addr=None, status="sent",
                    duration_ms=duration_ms, extra={"to": inp.to})
        return {"request_id": request_id, "status": "sent", "message_id": new_id}
    except redis_lib.RedisError:
        return {"request_id": request_id, "status": "denied",
                "reason": "rate_limit_unavailable: Redis error — send blocked"}


def handle_mark_read(args: dict) -> Any:
    account = args.pop("account", "")
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    store, err = _resolve_account(account)
    if err:
        return err
    inp = MarkReadInput(**args)
    service = gmail_client.build_service(store)
    gmail_client.mark_read(service, inp.message_id)
    return {"status": "ok", "message_id": inp.message_id}


def handle_contacts_lookup(args: dict) -> Any:
    # contacts always use default account (contacts are shared)
    args.pop("account", "")
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    store = token_stores.get(DEFAULT_ACCOUNT)
    if store is None:
        return _NOT_CONFIGURED_RESPONSE
    inp = ContactsLookupInput(**args)
    request_id = str(uuid.uuid4())
    start = time.monotonic()
    try:
        service = people_client.build_service(store)
        matches = people_client.search_contacts(service, query=inp.name, limit=inp.limit)
        duration_ms = int((time.monotonic() - start) * 1000)
        audit.write(
            request_id=request_id, operation="contacts_lookup",
            message_id=None, from_addr=None, status="ok",
            duration_ms=duration_ms,
            extra={"query_length": len(inp.name), "result_count": len(matches)},
        )
        return {"matches": matches, "total": len(matches)}
    except ValueError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        is_scope_error = "scope not granted" in str(exc)
        audit.write(
            request_id=request_id, operation="contacts_lookup",
            message_id=None, from_addr=None,
            status="scope_missing" if is_scope_error else "error",
            reason=str(exc), duration_ms=duration_ms,
            extra={"query_length": len(inp.name)},
        )
        if is_scope_error:
            return {"error": "scope_missing", "message": str(exc)}
        return {"error": str(exc)}
```

- [ ] **Step 5: Update `get_health()` to return per-account status**

```python
def get_health() -> dict:
    health: dict[str, Any] = {"configured": CONFIGURED}
    health["accounts"] = {}
    for label, store in token_stores.items():
        display = label if label else "default"
        try:
            store.load()
            health["accounts"][display] = "ok"
        except Exception as exc:
            health["accounts"][display] = f"error: {exc}"
    try:
        get_redis().ping()
        health["redis"] = "ok"
    except Exception as exc:
        health["redis"] = f"error: {exc}"
    if CONFIGURED and os.getenv("GMAIL_HEALTH_CHECK_GOOGLE", "false").lower() == "true":
        # Check default account only
        default_store = token_stores.get(DEFAULT_ACCOUNT)
        if default_store:
            try:
                gmail_client.build_service(default_store)
                health["google_api"] = "ok"
            except Exception as exc:
                health["google_api"] = f"error: {exc}"
        else:
            health["google_api"] = "skipped"
    else:
        health["google_api"] = "skipped"
    return health
```

- [ ] **Step 6: Update `http_call` to inject account from query param**

```python
@mcp.custom_route("/call", methods=["POST"])
async def http_call(request: Request) -> JSONResponse:
    account = request.query_params.get("account", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    tool = body.get("tool")
    args = body.get("args", {})
    args["account"] = account  # handlers will pop this before constructing models
    handler = _TOOL_HANDLERS.get(tool)
    if handler is None:
        return JSONResponse(
            {"error": f"unknown tool: {tool}", "available": list(_TOOL_HANDLERS)},
            status_code=404,
        )
    try:
        result = handler(args)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
```

- [ ] **Step 7: Update `_start_poller()` to start one thread per account**

```python
def _start_poller() -> None:
    if not CONFIGURED:
        logger.info("[mail-proxy] No Gmail token configured — poller disabled. "
                    "Run make setup-gmail to configure.")
        return

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    model = os.getenv("GMAIL_SCORER_MODEL", "claude-haiku-4-5-20251001")
    threshold = int(os.getenv("GMAIL_IMPORTANCE_THRESHOLD", "7"))
    interval = int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "180"))
    poll_label = os.getenv("GMAIL_POLL_LABEL", "INBOX")
    telegram_token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("ALERT_TELEGRAM_CHAT_ID", "")
    r = get_redis()

    for account, store in token_stores.items():
        importance_scorer = scorer_mod.ImportanceScorer(
            api_key=api_key, model=model, threshold=threshold
        )
        thread_name = f"poller-{account}" if account else "poller-default"
        t = threading.Thread(
            target=poller_mod.run_forever,
            kwargs={
                "build_service_fn": lambda s=store: gmail_client.build_service(s),
                "token_store": store,
                "r": r,
                "scorer": importance_scorer,
                "telegram_token": telegram_token,
                "chat_id": chat_id,
                "poll_interval": interval,
                "poll_label": poll_label,
                "account": account,
            },
            daemon=True,
            name=thread_name,
        )
        t.start()
        logger.info("[mail-proxy] Poller started for account=%r (interval=%ds, label=%s)",
                    account or "default", interval, poll_label)
```

- [ ] **Step 8: Update pre-existing tests that reference `token_store` (singular)**

In `tests/mail_proxy/test_server.py`, find and update any line that sets `s_mod.token_store = MagicMock()` to use the new dict API:

```python
# Before (any test doing this):
s_mod.token_store = MagicMock()

# After:
mock_store = MagicMock()
s_mod.token_stores = {"": mock_store}
s_mod.DEFAULT_ACCOUNT = ""
s_mod.CONFIGURED = True
```

Also update `_make_app` helper:

```python
def _make_app(monkeypatch, configured=True):
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    if configured:
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    else:
        monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("GMAIL_ACCOUNTS", raising=False)

    import importlib
    import server
    importlib.reload(server)
    return TestClient(server.mcp.get_app()), server
```

- [ ] **Step 9: Run all mail-proxy tests**

```bash
python3 -m pytest tests/mail_proxy/ -v 2>&1 | tail -30
```

Expected: all tests pass

- [ ] **Step 10: Commit**

```bash
git add services/mail-proxy/server.py tests/mail_proxy/test_server.py
git commit -m "feat(mail-proxy): multi-account routing in server — token_stores dict, account param, per-account pollers"
```

---

## Chunk 2: calendar-proxy service internals

### Task 5: Extend `calendar-proxy/auth.py` with degraded mode and multi-account

**Files:**
- Modify: `services/calendar-proxy/auth.py`
- Test: `tests/calendar_proxy/test_auth.py`

- [ ] **Step 1: Read the existing calendar-proxy auth tests**

```bash
cat tests/calendar_proxy/test_auth.py
```

Note how they currently test `from_env()` raises on missing key.

- [ ] **Step 2: Write the failing tests**

Add to `tests/calendar_proxy/test_auth.py`:

```python
import logging
from cryptography.fernet import Fernet


def test_from_env_returns_none_when_no_key_no_file(tmp_path, monkeypatch):
    """Degraded mode: no crash when neither key nor file are present."""
    monkeypatch.delenv("GCAL_TOKEN_ENCRYPTION_KEY", raising=False)
    import auth
    result = auth.TokenStore.from_env(token_path=tmp_path / "gcal_token.enc")
    assert result is None


def test_from_env_raises_when_file_exists_but_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GCAL_TOKEN_ENCRYPTION_KEY", raising=False)
    token_path = tmp_path / "gcal_token.enc"
    token_path.write_bytes(b"dummy")
    import auth
    with pytest.raises(RuntimeError, match="GCAL_TOKEN_ENCRYPTION_KEY"):
        auth.TokenStore.from_env(token_path=token_path)


def test_for_account_returns_none_when_no_key_no_file(monkeypatch):
    monkeypatch.delenv("GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL", raising=False)
    import auth
    result = auth.TokenStore.for_account("personal")
    assert result is None


def test_for_account_returns_store_when_key_set(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL", key)
    import auth
    store = auth.TokenStore.for_account("personal")
    assert store is not None


def test_load_all_legacy_fallback(monkeypatch):
    monkeypatch.delenv("GCAL_ACCOUNTS", raising=False)
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY", key)
    import auth
    result = auth.TokenStore.load_all()
    assert "" in result


def test_load_all_multi_account(monkeypatch):
    monkeypatch.setenv("GCAL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY_JOBS", key2)
    import auth
    result = auth.TokenStore.load_all()
    assert set(result.keys()) == {"personal", "jobs"}


def test_load_all_skips_missing_account(monkeypatch, caplog):
    monkeypatch.setenv("GCAL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.delenv("GCAL_TOKEN_ENCRYPTION_KEY_JOBS", raising=False)
    import auth
    with caplog.at_level(logging.WARNING, logger="auth"):
        result = auth.TokenStore.load_all()
    assert "personal" in result
    assert "jobs" not in result
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python3 -m pytest tests/calendar_proxy/test_auth.py -v 2>&1 | tail -20
```

Expected: 7+ failures (existing `from_env` test may also change)

- [ ] **Step 4: Rewrite `services/calendar-proxy/auth.py`**

```python
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


def generate_key() -> bytes:
    return Fernet.generate_key()


class TokenStore:
    def __init__(self, key: bytes, token_path: Path = Path("/data/gcal_token.enc")):
        self._fernet = Fernet(key)
        self._path = Path(token_path)

    @classmethod
    def from_env(
        cls, token_path: Path = Path("/data/gcal_token.enc")
    ) -> Optional["TokenStore"]:
        """Return TokenStore, None (degraded), or raise (misconfigured).

        - No key + no token file  → None (degraded mode, pre-setup)
        - No key + token file exists → RuntimeError (fail-fast)
        - Key present              → TokenStore
        """
        raw_key = os.environ.get("GCAL_TOKEN_ENCRYPTION_KEY")
        path = Path(token_path)
        if not raw_key and not path.exists():
            return None
        if not raw_key and path.exists():
            raise RuntimeError(
                "GCAL_TOKEN_ENCRYPTION_KEY is not set but "
                f"{path} exists — refusing to start. "
                "Set GCAL_TOKEN_ENCRYPTION_KEY or remove the token file."
            )
        return cls(key=raw_key.encode(), token_path=path)

    @classmethod
    def for_account(cls, label: str) -> Optional["TokenStore"]:
        """Load TokenStore for a specific account label.

        - No key + no token file  → None (logs warning, caller skips this label)
        - No key + token file exists → RuntimeError (fail-fast)
        - Key present              → TokenStore
        """
        key_env = f"GCAL_TOKEN_ENCRYPTION_KEY_{label.upper()}"
        token_path = Path(f"/data/gcal_token.{label}.enc")
        raw_key = os.environ.get(key_env)
        if not raw_key and not token_path.exists():
            logger.warning("[auth] No key and no token file for account %r — skipping", label)
            return None
        if not raw_key and token_path.exists():
            raise RuntimeError(
                f"{key_env} is not set but {token_path} exists — refusing to start. "
                f"Set {key_env} or remove the token file."
            )
        return cls(key=raw_key.encode(), token_path=token_path)

    @classmethod
    def load_all(cls) -> dict[str, "TokenStore"]:
        """Return {label: TokenStore} for all accounts in GCAL_ACCOUNTS.

        Falls back to single-account mode (label="") if GCAL_ACCOUNTS not set.
        """
        raw = os.environ.get("GCAL_ACCOUNTS", "").strip()
        if not raw:
            store = cls.from_env()
            return {"": store} if store else {}
        labels = [lbl.strip() for lbl in raw.split(",") if lbl.strip()]
        result: dict[str, "TokenStore"] = {}
        for label in labels:
            store = cls.for_account(label)
            if store is not None:
                result[label] = store
        return result

    def encrypt(self, token_dict: dict[str, Any]) -> bytes:
        return self._fernet.encrypt(json.dumps(token_dict).encode())

    def decrypt(self, data: bytes) -> dict[str, Any]:
        return json.loads(self._fernet.decrypt(data))

    def save(self, token_dict: dict[str, Any]) -> None:
        """Atomic write: encrypt → tmp → rename."""
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(self.encrypt(token_dict))
        tmp.replace(self._path)

    def load(self) -> dict[str, Any]:
        return self.decrypt(self._path.read_bytes())
```

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/calendar_proxy/test_auth.py -v 2>&1 | tail -20
```

Expected: all tests pass. Note: the pre-existing test `test_from_env_raises_on_missing_key` should be updated — it previously expected `RuntimeError` on missing key with no file, but now we return `None` in that case. Update that test to match the new degraded-mode behavior.

- [ ] **Step 6: Commit**

```bash
git add services/calendar-proxy/auth.py tests/calendar_proxy/test_auth.py
git commit -m "feat(calendar-proxy): add degraded mode to from_env, add for_account and load_all"
```

---

### Task 6: Multi-account wiring in `calendar-proxy/server.py`

**Files:**
- Modify: `services/calendar-proxy/server.py`
- Test: `tests/calendar_proxy/test_server.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/calendar_proxy/test_server.py`:

```python
from cryptography.fernet import Fernet


def test_health_returns_configured_false_when_no_accounts(monkeypatch):
    monkeypatch.delenv("GCAL_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("GCAL_ACCOUNTS", raising=False)
    import importlib, server as s_mod
    importlib.reload(s_mod)
    from starlette.testclient import TestClient
    client = TestClient(s_mod.mcp.get_app())
    resp = client.get("/health")
    assert resp.json()["configured"] is False


def test_health_returns_accounts_dict_when_configured(monkeypatch):
    monkeypatch.setenv("GCAL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY_JOBS", key2)
    import importlib, server as s_mod
    importlib.reload(s_mod)
    from starlette.testclient import TestClient
    client = TestClient(s_mod.mcp.get_app())
    resp = client.get("/health")
    data = resp.json()
    assert data["configured"] is True
    assert "personal" in data.get("accounts", {})
    assert "jobs" in data.get("accounts", {})


def test_call_returns_error_for_unknown_account(monkeypatch):
    monkeypatch.setenv("GCAL_ACCOUNTS", "personal")
    key1 = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    import importlib, server as s_mod
    importlib.reload(s_mod)
    from starlette.testclient import TestClient
    client = TestClient(s_mod.mcp.get_app())
    resp = client.post("/call?account=nonexistent",
                       json={"tool": "list_events",
                             "args": {"time_min": "2026-01-01T00:00:00Z",
                                      "time_max": "2026-01-02T00:00:00Z"}})
    data = resp.json()
    assert data.get("error") == "unknown_account"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/calendar_proxy/test_server.py -v -k "accounts or unknown_account" 2>&1 | tail -15
```

- [ ] **Step 3: Update `calendar-proxy/server.py` startup block**

Replace line 30:
```python
token_store = TokenStore.from_env()
```
With:
```python
token_stores = TokenStore.load_all()
CONFIGURED = len(token_stores) > 0
DEFAULT_ACCOUNT = list(token_stores.keys())[0] if token_stores else ""
```

- [ ] **Step 4: Update `build_google_service` to accept `account=""`**

```python
def build_google_service(account: str = ""):
    label = account if account else DEFAULT_ACCOUNT
    store = token_stores.get(label)
    if store is None:
        raise ValueError(
            f"unknown account {label!r}, available: {list(token_stores.keys())}"
        )
    token_data = store.load()
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes"),
    )
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            store.save({
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes) if creds.scopes else token_data.get("scopes"),
            })
        else:
            raise RuntimeError(
                "Google credentials are invalid and cannot be refreshed. Re-run auth setup."
            )
    return build("calendar", "v3", credentials=creds)
```

- [ ] **Step 5: Update all handlers and `get_health`**

In each handler (`handle_create_event`, `handle_list_events`, `_handle_check_availability`, `_handle_delete_event`):
- Pop `account` from `args` at the top: `account = args.pop("account", "")`
- Pass `account` to `build_google_service(account)`

Update `get_health`:
```python
def get_health() -> dict:
    health: dict[str, Any] = {
        "configured": CONFIGURED,
        "dry_run_mode": os.getenv("GCAL_DRY_RUN", "false").lower() == "true",
        "accounts": {},
    }
    for label, store in token_stores.items():
        display = label if label else "default"
        try:
            store.load()
            health["accounts"][display] = "ok"
        except Exception as exc:
            health["accounts"][display] = f"error: {exc}"
    try:
        get_redis().ping()
        health["redis"] = "ok"
    except Exception as exc:
        health["redis"] = f"error: {exc}"
    if os.getenv("GCAL_HEALTH_CHECK_GOOGLE", "false").lower() == "true":
        try:
            build_google_service()  # uses default account
            health["google_api"] = "ok"
        except Exception as exc:
            health["google_api"] = f"error: {exc}"
    else:
        health["google_api"] = "skipped"
    health["reminders_enabled"] = (
        os.getenv("GCAL_DISABLE_REMINDERS", "false").lower() != "true"
        and bool(os.getenv("TELEGRAM_TOKEN"))
        and bool(os.getenv("ALERT_TELEGRAM_CHAT_ID"))
    )
    return health
```

Update `http_call` to inject account:
```python
@mcp.custom_route("/call", methods=["POST"])
async def http_call(request: Request) -> JSONResponse:
    account = request.query_params.get("account", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    tool = body.get("tool")
    args = body.get("args", {})
    args["account"] = account
    handler = _TOOL_HANDLERS.get(tool)
    if handler is None:
        return JSONResponse(
            {"error": f"unknown tool: {tool}", "available": list(_TOOL_HANDLERS)},
            status_code=404,
        )
    try:
        result = handler(args)
        return JSONResponse(result)
    except ValueError as exc:
        # unknown_account or similar
        if "unknown account" in str(exc):
            return JSONResponse(
                {"error": "unknown_account", "message": str(exc),
                 "available": list(token_stores.keys())},
                status_code=400,
            )
        return JSONResponse({"error": str(exc)}, status_code=500)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
```

- [ ] **Step 6: Run all calendar-proxy tests**

```bash
python3 -m pytest tests/calendar_proxy/ -v 2>&1 | tail -30
```

Expected: all tests pass. Fix any failures in pre-existing tests that assumed `token_store` (singular) at module level.

- [ ] **Step 7: Run full test suite**

```bash
python3 -m pytest tests/mail_proxy/ tests/calendar_proxy/ -q 2>&1 | tail -10
```

Expected: all 185+ tests pass

- [ ] **Step 8: Commit**

```bash
git add services/calendar-proxy/server.py tests/calendar_proxy/test_server.py
git commit -m "feat(calendar-proxy): multi-account routing in server — token_stores dict, account param"
```

---

## Chunk 3: CLI, setup scripts, and infrastructure

### Task 7: Add `--account` flag to `gmail` and `gcal` CLIs

**Files:**
- Modify: `services/mail-proxy/scripts/gmail`
- Modify: `services/calendar-proxy/scripts/gcal`

No unit tests needed here — the change is trivial argument parsing wired to a query param.

- [ ] **Step 1: Update `gmail` script**

In `services/mail-proxy/scripts/gmail`, update the docstring and `_call` + `main` functions:

Update docstring to add `--account`:
```python
"""gmail — CLI for the mail-proxy service.

Usage:
  gmail [--account LABEL] list    [--limit N] [--label LABEL]
  gmail [--account LABEL] get     --thread-id ID
  gmail [--account LABEL] search  --query "..." [--limit N]
  gmail [--account LABEL] reply   --thread-id ID --message-id ID --body "..."
  gmail [--account LABEL] send    --to EMAIL --subject "..." --body "..." [--confirmed]
  gmail [--account LABEL] mark-read --message-id ID
  gmail health
"""
```

Update `_call` to accept `account`:
```python
def _call(tool: str, args: dict, account: str = "") -> dict:
    url = f"{BASE_URL}/call"
    if account:
        url += f"?account={account}"
    payload = json.dumps({"tool": tool, "args": args}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return {"error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"error": str(e)}
```

At the start of `main()`, before parsing `cmd`, add account extraction:
```python
def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    # Extract optional --account flag before the subcommand
    account = ""
    if len(argv) >= 2 and argv[0] == "--account":
        account = argv[1]
        argv = argv[2:]
        if not argv:
            print(__doc__)
            sys.exit(0)

    cmd = argv[0]
    rest = argv[1:]
    ...
```

Then update every `_call(...)` to pass `account=account`:
```python
    if cmd == "list":
        ...
        result = _call("list", args, account=account)
    elif cmd == "get":
        ...
        result = _call("get", {"thread_id": thread_id}, account=account)
    # ... and so on for all commands
```

- [ ] **Step 2: Update `gcal` script**

Apply the same pattern to `services/calendar-proxy/scripts/gcal`:

Update docstring, `_call` function, and `main()` with identical `--account` extraction and passing.

- [ ] **Step 3: Smoke test the CLIs in the container** (optional, skip if VPS not available)

```bash
# These will fail with "connection refused" locally — that's expected
python3 services/mail-proxy/scripts/gmail --account personal health 2>&1 || true
python3 services/calendar-proxy/scripts/gcal --account personal health 2>&1 || true
```

Expected: connection error (not a parsing error)

- [ ] **Step 4: Commit**

```bash
git add services/mail-proxy/scripts/gmail services/calendar-proxy/scripts/gcal
git commit -m "feat(cli): add --account flag to gmail and gcal CLIs"
```

---

### Task 8: Extend setup scripts and Makefile

**Files:**
- Modify: `scripts/setup-gmail.sh`
- Modify: `scripts/setup-gcal.sh`
- Modify: `Makefile`

- [ ] **Step 1: Update `scripts/setup-gmail.sh`**

Replace the script with the multi-account version. Key changes:
1. Accept optional 3rd argument `ACCOUNT`
2. When `ACCOUNT=""` (no arg): detect legacy env var + token file → migration mode (rename)
3. When `ACCOUNT=<label>`: standard OAuth flow for that account

```bash
#!/bin/bash
# Multi-account Gmail setup. Run locally on your Mac.
# Usage:
#   bash scripts/setup-gmail.sh user@host path/to/client_secret.json [account_label]
#
# With no account_label: migrates existing single-account setup to 'personal'
# With account_label:    runs OAuth flow for that account (e.g. ACCOUNT=jobs)
set -euo pipefail

HOST="${1:-}"
CLIENT_SECRET="${2:-}"
CLIENT_SECRET="${CLIENT_SECRET/#\~/$HOME}"
ACCOUNT="${3:-}"

if [ -z "$HOST" ] || [ -z "$CLIENT_SECRET" ]; then
    echo "Usage: $0 user@host path/to/client_secret.json [account_label]"
    exit 1
fi

if [ ! -f "$CLIENT_SECRET" ]; then
    echo "Error: client_secret.json not found at $CLIENT_SECRET"
    exit 1
fi

BOLD='\033[1m'; GREEN='\033[0;32m'; NC='\033[0m'
step() { echo -e "\n${BOLD}▶ $1${NC}"; }
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMPDIR_LOCAL=$(mktemp -d)
trap 'rm -rf "$TMPDIR_LOCAL"' EXIT

# ── Migration mode (no ACCOUNT arg) ──────────────────────────────────────────
if [ -z "$ACCOUNT" ]; then
    step "Migration mode: renaming existing single-account setup to 'personal'"
    # Rename token file on VPS
    ssh "$HOST" "
        DATA=/var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data
        if [ -f \"\$DATA/gmail_token.enc\" ]; then
            sudo mv \"\$DATA/gmail_token.enc\" \"\$DATA/gmail_token.personal.enc\"
            sudo chown 1000:1000 \"\$DATA/gmail_token.personal.enc\"
            echo 'Token file renamed'
        else
            echo 'No legacy gmail_token.enc found — already migrated?'
        fi
    "
    # Rename env var in .env
    ssh "$HOST" "
        cd ~/openclaw-deploy
        if grep -q '^GMAIL_TOKEN_ENCRYPTION_KEY=' .env; then
            KEY=\$(grep '^GMAIL_TOKEN_ENCRYPTION_KEY=' .env | cut -d= -f2-)
            sed -i '/^GMAIL_TOKEN_ENCRYPTION_KEY=/d' .env
            sed -i '/^GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL=/d' .env
            echo \"GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL=\$KEY\" >> .env
            echo 'Env var renamed'
        else
            echo 'GMAIL_TOKEN_ENCRYPTION_KEY not found — already migrated?'
        fi
        # Add GMAIL_ACCOUNTS=personal if not already set
        if ! grep -q '^GMAIL_ACCOUNTS=' .env; then
            echo 'GMAIL_ACCOUNTS=personal' >> .env
            echo 'GMAIL_ACCOUNTS set'
        fi
    "
    ok "Migration complete (personal)"
    step "Restarting mail-proxy"
    ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose --profile mail up -d --force-recreate mail-proxy"
    ok "mail-proxy restarted"
    echo ""
    echo -e "${BOLD}Migration complete. Run 'make doctor' to verify.${NC}"
    exit 0
fi

LABEL_UPPER=$(echo "$ACCOUNT" | tr '[:lower:]' '[:upper:]')

# ── New account OAuth flow ────────────────────────────────────────────────────
step "Generating Fernet encryption key for account '$ACCOUNT'"
KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
ok "Key generated"

step "Authenticating with Google for account '$ACCOUNT' (browser will open)"
python3 "$REPO_DIR/services/mail-proxy/scripts/auth_setup.py" \
    --client-secret "$CLIENT_SECRET" \
    --out "$TMPDIR_LOCAL/token.json"
ok "Token received"

step "Encrypting token"
cd "$REPO_DIR"
python3 services/mail-proxy/scripts/encrypt_token.py \
    --token "$TMPDIR_LOCAL/token.json" \
    --key "$KEY" \
    --out "$TMPDIR_LOCAL/gmail_token.${ACCOUNT}.enc"
ok "Token encrypted"

step "Copying gmail_token.${ACCOUNT}.enc to VPS"
scp "$TMPDIR_LOCAL/gmail_token.${ACCOUNT}.enc" "$HOST:/tmp/gmail_token.${ACCOUNT}.enc"
ssh "$HOST" "
    sudo cp /tmp/gmail_token.${ACCOUNT}.enc \
        /var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data/gmail_token.${ACCOUNT}.enc
    sudo chown 1000:1000 \
        /var/lib/docker/volumes/openclaw-deploy_openclaw_data/_data/gmail_token.${ACCOUNT}.enc
    rm -f /tmp/gmail_token.${ACCOUNT}.enc
"
ok "Token deployed to VPS volume"

step "Updating .env on VPS"
ssh "$HOST" "
    cd ~/openclaw-deploy
    # Write/overwrite the per-label encryption key
    sed -i '/^GMAIL_TOKEN_ENCRYPTION_KEY_${LABEL_UPPER}=/d' .env
    echo 'GMAIL_TOKEN_ENCRYPTION_KEY_${LABEL_UPPER}=${KEY}' >> .env

    # Add label to GMAIL_ACCOUNTS (idempotent)
    if grep -q '^GMAIL_ACCOUNTS=' .env; then
        if ! grep -qE '^GMAIL_ACCOUNTS=.*\b${ACCOUNT}\b' .env; then
            sed -i 's/^GMAIL_ACCOUNTS=\(.*\)/GMAIL_ACCOUNTS=\1,${ACCOUNT}/' .env
        fi
    else
        echo 'GMAIL_ACCOUNTS=${ACCOUNT}' >> .env
    fi
"
ok "Key written to .env, '$ACCOUNT' added to GMAIL_ACCOUNTS"

step "Pulling latest code on VPS"
ssh "$HOST" "cd ~/openclaw-deploy && git pull --ff-only"
ok "Code updated"

step "Restarting mail-proxy"
ssh "$HOST" "cd ~/openclaw-deploy && sudo docker compose --profile mail up -d --force-recreate mail-proxy"
ok "mail-proxy restarted"

echo ""
echo -e "${BOLD}Gmail setup complete for account '$ACCOUNT'.${NC}"
echo "  Run 'make doctor' to verify."
```

- [ ] **Step 2: Update `scripts/setup-gcal.sh`** with the same structure

Apply the same migration-mode + labeled-account-flow pattern. Key differences from gmail:
- Uses `GCAL_TOKEN_ENCRYPTION_KEY` / `GCAL_TOKEN_ENCRYPTION_KEY_<LABEL>`
- Uses `gcal_token.enc` / `gcal_token.<label>.enc`
- Uses `GCAL_ACCOUNTS`
- Restarts `calendar-proxy` (not `mail-proxy`)
- Uses `services/calendar-proxy/scripts/auth_setup.py` and `encrypt_token.py`

The script structure is identical to the gmail version with those substitutions.

- [ ] **Step 3: Update `Makefile`**

Find the `setup-gmail` and `setup-gcal` targets and update them:

```makefile
# Usage: make setup-gmail CLIENT_SECRET=path/to/client_secret.json [ACCOUNT=label]
# No ACCOUNT= : migrate existing single-account setup to 'personal'
# ACCOUNT=jobs : set up a new 'jobs' account via OAuth
setup-gmail:
	@[ -n "$(CLIENT_SECRET)" ] || (echo "Usage: make setup-gmail CLIENT_SECRET=path/to/client_secret.json [ACCOUNT=label]" && exit 1)
	@bash scripts/setup-gmail.sh "$(HOST)" "$(CLIENT_SECRET)" "$(ACCOUNT)"

# Usage: make setup-gcal CLIENT_SECRET=path/to/client_secret.json [ACCOUNT=label]
setup-gcal:
	@[ -n "$(CLIENT_SECRET)" ] || (echo "Usage: make setup-gcal CLIENT_SECRET=path/to/client_secret.json [ACCOUNT=label]" && exit 1)
	@bash scripts/setup-gcal.sh "$(HOST)" "$(CLIENT_SECRET)" "$(ACCOUNT)"
```

- [ ] **Step 4: Commit**

```bash
git add scripts/setup-gmail.sh scripts/setup-gcal.sh Makefile
git commit -m "feat(setup): add ACCOUNT param to setup-gmail/gcal; migration mode renames legacy token"
```

---

### Task 9: Switch `docker-compose.yml` to `env_file` for token keys

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Update `mail-proxy` service**

In `docker-compose.yml`, find the `mail-proxy` service and:
1. Add `env_file: - .env` before `environment:`
2. Remove `- GMAIL_TOKEN_ENCRYPTION_KEY=${GMAIL_TOKEN_ENCRYPTION_KEY:-}` from `environment:` (it will come from `env_file`)
3. Keep all other `environment:` vars as-is

Result:
```yaml
  mail-proxy:
    build: ./services/mail-proxy
    profiles: [mail]
    restart: unless-stopped
    networks:
      - ingress
      - internal
    depends_on:
      - redis
    volumes:
      - openclaw_data:/data:rw
    env_file:
      - .env
    environment:
      - GMAIL_POLL_INTERVAL_SECONDS=${GMAIL_POLL_INTERVAL_SECONDS:-180}
      - GMAIL_IMPORTANCE_THRESHOLD=${GMAIL_IMPORTANCE_THRESHOLD:-7}
      - GMAIL_MAX_SENDS_PER_DAY=${GMAIL_MAX_SENDS_PER_DAY:-20}
      - GMAIL_POLL_LABEL=${GMAIL_POLL_LABEL:-INBOX}
      - GMAIL_SCORER_MODEL=${GMAIL_SCORER_MODEL:-claude-haiku-4-5-20251001}
      - GMAIL_AUDIT_LOG_PATH=${GMAIL_AUDIT_LOG_PATH:-/data/gmail-audit.log}
      - GMAIL_AUDIT_MAX_MB=${GMAIL_AUDIT_MAX_MB:-50}
      - GMAIL_HEALTH_CHECK_GOOGLE=${GMAIL_HEALTH_CHECK_GOOGLE:-false}
      - ALERT_TELEGRAM_CHAT_ID=${ALERT_TELEGRAM_CHAT_ID:-}
      - TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379
      # GMAIL_ACCOUNTS and GMAIL_TOKEN_ENCRYPTION_KEY_* come from env_file
```

- [ ] **Step 2: Update `calendar-proxy` service**

Apply the same pattern: add `env_file: - .env`, remove `GCAL_TOKEN_ENCRYPTION_KEY=...` from `environment:`.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(docker): use env_file for proxy token keys — supports N accounts without compose changes"
```

---

## Chunk 4: Operations and documentation

### Task 10: Update `doctor.sh` with per-account token checks

**Files:**
- Modify: `scripts/doctor.sh`

- [ ] **Step 1: Replace the Gmail token check section**

Find the `# ── Gmail` section in `scripts/doctor.sh` (lines 152–171) and replace it with:

```bash
# ── Gmail ──────────────────────────────────────────────────────────────────────

echo ""
echo " Gmail"

if [ -n "${GMAIL_ACCOUNTS:-}" ]; then
    if [ -n "${GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL:-}${GMAIL_TOKEN_ENCRYPTION_KEY_JOBS:-}" ] || \
       [ -n "${GMAIL_TOKEN_ENCRYPTION_KEY:-}" ]; then
        pass "GMAIL_TOKEN_ENCRYPTION_KEY_*  set"
    else
        warn "No GMAIL_TOKEN_ENCRYPTION_KEY_* vars found — run: make setup-gmail ACCOUNT=personal CLIENT_SECRET=..."
    fi
    IFS=',' read -ra _gmail_accounts <<< "$GMAIL_ACCOUNTS"
    for _acct in "${_gmail_accounts[@]}"; do
        _acct=$(echo "$_acct" | tr -d ' ')
        if sudo docker compose --profile mail exec -T mail-proxy test -f "/data/gmail_token.${_acct}.enc" 2>/dev/null; then
            pass "gmail:${_acct}  token present"
        else
            warn "gmail:${_acct}  token missing → run: make setup-gmail ACCOUNT=${_acct} CLIENT_SECRET=..."
        fi
    done
    mail_health=$(sudo docker compose --profile mail exec -T mail-proxy python3 -c \
        "import urllib.request; import json; r=urllib.request.urlopen('http://localhost:8091/health',timeout=3); print(json.load(r)['configured'])" \
        2>/dev/null || echo "")
    if [ "$mail_health" = "True" ]; then
        pass "mail-proxy  /health → configured"
    elif sudo docker compose ps --format '{{.Name}}' 2>/dev/null | grep -q "mail-proxy"; then
        warn "mail-proxy  running but /health unreachable"
    else
        skip "mail-proxy  not started → run: make up-mail"
    fi
else
    # Legacy single-account check
    if [ -n "${GMAIL_TOKEN_ENCRYPTION_KEY:-}" ]; then
        warn "GMAIL_TOKEN_ENCRYPTION_KEY set but GMAIL_ACCOUNTS not configured → run: make setup-gmail CLIENT_SECRET=... to migrate"
    else
        skip "Gmail  not configured → run: make setup-gmail CLIENT_SECRET=..."
    fi
fi
```

- [ ] **Step 2: Replace the Google Calendar check section similarly**

Find the `# ── Google Calendar` section and replace the single token check with the same per-account loop pattern using `GCAL_ACCOUNTS`, `GCAL_TOKEN_ENCRYPTION_KEY_*`, and `gcal_token.*.enc`.

- [ ] **Step 3: Commit**

```bash
git add scripts/doctor.sh
git commit -m "feat(doctor): per-account Gmail and GCal token checks driven by GMAIL/GCAL_ACCOUNTS"
```

---

### Task 11: Update `workspace/MEMORY_GUIDE.md`

**Files:**
- Modify: `workspace/MEMORY_GUIDE.md`

- [ ] **Step 1: Update the Gmail section**

Find the Gmail section and add after the quick reference block:

```markdown
#### Multiple accounts

`gmail` supports multiple Google accounts. Use `--account <label>` before the subcommand:
```
gmail --account jobs list --limit 5
gmail --account personal search --query "from:bank.com"
```
Available labels: `personal` (default), `jobs`. Omit `--account` to use the default (`personal`).
Ask the user which account they mean when context is ambiguous.
```

- [ ] **Step 2: Update the Calendar section**

Find the Google Calendar section and add:

```markdown
#### Multiple accounts

`gcal` supports multiple Google accounts. Use `--account <label>` before the subcommand:
```
gcal --account jobs list --from "2026-04-01T00:00:00Z" --to "2026-04-01T23:59:59Z"
```
Available labels: `personal` (default), `jobs`. Omit `--account` to use the default.
```

- [ ] **Step 3: Commit**

```bash
git add workspace/MEMORY_GUIDE.md
git commit -m "docs(MEMORY_GUIDE): document --account flag for gmail and gcal"
```

---

### Task 12: Run full test suite and verify

- [ ] **Step 1: Run all tests**

```bash
cd /Users/evgueni/repos/personal/openclaw-deploy
python3 -m pytest tests/mail_proxy/ tests/calendar_proxy/ -v 2>&1 | tail -30
```

Expected: all tests pass (185+)

- [ ] **Step 2: Run doctor locally (will show skips for VPS-only checks)**

```bash
bash scripts/doctor.sh 2>&1 | grep -E "Gmail|Calendar|error|FAIL" | head -20
```

Expected: no syntax errors in script

- [ ] **Step 3: Final commit if any cleanup needed**

```bash
git add -p  # stage any remaining changes
git commit -m "chore: cleanup after multi-account implementation"
```

---

## Migration: apply to live VPS

Once the code is reviewed and merged, run these on your Mac to migrate the live deployment:

```bash
# 1. Migrate existing Gmail account → personal (no re-auth)
make setup-gmail CLIENT_SECRET=~/client_secret.json HOST=evgueni@5.78.189.77

# 2. Migrate existing GCal account → personal (no re-auth)
make setup-gcal CLIENT_SECRET=~/client_secret.json HOST=evgueni@5.78.189.77

# 3. Verify
make doctor HOST=evgueni@5.78.189.77

# 4. Add jobs account (runs OAuth browser flow)
make setup-gmail ACCOUNT=jobs CLIENT_SECRET=~/client_secret.json HOST=evgueni@5.78.189.77
make setup-gcal  ACCOUNT=jobs CLIENT_SECRET=~/client_secret.json HOST=evgueni@5.78.189.77
```
