"""Table and JSON renderers (Task 5).

Both renderers sort newest-first (``received_at`` descending, ``id`` ascending
tiebreak) via :func:`models.sort_emails` and write to stdout only, so JSON
output stays clean for piping. Logs belong on stderr and are not handled here.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import TextIO

from .models import Email, _format_datetime, build_envelope, sort_emails

DEFAULT_SERVICE = "system.ai.gmail"
NO_MESSAGES = "No messages."

_HEADERS = ("SUBJECT", "SENDER", "RECEIVED_AT", "ID")
_MAX_COL = 60
_PLACEHOLDER = "-"


def _clean(value: str | None) -> str:
    """Collapse whitespace/newlines to single spaces; None or empty -> '-'."""
    if value is None:
        return _PLACEHOLDER
    text = " ".join(str(value).split())
    return text or _PLACEHOLDER


def _truncate(text: str, width: int = _MAX_COL) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _row(email: Email) -> tuple[str, str, str, str]:
    return (
        _truncate(_clean(email.subject)),
        _truncate(_clean(email.sender)),
        _format_datetime(email.received_at) or _PLACEHOLDER,
        _clean(email.id),
    )


def format_table(emails: list[Email]) -> str:
    """Return the table as a string (no trailing newline)."""
    if not emails:
        return NO_MESSAGES
    rows = [_row(e) for e in sort_emails(emails)]
    widths = [
        max(len(_HEADERS[i]), *(len(r[i]) for r in rows)) for i in range(len(_HEADERS))
    ]

    def fmt(cells: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()

    lines = [fmt(_HEADERS), fmt(tuple("-" * w for w in widths))]
    lines.extend(fmt(r) for r in rows)
    return "\n".join(lines)


def format_json(
    emails: list[Email],
    *,
    tool: str,
    count: int | None = None,
    service: str = DEFAULT_SERVICE,
    fetched_at: datetime | None = None,
) -> str:
    """Return the JSON envelope (sorted newest-first) as a string.

    ``count`` is the requested count; it defaults to ``len(emails)``.
    """
    ordered = sort_emails(emails)
    envelope = build_envelope(
        service=service,
        tool=tool,
        count=len(ordered) if count is None else count,
        emails=ordered,
        fetched_at=fetched_at,
    )
    return json.dumps(envelope, indent=2, ensure_ascii=False)


def render_table(emails: list[Email], out: TextIO | None = None) -> None:
    """Write the table (or a "no messages" notice) to stdout."""
    stream = out if out is not None else sys.stdout
    stream.write(format_table(emails) + "\n")


def render_json(
    emails: list[Email],
    *,
    tool: str,
    count: int | None = None,
    service: str = DEFAULT_SERVICE,
    fetched_at: datetime | None = None,
    out: TextIO | None = None,
) -> None:
    """Write the JSON envelope to stdout."""
    stream = out if out is not None else sys.stdout
    stream.write(
        format_json(
            emails, tool=tool, count=count, service=service, fetched_at=fetched_at
        )
        + "\n"
    )


def render(
    emails: list[Email],
    *,
    as_json: bool = False,
    tool: str = "",
    count: int | None = None,
    service: str = DEFAULT_SERVICE,
    out: TextIO | None = None,
) -> None:
    """Dispatch to the JSON or table renderer."""
    if as_json:
        render_json(emails, tool=tool, count=count, service=service, out=out)
    else:
        render_table(emails, out=out)
