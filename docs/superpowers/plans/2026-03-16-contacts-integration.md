# Contacts Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only Google Contacts lookup to `mail-proxy` so the agent can resolve names to email addresses before composing Gmail.

**Architecture:** `people_client.py` wraps the Google People API (same pattern as `gmail_client.py`). A `contacts_lookup` handler is added to `server.py`'s `_TOOL_HANDLERS`. A `contacts` CLI binary (same pattern as `gmail`) is installed into the openclaw container by `setup-gmail.sh`. No new service, port, or credentials.

**Tech Stack:** Python 3, googleapiclient (already in requirements.txt), Pydantic v2, FastMCP, pytest + MagicMock.

---

## Chunk 1: Python implementation

### Task 1: `ContactsLookupInput` model

**Files:**
- Modify: `services/mail-proxy/models.py`
- Test: `tests/mail_proxy/test_models.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/mail_proxy/test_models.py`:

```python
def test_contacts_lookup_input_defaults():
    import models
    m = models.ContactsLookupInput(name="Alice")
    assert m.limit == 5


def test_contacts_lookup_input_rejects_empty_name():
    import models
    with pytest.raises(Exception):
        models.ContactsLookupInput(name="")


def test_contacts_lookup_input_rejects_name_over_200_chars():
    import models
    with pytest.raises(Exception):
        models.ContactsLookupInput(name="A" * 201)


def test_contacts_lookup_input_rejects_limit_over_10():
    import models
    with pytest.raises(Exception):
        models.ContactsLookupInput(name="Alice", limit=11)


def test_contacts_lookup_input_rejects_limit_zero():
    import models
    with pytest.raises(Exception):
        models.ContactsLookupInput(name="Alice", limit=0)


def test_contacts_lookup_input_accepts_limit_10():
    import models
    m = models.ContactsLookupInput(name="Alice", limit=10)
    assert m.limit == 10
```

- [ ] **Step 2: Run to confirm failure**

```bash
python3 -m pytest tests/mail_proxy/test_models.py -k "contacts" -v
```

Expected: `AttributeError: module 'models' has no attribute 'ContactsLookupInput'`

- [ ] **Step 3: Add `ContactsLookupInput` to `models.py`**

Change the pydantic import line from:

```python
from pydantic import BaseModel, field_validator
```

to:

```python
from pydantic import BaseModel, Field, field_validator
```

Then add at the end of the file, after `class PolicyResult`:

```python
class ContactsLookupInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    limit: int = 5

    @field_validator("limit")
    @classmethod
    def check_limit(cls, v: int) -> int:
        if not (1 <= v <= 10):
            raise ValueError("limit must be between 1 and 10")
        return v
```

- [ ] **Step 4: Run tests to confirm passing**

```bash
python3 -m pytest tests/mail_proxy/test_models.py -k "contacts" -v
```

Expected: 6 tests PASSED

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
python3 -m pytest tests/mail_proxy/ -q
```

Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add services/mail-proxy/models.py tests/mail_proxy/test_models.py
git commit -m "feat(contacts): add ContactsLookupInput model with input validation"
```

---

### Task 2: `people_client.py`

**Files:**
- Create: `services/mail-proxy/people_client.py`
- Create: `tests/mail_proxy/test_people_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/mail_proxy/test_people_client.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/mail-proxy'))

import pytest
from unittest.mock import MagicMock
from googleapiclient.errors import HttpError

import people_client


def _make_person(name, emails, phones=None):
    """Build a People API person resource dict."""
    return {
        "names": [{"displayName": name}],
        "emailAddresses": [{"value": e} for e in emails],
        "phoneNumbers": [{"value": p} for p in (phones or [])],
    }


def _make_service(contacts_results=None, other_results=None):
    """Build a mock People API service.

    people().searchContacts().execute() → {"results": [{"person": ...}]}
    otherContacts().search().execute()  → {"otherContacts": [...]}
    """
    service = MagicMock()
    service.people.return_value.searchContacts.return_value.execute.return_value = {
        "results": [{"person": p} for p in (contacts_results or [])],
    }
    service.otherContacts.return_value.search.return_value.execute.return_value = {
        "otherContacts": (other_results or []),
    }
    return service


def test_search_returns_match_from_contacts():
    service = _make_service(
        contacts_results=[_make_person("Alice Johnson", ["alice@work.com"], ["+1-555-0100"])],
    )
    results = people_client.search_contacts(service, "Alice")
    assert len(results) == 1
    assert results[0]["name"] == "Alice Johnson"
    assert "alice@work.com" in results[0]["emails"]
    assert "+1-555-0100" in results[0]["phones"]


def test_search_returns_match_from_other_contacts():
    service = _make_service(
        contacts_results=[],
        other_results=[_make_person("Bob Smith", ["bob@example.com"])],
    )
    results = people_client.search_contacts(service, "Bob")
    assert len(results) == 1
    assert results[0]["name"] == "Bob Smith"
    assert "bob@example.com" in results[0]["emails"]
    assert results[0]["phones"] == []


def test_search_deduplicates_same_email_across_sources():
    """Contact appearing in both sources with same email should only appear once."""
    person = _make_person("Alice", ["alice@work.com"])
    service = _make_service(
        contacts_results=[person],
        other_results=[person],
    )
    results = people_client.search_contacts(service, "Alice")
    assert len(results) == 1


def test_search_returns_multiple_matches():
    service = _make_service(
        contacts_results=[
            _make_person("Alice Johnson", ["alice@work.com"]),
            _make_person("Alice Chen", ["achen@example.com"]),
        ],
    )
    results = people_client.search_contacts(service, "Alice")
    assert len(results) == 2


def test_search_merges_results_from_both_sources():
    """Contacts with different emails from both sources should both be returned."""
    service = _make_service(
        contacts_results=[_make_person("Alice Johnson", ["alice@work.com"])],
        other_results=[_make_person("Alice Chen", ["achen@example.com"])],
    )
    results = people_client.search_contacts(service, "Alice", limit=5)
    assert len(results) == 2
    names = {r["name"] for r in results}
    assert "Alice Johnson" in names
    assert "Alice Chen" in names


def test_search_returns_empty_list_when_no_results():
    service = _make_service()
    results = people_client.search_contacts(service, "ZZZnonexistent")
    assert results == []


def test_search_raises_value_error_on_403_from_contacts():
    service = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status = 403
    service.people.return_value.searchContacts.return_value.execute.side_effect = (
        HttpError(resp=mock_resp, content=b"Forbidden")
    )
    with pytest.raises(ValueError, match="scope not granted"):
        people_client.search_contacts(service, "Alice")


def test_search_raises_value_error_on_403_from_other_contacts():
    service = MagicMock()
    mock_resp_403 = MagicMock()
    mock_resp_403.status = 403
    # searchContacts succeeds, otherContacts.search fails with 403
    service.people.return_value.searchContacts.return_value.execute.return_value = {
        "results": []
    }
    service.otherContacts.return_value.search.return_value.execute.side_effect = (
        HttpError(resp=mock_resp_403, content=b"Forbidden")
    )
    with pytest.raises(ValueError, match="scope not granted"):
        people_client.search_contacts(service, "Alice")


def test_search_propagates_non_403_http_errors():
    service = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status = 500
    service.people.return_value.searchContacts.return_value.execute.side_effect = (
        HttpError(resp=mock_resp, content=b"Internal Server Error")
    )
    with pytest.raises(HttpError):
        people_client.search_contacts(service, "Alice")


def test_build_service_raises_runtime_error_when_no_refresh_token():
    """build_service raises RuntimeError when token is invalid and has no refresh_token."""
    mock_store = MagicMock()
    mock_store.load.return_value = {
        "token": None,
        "refresh_token": None,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "id",
        "client_secret": "sec",
        "scopes": [],
    }
    with pytest.raises(RuntimeError, match="cannot be refreshed"):
        people_client.build_service(mock_store)


def test_search_respects_limit():
    service = _make_service(
        contacts_results=[_make_person(f"Person {i}", [f"p{i}@example.com"]) for i in range(10)],
    )
    results = people_client.search_contacts(service, "Person", limit=3)
    assert len(results) == 3
```

- [ ] **Step 2: Run to confirm failure**

```bash
python3 -m pytest tests/mail_proxy/test_people_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'people_client'`

- [ ] **Step 3: Create `services/mail-proxy/people_client.py`**

```python
"""Google People API wrapper. No policy logic — just API calls."""
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def build_service(token_store) -> Any:
    """Build and return an authenticated People API v1 service. Refreshes token if needed."""
    token_data = token_store.load()
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes"),
    )
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            token_store.save({
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes) if creds.scopes else token_data.get("scopes"),
            })
        else:
            raise RuntimeError(
                "People API credentials invalid and cannot be refreshed. Re-run make setup-gmail."
            )
    return build("people", "v1", credentials=creds)


def search_contacts(service, query: str, limit: int = 5) -> list[dict]:
    """Search saved contacts and otherContacts for the given query.

    Returns list of {"name": str, "emails": list[str], "phones": list[str]} dicts.
    Raises ValueError if the contacts.readonly scope is missing (HTTP 403).
    Propagates other HttpErrors.
    """
    results = []

    # Search saved contacts — returns results under results[].person
    try:
        resp = service.people().searchContacts(
            query=query,
            readMask="names,emailAddresses,phoneNumbers",
            pageSize=limit,
        ).execute()
        for item in resp.get("results", []):
            results.append(_normalise_person(item.get("person", {})))
    except HttpError as e:
        if e.resp.status == 403:
            raise ValueError(
                "contacts.readonly scope not granted — re-run: make setup-gmail CLIENT_SECRET=..."
            )
        raise

    # Search otherContacts (people emailed but not explicitly saved) — returns results under
    # otherContacts[] (different shape from searchContacts). Deduplicate against saved contacts.
    existing_emails = {email for r in results for email in r["emails"]}
    try:
        resp = service.otherContacts().search(
            query=query,
            readMask="names,emailAddresses,phoneNumbers",
            pageSize=limit,
        ).execute()
        for item in resp.get("otherContacts", []):
            contact = _normalise_person(item)
            if not any(e in existing_emails for e in contact["emails"]):
                results.append(contact)
                existing_emails.update(contact["emails"])
    except HttpError as e:
        if e.resp.status == 403:
            raise ValueError(
                "contacts.readonly scope not granted — re-run: make setup-gmail CLIENT_SECRET=..."
            )
        raise

    return results[:limit]


def _normalise_person(person: dict) -> dict:
    """Extract name, emails, phones from a People API person resource dict."""
    names = person.get("names", [])
    name = names[0].get("displayName", "") if names else ""
    emails = [e.get("value", "") for e in person.get("emailAddresses", []) if e.get("value")]
    phones = [p.get("value", "") for p in person.get("phoneNumbers", []) if p.get("value")]
    return {"name": name, "emails": emails, "phones": phones}
```

- [ ] **Step 4: Run tests to confirm passing**

```bash
python3 -m pytest tests/mail_proxy/test_people_client.py -v
```

Expected: 11 tests PASSED

- [ ] **Step 5: Run full mail-proxy test suite**

```bash
python3 -m pytest tests/mail_proxy/ -q
```

Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add services/mail-proxy/people_client.py tests/mail_proxy/test_people_client.py
git commit -m "feat(contacts): add people_client.py wrapping Google People API"
```

---

### Task 3: `handle_contacts_lookup` in `server.py`

**Files:**
- Modify: `services/mail-proxy/server.py`
- Create: `tests/mail_proxy/test_contacts_server.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/mail_proxy/test_contacts_server.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/mail-proxy'))

import pytest
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet
from starlette.testclient import TestClient


def _make_app(monkeypatch, configured=True):
    """Reload server module in configured or degraded (no token) state."""
    monkeypatch.setenv("GMAIL_DISABLE_POLLER", "true")
    if configured:
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    else:
        monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY", raising=False)
    import importlib
    import server
    importlib.reload(server)
    return TestClient(server.mcp.get_app()), server


def test_contacts_lookup_returns_matches(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    fake_matches = [{"name": "Alice Johnson", "emails": ["alice@work.com"], "phones": []}]
    with patch("server.people_client.build_service", return_value=MagicMock()), \
         patch("server.people_client.search_contacts", return_value=fake_matches):
        resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Alice"}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["matches"][0]["name"] == "Alice Johnson"


def test_contacts_lookup_passes_limit_to_search(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    with patch("server.people_client.build_service", return_value=MagicMock()), \
         patch("server.people_client.search_contacts", return_value=[]) as mock_search:
        client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Bob", "limit": 3}})
    assert mock_search.call_count == 1, "search_contacts should be called exactly once"
    assert mock_search.call_args.kwargs["limit"] == 3


def test_contacts_lookup_rejects_limit_over_10(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Alice", "limit": 99}})
    assert "error" in resp.json()


def test_contacts_lookup_rejects_empty_name(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": ""}})
    assert "error" in resp.json()


def test_contacts_lookup_rejects_name_over_200_chars(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "A" * 201}})
    assert "error" in resp.json()


def test_contacts_lookup_not_configured(monkeypatch):
    client, _ = _make_app(monkeypatch, configured=False)
    resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Alice"}})
    data = resp.json()
    assert data["error"] == "not_configured"
    assert "message" in data


def test_contacts_lookup_scope_missing_returns_scope_error(monkeypatch):
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    scope_err = ValueError(
        "contacts.readonly scope not granted — re-run: make setup-gmail CLIENT_SECRET=..."
    )
    with patch("server.people_client.build_service", return_value=MagicMock()), \
         patch("server.people_client.search_contacts", side_effect=scope_err):
        resp = client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Alice"}})
    data = resp.json()
    assert data["error"] == "scope_missing"


def test_contacts_lookup_audit_log_omits_emails_and_phones(monkeypatch, tmp_path):
    monkeypatch.setenv("GMAIL_AUDIT_LOG_PATH", str(tmp_path / "audit.log"))
    client, s = _make_app(monkeypatch)
    s.token_store = MagicMock()
    fake_matches = [{"name": "Alice", "emails": ["alice@secret.com"], "phones": ["+1-800-SECRET"]}]
    with patch("server.people_client.build_service", return_value=MagicMock()), \
         patch("server.people_client.search_contacts", return_value=fake_matches):
        client.post("/call", json={"tool": "contacts_lookup", "args": {"name": "Alice"}})
    log_content = (tmp_path / "audit.log").read_text()
    assert "alice@secret.com" not in log_content
    assert "+1-800-SECRET" not in log_content
    assert "result_count" in log_content
```

- [ ] **Step 2: Run to confirm failure**

```bash
python3 -m pytest tests/mail_proxy/test_contacts_server.py -v
```

Expected: failures — `contacts_lookup` not in `_TOOL_HANDLERS` yet, `people_client` not imported in server.

- [ ] **Step 3: Update `server.py`**

**3a.** Add `people_client` import after `import gmail_client` (line 16):

old:
```python
import gmail_client
import poller as poller_mod
```

new:
```python
import gmail_client
import people_client
import poller as poller_mod
```

**3b.** Add `ContactsLookupInput` to the models import:

old:
```python
from models import (
    ListInput, GetInput, SearchInput, ReplyInput, SendInput, MarkReadInput,
)
```

new:
```python
from models import (
    ListInput, GetInput, SearchInput, ReplyInput, SendInput, MarkReadInput,
    ContactsLookupInput,
)
```

**3c.** Add `handle_contacts_lookup` after `handle_mark_read` (before `get_health`):

```python
def handle_contacts_lookup(args: dict) -> Any:
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    inp = ContactsLookupInput(**args)
    request_id = str(uuid.uuid4())
    start = time.monotonic()
    try:
        service = people_client.build_service(token_store)
        matches = people_client.search_contacts(service, query=inp.name, limit=inp.limit)
        duration_ms = int((time.monotonic() - start) * 1000)
        audit.write(
            request_id=request_id,
            operation="contacts_lookup",
            message_id=None,
            from_addr=None,
            status="ok",
            duration_ms=duration_ms,
            extra={"query_length": len(inp.name), "result_count": len(matches)},
        )
        return {"matches": matches, "total": len(matches)}
    except ValueError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        is_scope_error = "scope not granted" in str(exc)
        audit.write(
            request_id=request_id,
            operation="contacts_lookup",
            message_id=None,
            from_addr=None,
            status="scope_missing" if is_scope_error else "error",
            reason=str(exc),
            duration_ms=duration_ms,
            extra={"query_length": len(inp.name)},
        )
        if is_scope_error:
            return {"error": "scope_missing", "message": str(exc)}
        return {"error": str(exc)}
```

**3d.** Add `contacts_lookup` to `_TOOL_HANDLERS`:

old:
```python
_TOOL_HANDLERS = {
    "list": handle_list,
    "get": handle_get,
    "search": handle_search,
    "reply": handle_reply,
    "send": handle_send,
    "mark_read": handle_mark_read,
}
```

new:
```python
_TOOL_HANDLERS = {
    "list": handle_list,
    "get": handle_get,
    "search": handle_search,
    "reply": handle_reply,
    "send": handle_send,
    "mark_read": handle_mark_read,
    "contacts_lookup": handle_contacts_lookup,
}
```

- [ ] **Step 4: Run tests to confirm passing**

```bash
python3 -m pytest tests/mail_proxy/test_contacts_server.py -v
```

Expected: 8 tests PASSED

- [ ] **Step 5: Run full mail-proxy test suite**

```bash
python3 -m pytest tests/mail_proxy/ -q
```

Expected: all passing

- [ ] **Step 6: Run full test suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all passing

- [ ] **Step 7: Commit**

```bash
git add services/mail-proxy/server.py tests/mail_proxy/test_contacts_server.py
git commit -m "feat(contacts): add contacts_lookup handler to mail-proxy server"
```

---

## Chunk 2: CLI binary and deployment

### Task 4: `contacts` CLI binary

**Files:**
- Create: `services/mail-proxy/scripts/contacts`

No automated tests — the binary is a thin HTTP client matching the `gmail` script pattern. Verify manually after deployment.

- [ ] **Step 1: Create `services/mail-proxy/scripts/contacts`**

```python
#!/usr/bin/env python3
"""contacts — CLI for the mail-proxy contacts lookup.

Usage:
  contacts lookup  --name "Alice" [--limit N]
  contacts health
"""
import json
import sys
import urllib.request
import urllib.error

BASE_URL = "http://mail-proxy:8091"


def _call(tool: str, args: dict) -> dict:
    payload = json.dumps({"tool": tool, "args": args}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/call",
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


def _health() -> dict:
    req = urllib.request.Request(f"{BASE_URL}/health", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except Exception as e:
        return {"error": str(e)}


def _flag(args: list[str], flag: str, default=None):
    try:
        i = args.index(flag)
        return args[i + 1]
    except (ValueError, IndexError):
        return default


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    cmd = argv[0]
    rest = argv[1:]

    if cmd == "health":
        result = _health()
    elif cmd == "lookup":
        name = _flag(rest, "--name")
        if not name:
            print("Error: --name is required", file=sys.stderr)
            sys.exit(1)
        args: dict = {"name": name}
        if _flag(rest, "--limit"):
            args["limit"] = int(_flag(rest, "--limit"))
        result = _call("contacts_lookup", args)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x services/mail-proxy/scripts/contacts
```

- [ ] **Step 3: Commit**

```bash
git add services/mail-proxy/scripts/contacts
git commit -m "feat(contacts): add contacts CLI binary"
```

---

### Task 5: Auth scope + `setup-gmail.sh` deploy steps

**Files:**
- Modify: `services/mail-proxy/scripts/auth_setup.py`
- Modify: `scripts/setup-gmail.sh`

No automated tests. Verified manually by re-running `make setup-gmail` and confirming `contacts.readonly` appears in the token scopes, and `contacts lookup` works from the openclaw container.

- [ ] **Step 1: Add `contacts.readonly` scope to `auth_setup.py`**

old:
```python
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]
```

new:
```python
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/contacts.readonly",
]
```

- [ ] **Step 2: Add Step 6b (contacts CLI install) to `setup-gmail.sh`**

After the line `ok "gmail CLI installed at /home/node/.openclaw/bin/gmail"` (after Step 6), insert:

```bash
# ── Step 6b: Install contacts CLI into openclaw container ─────────────────────
step "Installing contacts CLI into openclaw container"
scp "$REPO_DIR/services/mail-proxy/scripts/contacts" "$HOST:/tmp/contacts"
ssh "$HOST" "sudo docker compose -f ~/openclaw-deploy/docker-compose.yml cp /tmp/contacts openclaw:/home/node/.openclaw/bin/contacts \
    && sudo docker compose -f ~/openclaw-deploy/docker-compose.yml exec -T openclaw chmod +x /home/node/.openclaw/bin/contacts \
    && rm -f /tmp/contacts"
ok "contacts CLI installed at /home/node/.openclaw/bin/contacts"
```

- [ ] **Step 3: Extend Step 7 to register contacts + update safeBins**

Replace the existing Step 7 block:

old:
```bash
# ── Step 7: Register gmail CLI on exec approvals allowlist ────────────────────
step "Registering gmail CLI on exec approvals allowlist"
ssh "$HOST" "cd ~/openclaw-deploy && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add '/home/node/.openclaw/bin/gmail' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add 'gmail' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add 'gmail *' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw config set tools.exec.safeBins '[\"gcal\",\"date\",\"ai\",\"gmail\"]' && \
    sudo docker compose exec -T openclaw openclaw config set tools.exec.safeBinProfiles.gmail '{}' && \
    sudo docker compose restart openclaw"
ok "gmail CLI registered on allowlist"
```

new:
```bash
# ── Step 7: Register gmail + contacts CLIs on exec approvals allowlist ─────────
step "Registering gmail + contacts CLIs on exec approvals allowlist"
ssh "$HOST" "cd ~/openclaw-deploy && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add '/home/node/.openclaw/bin/gmail' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add 'gmail' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add 'gmail *' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add '/home/node/.openclaw/bin/contacts' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add 'contacts' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw approvals allowlist add 'contacts *' --agent main --gateway && \
    sudo docker compose exec -T openclaw openclaw config set tools.exec.safeBins '[\"gcal\",\"date\",\"ai\",\"gmail\",\"contacts\"]' && \
    sudo docker compose exec -T openclaw openclaw config set tools.exec.safeBinProfiles.gmail '{}' && \
    sudo docker compose exec -T openclaw openclaw config set tools.exec.safeBinProfiles.contacts '{}' && \
    sudo docker compose restart openclaw"
ok "gmail + contacts CLIs registered on allowlist"
```

- [ ] **Step 4: Verify the diff looks correct**

```bash
git diff scripts/setup-gmail.sh
```

Expected: Step 6b added (contacts CLI install), Step 7 extended with contacts allowlist entries and updated safeBins.

- [ ] **Step 5: Commit**

```bash
git add services/mail-proxy/scripts/auth_setup.py scripts/setup-gmail.sh
git commit -m "feat(contacts): add contacts.readonly scope and deploy steps to setup-gmail.sh"
```

---

### Task 6: `workspace/MEMORY_GUIDE.md`

**Files:**
- Modify: `workspace/MEMORY_GUIDE.md`

- [ ] **Step 1: Add `contacts` exec reference to the CRITICAL note block**

Find the "CRITICAL: Never use bash or shell commands" block. It currently has lines for `gcal` and `gmail`. Add `contacts` after the `gmail` line:

old:
```
- For gcal: use exec with `{"command": "gcal ...", "workdir": "/home/node/.openclaw/workspace"}`
- For gmail: use exec with `{"command": "gmail ...", "workdir": "/home/node/.openclaw/workspace"}`
```

new:
```
- For gcal: use exec with `{"command": "gcal ...", "workdir": "/home/node/.openclaw/workspace"}`
- For gmail: use exec with `{"command": "gmail ...", "workdir": "/home/node/.openclaw/workspace"}`
- For contacts: use exec with `{"command": "contacts ...", "workdir": "/home/node/.openclaw/workspace"}`
```

- [ ] **Step 2: Add `### Contacts` section after the Gmail quick-reference block**

After the closing triple-backtick of the Gmail quick reference block (after `gmail health`), add:

````markdown
---

### Contacts

You have access to Google Contacts via the `contacts` CLI. **Always use it when you need to find someone's email address by name before sending mail.**

#### Workflow
1. Call `contacts lookup --name "..."` when you have a name but not an email
2. If multiple matches, show them to the user and ask which to use
3. Then proceed with `gmail send --to <resolved_email> ...`

#### Quick reference
```
contacts lookup --name "Alice"
contacts lookup --name "Smith" --limit 5
contacts health
```
````

- [ ] **Step 3: Commit**

```bash
git add workspace/MEMORY_GUIDE.md
git commit -m "feat(contacts): add contacts quick-reference to MEMORY_GUIDE.md"
```

---

## Final verification

- [ ] **Run full test suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all tests passing (no regressions)
