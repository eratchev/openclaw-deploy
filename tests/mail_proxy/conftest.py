import os
import sys
import pytest

_MP_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../services/mail-proxy"))


@pytest.fixture(autouse=True, scope="module")
def _ensure_mail_proxy_path():
    """Add mail-proxy to sys.path and evict cached server module.

    server.py calls TokenStore.from_env() at module level — tests import it
    lazily inside test bodies after monkeypatching env vars.
    """
    if _MP_DIR not in sys.path:
        sys.path.insert(0, _MP_DIR)
    sys.modules.pop("server", None)
    yield
    sys.modules.pop("server", None)
