import json
import time
import pytest
from unittest.mock import MagicMock, patch


def _make_scorer(threshold=7, model="claude-haiku-4-5-20251001"):
    import scorer
    s = scorer.ImportanceScorer(api_key="test-key", model=model, threshold=threshold)
    return s


def _fake_response(results: list[dict]) -> MagicMock:
    content = MagicMock()
    content.text = json.dumps(results)
    response = MagicMock()
    response.content = [content]
    return response


def test_score_returns_messages_above_threshold():
    import scorer
    s = _make_scorer(threshold=7)
    messages = [
        {"message_id": "m1", "from_addr": "a@b.com", "subject": "Urgent", "snippet": "..."},
        {"message_id": "m2", "from_addr": "c@d.com", "subject": "Newsletter", "snippet": "..."},
    ]
    api_results = [
        {"message_id": "m1", "score": 9, "summary": "Very important"},
        {"message_id": "m2", "score": 3, "summary": "Spam newsletter"},
    ]
    with patch.object(s, "_call_api", return_value=api_results):
        results, circuit_open = s.score(messages)
    assert len(results) == 1
    assert results[0]["message_id"] == "m1"
    assert circuit_open is False


def test_score_returns_empty_list_at_threshold_boundary():
    import scorer
    s = _make_scorer(threshold=7)
    api_results = [{"message_id": "m1", "score": 6, "summary": "Below threshold"}]
    with patch.object(s, "_call_api", return_value=api_results):
        results, _ = s.score([{"message_id": "m1", "from_addr": "a@b.com",
                                "subject": "s", "snippet": "sn"}])
    assert results == []


def test_circuit_breaker_opens_after_3_failures():
    import scorer
    s = _make_scorer()
    with patch.object(s, "_call_api", side_effect=Exception("API error")):
        _, open1 = s.score([{"message_id": "m1", "from_addr": "a@b.com",
                              "subject": "s", "snippet": "sn"}])
        _, open2 = s.score([{"message_id": "m1", "from_addr": "a@b.com",
                              "subject": "s", "snippet": "sn"}])
        _, open3 = s.score([{"message_id": "m1", "from_addr": "a@b.com",
                              "subject": "s", "snippet": "sn"}])
    assert open1 is False  # failure 1: not yet open
    assert open2 is False  # failure 2: not yet open
    assert open3 is True   # failure 3: circuit opens
    assert s.is_circuit_open() is True


def test_circuit_breaker_resets_on_success():
    import scorer
    s = _make_scorer()
    with patch.object(s, "_call_api", side_effect=Exception("fail")):
        for _ in range(3):
            s.score([{"message_id": "m", "from_addr": "a@b.com",
                      "subject": "s", "snippet": "sn"}])
    assert s.is_circuit_open() is True

    # Force backoff to expire
    s._breaker._backoff_until = time.time() - 1

    good_results = [{"message_id": "m", "score": 8, "summary": "Good"}]
    with patch.object(s, "_call_api", return_value=good_results):
        results, open_after = s.score([{"message_id": "m", "from_addr": "a@b.com",
                                         "subject": "s", "snippet": "sn"}])
    assert open_after is False
    assert s.is_circuit_open() is False
    assert s.failure_count() == 0


def test_score_skips_when_circuit_open():
    import scorer
    s = _make_scorer()
    s._breaker._backoff_until = time.time() + 9999  # force open
    results, circuit_open = s.score([{"message_id": "m", "from_addr": "a@b.com",
                                       "subject": "s", "snippet": "sn"}])
    assert results == []
    assert circuit_open is True


def test_call_api_builds_correct_prompt():
    import scorer
    s = _make_scorer()
    messages = [{"message_id": "m1", "from_addr": "alice@example.com",
                 "subject": "Test subject", "snippet": "A" * 300}]
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        content = MagicMock()
        content.text = json.dumps([{"message_id": "m1", "score": 5, "summary": "ok"}])
        resp = MagicMock()
        resp.content = [content]
        return resp

    s._client.messages.create = fake_create
    s._call_api(messages)
    # snippet should be truncated to 200 chars
    user_content = captured["messages"][0]["content"]
    assert "A" * 201 not in user_content
    # system prompt should include "untrusted data"
    assert "untrusted" in captured["system"]
