import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/mail-proxy'))

import pytest
from unittest.mock import MagicMock
from googleapiclient.errors import HttpError

import people_client


def _make_person(name, emails, phones=None):
    """Build a People API person resource dict."""
    return {
        "names": [{"displayName": name}],
        "emailAddresses": [{"value": e} for e in emails],
        "phoneNumbers": [{"value": p} for p in (phones or [])],
    }


def _make_service(contacts_results=None, other_results=None):
    """Build a mock People API service.

    people().searchContacts().execute() → {"results": [{"person": ...}]}
    otherContacts().search().execute()  → {"otherContacts": [...]}
    """
    service = MagicMock()
    service.people.return_value.searchContacts.return_value.execute.return_value = {
        "results": [{"person": p} for p in (contacts_results or [])],
    }
    service.otherContacts.return_value.search.return_value.execute.return_value = {
        "otherContacts": (other_results or []),
    }
    return service


def test_search_returns_match_from_contacts():
    service = _make_service(
        contacts_results=[_make_person("Alice Johnson", ["alice@work.com"], ["+1-555-0100"])],
    )
    results = people_client.search_contacts(service, "Alice")
    assert len(results) == 1
    assert results[0]["name"] == "Alice Johnson"
    assert "alice@work.com" in results[0]["emails"]
    assert "+1-555-0100" in results[0]["phones"]


def test_search_returns_match_from_other_contacts():
    service = _make_service(
        contacts_results=[],
        other_results=[_make_person("Bob Smith", ["bob@example.com"])],
    )
    results = people_client.search_contacts(service, "Bob")
    assert len(results) == 1
    assert results[0]["name"] == "Bob Smith"
    assert "bob@example.com" in results[0]["emails"]
    assert results[0]["phones"] == []


def test_search_deduplicates_same_email_across_sources():
    """Contact appearing in both sources with same email should only appear once."""
    person = _make_person("Alice", ["alice@work.com"])
    service = _make_service(
        contacts_results=[person],
        other_results=[person],
    )
    results = people_client.search_contacts(service, "Alice")
    assert len(results) == 1


def test_search_merges_results_from_both_sources():
    """Contacts with different emails from both sources should both be returned."""
    service = _make_service(
        contacts_results=[_make_person("Alice Johnson", ["alice@work.com"])],
        other_results=[_make_person("Alice Chen", ["achen@example.com"])],
    )
    results = people_client.search_contacts(service, "Alice", limit=5)
    assert len(results) == 2
    names = {r["name"] for r in results}
    assert "Alice Johnson" in names
    assert "Alice Chen" in names


def test_search_returns_multiple_matches():
    service = _make_service(
        contacts_results=[
            _make_person("Alice Johnson", ["alice@work.com"]),
            _make_person("Alice Chen", ["achen@example.com"]),
        ],
    )
    results = people_client.search_contacts(service, "Alice")
    assert len(results) == 2


def test_search_returns_empty_list_when_no_results():
    service = _make_service()
    results = people_client.search_contacts(service, "ZZZnonexistent")
    assert results == []


def test_search_raises_value_error_on_403_from_contacts():
    service = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status = 403
    service.people.return_value.searchContacts.return_value.execute.side_effect = (
        HttpError(resp=mock_resp, content=b"Forbidden")
    )
    with pytest.raises(ValueError, match="scope not granted"):
        people_client.search_contacts(service, "Alice")


def test_search_raises_value_error_on_403_from_other_contacts():
    service = MagicMock()
    mock_resp_403 = MagicMock()
    mock_resp_403.status = 403
    # searchContacts succeeds, otherContacts.search fails with 403
    service.people.return_value.searchContacts.return_value.execute.return_value = {
        "results": []
    }
    service.otherContacts.return_value.search.return_value.execute.side_effect = (
        HttpError(resp=mock_resp_403, content=b"Forbidden")
    )
    with pytest.raises(ValueError, match="scope not granted"):
        people_client.search_contacts(service, "Alice")


def test_search_propagates_non_403_http_errors():
    service = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status = 500
    service.people.return_value.searchContacts.return_value.execute.side_effect = (
        HttpError(resp=mock_resp, content=b"Internal Server Error")
    )
    with pytest.raises(HttpError):
        people_client.search_contacts(service, "Alice")


def test_search_respects_limit():
    service = _make_service(
        contacts_results=[_make_person(f"Person {i}", [f"p{i}@example.com"]) for i in range(10)],
    )
    results = people_client.search_contacts(service, "Person", limit=3)
    assert len(results) == 3


def test_build_service_raises_runtime_error_when_no_refresh_token():
    """build_service raises RuntimeError when token is invalid and has no refresh_token."""
    mock_store = MagicMock()
    mock_store.load.return_value = {
        "token": None,
        "refresh_token": None,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "id",
        "client_secret": "sec",
        "scopes": [],
    }
    with pytest.raises(RuntimeError, match="cannot be refreshed"):
        people_client.build_service(mock_store)
