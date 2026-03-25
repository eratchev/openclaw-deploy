"""Background polling loop for Gmail new-message notifications."""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

import redis as redis_lib

logger = logging.getLogger(__name__)

_SEEN_TTL = 3600  # 1 hour dedup window


def _history_id_key(account: str) -> str:
    return f"gmail:historyId:{account}" if account else "gmail:historyId"


def _seen_key(account: str, message_id: str) -> str:
    return f"gmail:seen:{account}:{message_id}" if account else f"gmail:seen:{message_id}"


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def notify_telegram(messages: list[dict], token: str, chat_id: str) -> None:
    for msg in messages:
        text = (
            f"📧 <b>From:</b> {msg['from_addr']}\n"
            f"<b>Subject:</b> {msg['subject']}\n"
            f"{msg.get('summary', '')}"
        )
        try:
            _send_telegram(token, chat_id, text)
        except Exception as exc:
            logger.warning("Telegram notify failed for %s: %s", msg["message_id"], exc)


def _extract_message_meta(service, message_id: str) -> Optional[dict]:
    """Fetch message metadata (no body). Returns None on error."""
    try:
        raw = service.users().messages().get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in raw.get("payload", {}).get("headers", [])}
        return {
            "message_id": raw["id"],
            "thread_id": raw.get("threadId", ""),
            "from_addr": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "snippet": raw.get("snippet", ""),
        }
    except Exception as exc:
        logger.warning("Failed to fetch message %s: %s", message_id, exc)
        return None


def poll_once(
    service,
    r: redis_lib.Redis,
    scorer,
    notify_fn: Callable[[list[dict]], None],
    poll_label: str,
    account: str = "",
) -> None:
    """Single poll cycle: fetch new messages, score, notify.

    Handles first-run (no historyId) by recording current position without notifying.
    """
    # Check circuit breaker before doing any work
    if scorer.is_circuit_open():
        logger.debug("Circuit breaker open — skipping poll cycle")
        return

    history_id_bytes = r.get(_history_id_key(account))

    if history_id_bytes is None:
        # First run: record current historyId, notify nothing
        profile = service.users().getProfile(userId="me").execute()
        current_id = str(profile.get("historyId", ""))
        if current_id:
            r.set(_history_id_key(account), current_id.encode())
        return

    start_history_id = history_id_bytes.decode()

    try:
        resp = service.users().history().list(
            userId="me",
            startHistoryId=start_history_id,
            labelId=poll_label,
            historyTypes=["messageAdded"],
        ).execute()
    except Exception as exc:
        logger.warning("Gmail history.list failed: %s", exc)
        return

    new_id = str(resp.get("historyId", start_history_id))
    history_records = resp.get("history", [])

    # Collect new message IDs (deduplicated within this batch)
    seen_in_batch: set[str] = set()
    candidate_ids: list[str] = []
    for record in history_records:
        for added in record.get("messagesAdded", []):
            msg_id = added.get("message", {}).get("id")
            if msg_id and msg_id not in seen_in_batch:
                seen_in_batch.add(msg_id)
                candidate_ids.append(msg_id)

    # Filter already-deduped messages
    fresh_ids = [mid for mid in candidate_ids if not r.exists(_seen_key(account, mid))]

    if fresh_ids:
        # Fetch metadata for fresh messages
        messages = [m for mid in fresh_ids if (m := _extract_message_meta(service, mid))]

        if messages:
            # Score (may return unscored marker if circuit open)
            scored, _ = scorer.score(messages)

            # Set dedup keys BEFORE notifying (crash-safe ordering)
            for msg in messages:
                r.setex(_seen_key(account, msg["message_id"]), _SEEN_TTL, b"1")

            if scored:
                notify_fn(scored)

    # Update historyId last (after dedup keys are set)
    r.set(_history_id_key(account), new_id.encode())


def run_forever(
    *,
    build_service_fn: Callable,
    token_store,
    r: redis_lib.Redis,
    scorer,
    telegram_token: str,
    chat_id: str,
    poll_interval: int,
    poll_label: str,
    account: str = "",
) -> None:
    """Blocking loop. Run in a daemon thread."""
    if not chat_id:
        logger.warning("ALERT_TELEGRAM_CHAT_ID not set — proactive notifications disabled")

    def _notify(messages: list[dict]) -> None:
        if not chat_id:
            return
        notify_telegram(messages, token=telegram_token, chat_id=chat_id)

    _circuit_alert_sent = False
    while True:
        try:
            service = build_service_fn()
            was_open = scorer.is_circuit_open()
            poll_once(service=service, r=r, scorer=scorer,
                      notify_fn=_notify, poll_label=poll_label, account=account)
            now_open = scorer.is_circuit_open()
            # Send Telegram alert the first time the circuit opens
            if now_open and not was_open and not _circuit_alert_sent:
                _circuit_alert_sent = True
                if chat_id:
                    try:
                        _send_telegram(
                            telegram_token, chat_id,
                            "⚠️ Gmail importance scorer unavailable — notifications paused 30 min",
                        )
                    except Exception as alert_exc:
                        logger.warning("Failed to send circuit-breaker alert: %s", alert_exc)
            elif not now_open:
                _circuit_alert_sent = False  # reset when circuit closes
        except StopIteration:
            raise
        except Exception as exc:
            logger.error("Poller error: %s", exc)
        time.sleep(poll_interval)
