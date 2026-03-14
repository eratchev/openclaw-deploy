import os
import sys
import pytest

_MP_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../services/mail-proxy"))

# Module names that exist in both calendar-proxy and mail-proxy (and potentially
# voice-proxy).  We must evict all of them before each mail-proxy test module so
# that `import auth` (etc.) inside test bodies resolves to the mail-proxy copy.
_SHARED_MODULES = {"audit", "auth", "models", "policies", "server"}


@pytest.fixture(autouse=True, scope="module")
def _ensure_mail_proxy_path():
    """Ensure sys.path and sys.modules point to mail-proxy for all shared module names.

    Both calendar-proxy and mail-proxy expose modules with identical names (auth,
    audit, models, policies, server).  When pytest collects both suites, whichever
    service directory was inserted into sys.path first will win unless we evict the
    stale cached modules before each mail-proxy test module runs.
    """
    # Bring mail-proxy to the front of sys.path so bare `import auth` finds it.
    if _MP_DIR in sys.path:
        sys.path.remove(_MP_DIR)
    sys.path.insert(0, _MP_DIR)

    # Evict any cached versions of shared modules so they are re-imported fresh
    # from the mail-proxy directory.
    for mod in _SHARED_MODULES:
        sys.modules.pop(mod, None)

    yield

    # Cleanup: evict mail-proxy modules so subsequent test suites (if any) start
    # from a clean state.
    for mod in _SHARED_MODULES:
        sys.modules.pop(mod, None)
