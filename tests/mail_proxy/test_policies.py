import time
import pytest
import fakeredis


def _redis():
    return fakeredis.FakeRedis(decode_responses=False)


def test_update_seen_domains_adds_sender_domain():
    import policies
    r = _redis()
    messages = [
        {"from_addr": "Alice <alice@example.com>"},
        {"from_addr": "bob@other.org"},
    ]
    policies.update_seen_domains(r, messages)
    members = r.zrange("gmail:seen_domains", 0, -1)
    domains = [m.decode() for m in members]
    assert "example.com" in domains
    assert "other.org" in domains


def test_update_seen_domains_resets_ttl(monkeypatch):
    import policies
    r = _redis()
    messages = [{"from_addr": "x@domain.io"}]
    policies.update_seen_domains(r, messages)
    ttl = r.ttl("gmail:seen_domains")
    assert 86390 < ttl <= 86400


def test_check_novel_domain_allowed_when_seen():
    import policies
    r = _redis()
    r.zadd("gmail:seen_domains", {"trusted.com": time.time()})
    ok, reason = policies.check_novel_domain(r, "someone@trusted.com")
    assert ok is True
    assert reason is None


def test_check_novel_domain_denied_when_unseen():
    import policies
    r = _redis()
    ok, reason = policies.check_novel_domain(r, "someone@unseen.com")
    assert ok is False
    assert "domain_not_allowed" in reason


def test_check_novel_domain_denied_when_redis_unavailable():
    """Caller must treat Redis errors as fail-closed for sends."""
    import redis as redis_lib
    import policies
    from unittest.mock import patch
    r = _redis()
    with patch.object(r, "zscore", side_effect=redis_lib.exceptions.ConnectionError("down")):
        with pytest.raises(redis_lib.exceptions.ConnectionError):
            policies.check_novel_domain(r, "a@b.com")


def test_rate_limit_allows_under_max(monkeypatch):
    import policies
    monkeypatch.setenv("GMAIL_MAX_SENDS_PER_DAY", "5")
    r = _redis()
    ok, reason = policies.check_rate_limit(r, date_str="2026-03-13")
    assert ok is True


def test_rate_limit_denies_at_max(monkeypatch):
    import policies
    monkeypatch.setenv("GMAIL_MAX_SENDS_PER_DAY", "2")
    r = _redis()
    r.set("gmail:sends:2026-03-13", "2")
    ok, reason = policies.check_rate_limit(r, date_str="2026-03-13")
    assert ok is False
    assert "rate_limit" in reason


def test_record_send_increments_counter():
    import policies
    r = _redis()
    policies.record_send(r, date_str="2026-03-13")
    policies.record_send(r, date_str="2026-03-13")
    assert int(r.get("gmail:sends:2026-03-13")) == 2


def test_extract_domain_handles_display_name():
    import policies
    assert policies._extract_domain("John Smith <john@acme.com>") == "acme.com"
    assert policies._extract_domain("plain@email.org") == "email.org"


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
