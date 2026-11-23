"""Hydrate id-only search results via the read-message tool (Task 21).

``gmail_search`` may return only message ids. For every entry missing subject,
sender or ``received_at`` this module calls the read-message tool once, with at
most ``count`` calls and at most ``MAX_CONCURRENCY`` in flight, and merges the
missing fields into the stub. A failed read keeps the stub and logs a warning.
Entries that are already complete trigger no calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from typing import Any

from recent_mail_fetcher.args import _properties, find_property, tool_input_schema
from recent_mail_fetcher.exitcodes import EXIT_AUTH, FailError
from recent_mail_fetcher.invoker import call_tool
from recent_mail_fetcher.models import Email
from recent_mail_fetcher.parser import _block_text, is_complete, parse_result

logger = logging.getLogger(__name__)

MAX_CONCURRENCY = 5
ID_ALIASES: tuple[str, ...] = ("message_id", "messageid", "msg_id", "id")
_MERGE_FIELDS = ("thread_id", "sender", "subject", "received_at", "snippet", "has_attachments")


def id_param_name(tool: Any) -> str:
    """Name of the id parameter from the tool's schema (fallback ``message_id``)."""
    schema = tool_input_schema(tool)
    found = find_property(schema, ID_ALIASES)
    if found:
        return found
    required = schema.get("required") if isinstance(schema, dict) else None
    if isinstance(required, list) and len(required) == 1 and isinstance(required[0], str):
        return required[0]
    props = list(_properties(schema))
    if len(props) == 1:
        return props[0]
    return "message_id"


def _parse_read(content: Any, msg_id: str) -> Email | None:
    """Parse a read-message result into an Email (id injected when absent)."""
    parsed = parse_result(content)
    for email in parsed.emails:
        if email.id == msg_id:
            return email
    if parsed.emails:
        return parsed.emails[0]
    for block in content or []:
        text = _block_text(block)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            inner = payload.get("message") if isinstance(payload.get("message"), dict) else payload
            fallback = parse_result(
                [{"type": "text", "text": json.dumps({**inner, "id": msg_id})}]
            )
            if fallback.emails:
                return fallback.emails[0]
    return None


def _merge(stub: Email, full: Email) -> Email:
    updates = {
        name: getattr(full, name)
        for name in _MERGE_FIELDS
        if getattr(stub, name) is None and getattr(full, name) is not None
    }
    if not stub.recipients and full.recipients:
        updates["recipients"] = list(full.recipients)
    if not stub.label_ids and full.label_ids:
        updates["label_ids"] = list(full.label_ids)
        updates["is_unread"] = full.is_unread
    return replace(stub, **updates)


async def hydrate_emails(
    session: Any,
    emails: list[Email],
    read_tool: Any | None,
    count: int,
) -> list[Email]:
    """Return ``emails`` with incomplete entries filled in via ``read_tool``."""
    pending = [i for i, e in enumerate(emails) if not is_complete(e)]
    if not pending:
        return emails
    if read_tool is None:
        logger.warning(
            "Read-message tool not found; printing %d id-only entries without hydration",
            len(pending),
        )
        return emails
    pending = pending[: max(count, 0)]
    if len(pending) < sum(1 for e in emails if not is_complete(e)):
        logger.warning("Hydrating only the first %d entries (--count limit)", len(pending))

    param = id_param_name(read_tool)
    schema = tool_input_schema(read_tool)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    out = list(emails)

    async def one(index: int) -> None:
        stub = emails[index]
        async with sem:
            try:
                result = await call_tool(
                    session, read_tool.name, {param: stub.id}, input_schema=schema
                )
            except FailError as err:
                if err.code == EXIT_AUTH:
                    raise
                logger.warning("Could not read message %s; keeping id-only entry", stub.id)
                return
            except Exception as exc:  # noqa: BLE001 - one bad read must not abort the run
                logger.warning(
                    "Could not read message %s (%s: %s); keeping id-only entry",
                    stub.id, type(exc).__name__, exc,
                )
                return
        full = _parse_read(getattr(result, "content", None), stub.id)
        if full is None:
            logger.warning("Unparseable read result for message %s; keeping id-only entry", stub.id)
            return
        out[index] = _merge(stub, full)

    await asyncio.gather(*(one(i) for i in pending))
    return out
