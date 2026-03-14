import pytest


def test_list_input_defaults():
    import models
    m = models.ListInput()
    assert m.limit == 10
    assert m.label == "INBOX"


def test_list_input_rejects_large_limit():
    import models
    with pytest.raises(Exception):
        models.ListInput(limit=200)


def test_send_input_valid():
    import models
    m = models.SendInput(to="alice@example.com", subject="Hi", body="Hello")
    assert m.confirmed is False


def test_send_input_rejects_multiple_recipients():
    import models
    with pytest.raises(Exception):
        models.SendInput(to="a@b.com,c@d.com", subject="s", body="b")


def test_send_input_rejects_no_at_symbol():
    import models
    with pytest.raises(Exception):
        models.SendInput(to="notanemail", subject="s", body="b")


def test_reply_input_requires_fields():
    import models
    with pytest.raises(Exception):
        models.ReplyInput(body="hi")  # missing thread_id and message_id


def test_get_input_valid():
    import models
    m = models.GetInput(thread_id="thread-123")
    assert m.thread_id == "thread-123"


def test_search_input_valid():
    import models
    m = models.SearchInput(query="from:boss@company.com")
    assert m.limit == 10


def test_mark_read_input_valid():
    import models
    m = models.MarkReadInput(message_id="msg-abc")
    assert m.message_id == "msg-abc"


def test_send_input_accepts_display_name_format():
    import models
    m = models.SendInput(to="Alice <alice@example.com>", subject="Hi", body="Hello")
    assert m.to == "Alice <alice@example.com>"


def test_search_input_rejects_large_limit():
    import models
    with pytest.raises(Exception):
        models.SearchInput(query="test", limit=200)
