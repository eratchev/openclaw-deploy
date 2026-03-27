import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/voice-proxy"))

from server import detect_voice, get_chat_id, mutate_update

# ── detect_voice ─────────────────────────────────────────────────────────

def test_detect_voice_returns_voice_dict():
    update = {"message": {"voice": {"file_id": "abc", "duration": 5, "file_size": 1000}}}
    result = detect_voice(update)
    assert result == {"file_id": "abc", "duration": 5, "file_size": 1000}

def test_detect_audio_returns_audio_dict():
    update = {"message": {"audio": {"file_id": "xyz", "duration": 30, "file_size": 5000}}}
    result = detect_voice(update)
    assert result == {"file_id": "xyz", "duration": 30, "file_size": 5000}

def test_detect_voice_returns_none_for_text_message():
    update = {"message": {"text": "hello"}}
    assert detect_voice(update) is None

def test_detect_voice_returns_none_for_empty_update():
    assert detect_voice({}) is None

def test_detect_voice_handles_edited_message():
    update = {"edited_message": {"voice": {"file_id": "def", "duration": 3}}}
    result = detect_voice(update)
    assert result is not None
    assert result["file_id"] == "def"

# ── get_chat_id ───────────────────────────────────────────────────────────

def test_get_chat_id_returns_id():
    update = {"message": {"chat": {"id": 12345, "type": "private"}}}
    assert get_chat_id(update) == 12345

def test_get_chat_id_returns_none_for_missing_chat():
    assert get_chat_id({}) is None

def test_get_chat_id_handles_edited_message():
    update = {"edited_message": {"chat": {"id": 99}}}
    assert get_chat_id(update) == 99

# ── mutate_update ─────────────────────────────────────────────────────────

def test_mutate_update_adds_text():
    update = {"message": {"voice": {"file_id": "abc"}, "chat": {"id": 1}}}
    result = mutate_update(update, "hello world")
    assert result["message"]["text"] == "hello world"

def test_mutate_update_removes_voice_field():
    update = {"message": {"voice": {"file_id": "abc"}, "chat": {"id": 1}}}
    result = mutate_update(update, "hello")
    assert "voice" not in result["message"]


def test_mutate_update_removes_audio_field():
    update = {"message": {"audio": {"file_id": "xyz"}, "chat": {"id": 1}}}
    result = mutate_update(update, "hello")
    assert "audio" not in result["message"]

def test_mutate_update_sets_voice_transcription_flag():
    update = {"message": {"voice": {"file_id": "abc"}}}
    result = mutate_update(update, "hi")
    assert result["message"]["voice_transcription"] is True

def test_mutate_update_does_not_modify_original():
    update = {"message": {"voice": {"file_id": "abc"}}}
    mutate_update(update, "hi")
    assert "text" not in update["message"]

def test_mutate_update_handles_edited_message():
    update = {"edited_message": {"voice": {"file_id": "abc"}}}
    result = mutate_update(update, "hi")
    assert result["edited_message"]["text"] == "hi"

def test_mutate_update_returns_unchanged_for_unknown_update_type():
    update = {"callback_query": {"data": "something"}}
    result = mutate_update(update, "hi")
    assert "callback_query" in result
    assert "text" not in result.get("callback_query", {})
