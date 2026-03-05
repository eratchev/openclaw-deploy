import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

import json
import pytest
from aioresponses import aioresponses
import aiohttp

from server import forward_raw

pytestmark = pytest.mark.asyncio

UPSTREAM = "http://openclaw:18789"


async def test_forward_raw_returns_upstream_response():
    body = b'{"update_id": 1}'
    with aioresponses() as m:
        m.post(f"{UPSTREAM}/", body=b"ok", status=200)
        async with aiohttp.ClientSession() as session:
            resp = await forward_raw(body, "/", {}, UPSTREAM, session)
    assert resp.status == 200


async def test_forward_raw_strips_host_header():
    body = b'{"update_id": 1}'
    with aioresponses() as m:
        m.post(f"{UPSTREAM}/", body=b"ok", status=200)
        async with aiohttp.ClientSession() as session:
            # Should not raise even with host header present
            resp = await forward_raw(
                body, "/", {"host": "example.com", "content-type": "application/json"}, UPSTREAM, session
            )
    assert resp.status == 200


async def test_forward_raw_sends_body_unchanged():
    body = b'{"update_id": 42, "message": {"text": "hi"}}'
    captured = {}

    with aioresponses() as m:
        async def capture_request(url, **kwargs):
            captured["data"] = kwargs.get("data")
            from aioresponses.core import CallbackResult
            return CallbackResult(status=200, body=b"ok")
        m.post(f"{UPSTREAM}/", callback=capture_request)
        async with aiohttp.ClientSession() as session:
            await forward_raw(body, "/", {}, UPSTREAM, session)

    assert captured["data"] == body


async def test_forward_raw_returns_502_response_from_upstream():
    body = b'{}'
    with aioresponses() as m:
        m.post(f"{UPSTREAM}/", body=b"error", status=502)
        async with aiohttp.ClientSession() as session:
            resp = await forward_raw(body, "/", {}, UPSTREAM, session)
    assert resp.status == 502
