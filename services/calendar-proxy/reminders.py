"""Background polling loop for proactive calendar reminders."""
import json
import logging
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable

import redis as redis_lib

logger = logging.getLogger(__name__)

_REMINDED_PREFIX = "gcal:reminded:"


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


def notify_telegram(events: list[dict], token: str, chat_id: str, lead_minutes: int) -> None:
    for event in events:
        summary = event.get("summary", "(no title)")
        start_raw = event.get("start", {}).get("dateTime", "")
        try:
            dt = datetime.fromisoformat(start_raw)
            # Include ISO date (YYYY-MM-DD) for machine-readable parsing,
            # plus a human-friendly time.  %-d / %-I are Linux (glibc) only
            # — fine in Docker container.
            iso_date = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%-I:%M %p")
            start_str = f"{iso_date} at {time_str}"
        except Exception:
            start_str = start_raw
        text = f"📅 <b>{summary}</b>\nStarts {start_str}"
        try:
            _send_telegram(token, chat_id, text)
        except Exception as exc:
            logger.warning("Telegram reminder failed for %s: %s", event.get("id"), exc)


def remind_once(
    service,
    r: redis_lib.Redis,
    lead_minutes: int,
    notify_fn: Callable[[list[dict]], None],
    calendar_ids: list[str],
) -> None:
    """Single reminder poll: find events starting within lead_minutes and notify."""
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(minutes=lead_minutes)).isoformat()

    to_notify = []
    for cal_id in calendar_ids:
        try:
            result = service.events().list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
        except Exception as exc:
            logger.warning("Failed to list events for %s: %s", cal_id, exc)
            continue

        for event in result.get("items", []):
            # Skip all-day events (they have 'date' not 'dateTime')
            if "dateTime" not in event.get("start", {}):
                continue
            event_id = event["id"]
            if not r.exists(f"{_REMINDED_PREFIX}{event_id}"):
                to_notify.append(event)

    if to_notify:
        # Set dedup keys BEFORE notifying (crash-safe)
        ttl = lead_minutes * 60 * 3
        for event in to_notify:
            r.setex(f"{_REMINDED_PREFIX}{event['id']}", ttl, b"1")
        notify_fn(to_notify)


def run_forever(
    *,
    build_service_fn: Callable,
    r: redis_lib.Redis,
    telegram_token: str,
    chat_id: str,
    lead_minutes: int,
    poll_interval: int,
    calendar_ids: list[str],
) -> None:
    """Blocking loop. Run in a daemon thread."""
    if not telegram_token or not chat_id:
        logger.warning("TELEGRAM_TOKEN or ALERT_TELEGRAM_CHAT_ID not set — calendar reminders disabled")
        return

    def _notify(events: list[dict]) -> None:
        notify_telegram(events, token=telegram_token, chat_id=chat_id, lead_minutes=lead_minutes)

    while True:
        try:
            service = build_service_fn()
            remind_once(
                service=service,
                r=r,
                lead_minutes=lead_minutes,
                notify_fn=_notify,
                calendar_ids=calendar_ids,
            )
        except StopIteration:
            raise
        except Exception as exc:
            logger.error("Reminder poller error: %s", exc)
        time.sleep(poll_interval)
