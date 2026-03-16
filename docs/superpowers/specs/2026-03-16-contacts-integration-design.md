# Contacts Integration Design

**Goal:** Allow the OpenClaw agent to look up contact names, email addresses, and phone numbers via Google People API, integrated into the existing `mail-proxy` service.

---

## Problem

When the agent composes email, it often has a person's name but not their address. Without contacts access, the agent must ask the user every time. With it, the agent can resolve "Alice" to `alice@work.com` before calling `gmail send`.

---

## Architecture

Contacts lookup lives entirely inside `mail-proxy`. No new service, no new Docker Compose profile, no new port, no new credentials.

### New file

**`services/mail-proxy/people_client.py`** — wraps the Google People API. Exposes one function:

```python
def search_contacts(service, query: str, limit: int = 5) -> list[dict]
```

Returns a list of `{"name": str, "emails": list[str], "phones": list[str]}` dicts. Searches both `contacts` and `otherContacts` (people emailed but not explicitly saved). Uses `people.searchContacts` and `otherContacts.search` endpoints.

### Modified files

**`services/mail-proxy/server.py`** — adds a `contacts_lookup` MCP tool that calls `people_client.search_contacts` and exposes it as `contacts lookup --name "..." [--limit N]`.

**`services/mail-proxy/scripts/auth_setup.py`** — adds `https://www.googleapis.com/auth/contacts.readonly` to the `SCOPES` list.

**`workspace/MEMORY_GUIDE.md`** — adds a `### Contacts` section to the Gmail quick-reference block.

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

- Default limit: 5. Maximum: 10.
- Empty `matches` list (not an error) when no results found.
- Returns both emails and phones — phones are included for forward-compatibility with the SMS feature.
- `contacts health` returns `{"status": "ok", "token": "present"|"missing"}`.

---

## Auth

No new credentials. The contacts integration reuses the Gmail token entirely:

- Same `GMAIL_TOKEN_ENCRYPTION_KEY`
- Same encrypted token file (`/data/gmail_token.enc`)
- Same `TokenStore` / `auth.py` — no changes

The only change: `services/mail-proxy/scripts/auth_setup.py` adds `contacts.readonly` to its `SCOPES` list. Re-running `make setup-gmail` generates a fresh token with the expanded scope.

**Degraded mode:** when the token is absent or `GMAIL_TOKEN_ENCRYPTION_KEY` is unset, `contacts lookup` returns:

```json
{
  "error": "not_configured",
  "message": "Run 'make setup-gmail CLIENT_SECRET=...' to configure Gmail access"
}
```

This matches the existing Gmail not-configured response pattern.

---

## Agent Workflow

`workspace/MEMORY_GUIDE.md` gets a `### Contacts` subsection under Gmail:

```
Always call `contacts lookup --name "..."` before `gmail send` when you have
a person's name but not their email address. If multiple matches are returned,
show them to the user and ask which one to use.
```

---

## Out of Scope

- Write operations (create/update contacts) — read-only is sufficient
- Caching — contacts lookups are occasional; live API calls are fast enough
- Auto-resolution baked into `gmail send` — explicit lookup is clearer and easier to debug
- Proactive notifications for contacts changes

---

## Testing

**`tests/mail_proxy/test_people_client.py`** — unit tests with mocked Google API:
- Normal match (one result)
- Multiple matches
- No results (empty list)
- API error → propagated as exception

**`tests/mail_proxy/test_contacts_server.py`** — MCP tool tests with mocked `people_client`:
- Lookup returns matches
- Lookup with `--limit`
- Not-configured response when token absent
- Health check returns token status

No live Google API tests — same approach as Gmail and Calendar.
