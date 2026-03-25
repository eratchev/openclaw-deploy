# Multi-Account Gmail and Google Calendar Design

**Date:** 2026-03-24
**Status:** Draft
**Scope:** N-account support for `mail-proxy` and `calendar-proxy`; label-based routing; zero container growth

---

## Overview

Extend `mail-proxy` and `calendar-proxy` to support N Google accounts per service. Accounts are identified by short slugs (labels) chosen at setup time — e.g. `personal`, `jobs`. Each label maps to a separate encrypted token file and encryption key. The agent selects an account via an `--account <label>` CLI flag; omitting it uses the default (first label in `GMAIL_ACCOUNTS`). A single GCP project / `client_secret.json` serves all accounts.

---

## Architecture

```
OpenClaw → exec gmail --account jobs list
               → POST /call?account=jobs → mail-proxy:8091
                                              ↓
                                   TokenStore["jobs"] → Gmail API (jobs token)

               → POST /call (no account param)
                                              ↓
                                   TokenStore["personal"] → Gmail API (default)
```

One `mail-proxy` container, one `calendar-proxy` container — regardless of how many accounts are configured.

---

## Account Model

### Labels

A label is a lowercase slug (`personal`, `jobs`, `freelance`). The set of configured labels is defined in `.env`:

```
GMAIL_ACCOUNTS=personal,jobs
GCAL_ACCOUNTS=personal,jobs
```

The **first label is the default**. When no `--account` flag is passed, the service uses it.

Adding a third account later requires only:
1. Running `make setup-gmail ACCOUNT=freelance CLIENT_SECRET=...`
2. The setup script appends `freelance` to `GMAIL_ACCOUNTS` in `.env` and writes the new key
3. Restarting the proxy (picks up the new account automatically via `env_file`)

### Token files

```
/data/gmail_token.personal.enc
/data/gmail_token.jobs.enc
/data/gcal_token.personal.enc
/data/gcal_token.jobs.enc
```

### Encryption keys (`.env`)

```
GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL=<fernet-key>
GMAIL_TOKEN_ENCRYPTION_KEY_JOBS=<fernet-key>

GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL=<fernet-key>
GCAL_TOKEN_ENCRYPTION_KEY_JOBS=<fernet-key>
```

Key name format: `GMAIL_TOKEN_ENCRYPTION_KEY_<LABEL_UPPERCASE>`.

### Backward compatibility

If `GMAIL_ACCOUNTS` is not set, the service falls back to single-account mode: reads `GMAIL_TOKEN_ENCRYPTION_KEY` and `/data/gmail_token.enc` exactly as before. This ensures zero breakage during the migration window.

---

## Migration of the Existing Account

The existing working account becomes `personal` without re-authentication:

1. **Rename token file** on the VPS volume:
   `gmail_token.enc` → `gmail_token.personal.enc`
   `gcal_token.enc` → `gcal_token.personal.enc`

2. **Rename env vars** in `.env`:
   `GMAIL_TOKEN_ENCRYPTION_KEY` → `GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL`
   `GCAL_TOKEN_ENCRYPTION_KEY` → `GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL`

3. **Set account lists** in `.env`:
   `GMAIL_ACCOUNTS=personal`
   `GCAL_ACCOUNTS=personal`

4. Restart proxies. Verify health. Remove old `GMAIL_TOKEN_ENCRYPTION_KEY` (no suffix) and `gmail_token.enc` once confirmed working.

`make setup-gmail` (no `ACCOUNT=`) performs steps 1–4 automatically: it detects the legacy env var + token file, renames them, and sets `GMAIL_ACCOUNTS=personal` — no new OAuth flow required.

**Calendar-proxy migration note:** `calendar-proxy/auth.py` currently raises `RuntimeError` if the key is missing (no graceful degraded mode). During migration, `GCAL_TOKEN_ENCRYPTION_KEY` is renamed before `GCAL_ACCOUNTS` is set. To prevent a crash during this window, `calendar-proxy/auth.py` must gain the same degraded-mode behaviour as `mail-proxy` (return `None` when no key and no token file). This change is part of this feature's implementation scope.

---

## Service Internals

### `auth.py` changes

Add two factory methods:

```python
@classmethod
def for_account(cls, label: str, service: str = "gmail") -> Optional["TokenStore"]:
    """Load TokenStore for a specific account label.

    Returns None (degraded) if key is absent and token file does not exist.
    Raises RuntimeError if token file exists but key is absent.
    Logs a warning and returns None for any other missing-key case.
    """
    key_env = f"{service.upper()}_TOKEN_ENCRYPTION_KEY_{label.upper()}"
    token_path = Path(f"/data/{service}_token.{label}.enc")
    raw_key = os.environ.get(key_env)
    if not raw_key and not token_path.exists():
        logger.warning("[auth] No key and no token for account %r — skipping", label)
        return None
    if not raw_key and token_path.exists():
        raise RuntimeError(
            f"{key_env} is not set but {token_path} exists — refusing to start."
        )
    return cls(key=raw_key.encode(), token_path=token_path)

@classmethod
def load_all(cls, service: str = "gmail") -> dict[str, "TokenStore"]:
    """Return {label: TokenStore} for all accounts in GMAIL_ACCOUNTS / GCAL_ACCOUNTS.

    Filters out None returns (unconfigured labels) with a per-label warning.
    Falls back to single-account mode if the accounts env var is not set.

    Legacy fallback uses the empty string "" as the label. policies.py treats
    "" as "no namespace" and uses the old non-prefixed Redis keys — so no
    orphaned "default:*" keys are ever written during the pre-migration window.
    """
    env_var = f"{service.upper()}_ACCOUNTS"
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        # backward-compat: single-account fallback, label="" = no namespace injection
        store = cls.from_env()
        return {"": store} if store else {}
    labels = [l.strip() for l in raw.split(",") if l.strip()]
    result = {}
    for label in labels:
        store = cls.for_account(label, service)
        if store is not None:
            result[label] = store
        # else: warning already logged inside for_account
    return result
```

`calendar-proxy/auth.py` gains the same degraded-mode logic (return `None` when no key + no token file) to match `mail-proxy` and support the migration window.

### `server.py` changes

Module-level `token_store` (single instance) → `token_stores: dict[str, TokenStore]` populated by `TokenStore.load_all()` at startup.

```python
token_stores = TokenStore.load_all()
CONFIGURED = len(token_stores) > 0
DEFAULT_ACCOUNT = list(token_stores.keys())[0] if token_stores else None
```

All MCP tool handlers gain an optional `account: str = ""` parameter:
- `""` → use `DEFAULT_ACCOUNT`
- Known label → use `token_stores[label]`
- Unknown label → return structured error: `{"error": "unknown account", "available": list(token_stores.keys())}`

### `policies.py` changes

All Redis keys are namespaced by account. **All three key families** must include the label:

```python
# Send rate limit
f"gmail:send:{account}:{today}"          # was: f"gmail:send:{today}"

# Novel-domain guard (send safety check)
f"gmail:seen_domains:{account}"          # was: "gmail:seen_domains"

# Poller dedup (prevents re-notifying on seen messages)
f"gmail:seen:{account}:{message_id}"     # was: f"gmail:seen:{message_id}"
```

In legacy single-account mode (`GMAIL_ACCOUNTS` not set), `load_all` returns `{"": store}` and `policies.py` treats `account=""` as "no namespace" — using the old non-prefixed Redis keys exactly as before. Once `GMAIL_ACCOUNTS=personal` is set after migration, the label becomes `"personal"` and new Redis keys are written with the `personal:` prefix. Old non-prefixed keys expire naturally — no backfill needed, no orphaned `default:*` keys.

### `poller.py` changes

One poller thread per account. Each maintains its own state under namespaced Redis keys:

```python
f"gmail:historyId:{account}"    # was: "gmail:historyId"
```

Pollers are independent — one failing does not affect others. Thread naming includes the label for log clarity: `poller-personal`, `poller-jobs`.

### `audit.py` changes

Every audit log entry gains an `account` field:

```json
{"ts": "...", "account": "personal", "action": "send", "to": "...", ...}
```

Single log file per service (unchanged). Each entry is attributable to an account.

### `/health` endpoint response

Updated to report per-account status:

```json
{
  "configured": true,
  "accounts": {
    "personal": "ok",
    "jobs": "ok"
  },
  "redis": "ok"
}
```

`configured` is `true` if at least one account is loaded. `doctor.sh` continues to check `configured`; the per-account breakdown is informational.

---

## CLI Changes

### `gmail` and `gcal` scripts

Add `--account <label>` as the first optional flag:

```bash
gmail list                          # default account
gmail --account jobs list           # jobs account
gcal --account jobs list --today    # jobs calendar
```

The flag is forwarded as a `?account=<label>` query parameter to the proxy's `/call` endpoint. If the label is unknown, the proxy returns a structured error (see `server.py` changes above) — the CLI prints it and exits non-zero. No client-side label validation is added; the proxy is authoritative.

The existing wildcard approvals allowlist entries (`gmail *`, `gcal *`) already cover `gmail --account jobs list` — no allowlist changes needed.

### `MEMORY_GUIDE.md` addition

```markdown
## Accounts

`gmail` and `gcal` support multiple Google accounts via `--account <label>`.
Available labels: personal (default), jobs.
Omit `--account` to use the default (personal).
Ask the user which account they mean if context is ambiguous.
Example: `gmail --account jobs list --limit 5`
```

---

## Setup Flow

### Script signature

`scripts/setup-gmail.sh` and `scripts/setup-gcal.sh` gain an optional third positional argument:

```bash
bash scripts/setup-gmail.sh <host> <client_secret_path> [account_label]
```

The Makefile threads the `ACCOUNT=` variable through:

```makefile
setup-gmail:
	@bash scripts/setup-gmail.sh "$(HOST)" "$(CLIENT_SECRET)" "$(ACCOUNT)"

setup-gcal:
	@bash scripts/setup-gcal.sh "$(HOST)" "$(CLIENT_SECRET)" "$(ACCOUNT)"
```

When `ACCOUNT` is empty (no `ACCOUNT=` on the command line), the script enters migration mode (see Migration section above).

### Adding a new account

```bash
make setup-gmail ACCOUNT=jobs CLIENT_SECRET=~/client_secret.json
make setup-gcal  ACCOUNT=jobs CLIENT_SECRET=~/client_secret.json
```

Each script:
1. Runs OAuth browser flow → `token.json`
2. Encrypts token → `gmail_token.jobs.enc`
3. Deploys token file to VPS volume at `/data/gmail_token.jobs.enc`
4. Writes `GMAIL_TOKEN_ENCRYPTION_KEY_JOBS=<key>` to `.env` (removes old entry first)
5. Adds `jobs` to `GMAIL_ACCOUNTS` in `.env` idempotently (no duplicate if re-run)
6. Restarts `mail-proxy` to load the new account

Step 5 uses a simple sed+append pattern:

```bash
# Remove existing entry for this label, then append
sed -i "/^GMAIL_TOKEN_ENCRYPTION_KEY_${LABEL_UPPER}=/d" .env
echo "GMAIL_TOKEN_ENCRYPTION_KEY_${LABEL_UPPER}=${KEY}" >> .env

# Add label to GMAIL_ACCOUNTS if not already present
if grep -q "^GMAIL_ACCOUNTS=" .env; then
    if ! grep -q "^GMAIL_ACCOUNTS=.*\b${ACCOUNT}\b" .env; then
        sed -i "s/^GMAIL_ACCOUNTS=\(.*\)/GMAIL_ACCOUNTS=\1,${ACCOUNT}/" .env
    fi
else
    echo "GMAIL_ACCOUNTS=${ACCOUNT}" >> .env
fi
```

---

## Docker Compose Changes

**Switch `mail-proxy` and `calendar-proxy` from an explicit `environment:` block to `env_file: .env`** for the token encryption keys. This is the only way to support N accounts without editing `docker-compose.yml` per account.

```yaml
mail-proxy:
  env_file:
    - .env
  environment:
    # Non-secret config vars remain explicit so they are visible in the compose file
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
    # GMAIL_ACCOUNTS, GMAIL_TOKEN_ENCRYPTION_KEY_* come from env_file
```

`env_file: .env` passes all vars from `.env` into the container, including any `GMAIL_TOKEN_ENCRYPTION_KEY_<NEW_LABEL>` added by future setup runs. No `docker-compose.yml` edit is needed when adding a third (or fourth) account.

The same pattern applies to `calendar-proxy`.

---

## `doctor.sh` Changes

Replace the single Gmail/GCal token check with loops driven by `GMAIL_ACCOUNTS` / `GCAL_ACCOUNTS`:

```bash
# Gmail
if [ -n "${GMAIL_ACCOUNTS:-}" ]; then
    IFS=',' read -ra _gmail_accounts <<< "$GMAIL_ACCOUNTS"
    for _acct in "${_gmail_accounts[@]}"; do
        if sudo docker compose exec -T openclaw test -f "/data/gmail_token.${_acct}.enc" 2>/dev/null; then
            pass "gmail:${_acct}  token present"
        else
            warn "gmail:${_acct}  token missing → run: make setup-gmail ACCOUNT=${_acct}"
        fi
    done
else
    # legacy single-account check
    if sudo docker compose exec -T openclaw test -f /data/gmail_token.enc 2>/dev/null; then
        pass "gmail_token.enc  present (legacy)"
    else
        skip "Gmail  not configured → run: make setup-gmail CLIENT_SECRET=..."
    fi
fi
```

Same pattern for `GCAL_ACCOUNTS` / `gcal_token.*.enc`.

Health check still hits `/health` on the running service and checks `configured`.

---

## Testing

- `TokenStore.load_all()`: single account, multi-account, legacy fallback, missing key (skipped with warning), token-exists-but-no-key (RuntimeError)
- `policies.py`: all three Redis key families include account label; default account uses correct prefix
- `poller.py`: one thread per account; independent historyIds; one thread failure does not stop others
- `server.py`: `account=""` routes to default; `account="jobs"` routes to jobs store; unknown label returns structured error
- CLI: `--account jobs` forwards `?account=jobs` to proxy
- Migration: legacy `gmail_token.enc` + `GMAIL_TOKEN_ENCRYPTION_KEY` detected and renamed correctly
- Backward compat: all existing single-account tests pass unchanged

---

## What Does Not Change

- Number of Docker containers
- Port numbers (8091 for mail-proxy, 8080 for calendar-proxy)
- MCP tool names (`gmail_list`, `gmail_send`, etc.)
- Approval allowlist entries
- Audit log location or rotation
- Rate limit values (limits are per-account, same values)
- The `contacts` tool (tied to the default account only — contacts are typically shared across accounts)
