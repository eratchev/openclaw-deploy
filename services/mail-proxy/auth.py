import json
import os
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet


class TokenStore:
    def __init__(self, key: bytes, token_path: Path = Path("/data/gmail_token.enc")):
        self._fernet = Fernet(key)
        self._path = Path(token_path)

    @classmethod
    def from_env(
        cls, token_path: Path = Path("/data/gmail_token.enc")
    ) -> Optional["TokenStore"]:
        """Return TokenStore, None (degraded), or raise (misconfigured).

        - No key + no token file  → None (degraded mode, pre-setup)
        - No key + token file exists → RuntimeError (fail-fast)
        - Key present              → TokenStore (token file may or may not exist yet)
        """
        raw_key = os.environ.get("GMAIL_TOKEN_ENCRYPTION_KEY")
        path = Path(token_path)
        if not raw_key and not path.exists():
            return None
        if not raw_key and path.exists():
            raise RuntimeError(
                "GMAIL_TOKEN_ENCRYPTION_KEY is not set but "
                f"{path} exists — refusing to start. "
                "Set GMAIL_TOKEN_ENCRYPTION_KEY or remove the token file."
            )
        return cls(key=raw_key.encode(), token_path=path)

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
