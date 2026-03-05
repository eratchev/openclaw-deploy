import os
import sys
import pytest

_CP_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../services/calendar-proxy"))


@pytest.fixture(autouse=True, scope="module")
def _ensure_calendar_proxy_server():
    """Ensure sys.modules["server"] points to calendar-proxy during calendar-proxy tests.

    We do NOT import server here because server.py calls TokenStore.from_env() at
    module level and will fail without the required env vars. Instead we just evict
    any foreign module from the cache and make sure the calendar-proxy directory is
    first on sys.path. Each test imports server lazily inside its own body (after
    monkeypatching env vars), so the first import picks up the right file.
    """
    if _CP_DIR not in sys.path:
        sys.path.insert(0, _CP_DIR)
    sys.modules.pop("server", None)
    yield
    sys.modules.pop("server", None)
