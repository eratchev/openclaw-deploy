import os
import tempfile
import threading
import time
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis as redis_lib
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import gmail_client
import poller as poller_mod
import policies
import scorer as scorer_mod
from auth import TokenStore
from audit import AuditLog
from models import (
    ListInput, GetInput, SearchInput, ReplyInput, SendInput, MarkReadInput,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Startup ───────────────────────────────────────────────────────────────────

token_store = TokenStore.from_env()
CONFIGURED = token_store is not None

_audit_log_path = os.getenv("GMAIL_AUDIT_LOG_PATH", "/data/gmail-audit.log")
_audit_max_bytes = int(os.getenv("GMAIL_AUDIT_MAX_MB", "50")) * 1024 * 1024

try:
    audit = AuditLog(log_path=_audit_log_path, max_bytes=_audit_max_bytes)
except OSError:
    # Fall back to a temp file when the configured path is not writable (e.g. in tests).
    _fallback_path = Path(tempfile.mkdtemp()) / "gmail-audit.log"
    logger.warning(
        "[mail-proxy] Audit log path %r not writable — using fallback %s",
        _audit_log_path, _fallback_path,
    )
    audit = AuditLog(log_path=_fallback_path, max_bytes=_audit_max_bytes)

mcp = FastMCP("mail-proxy", host="0.0.0.0", port=8091)
# Alias for testability: TestClient(mcp.get_app()) → mcp.sse_app()
mcp.get_app = mcp.sse_app

_NOT_CONFIGURED_RESPONSE = {
    "error": "not_configured",
    "message": "Run 'make setup-gmail CLIENT_SECRET=...' to configure Gmail access",
}


def get_redis() -> redis_lib.Redis:
    return redis_lib.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))


def _today() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


# ── Operation handlers ────────────────────────────────────────────────────────

def handle_list(args: dict) -> Any:
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    inp = ListInput(**args)
    service = gmail_client.build_service(token_store)
    messages = gmail_client.list_messages(service, label=inp.label, limit=inp.limit)
    # Update seen-domains cache (fail-open: read still works if Redis down)
    try:
        policies.update_seen_domains(get_redis(), messages)
    except Exception as exc:
        logger.warning("update_seen_domains failed: %s", exc)
    return messages


def handle_get(args: dict) -> Any:
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    inp = GetInput(**args)
    service = gmail_client.build_service(token_store)
    thread = gmail_client.get_thread(service, inp.thread_id)
    # Update seen-domains from thread participants
    try:
        flat = [{"from_addr": m["from_addr"]} for m in thread.get("messages", [])]
        policies.update_seen_domains(get_redis(), flat)
    except Exception as exc:
        logger.warning("update_seen_domains failed: %s", exc)
    return thread


def handle_search(args: dict) -> Any:
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    inp = SearchInput(**args)
    service = gmail_client.build_service(token_store)
    return gmail_client.search_messages(service, query=inp.query, limit=inp.limit)


def handle_reply(args: dict) -> Any:
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    inp = ReplyInput(**args)
    request_id = str(uuid.uuid4())
    start = time.monotonic()
    try:
        r = get_redis()
        date_str = _today()
        ok, reason = policies.check_rate_limit(r, date_str)
        if not ok:
            audit.write(request_id=request_id, operation="reply",
                        message_id=inp.message_id, from_addr=None,
                        status="denied", reason=reason)
            return {"request_id": request_id, "status": "denied", "reason": reason}
        service = gmail_client.build_service(token_store)
        new_id = gmail_client.reply_to_thread(
            service, thread_id=inp.thread_id, message_id=inp.message_id, body=inp.body
        )
        policies.record_send(r, date_str)
        duration_ms = int((time.monotonic() - start) * 1000)
        audit.write(request_id=request_id, operation="reply",
                    message_id=new_id, from_addr=None, status="sent",
                    duration_ms=duration_ms)
        return {"request_id": request_id, "status": "sent", "message_id": new_id}
    except redis_lib.RedisError:
        return {"request_id": request_id, "status": "denied",
                "reason": "rate_limit_unavailable: Redis error — send blocked"}


def handle_send(args: dict) -> Any:
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    inp = SendInput(**args)
    request_id = str(uuid.uuid4())
    start = time.monotonic()

    if not inp.confirmed:
        audit.write(request_id=request_id, operation="send",
                    message_id=None, from_addr=None, status="needs_confirmation",
                    extra={"to": inp.to})
        return {
            "request_id": request_id,
            "status": "needs_confirmation",
            "message": f"Ready to send to {inp.to!r}. Call again with confirmed=true to execute.",
        }

    try:
        r = get_redis()
        # Novel-domain check
        ok_domain, domain_reason = policies.check_novel_domain(r, inp.to)
        if not ok_domain:
            audit.write(request_id=request_id, operation="send",
                        message_id=None, from_addr=None, status="denied",
                        reason=domain_reason, extra={"to": inp.to})
            return {"request_id": request_id, "status": "denied", "reason": domain_reason}
        # Rate limit
        date_str = _today()
        ok_rate, rate_reason = policies.check_rate_limit(r, date_str)
        if not ok_rate:
            audit.write(request_id=request_id, operation="send",
                        message_id=None, from_addr=None, status="denied",
                        reason=rate_reason, extra={"to": inp.to})
            return {"request_id": request_id, "status": "denied", "reason": rate_reason}

        service = gmail_client.build_service(token_store)
        new_id = gmail_client.send_email(service, to=inp.to,
                                          subject=inp.subject, body=inp.body)
        policies.record_send(r, date_str)
        duration_ms = int((time.monotonic() - start) * 1000)
        audit.write(request_id=request_id, operation="send",
                    message_id=new_id, from_addr=None, status="sent",
                    duration_ms=duration_ms, extra={"to": inp.to})
        return {"request_id": request_id, "status": "sent", "message_id": new_id}
    except redis_lib.RedisError:
        return {"request_id": request_id, "status": "denied",
                "reason": "rate_limit_unavailable: Redis error — send blocked"}


def handle_mark_read(args: dict) -> Any:
    if not CONFIGURED:
        return _NOT_CONFIGURED_RESPONSE
    inp = MarkReadInput(**args)
    service = gmail_client.build_service(token_store)
    gmail_client.mark_read(service, inp.message_id)
    return {"status": "ok", "message_id": inp.message_id}


def get_health() -> dict:
    health: dict[str, Any] = {"configured": CONFIGURED}
    try:
        get_redis().ping()
        health["redis"] = "ok"
    except Exception as exc:
        health["redis"] = f"error: {exc}"
    if CONFIGURED:
        try:
            token_store.load()
            health["token"] = "ok"
        except Exception as exc:
            health["token"] = f"error: {exc}"
        if os.getenv("GMAIL_HEALTH_CHECK_GOOGLE", "false").lower() == "true":
            try:
                gmail_client.build_service(token_store)
                health["google_api"] = "ok"
            except Exception as exc:
                health["google_api"] = f"error: {exc}"
        else:
            health["google_api"] = "skipped"
    return health


# ── REST endpoints ────────────────────────────────────────────────────────────

_TOOL_HANDLERS = {
    "list": handle_list,
    "get": handle_get,
    "search": handle_search,
    "reply": handle_reply,
    "send": handle_send,
    "mark_read": handle_mark_read,
}


@mcp.custom_route("/health", methods=["GET"])
async def http_health(request: Request) -> JSONResponse:
    return JSONResponse(get_health())


@mcp.custom_route("/call", methods=["POST"])
async def http_call(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    tool = body.get("tool")
    args = body.get("args", {})
    handler = _TOOL_HANDLERS.get(tool)
    if handler is None:
        return JSONResponse(
            {"error": f"unknown tool: {tool}", "available": list(_TOOL_HANDLERS)},
            status_code=404,
        )
    try:
        result = handler(args)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Background poller ─────────────────────────────────────────────────────────

def _start_poller() -> None:
    if not CONFIGURED:
        logger.info("[mail-proxy] No Gmail token configured — poller disabled. "
                    "Run make setup-gmail to configure.")
        return

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    model = os.getenv("GMAIL_SCORER_MODEL", "claude-haiku-4-5-20251001")
    threshold = int(os.getenv("GMAIL_IMPORTANCE_THRESHOLD", "7"))
    interval = int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "180"))
    poll_label = os.getenv("GMAIL_POLL_LABEL", "INBOX")
    telegram_token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("ALERT_TELEGRAM_CHAT_ID", "")

    importance_scorer = scorer_mod.ImportanceScorer(
        api_key=api_key, model=model, threshold=threshold
    )
    r = get_redis()

    t = threading.Thread(
        target=poller_mod.run_forever,
        kwargs={
            "build_service_fn": lambda: gmail_client.build_service(token_store),
            "token_store": token_store,
            "r": r,
            "scorer": importance_scorer,
            "telegram_token": telegram_token,
            "chat_id": chat_id,
            "poll_interval": interval,
            "poll_label": poll_label,
        },
        daemon=True,
    )
    t.start()
    logger.info("[mail-proxy] Poller started (interval=%ds, label=%s)", interval, poll_label)


if os.getenv("GMAIL_DISABLE_POLLER", "false").lower() != "true":
    _start_poller()


if __name__ == "__main__":
    mcp.run(transport="sse")
