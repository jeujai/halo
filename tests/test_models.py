"""Unit tests for the Email data model and JSON envelope (Task 3).

Validates: Requirements "Parsing results" (normalized Email fields and the
received_at normalization), "Rendering results"/--json (the envelope shape:
service, fetched_at, tool, count, emails), and the sort key (newest-first by
received_at descending with a stable id ascending tiebreak, None last).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from recent_mail_fetcher.models import (
    Email,
    build_envelope,
    parse_received_at,
    sort_emails,
)

UTC = timezone.utc


def _dt(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    """Shorthand for a timezone-aware UTC datetime."""
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


# --- Email serialization ---------------------------------------------------

def test_populated_email_serializes_to_expected_dict():
    email = Email(
        id="msg-1",
        thread_id="thread-1",
        sender="alice@example.com",
        recipients=["bob@example.com", "carol@example.com"],
        subject="Hello",
        received_at=_dt(2025, 1, 20, 13, 40),
        snippet="A short preview of the message body.",
        label_ids=["INBOX", "UNREAD"],
        is_unread=True,
        has_attachments=False,
    )
    assert email.to_dict() == {
        "id": "msg-1",
        "thread_id": "thread-1",
        "sender": "alice@example.com",
        "recipients": ["bob@example.com", "carol@example.com"],
        "subject": "Hello",
        "received_at": "2025-01-20T13:40:00Z",
        "snippet": "A short preview of the message body.",
        "label_ids": ["INBOX", "UNREAD"],
        "is_unread": True,
        "has_attachments": False,
    }


def test_email_defaults_for_missing_values():
    email = Email(id="msg-1")
    assert email.thread_id is None
    assert email.sender is None
    assert email.recipients == []
    assert email.subject is None
    assert email.received_at is None
    assert email.snippet is None
    assert email.label_ids == []
    assert email.is_unread is False
    assert email.has_attachments is None
    assert email.to_dict()["received_at"] is None
    assert email.to_dict()["has_attachments"] is None


def test_email_to_dict_copies_mutable_lists():
    email = Email(id="msg-1", recipients=["a@x"], label_ids=["INBOX"])
    out = email.to_dict()
    out["recipients"].append("b@x")
    out["label_ids"].append("UNREAD")
    # Mutating the serialized copy must not touch the source Email.
    assert email.recipients == ["a@x"]
    assert email.label_ids == ["INBOX"]


# --- build_envelope --------------------------------------------------------

def test_build_envelope_shape_with_explicit_fetched_at():
    email = Email(
        id="msg-1",
        sender="alice@example.com",
        recipients=["bob@example.com"],
        subject="Hello",
        received_at=_dt(2025, 1, 20, 13, 40),
        snippet="preview",
        label_ids=["INBOX", "UNREAD"],
        is_unread=True,
    )
    envelope = build_envelope(
        service="system.ai.gmail",
        tool="list_messages",
        count=10,
        emails=[email],
        fetched_at=_dt(2025, 1, 20, 13, 40),
    )
    assert set(envelope.keys()) == {"service", "fetched_at", "tool", "count", "emails"}
    assert envelope["service"] == "system.ai.gmail"
    assert envelope["fetched_at"] == "2025-01-20T13:40:00Z"
    assert envelope["tool"] == "list_messages"
    assert envelope["count"] == 10
    assert envelope["emails"] == [email.to_dict()]


def test_build_envelope_fetched_at_defaults_to_now_utc():
    envelope = build_envelope("system.ai.gmail", "list_messages", 10, [])
    assert isinstance(envelope["fetched_at"], str)
    assert envelope["fetched_at"].endswith("Z")
    parsed = parse_received_at(envelope["fetched_at"])
    assert parsed is not None and parsed.tzinfo == UTC


def test_build_envelope_count_is_requested_not_actual_length():
    # Fewer-than-count case: count stays 5 even with 1 email.
    envelope = build_envelope("system.ai.gmail", "list_messages", 5, [Email(id="m1")])
    assert envelope["count"] == 5
    assert len(envelope["emails"]) == 1


# --- JSON round-trip -------------------------------------------------------

def test_envelope_round_trips_through_json():
    email = Email(
        id="msg-1",
        thread_id="thread-1",
        sender="alice@example.com",
        recipients=["bob@example.com"],
        subject="Hello",
        received_at=_dt(2025, 1, 20, 13, 40),
        snippet="preview",
        label_ids=["INBOX", "UNREAD"],
        is_unread=True,
        has_attachments=None,
    )
    envelope = build_envelope(
        service="system.ai.gmail",
        tool="list_messages",
        count=10,
        emails=[email],
        fetched_at=_dt(2025, 1, 20, 13, 40),
    )
    encoded = json.dumps(envelope)
    assert json.loads(encoded) == envelope


# --- parse_received_at -----------------------------------------------------

def test_parse_received_at_iso_with_trailing_z():
    assert parse_received_at("2025-01-20T13:40:00Z") == _dt(2025, 1, 20, 13, 40)


def test_parse_received_at_iso_with_offset_normalized_to_utc():
    assert parse_received_at("2025-01-20T15:40:00+02:00") == _dt(2025, 1, 20, 13, 40)


def test_parse_received_at_iso_naive_assumed_utc():
    assert parse_received_at("2025-01-20T13:40:00") == _dt(2025, 1, 20, 13, 40)


def test_parse_received_at_rfc2822_with_utc():
    assert parse_received_at("Mon, 20 Jan 2025 13:40:00 +0000") == _dt(2025, 1, 20, 13, 40)


def test_parse_received_at_rfc2822_with_offset_normalized_to_utc():
    assert parse_received_at("Mon, 20 Jan 2025 15:40:00 +0200") == _dt(2025, 1, 20, 13, 40)


def test_parse_received_at_rfc2822_naive_assumed_utc():
    assert parse_received_at("Mon, 20 Jan 2025 13:40:00") == _dt(2025, 1, 20, 13, 40)


def test_parse_received_at_datetime_passthrough_normalizes_to_utc():
    aware = datetime(2025, 1, 20, 15, 40, 0, tzinfo=timezone(timedelta(hours=2)))
    assert parse_received_at(aware) == _dt(2025, 1, 20, 13, 40)
    naive = datetime(2025, 1, 20, 13, 40, 0)
    assert parse_received_at(naive) == _dt(2025, 1, 20, 13, 40)


def test_parse_received_at_returns_none_for_empty():
    assert parse_received_at("") is None
    assert parse_received_at("   ") is None


def test_parse_received_at_returns_none_for_garbage():
    assert parse_received_at("not a date at all") is None


def test_parse_received_at_returns_none_for_wrong_type():
    assert parse_received_at(None) is None
    assert parse_received_at(12345) is None
    assert parse_received_at(["2025-01-20"]) is None


# --- sort_emails -----------------------------------------------------------

def test_sort_emails_newest_first_descending():
    e1 = Email(id="a", received_at=_dt(2025, 1, 1))
    e2 = Email(id="b", received_at=_dt(2025, 1, 3))
    e3 = Email(id="c", received_at=_dt(2025, 1, 2))
    assert [e.id for e in sort_emails([e1, e2, e3])] == ["b", "c", "a"]


def test_sort_emails_tiebreaks_by_id_ascending():
    e1 = Email(id="msg-3", received_at=_dt(2025, 1, 1))
    e2 = Email(id="msg-1", received_at=_dt(2025, 1, 1))
    e3 = Email(id="msg-2", received_at=_dt(2025, 1, 1))
    assert [e.id for e in sort_emails([e3, e1, e2])] == ["msg-1", "msg-2", "msg-3"]


def test_sort_emails_none_received_at_sorts_last():
    e1 = Email(id="a", received_at=_dt(2025, 1, 1))
    e2 = Email(id="b")  # None
    e3 = Email(id="c", received_at=_dt(2024, 12, 31))
    assert [e.id for e in sort_emails([e2, e1, e3])] == ["a", "c", "b"]


def test_sort_emails_all_none_tiebreaks_by_id_ascending():
    emails = [Email(id="c"), Email(id="a"), Email(id="b")]
    assert [e.id for e in sort_emails(emails)] == ["a", "b", "c"]


def test_sort_emails_does_not_mutate_input():
    e1 = Email(id="a", received_at=_dt(2025, 1, 1))
    e2 = Email(id="b", received_at=_dt(2025, 1, 2))
    original = [e1, e2]
    sort_emails(original)
    assert original == [e1, e2]
