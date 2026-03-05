import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from server import transcribe_audio

pytestmark = pytest.mark.asyncio


async def test_transcribe_returns_text():
    mock_result = MagicMock()
    mock_result.text = "hello world"
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_result)

    with patch("server.openai.AsyncOpenAI", return_value=mock_client):
        result = await transcribe_audio(b"fake_audio_bytes", "test-key")

    assert result == "hello world"


async def test_transcribe_calls_whisper_1_model():
    mock_result = MagicMock()
    mock_result.text = "test"
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_result)

    with patch("server.openai.AsyncOpenAI", return_value=mock_client):
        await transcribe_audio(b"audio", "test-key")

    call_kwargs = mock_client.audio.transcriptions.create.call_args
    assert call_kwargs.kwargs.get("model") == "whisper-1" or call_kwargs.args[0] if call_kwargs.args else False or \
        any(v == "whisper-1" for v in (call_kwargs.kwargs or {}).values())


async def test_transcribe_passes_bytes_as_file():
    mock_result = MagicMock()
    mock_result.text = "hi"
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_result)

    with patch("server.openai.AsyncOpenAI", return_value=mock_client):
        await transcribe_audio(b"\x00\x01\x02", "key")

    call_kwargs = mock_client.audio.transcriptions.create.call_args.kwargs
    audio_file = call_kwargs["file"]
    assert hasattr(audio_file, "read")  # file-like object
    assert audio_file.name == "voice.ogg"


async def test_transcribe_timeout_raises():
    import asyncio
    mock_client = AsyncMock()
    mock_client.audio.transcriptions.create = AsyncMock(
        side_effect=asyncio.TimeoutError()
    )

    with patch("server.openai.AsyncOpenAI", return_value=mock_client):
        with pytest.raises(asyncio.TimeoutError):
            await transcribe_audio(b"audio", "key")
