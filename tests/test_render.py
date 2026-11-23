"""Unit tests for the table and JSON renderers (Task 5).

Validates: Requirements "Rendering results" — newest-first ordering, JSON
envelope shape, and stdout-only output.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone

from recent_mail_fetcher.models import Email
from recent_mail_fetcher.render import (
    NO_MESSAGES,
    format_json,
    format_table,
    render,
    render_json,
    render_table,
)

UTC = timezone.utc


def _dt(d: int, h: int = 0) -> datetime:
    return datetime(2025, 1, d, h, 0, tzinfo=UTC)


def _emails() -> list[Email]:
    return [
        Email(id="old", subject="Oldest", sender="a@x", received_at=_dt(1)),
        Email(id="new", subject="Newest", sender="b@x", received_at=_dt(3)),
        Email(id="mid", subject="Middle", sender="c@x", received_at=_dt(2)),
    ]


def test_table_has_headers_and_sorted_newest_first():
    lines = format_table(_emails()).splitlines()
    assert lines[0].split() == ["SUBJECT", "SENDER", "RECEIVED_AT", "ID"]
    body = lines[2:]
    assert [row.split()[-1] for row in body] == ["new", "mid", "old"]
    assert "2025-01-03T00:00:00Z" in body[0]


def test_table_empty_prints_no_messages():
    assert format_table([]) == NO_MESSAGES


def test_table_placeholders_and_whitespace_collapsed():
    out = format_table([Email(id="m1", subject="a\nb   c")])
    row = out.splitlines()[2]
    assert "a b c" in row
    assert row.count("-") >= 2  # sender and received_at placeholders


def test_table_truncates_long_values():
    out = format_table([Email(id="m1", subject="x" * 200)])
    assert "…" in out
    assert "x" * 100 not in out


def test_json_envelope_shape_and_order():
    fetched = _dt(5)
    data = json.loads(
        format_json(_emails(), tool="list_messages", count=10, fetched_at=fetched)
    )
    assert set(data) == {"service", "fetched_at", "tool", "count", "emails"}
    assert data["service"] == "system.ai.gmail"
    assert data["tool"] == "list_messages"
    assert data["count"] == 10
    assert data["fetched_at"] == "2025-01-05T00:00:00Z"
    assert [e["id"] for e in data["emails"]] == ["new", "mid", "old"]


def test_json_count_defaults_to_length():
    data = json.loads(format_json(_emails(), tool="t"))
    assert data["count"] == 3


def test_json_empty_emails():
    data = json.loads(format_json([], tool="t", count=10))
    assert data["emails"] == []
    assert data["count"] == 10


def test_render_table_writes_only_to_given_stream(capsys):
    buf = io.StringIO()
    render_table(_emails(), out=buf)
    assert buf.getvalue().endswith("\n")
    assert "Newest" in buf.getvalue()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_render_defaults_to_stdout_only(capsys):
    render(_emails(), as_json=False)
    captured = capsys.readouterr()
    assert "Newest" in captured.out
    assert captured.err == ""


def test_render_json_to_stdout_is_valid_json_and_stderr_empty(capsys):
    render(_emails(), as_json=True, tool="list_messages", count=10)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert [e["id"] for e in data["emails"]] == ["new", "mid", "old"]
    assert captured.err == ""


def test_render_json_does_not_mutate_input():
    emails = _emails()
    ids = [e.id for e in emails]
    render_json(emails, tool="t", out=io.StringIO())
    assert [e.id for e in emails] == ids
