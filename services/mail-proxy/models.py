import re
from typing import Optional
from pydantic import BaseModel, field_validator


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _check_limit(v: int) -> int:
    if v < 1 or v > 50:
        raise ValueError("limit must be between 1 and 50")
    return v


class ListInput(BaseModel):
    limit: int = 10
    label: str = "INBOX"

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, v: int) -> int:
        return _check_limit(v)


class GetInput(BaseModel):
    thread_id: str


class SearchInput(BaseModel):
    query: str
    limit: int = 10

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, v: int) -> int:
        return _check_limit(v)


class ReplyInput(BaseModel):
    thread_id: str
    message_id: str   # original message to reply to (for threading headers)
    body: str


class SendInput(BaseModel):
    to: str
    subject: str
    body: str
    confirmed: bool = False

    @field_validator("to")
    @classmethod
    def validate_single_recipient(cls, v: str) -> str:
        if "," in v or ";" in v:
            raise ValueError("Only a single recipient is allowed (no CC/BCC in Phase 1)")
        # extract address from "Name <email>" format
        match = re.search(r"<([^>]+)>", v)
        addr = match.group(1) if match else v.strip()
        if not _EMAIL_RE.match(addr):
            raise ValueError(f"Invalid email address: {addr!r}")
        return v


class MarkReadInput(BaseModel):
    message_id: str


# ── Response types ────────────────────────────────────────────────────────────

class MessageSummary(BaseModel):
    message_id: str
    thread_id: str
    from_addr: str
    subject: str
    snippet: str
    date: str
    unread: bool


class ThreadMessage(BaseModel):
    message_id: str
    from_addr: str
    to_addr: str
    subject: str
    date: str
    body: str  # plain-text only, truncated to 5000 chars in gmail_client


class ThreadDetail(BaseModel):
    thread_id: str
    messages: list[ThreadMessage]


class PolicyResult(BaseModel):
    allowed: bool
    reason: Optional[str] = None
    needs_confirmation: bool = False
