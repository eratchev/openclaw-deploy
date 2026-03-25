import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/calendar-proxy'))

import json
import logging
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet
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


def test_fail_fast_missing_key(tmp_path, monkeypatch):
    """Degraded mode: no key and no token file → None (not a crash)."""
    monkeypatch.delenv("GCAL_TOKEN_ENCRYPTION_KEY", raising=False)
    result = TokenStore.from_env(token_path=tmp_path / "gcal_token.enc")
    assert result is None


def test_load_from_env(tmp_path, monkeypatch):
    key = generate_key()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY", key.decode())
    token_path = tmp_path / "gcal_token.enc"
    token = {"access_token": "tok"}
    store_write = TokenStore(key=key, token_path=token_path)
    store_write.save(token)

    store_read = TokenStore.from_env(token_path=token_path)
    assert store_read.load() == token


# ---------------------------------------------------------------------------
# Degraded-mode and multi-account tests
# ---------------------------------------------------------------------------

def test_from_env_returns_none_when_no_key_no_file(tmp_path, monkeypatch):
    """Degraded mode: no crash when neither key nor file are present."""
    monkeypatch.delenv("GCAL_TOKEN_ENCRYPTION_KEY", raising=False)
    result = TokenStore.from_env(token_path=tmp_path / "gcal_token.enc")
    assert result is None


def test_from_env_raises_when_file_exists_but_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GCAL_TOKEN_ENCRYPTION_KEY", raising=False)
    token_path = tmp_path / "gcal_token.enc"
    token_path.write_bytes(b"dummy")
    with pytest.raises(RuntimeError, match="GCAL_TOKEN_ENCRYPTION_KEY"):
        TokenStore.from_env(token_path=token_path)


def test_for_account_returns_none_when_no_key_no_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL", raising=False)
    result = TokenStore.for_account("personal", token_dir=tmp_path)
    assert result is None


def test_for_account_returns_store_when_key_set(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL", key)
    store = TokenStore.for_account("personal", token_dir=tmp_path)
    assert store is not None


def test_load_all_legacy_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("GCAL_ACCOUNTS", raising=False)
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY", key)
    result = TokenStore.load_all(token_path=tmp_path / "gcal_token.enc")
    assert "" in result


def test_load_all_multi_account(tmp_path, monkeypatch):
    monkeypatch.setenv("GCAL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY_JOBS", key2)
    result = TokenStore.load_all(token_dir=tmp_path)
    assert set(result.keys()) == {"personal", "jobs"}


def test_load_all_skips_missing_account(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("GCAL_ACCOUNTS", "personal,jobs")
    key1 = Fernet.generate_key().decode()
    monkeypatch.setenv("GCAL_TOKEN_ENCRYPTION_KEY_PERSONAL", key1)
    monkeypatch.delenv("GCAL_TOKEN_ENCRYPTION_KEY_JOBS", raising=False)
    with caplog.at_level(logging.WARNING, logger="auth"):
        result = TokenStore.load_all(token_dir=tmp_path)
    assert "personal" in result
    assert "jobs" not in result
