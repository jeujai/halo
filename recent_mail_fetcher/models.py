"""Email data model and JSON output envelope (Task 3).

This module is the pure data layer of recent-mail-fetcher: it owns the
normalized :class:`Email` representation, the date-normalization helper used to
populate ``received_at``, the JSON envelope builder, and the sort key that
defines "newest-first". It performs no network or IO and has no side effects,
so the parser (task #6) and renderer (task #5) can compose it freely.

Date handling is centralized here because the data model is the single place
where ``received_at`` is normalized: tool results arrive as free-form strings
(ISO-8601 or RFC-2822) and are collapsed to a timezone-aware UTC ``datetime``
(or ``None`` when unparseable). Callers that hit an unparseable value decide
what to do — the parser emits a warning and exit 5; this module just returns
``None`` so it stays a pure, side-effect-free data layer.

Exit-code awareness (0/2/3/4/5) is centralized in task #4; nothing here knows
about exit codes by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


@dataclass
class Email:
    """A normalized email message.

    Fields are best-effort from the tool result: anything the service omits
    becomes ``None`` (or an empty list for the mutable list fields). ``is_unread``
    defaults to ``False`` so it is always a concrete bool rather than ``None``;
    ``has_attachments`` stays ``None`` when the service does not report it.

    The mutable list fields (``recipients``, ``label_ids``) use
    ``field(default_factory=list)`` so instances never share list state.
    """

    id: str
    thread_id: str | None = None
    sender: str | None = None
    recipients: list[str] = field(default_factory=list)
    subject: str | None = None
    received_at: datetime | None = None
    snippet: str | None = None
    label_ids: list[str] = field(default_factory=list)
    is_unread: bool = False
    has_attachments: bool | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable dict for the output envelope.

        ``received_at`` is rendered as an ISO-8601 UTC string with a trailing
        ``Z`` (or ``None``). Mutable list fields are copied so the serialized
        dict cannot be used to mutate the source :class:`Email`.
        """
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "sender": self.sender,
            "recipients": list(self.recipients),
            "subject": self.subject,
            "received_at": _format_datetime(self.received_at),
            "snippet": self.snippet,
            "label_ids": list(self.label_ids),
            "is_unread": self.is_unread,
            "has_attachments": self.has_attachments,
        }


def _to_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC.

    A naive datetime is assumed to already be UTC (the service contract sends
    UTC), so no local-time conversion is applied — only the tzinfo is filled
    in. This keeps sorting and formatting deterministic regardless of host
    locale.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_datetime(dt: datetime | None) -> str | None:
    """Render a datetime as an ISO-8601 UTC string with a trailing ``Z``.

    Returns ``None`` for a missing date so the envelope shows a JSON ``null``.
    After :func:`_to_utc` the offset is always ``+00:00``, which is rewritten to
    ``Z`` to match the spec's envelope example (e.g. ``2025-01-20T13:40:00Z``).
    """
    if dt is None:
        return None
    return _to_utc(dt).isoformat().replace("+00:00", "Z")


def parse_received_at(value: object) -> datetime | None:
    """Parse ``received_at`` from ISO-8601 or RFC-2822 into a UTC ``datetime``.

    ISO-8601 is tried first (the common Gmail API shape); RFC-2822 is the
    fallback for headers-style dates. The result is always timezone-aware UTC.
    Any unparseable, empty, or wrongly-typed input yields ``None`` rather than
    raising, keeping this a side-effect-free data layer: the caller (parser,
    task #6) decides whether an unparseable date is worth a warning/exit 5.

    A trailing ``Z`` is rewritten to ``+00:00`` before ``fromisoformat`` so the
    parser works on Python 3.10, whose ``fromisoformat`` does not accept ``Z``.
    A passthrough :class:`datetime` is normalized to UTC; ``None`` returns
    ``None``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_utc(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    # Rewrite only the trailing Z so offsets like "+00:00" and naive strings
    # both reach fromisoformat intact. Python 3.10's fromisoformat rejects "Z".
    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        parsed = None
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    return _to_utc(parsed)


def build_envelope(
    service: str,
    tool: str,
    count: int,
    emails: list[Email],
    fetched_at: datetime | None = None,
) -> dict[str, object]:
    """Build the JSON output envelope as a plain dict.

    Returning a dict (not a JSON string) keeps this a pure data transform: the
    renderer (task #5) is responsible for the final ``json.dumps``. ``count`` is
    the requested count per the spec; ``emails`` may be shorter than ``count``
    in the fewer-than-count case, and the requested count is stored verbatim
    rather than recomputed from the list length. ``fetched_at`` defaults to now
    (UTC) and is rendered with a trailing ``Z``.
    """
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc)
    return {
        "service": service,
        "fetched_at": _format_datetime(fetched_at),
        "tool": tool,
        "count": count,
        "emails": [email.to_dict() for email in emails],
    }


def _sort_key(email: Email) -> tuple[bool, float, str]:
    """Sort key: dated emails first (newest first), then id ascending.

    ``None`` ``received_at`` sorts last — treated as the oldest possible value
    via the leading ``True`` (``False`` < ``True``). Epoch seconds are negated
    so larger (newer) timestamps sort first; the ``id`` tiebreak is ascending
    for a stable, deterministic ordering. ``received_at`` is normalized to UTC
    before taking the timestamp so naive datetimes sort consistently.
    """
    if email.received_at is None:
        return (True, 0.0, email.id)
    return (False, -_to_utc(email.received_at).timestamp(), email.id)


def sort_emails(emails: list[Email]) -> list[Email]:
    """Return emails sorted newest-first, ties broken by id ascending.

    A ``None`` ``received_at`` is treated as the oldest value and sorts to the
    end. A new list is returned; the input list is not mutated. Both the parser
    (task #6) and the renderer (task #5) rely on this for deterministic output.
    """
    return sorted(emails, key=_sort_key)
