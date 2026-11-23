"""Unit tests for recent_mail_fetcher.parser."""

from __future__ import annotations

import json
from types import SimpleNamespace

from recent_mail_fetcher.exitcodes import EXIT_PARSE_FAILURE, EXIT_SUCCESS
from recent_mail_fetcher.parser import parse_result


def text(payload) -> dict:
    return {"type": "text", "text": payload if isinstance(payload, str) else json.dumps(payload)}


def test_valid_bare_list():
    msgs = [
        {
            "id": "a",
            "threadId": "t1",
            "from": "x@example.com",
            "to": "y@example.com, z@example.com",
            "subject": "Hi",
            "date": "2025-01-20T13:40:00Z",
            "snippet": "s",
            "labelIds": ["INBOX", "UNREAD"],
            "hasAttachments": True,
        }
    ]
    r = parse_result([text(msgs)])
    assert r.exit_code == EXIT_SUCCESS
    (e,) = r.emails
    assert e.id == "a"
    assert e.thread_id == "t1"
    assert e.sender == "x@example.com"
    assert e.recipients == ["y@example.com", "z@example.com"]
    assert e.subject == "Hi"
    assert e.received_at.isoformat() == "2025-01-20T13:40:00+00:00"
    assert e.label_ids == ["INBOX", "UNREAD"]
    assert e.is_unread is True
    assert e.has_attachments is True


def test_single_message_object_and_attribute_blocks():
    block = SimpleNamespace(type="text", text=json.dumps({"id": "solo"}))
    r = parse_result([block])
    assert [e.id for e in r.emails] == ["solo"]
    assert r.emails[0].is_unread is False


def test_nested_wrapper():
    payload = {"data": {"messages": [{"id": "1"}, {"id": "2"}]}}
    r = parse_result([text(payload)])
    assert sorted(e.id for e in r.emails) == ["1", "2"]
    assert r.exit_code == EXIT_SUCCESS


def test_epoch_millis_date():
    r = parse_result([text([{"id": "a", "internalDate": "1737380400000"}])])
    assert r.emails[0].received_at.year == 2025


def test_malformed_json_falls_back_to_raw_text_exit_5():
    r = parse_result([text("not json {")])
    assert r.exit_code == EXIT_PARSE_FAILURE == 5
    assert r.raw_text == ["not json {"]
    assert r.emails == []
    assert r.warnings


def test_malformed_alongside_valid_keeps_emails_and_exit_5():
    r = parse_result([text([{"id": "a"}]), text("garbage")])
    assert r.exit_code == EXIT_PARSE_FAILURE
    assert [e.id for e in r.emails] == ["a"]
    assert r.raw_text == ["garbage"]


def test_empty_inputs():
    for content in (None, [], [text("   ")], [text([])], [text({})]):
        r = parse_result(content)
        assert r.emails == []
        assert r.exit_code == EXIT_SUCCESS
        assert r.raw_text == []


def test_mixed_block_types_ignore_non_text():
    content = [
        {"type": "image", "data": "xx", "mimeType": "image/png"},
        text([{"id": "a"}]),
        {"type": "resource", "resource": {}},
    ]
    r = parse_result(content)
    assert [e.id for e in r.emails] == ["a"]
    assert r.exit_code == EXIT_SUCCESS


def test_messages_without_id_skipped():
    r = parse_result([text({"messages": [{"id": "a"}, {"id": ""}, {"subject": "x"}]})])
    assert [e.id for e in r.emails] == ["a"]


def test_sort_newest_first_and_none_last():
    msgs = [
        {"id": "old", "date": "2025-01-01T00:00:00Z"},
        {"id": "none"},
        {"id": "new", "date": "2025-02-01T00:00:00Z"},
    ]
    r = parse_result([text(msgs)])
    assert [e.id for e in r.emails] == ["new", "old", "none"]


def test_tiebreak_id_ascending():
    d = "2025-01-01T00:00:00Z"
    msgs = [{"id": "c", "date": d}, {"id": "a", "date": d}, {"id": "b", "date": d}]
    r = parse_result([text(msgs)])
    assert [e.id for e in r.emails] == ["a", "b", "c"]


def test_truncate_after_sort_and_across_blocks():
    b1 = text([{"id": "1", "date": "2025-01-01T00:00:00Z"}, {"id": "3", "date": "2025-03-01T00:00:00Z"}])
    b2 = text([{"id": "2", "date": "2025-02-01T00:00:00Z"}])
    r = parse_result([b1, b2], count=2)
    assert [e.id for e in r.emails] == ["3", "2"]


def test_count_larger_than_available_and_none():
    content = [text([{"id": "a"}, {"id": "b"}])]
    assert len(parse_result(content, count=10).emails) == 2
    assert len(parse_result(content, count=None).emails) == 2
    assert parse_result(content, count=0).emails == []


# --- Gmail search compatibility (tasks 20-21) --------------------------------

import asyncio  # noqa: E402
import logging  # noqa: E402

import pytest  # noqa: E402

from recent_mail_fetcher.exitcodes import EXIT_TOOL_FAILURE, FailError  # noqa: E402
from recent_mail_fetcher.hydrate import MAX_CONCURRENCY, hydrate_emails  # noqa: E402
from recent_mail_fetcher.invoker import call_tool  # noqa: E402
from recent_mail_fetcher.parser import detect_tool_error, is_complete  # noqa: E402

INVALID_ARGS = {
    "errorType": "invalidArguments",
    "missing": ["query"],
    "success": False,
    "validFields": ["cursor", "max_results", "query"],
}


def test_detect_error_invalid_arguments_body():
    msg = detect_tool_error([text(INVALID_ARGS)])
    assert "invalidArguments" in msg and "query" in msg
    assert "cursor, max_results, query" in msg


def test_detect_error_normal_result_is_none():
    assert detect_tool_error([text({"messages": [{"id": "a"}]})]) is None
    assert detect_tool_error([text({"success": True})]) is None
    assert detect_tool_error([text("not json")]) is None
    assert detect_tool_error(None) is None


def test_detect_error_uses_message_field():
    assert detect_tool_error([text({"success": False, "message": "boom"})]) == "boom"


class ErrSession:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return self.result


def _result(payload, is_error=False):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))], isError=is_error)


def test_call_tool_success_false_exits_4_and_retries_once_without_count(capsys):
    session = ErrSession(_result(INVALID_ARGS))
    with pytest.raises(FailError) as ei:
        asyncio.run(call_tool(session, "gmail_search", {"max_results": 10}))
    assert ei.value.code == EXIT_TOOL_FAILURE
    assert [a for _, a in session.calls] == [{"max_results": 10}, {}]
    assert capsys.readouterr().out == ""


def test_call_tool_is_error_exits_4():
    session = ErrSession(_result({"x": 1}, is_error=True))
    with pytest.raises(FailError) as ei:
        asyncio.run(call_tool(session, "t", {}))
    assert ei.value.code == 4


def test_call_tool_normal_result_unaffected():
    session = ErrSession(_result({"messages": [{"id": "a"}]}))
    assert asyncio.run(call_tool(session, "t", {"max_results": 1})) is session.result
    assert len(session.calls) == 1


def test_parse_result_extracts_cursor():
    r = parse_result([text({"messages": [{"id": "a"}], "cursor": "abc"})])
    assert r.cursor == "abc"
    assert parse_result([text({"messages": [{"id": "a"}]})]).cursor is None


def test_id_only_entries_are_incomplete():
    (stub,) = parse_result([text([{"id": "a"}])]).emails
    assert not is_complete(stub)
    (full,) = parse_result([text([{"id": "a", "subject": "s", "from": "x", "date": "2025-01-01T00:00:00Z"}])]).emails
    assert is_complete(full)


class ReadSession:
    def __init__(self, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.reads = []
        self.active = 0
        self.max_active = 0

    async def call_tool(self, name, args):
        mid = args["message_id"]
        self.reads.append(mid)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
        finally:
            self.active -= 1
        if mid in self.fail_ids:
            return _result({"success": False, "message": "nope"})
        return _result({"id": mid, "subject": f"S{mid}", "from": "a@x.com", "date": "2025-01-02T00:00:00Z"})


READ_TOOL = SimpleNamespace(
    name="gmail_read_message",
    inputSchema={"type": "object", "properties": {"message_id": {"type": "string"}}, "required": ["message_id"]},
)


def _stubs(n):
    return parse_result([text([{"id": f"m{i}"} for i in range(n)])]).emails


def test_hydrate_ids_only_fills_fields():
    session = ReadSession()
    out = asyncio.run(hydrate_emails(session, _stubs(3), READ_TOOL, 10))
    assert all(is_complete(e) for e in out)
    assert [e.id for e in out] == ["m0", "m1", "m2"]
    assert sorted(session.reads) == ["m0", "m1", "m2"]


def test_hydrate_full_objects_makes_zero_calls():
    full = parse_result([text([{"id": "a", "subject": "s", "from": "x", "date": "2025-01-01T00:00:00Z"}])]).emails
    session = ReadSession()
    assert asyncio.run(hydrate_emails(session, full, READ_TOOL, 10)) == full
    assert session.reads == []


def test_hydrate_partial_failure_keeps_stub_and_warns(caplog):
    session = ReadSession(fail_ids={"m1"})
    with caplog.at_level(logging.WARNING):
        out = asyncio.run(hydrate_emails(session, _stubs(3), READ_TOOL, 10))
    assert [is_complete(e) for e in out] == [True, False, True]
    assert any("m1" in r.getMessage() for r in caplog.records)


def test_hydrate_calls_bounded_by_count():
    session = ReadSession()
    asyncio.run(hydrate_emails(session, _stubs(12), READ_TOOL, 4))
    assert len(session.reads) <= 4


def test_hydrate_concurrency_bounded_by_five():
    session = ReadSession()
    asyncio.run(hydrate_emails(session, _stubs(20), READ_TOOL, 20))
    assert len(session.reads) == 20
    assert 1 < session.max_active <= MAX_CONCURRENCY == 5


def test_hydrate_without_read_tool_returns_stubs_and_warns(caplog):
    stubs = _stubs(2)
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(hydrate_emails(ReadSession(), stubs, None, 10)) == stubs
    assert caplog.records
