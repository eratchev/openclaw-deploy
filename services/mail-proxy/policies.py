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
