import os
import sys
import pytest

_CP_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../services/calendar-proxy"))

# Module names that exist in both calendar-proxy and mail-proxy.  We must evict
# all of them before each calendar-proxy test module runs so that `import auth`
# (etc.) inside test bodies resolves to the calendar-proxy copy.
_SHARED_MODULES = {"audit", "auth", "models", "policies", "server"}


@pytest.fixture(autouse=True, scope="module")
def _ensure_calendar_proxy_server():
    """Ensure sys.path and sys.modules point to calendar-proxy for all shared module names.

    We do NOT import server here because server.py calls TokenStore.from_env() at
    module level and will fail without the required env vars. Instead we evict any
    foreign module from the cache and make sure the calendar-proxy directory is
    first on sys.path. Each test imports modules lazily inside its own body (after
    monkeypatching env vars), so the first import picks up the right file.
    """
    # Bring calendar-proxy to the front of sys.path so bare `import auth` finds it.
    if _CP_DIR in sys.path:
        sys.path.remove(_CP_DIR)
    sys.path.insert(0, _CP_DIR)

    # Evict any cached versions of shared modules so they are re-imported fresh
    # from the calendar-proxy directory.
    for mod in _SHARED_MODULES:
        sys.modules.pop(mod, None)

    yield

    # Cleanup: evict calendar-proxy modules so subsequent test suites start clean.
    for mod in _SHARED_MODULES:
        sys.modules.pop(mod, None)
