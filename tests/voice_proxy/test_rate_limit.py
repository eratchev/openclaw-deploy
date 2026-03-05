import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

import pytest
import fakeredis.aioredis as fakeredis

from server import is_rate_limited

pytestmark = pytest.mark.asyncio


async def make_redis():
    return fakeredis.FakeRedis()


async def test_first_message_is_not_rate_limited():
    r = await make_redis()
    assert await is_rate_limited(r, chat_id=1, limit=10) is False


async def test_at_limit_is_rate_limited():
    r = await make_redis()
    for _ in range(10):
        await is_rate_limited(r, chat_id=1, limit=10)
    # 11th call exceeds limit=10
    assert await is_rate_limited(r, chat_id=1, limit=10) is True


async def test_under_limit_is_not_rate_limited():
    r = await make_redis()
    for _ in range(9):
        result = await is_rate_limited(r, chat_id=1, limit=10)
        assert result is False


async def test_different_chats_have_separate_limits():
    r = await make_redis()
    for _ in range(10):
        await is_rate_limited(r, chat_id=1, limit=10)
    # chat_id=2 is a fresh bucket
    assert await is_rate_limited(r, chat_id=2, limit=10) is False


async def test_limit_of_one_blocks_second_call():
    r = await make_redis()
    assert await is_rate_limited(r, chat_id=1, limit=1) is False
    assert await is_rate_limited(r, chat_id=1, limit=1) is True
