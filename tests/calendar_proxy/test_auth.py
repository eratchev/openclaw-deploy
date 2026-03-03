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
