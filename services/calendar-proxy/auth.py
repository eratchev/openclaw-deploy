import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


def generate_key() -> bytes:
    return Fernet.generate_key()


class TokenStore:
    def __init__(self, key: bytes, token_path: Path = Path("/data/gcal_token.enc")):
        self._fernet = Fernet(key)
        self._path = Path(token_path)

    @classmethod
    def from_env(cls, token_path: Path = Path("/data/gcal_token.enc")) -> "TokenStore":
        raw_key = os.environ.get("GCAL_TOKEN_ENCRYPTION_KEY")
        if not raw_key:
            raise RuntimeError(
                "Missing GCAL_TOKEN_ENCRYPTION_KEY — refusing to start. "
                "Generate one with: python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        return cls(key=raw_key.encode(), token_path=token_path)

    def encrypt(self, token_dict: dict[str, Any]) -> bytes:
        return self._fernet.encrypt(json.dumps(token_dict).encode())

    def decrypt(self, data: bytes) -> dict[str, Any]:
        return json.loads(self._fernet.decrypt(data))

    def save(self, token_dict: dict[str, Any]) -> None:
        """Atomic write: encrypt → tmp → rename."""
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(self.encrypt(token_dict))
        tmp.replace(self._path)  # atomic on Linux

    def load(self) -> dict[str, Any]:
        return self.decrypt(self._path.read_bytes())
