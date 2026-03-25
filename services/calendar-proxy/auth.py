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
    def for_account(
        cls, label: str, token_dir: Path = Path("/data")
    ) -> Optional["TokenStore"]:
        """Load TokenStore for a specific account label.

        - No key + no token file  → None (logs warning, caller skips this label)
        - No key + token file exists → RuntimeError (fail-fast)
        - Key present              → TokenStore
        """
        key_env = f"GCAL_TOKEN_ENCRYPTION_KEY_{label.upper()}"
        token_path = Path(token_dir) / f"gcal_token.{label}.enc"
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
    def load_all(
        cls,
        token_path: Path = Path("/data/gcal_token.enc"),
        token_dir: Path = Path("/data"),
    ) -> dict[str, "TokenStore"]:
        """Return {label: TokenStore} for all accounts in GCAL_ACCOUNTS.

        Falls back to single-account mode (label="") if GCAL_ACCOUNTS not set.
        """
        raw = os.environ.get("GCAL_ACCOUNTS", "").strip()
        if not raw:
            store = cls.from_env(token_path=token_path)
            return {"": store} if store else {}
        labels = [lbl.strip() for lbl in raw.split(",") if lbl.strip()]
        result: dict[str, "TokenStore"] = {}
        for label in labels:
            store = cls.for_account(label, token_dir=token_dir)
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
