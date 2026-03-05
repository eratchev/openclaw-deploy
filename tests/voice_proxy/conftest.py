import os
import sys
import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

_VP_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))


@pytest.fixture(autouse=True, scope="module")
def _ensure_voice_proxy_server():
    """Ensure sys.modules["server"] points to voice-proxy during voice-proxy tests."""
    if _VP_DIR not in sys.path:
        sys.path.insert(0, _VP_DIR)
    sys.modules.pop("server", None)
    import server  # noqa: F401 - re-import to ensure correct module in cache
    yield
    sys.modules.pop("server", None)
