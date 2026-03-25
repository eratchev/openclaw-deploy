import logging
import os
import json
import pytest
from cryptography.fernet import Fernet
from pathlib import Path


def test_from_env_returns_none_when_nothing_configured(tmp_path, monkeypatch):
    """No key + no token file → degraded mode (None), no crash."""
    monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY", raising=False)
    import auth
    result = auth.TokenStore.from_env(token_path=tmp_path / "gmail_token.enc")
    assert result is None


def test_from_env_raises_when_token_exists_but_no_key(tmp_path, monkeypatch):
    """Token file present but no key → fail-fast."""
    monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY", raising=False)
    token_path = tmp_path / "gmail_token.enc"
    token_path.write_bytes(b"dummy")
    import auth
    with pytest.raises(RuntimeError, match="GMAIL_TOKEN_ENCRYPTION_KEY"):
        auth.TokenStore.from_env(token_path=token_path)


def test_from_env_returns_store_when_key_set(tmp_path, monkeypatch):
    """Key present → returns TokenStore instance."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    import auth
    store = auth.TokenStore.from_env(token_path=tmp_path / "gmail_token.enc")
    assert store is not None


def test_encrypt_decrypt_roundtrip(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    import auth
    store = auth.TokenStore.from_env(token_path=tmp_path / "token.enc")
    data = {"token": "abc", "refresh_token": "xyz", "scopes": ["gmail.readonly"]}
    encrypted = store.encrypt(data)
    assert store.decrypt(encrypted) == data


def test_save_is_atomic(tmp_path, monkeypatch):
    """save() writes via tmp file then renames — no partial writes."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    token_path = tmp_path / "token.enc"
    import auth
    store = auth.TokenStore.from_env(token_path=token_path)
    data = {"token": "t1", "refresh_token": "r1"}
    store.save(data)
    assert token_path.exists()
    assert not (tmp_path / "token.enc.tmp").exists()
    assert store.load() == data


def test_for_account_returns_none_when_no_key_no_file(monkeypatch):
    monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", raising=False)
    import auth
    result = auth.TokenStore.for_account("personal")
    assert result is None


def test_for_account_raises_when_file_exists_but_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL", raising=False)
    token_path = tmp_path / "gmail_token.personal.enc"
    token_path.write_bytes(b"dummy")
    import auth
    with pytest.raises(RuntimeError, match="GMAIL_TOKEN_ENCRYPTION_KEY_PERSONAL"):
        auth.TokenStore.for_account("personal", token_dir=tmp_path)


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
