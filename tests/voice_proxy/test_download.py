import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

import pytest
from aioresponses import aioresponses
import aiohttp

from server import get_file_path, download_audio

pytestmark = pytest.mark.asyncio

TOKEN = "testtoken"


async def test_get_file_path_returns_path():
    with aioresponses() as m:
        m.get(
            f"https://api.telegram.org/bot{TOKEN}/getFile?file_id=abc",
            payload={"ok": True, "result": {"file_path": "voice/file_10.oga"}},
        )
        async with aiohttp.ClientSession() as session:
            path = await get_file_path(TOKEN, "abc", session)
    assert path == "voice/file_10.oga"


async def test_get_file_path_raises_on_error():
    with aioresponses() as m:
        m.get(
            f"https://api.telegram.org/bot{TOKEN}/getFile?file_id=bad",
            payload={"ok": False, "description": "file not found"},
        )
        async with aiohttp.ClientSession() as session:
            with pytest.raises(RuntimeError, match="getFile failed"):
                await get_file_path(TOKEN, "bad", session)


async def test_download_audio_returns_bytes():
    fake_audio = b"\x00\x01\x02\x03"
    with aioresponses() as m:
        m.get(
            f"https://api.telegram.org/file/bot{TOKEN}/voice/file_10.oga",
            body=fake_audio,
            status=200,
        )
        async with aiohttp.ClientSession() as session:
            data = await download_audio(TOKEN, "voice/file_10.oga", session)
    assert data == fake_audio


async def test_download_audio_raises_on_http_error():
    with aioresponses() as m:
        m.get(
            f"https://api.telegram.org/file/bot{TOKEN}/bad/path",
            status=404,
        )
        async with aiohttp.ClientSession() as session:
            with pytest.raises(Exception):
                await download_audio(TOKEN, "bad/path", session)
