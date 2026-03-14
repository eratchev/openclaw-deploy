"""Pure Gmail API functions. No policy logic — just API calls."""
import base64
import email.mime.text
import re
from typing import Any, Optional

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


_PLAIN_TEXT_RE = re.compile(r"<[^>]+>")


def build_service(token_store) -> Any:
    """Build and return an authenticated Gmail API service. Refreshes token if needed."""
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
            raise RuntimeError("Gmail credentials invalid and cannot be refreshed. Re-run make setup-gmail.")
    return build("gmail", "v1", credentials=creds)


def list_messages(service, label: str = "INBOX", limit: int = 10) -> list[dict]:
    """List unread messages. Returns list of simplified message dicts."""
    resp = service.users().messages().list(
        userId="me", labelIds=[label, "UNREAD"], maxResults=limit
    ).execute()
    result = []
    for item in resp.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=item["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        result.append({
            "message_id": msg["id"],
            "thread_id": msg.get("threadId", ""),
            "from_addr": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "snippet": msg.get("snippet", ""),
            "date": headers.get("Date", ""),
            "unread": "UNREAD" in msg.get("labelIds", []),
        })
    return result


def get_thread(service, thread_id: str) -> dict:
    """Fetch full thread. Returns thread_id + list of messages with plain-text body."""
    thread = service.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()
    messages = []
    for msg in thread.get("messages", []):
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        body = _extract_plain_text(msg)
        messages.append({
            "message_id": msg["id"],
            "from_addr": headers.get("From", ""),
            "to_addr": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body": body,
        })
    return {"thread_id": thread_id, "messages": messages}


def search_messages(service, query: str, limit: int = 10) -> list[dict]:
    """Search using Gmail query syntax. Returns simplified message dicts."""
    resp = service.users().messages().list(
        userId="me", q=query, maxResults=limit
    ).execute()
    result = []
    for item in resp.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=item["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        result.append({
            "message_id": msg["id"],
            "thread_id": msg.get("threadId", ""),
            "from_addr": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "snippet": msg.get("snippet", ""),
            "date": headers.get("Date", ""),
            "unread": "UNREAD" in msg.get("labelIds", []),
        })
    return result


def send_email(service, to: str, subject: str, body: str) -> str:
    """Send a new email. Returns new message ID."""
    msg = email.mime.text.MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    return result["id"]


def reply_to_thread(service, thread_id: str, message_id: str, body: str) -> str:
    """Reply to an existing thread. Returns new message ID."""
    orig = service.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["Subject", "From", "Message-ID"],
    ).execute()
    headers = {h["name"]: h["value"] for h in orig.get("payload", {}).get("headers", [])}

    msg = email.mime.text.MIMEText(body)
    msg["to"] = headers.get("From", "")
    subject = headers.get("Subject", "")
    msg["subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"
    msg_id_header = headers.get("Message-ID", "")
    if msg_id_header:
        msg["In-Reply-To"] = msg_id_header
        msg["References"] = msg_id_header

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(
        userId="me", body={"raw": raw, "threadId": thread_id}
    ).execute()
    return result["id"]


def mark_read(service, message_id: str) -> None:
    """Remove UNREAD label from a message."""
    service.users().messages().modify(
        userId="me", id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def get_history(service, start_history_id: str, label: str = "INBOX") -> tuple[list[str], str]:
    """Return (new_message_ids, new_historyId) since start_history_id."""
    resp = service.users().history().list(
        userId="me",
        startHistoryId=start_history_id,
        labelId=label,
        historyTypes=["messageAdded"],
    ).execute()
    new_id = str(resp.get("historyId", start_history_id))
    msg_ids = []
    for record in resp.get("history", []):
        for added in record.get("messagesAdded", []):
            mid = added.get("message", {}).get("id")
            if mid:
                msg_ids.append(mid)
    return msg_ids, new_id


def _extract_plain_text(msg: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    payload = msg.get("payload", {})
    return _walk_parts(payload)


_MAX_BODY_CHARS = 5000  # prevent oversized bodies reaching OpenClaw context window


def _walk_parts(part: dict) -> str:
    mime = part.get("mimeType", "")
    if mime == "text/plain":
        data = part.get("body", {}).get("data", "")
        if data:
            text = base64.urlsafe_b64decode(data + "==").decode(errors="replace")
            return text[:_MAX_BODY_CHARS]
    for sub in part.get("parts", []):
        result = _walk_parts(sub)
        if result:
            return result
    return ""
