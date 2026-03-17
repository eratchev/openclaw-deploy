# Contacts Integration Design

**Goal:** Allow the OpenClaw agent to look up contact names, email addresses, and phone numbers via Google People API, integrated into the existing `mail-proxy` service.

---

## Problem

When the agent composes email, it often has a person's name but not their address. Without contacts access, the agent must ask the user every time. With it, the agent can resolve "Alice" to `alice@work.com` before calling `gmail send`.

---

## Architecture

Contacts lookup lives entirely inside `mail-proxy`. No new service, no new Docker Compose profile, no new port.

### New files

**`services/mail-proxy/people_client.py`** — wraps the Google People API. Exposes:

```python
def build_service(token_store) -> Any:
    """Build and return an authenticated People API v1 service.
    Same credential-building pattern as gmail_client.build_service —
    loads token, refreshes if expired, writes back to token_store.
    Returns googleapiclient.discovery.build('people', 'v1', credentials=creds).
    Raises RuntimeError if credentials invalid and cannot be refreshed."""

def search_contacts(service, query: str, limit: int = 5) -> list[dict]:
    """Search contacts and otherContacts. Returns list of
    {"name": str, "emails": list[str], "phones": list[str]} dicts.
    Catches HttpError 403 and raises ValueError with a clear re-auth message."""
```

`search_contacts` calls two Google People API endpoints and merges the results:
- `people.searchContacts` — returns results under `results[].person`
- `otherContacts.search` — returns results under `otherContacts[]`

Both return different response shapes. The function normalises them into the same `{"name", "emails", "phones"}` dict before returning.

**`services/mail-proxy/scripts/contacts`** — CLI binary, same pattern as `services/mail-proxy/scripts/gmail`. Posts to `http://mail-proxy:8091/call` for `lookup` and to `/health` for `health`.

### Modified files

**`services/mail-proxy/server.py`** — adds a `contacts_lookup` tool to `_TOOL_HANDLERS`. The handler calls `people_client.build_service(token_store)` then `people_client.search_contacts(service, name, limit)` and returns the result dict. The `contacts` CLI binary posts `{"tool": "contacts_lookup", "args": {...}}` to `/call` — matching the existing `mark_read` → `mark-read` CLI-to-tool naming pattern.

**`services/mail-proxy/models.py`** — adds `ContactsLookupInput`:

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

**`services/mail-proxy/scripts/auth_setup.py`** — adds `https://www.googleapis.com/auth/contacts.readonly` to `SCOPES`.

**`scripts/setup-gmail.sh`** — adds two steps after the existing gmail CLI install (Step 6 and 7):
- Step 6b: copy `services/mail-proxy/scripts/contacts` into the container at `/home/node/.openclaw/bin/contacts`, `chmod +x`
- Step 7b: register `contacts`, `contacts *`, and `/home/node/.openclaw/bin/contacts` on the exec approvals allowlist; update `tools.exec.safeBins` to `["gcal","date","ai","gmail","contacts"]`

**`workspace/MEMORY_GUIDE.md`** — adds `### Contacts` subsection immediately after the Gmail quick-reference block (before any future non-Gmail sections).

---

## CLI Interface

```
contacts lookup --name "Alice"
contacts lookup --name "Smith" --limit 5
contacts health
```

**Lookup response:**

```json
{
  "matches": [
    {
      "name": "Alice Johnson",
      "emails": ["alice@work.com", "alice@personal.com"],
      "phones": ["+1-555-0100"]
    }
  ],
  "total": 1
}
```

- Default limit: 5. Maximum: 10 (enforced by `ContactsLookupInput`).
- Empty `matches` list (not an error) when no results found.
- Returns both emails and phones — phones included for forward-compatibility with the SMS feature.
- `contacts health` calls the existing `/health` endpoint — same response shape as `gmail health`.

---

## Auth

No new credentials. The contacts integration reuses the Gmail token entirely:

- Same `GMAIL_TOKEN_ENCRYPTION_KEY`
- Same encrypted token file (`/data/gmail_token.enc`)
- Same `TokenStore` / `auth.py` — no changes

The only auth change: `services/mail-proxy/scripts/auth_setup.py` adds `contacts.readonly` to its `SCOPES` list. Re-running `make setup-gmail` generates a fresh token with the expanded scope.

**Scope upgrade:** Users with an existing Gmail token will get a 403 `insufficientPermissions` on their first contacts lookup. `people_client.search_contacts` catches `HttpError` with status 403 and raises `ValueError("contacts.readonly scope not granted — re-run: make setup-gmail CLIENT_SECRET=...")`. The tool handler surfaces this as `{"error": "scope_missing", "message": "..."}` in the CLI response.

**Degraded mode (token absent):** when `GMAIL_TOKEN_ENCRYPTION_KEY` is unset or the token file is missing, `contacts lookup` returns:

```json
{
  "error": "not_configured",
  "message": "Run 'make setup-gmail CLIENT_SECRET=...' to configure Gmail access"
}
```

This matches the existing Gmail not-configured response pattern.

---

## Agent Workflow

`workspace/MEMORY_GUIDE.md` gets a `### Contacts` subsection immediately after the Gmail quick-reference block:

```
### Contacts

Call `contacts lookup --name "..."` before `gmail send` when you have a
person's name but not their email address. If multiple matches are returned,
show them to the user and ask which one to use.

contacts lookup --name "Alice"
contacts lookup --name "Smith" --limit 5
contacts health
```

---

## Audit Logging

Every contacts lookup is written to the existing mail-proxy audit log (same `AuditLog` instance used by Gmail):

```
operation="contacts_lookup", query_length=<len(name)>, result_count=<N>, status="ok"|"error"
```

**What is NOT logged:** the actual `name` query, and the returned emails and phones. These are PII. The audit entry records only metadata — that a lookup happened, how long the query was, and how many results came back.

The `contacts_lookup` tool handler must pass only `result_count` (not `matches`) to the audit log's `extra=` dict. Passing the full result dict would write emails and phones to disk unredacted — `_REDACTED_FIELDS` in `audit.py` does not cover `emails` or `phones`.

---

## Out of Scope

- Write operations (create/update contacts) — read-only is sufficient
- Caching — contacts lookups are occasional; live API calls are fast enough
- Auto-resolution baked into `gmail send` — explicit lookup is clearer and easier to debug
- Proactive notifications for contacts changes

---

## Testing

**`tests/mail_proxy/test_people_client.py`** — unit tests with mocked Google API:
- Normal match (one result from `searchContacts`)
- Match from `otherContacts` (different response shape — tests the merge logic)
- Multiple matches across both sources
- No results (empty list, not an error)
- 403 HttpError → raises `ValueError` with re-auth message
- Other HttpError → propagated

**`tests/mail_proxy/test_contacts_server.py`** — MCP tool tests with mocked `people_client`:
- `contacts_lookup` returns matches
- `contacts_lookup` with `--limit`
- `ContactsLookupInput` rejects limit > 10
- `ContactsLookupInput` rejects empty name and name > 200 chars
- Not-configured response when token absent
- 403 scope error surfaces as `{"error": "scope_missing"}`
- Audit log entry contains `result_count` but not actual emails or phones

`contacts` CLI binary and `setup-gmail.sh` scope change have no automated tests — verified manually.
