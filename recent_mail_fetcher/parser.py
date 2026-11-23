"""Normalize a ``CallToolResult.content`` list into ``Email[]`` (Task 6).

Each text block is parsed as JSON; non-text blocks (image, resource, ...) are
ignored. JSON may be a bare list of messages, a single message object, or a
wrapper object such as ``{"messages": [...]}`` (searched recursively). A text
block that is not valid JSON falls back to raw text: it is kept in
``ParseResult.raw_text``, a warning is logged, and ``exit_code`` becomes
``EXIT_PARSE_FAILURE`` (5). The parser itself never exits the process; the CLI
dispatcher (task #11) decides what to do with ``exit_code`` and ``raw_text``.

Emails are sorted newest-first (``None`` dates last, ties by ``id`` ascending)
via :func:`models.sort_emails`, then truncated to ``count``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .exitcodes import EXIT_PARSE_FAILURE, EXIT_SUCCESS
from .models import Email, parse_received_at, sort_emails

logger = logging.getLogger(__name__)

# Keys that commonly wrap the message list in a tool result.
_WRAPPER_KEYS = ("messages", "emails", "results", "items", "data", "result")
_MAX_DEPTH = 8


@dataclass
class ParseResult:
    """Outcome of :func:`parse_result`."""

    emails: list[Email] = field(default_factory=list)
    exit_code: int = EXIT_SUCCESS
    raw_text: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cursor: str | None = None


_CURSOR_KEYS = ("cursor", "nextCursor", "next_cursor", "nextPageToken", "next_page_token")


def is_complete(email: Email) -> bool:
    """True when subject, sender and ``received_at`` are all present."""
    return bool(email.subject) and bool(email.sender) and email.received_at is not None


def _find_cursor(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in _CURSOR_KEYS:
            value = payload.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
                return str(value)
        for key in _WRAPPER_KEYS:
            if key in payload:
                found = _find_cursor(payload[key])
                if found:
                    return found
    return None


def parse_result(content: Iterable[Any] | None, count: int | None = None) -> ParseResult:
    """Parse content blocks into sorted, truncated emails plus an exit code."""
    result = ParseResult()
    order: list[Email] = []

    for block in content or []:
        text = _block_text(block)
        if text is None:
            continue  # non-text block: ignored
        if not text.strip():
            continue
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            msg = "Tool result text block is not valid JSON; using raw text"
            logger.warning(msg)
            result.warnings.append(msg)
            result.raw_text.append(text)
            result.exit_code = EXIT_PARSE_FAILURE
            continue
        if result.cursor is None:
            result.cursor = _find_cursor(payload)
        for item in _find_messages(payload):
            email = _to_email(item)
            if email is None:
                logger.debug("Skipping message without an id")
                continue
            order.append(email)

    emails = sort_emails(order)
    if count is not None and count >= 0:
        emails = emails[:count]
    result.emails = emails
    return result


def detect_tool_error(content: Iterable[Any] | None) -> str | None:
    """Return an error message if a text block is a ``{"success": false}`` object.

    The message includes ``validFields`` when the tool reports them. Returns
    ``None`` for normal results.
    """
    for block in content or []:
        if isinstance(block, dict):
            btype, text = block.get("type"), block.get("text")
        else:
            btype, text = getattr(block, "type", None), getattr(block, "text", None)
        if btype not in (None, "text") or not isinstance(text, str) or not text.strip():
            continue
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("success") is False:
            msg = payload.get("message") or payload.get("error")
            if not isinstance(msg, str) or not msg:
                etype = payload.get("errorType")
                msg = f"tool reported failure ({etype})" if etype else "tool reported failure"
                missing = payload.get("missing")
                if missing:
                    msg += f"; missing: {', '.join(map(str, missing))}"
            valid = payload.get("validFields")
            if valid:
                msg += f" (validFields: {', '.join(map(str, valid))})"
            return msg
    return None


# --- helpers ---------------------------------------------------------------


def _block_text(block: Any) -> str | None:
    """Return the text of a text block, or ``None`` for any other block."""
    if isinstance(block, dict):
        btype, text = block.get("type"), block.get("text")
    else:
        btype, text = getattr(block, "type", None), getattr(block, "text", None)
    if btype != "text" or not isinstance(text, str):
        return None
    return text


def _looks_like_message(obj: Any) -> bool:
    return isinstance(obj, dict) and any(
        k in obj for k in ("id", "messageId", "message_id")
    )


def _find_messages(payload: Any, depth: int = 0) -> list[dict]:
    """Locate message dicts in arbitrarily nested JSON."""
    if depth > _MAX_DEPTH:
        return []
    if isinstance(payload, list):
        found: list[dict] = []
        for item in payload:
            if _looks_like_message(item):
                found.append(item)
            else:
                found.extend(_find_messages(item, depth + 1))
        return found
    if isinstance(payload, dict):
        if _looks_like_message(payload):
            return [payload]
        for key in _WRAPPER_KEYS:
            if key in payload:
                found = _find_messages(payload[key], depth + 1)
                if found:
                    return found
        found = []
        for value in payload.values():
            if isinstance(value, (list, dict)):
                found.extend(_find_messages(value, depth + 1))
        return found
    return []


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v) != ""]
    return [str(value)]


def _parse_date(value: Any) -> datetime | None:
    # Epoch milliseconds (Gmail ``internalDate``) as int or digit string.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    return parse_received_at(value)


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    return None


def _to_email(item: dict) -> Email | None:
    raw_id = _first(item, "id", "messageId", "message_id")
    if raw_id is None or str(raw_id) == "":
        return None
    labels = _str_list(_first(item, "label_ids", "labelIds", "labels"))
    unread = _to_bool(_first(item, "is_unread", "isUnread", "unread"))
    if unread is None:
        unread = "UNREAD" in labels
    recipients = _str_list(_first(item, "recipients", "to"))
    recipients += _str_list(item.get("cc")) if "recipients" not in item else []
    return Email(
        id=str(raw_id),
        thread_id=_str_or_none(_first(item, "thread_id", "threadId")),
        sender=_str_or_none(_first(item, "sender", "from")),
        recipients=recipients,
        subject=_str_or_none(item.get("subject")),
        received_at=_parse_date(
            _first(item, "received_at", "receivedAt", "date", "internalDate")
        ),
        snippet=_str_or_none(item.get("snippet")),
        label_ids=labels,
        is_unread=unread,
        has_attachments=_to_bool(_first(item, "has_attachments", "hasAttachments")),
    )
