import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.asyncio


def _server():
    return sys.modules["server"]


async def test_transcribe_returns_text():
    server = _server()
    mock_result = MagicMock()
    mock_result.text = "hello world"
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_result)

    with patch.object(server, "_openai", mock_client):
        result = await server.transcribe_audio(b"fake_audio_bytes")

    assert result == "hello world"


async def test_transcribe_calls_whisper_1_model():
    server = _server()
    mock_result = MagicMock()
    mock_result.text = "test"
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_result)

    with patch.object(server, "_openai", mock_client):
        await server.transcribe_audio(b"audio")

    call_kwargs = mock_client.audio.transcriptions.create.call_args
    assert call_kwargs.kwargs.get("model") == "whisper-1"


async def test_transcribe_passes_bytes_as_file():
    server = _server()
    mock_result = MagicMock()
    mock_result.text = "hi"
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_result)

    with patch.object(server, "_openai", mock_client):
        await server.transcribe_audio(b"\x00\x01\x02")

    call_kwargs = mock_client.audio.transcriptions.create.call_args.kwargs
    audio_file = call_kwargs["file"]
    assert hasattr(audio_file, "read")  # file-like object
    assert audio_file.name == "voice.ogg"


async def test_transcribe_timeout_raises():
    import asyncio
    server = _server()

    async def slow_create(**kwargs):
        await asyncio.sleep(100)

    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = slow_create

    with patch.object(server, "_openai", mock_client), \
         patch.object(server, "WHISPER_TIMEOUT", 0.01):
        with pytest.raises(asyncio.TimeoutError):
            await server.transcribe_audio(b"audio")
