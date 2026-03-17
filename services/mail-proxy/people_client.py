"""Google People API wrapper. No policy logic — just API calls."""
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def build_service(token_store) -> Any:
    """Build and return an authenticated People API v1 service. Refreshes token if needed."""
    token_data = token_store.load()
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes"),
    )
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            token_store.save({
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes) if creds.scopes else token_data.get("scopes"),
            })
        else:
            raise RuntimeError(
                "People API credentials invalid and cannot be refreshed. Re-run make setup-gmail."
            )
    return build("people", "v1", credentials=creds)


def search_contacts(service, query: str, limit: int = 5) -> list[dict]:
    """Search saved contacts and otherContacts for the given query.

    Returns list of {"name": str, "emails": list[str], "phones": list[str]} dicts.
    Raises ValueError if the contacts.readonly scope is missing (HTTP 403).
    Propagates other HttpErrors.
    """
    results = []

    # Search saved contacts — returns results under results[].person
    try:
        resp = service.people().searchContacts(
            query=query,
            readMask="names,emailAddresses,phoneNumbers",
            pageSize=limit,
        ).execute()
        for item in resp.get("results", []):
            results.append(_normalise_person(item.get("person", {})))
    except HttpError as e:
        if e.resp.status == 403:
            raise ValueError(
                "contacts.readonly scope not granted — re-run: make setup-gmail CLIENT_SECRET=..."
            )
        raise

    # Search otherContacts (people emailed but not explicitly saved) — returns results under
    # otherContacts[] (different shape from searchContacts). Deduplicate against saved contacts.
    existing_emails = {email for r in results for email in r["emails"]}
    try:
        resp = service.otherContacts().search(
            query=query,
            readMask="names,emailAddresses,phoneNumbers",
            pageSize=limit,
        ).execute()
        for item in resp.get("otherContacts", []):
            contact = _normalise_person(item)
            if not any(e in existing_emails for e in contact["emails"]):
                results.append(contact)
                existing_emails.update(contact["emails"])
    except HttpError as e:
        if e.resp.status == 403:
            raise ValueError(
                "contacts.readonly scope not granted — re-run: make setup-gmail CLIENT_SECRET=..."
            )
        raise

    return results[:limit]


def _normalise_person(person: dict) -> dict:
    """Extract name, emails, phones from a People API person resource dict."""
    names = person.get("names", [])
    name = names[0].get("displayName", "") if names else ""
    emails = [e.get("value", "") for e in person.get("emailAddresses", []) if e.get("value")]
    phones = [p.get("value", "") for p in person.get("phoneNumbers", []) if p.get("value")]
    return {"name": name, "emails": emails, "phones": phones}
