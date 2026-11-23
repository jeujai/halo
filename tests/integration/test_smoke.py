"""Integration smoke test: the real CLI pipeline against a stub MCP server over HTTP."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from stub_server import TOKEN, StubMcpServer  # noqa: E402

from recent_mail_fetcher import app  # noqa: E402
from recent_mail_fetcher.__main__ import build_parser  # noqa: E402
from recent_mail_fetcher.invoker import call_tool as real_call_tool  # noqa: E402


@pytest.fixture
def stub():
    with StubMcpServer() as server:
        yield server


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def fast_call_tool(session, name, args=None, **kw):
        kw["sleep"] = fake_sleep
        return await real_call_tool(session, name, args, **kw)

    monkeypatch.setattr(app, "call_tool", fast_call_tool)
    monkeypatch.setattr(app, "_sleep", fake_sleep)
    return sleeps


def run(stub, extra=None, token=TOKEN):
    argv = ["--url", stub.url, "--json", *(extra or [])]
    return app.run(build_parser().parse_args(argv), {"DATABRICKS_TOKEN": token})


def test_successful_ten_email_fetch(stub, capsys):
    assert run(stub) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 10 and len(data["emails"]) == 10
    assert data["tool"] == "list_messages"
    # newest first
    assert data["emails"][0]["id"] == "m09"
    assert stub.tool_calls == 1
    assert stub.call_args == [{"count": 10, "label": "INBOX"}]
    assert all(h == f"Bearer {TOKEN}" for h in stub.auth_headers)


def test_fewer_than_count_prints_all_available(stub, capsys):
    stub.email_count = 3
    assert run(stub) == 0
    assert len(json.loads(capsys.readouterr().out)["emails"]) == 3


def test_empty_result_exits_zero(stub, capsys):
    stub.email_count = 0
    assert run(stub) == 0
    assert json.loads(capsys.readouterr().out)["emails"] == []


def test_empty_result_table_says_no_messages(stub, capsys):
    stub.email_count = 0
    argv = ["--url", stub.url]
    assert app.run(build_parser().parse_args(argv), {"DATABRICKS_TOKEN": TOKEN}) == 0
    assert "no messages" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("status", [429, 503])
def test_retries_then_succeeds_on_429_and_5xx(stub, capsys, fast_backoff, status):
    stub.call_plan = [("status", status), ("status", status)]
    assert run(stub) == 0
    assert stub.tool_calls == 3
    assert fast_backoff == [1.0, 2.0]  # exponential backoff
    assert len(json.loads(capsys.readouterr().out)["emails"]) == 10


def test_retries_exhausted_exits_4(stub, fast_backoff):
    stub.call_plan = [("status", 503)] * 5
    assert run(stub) == 4
    assert stub.tool_calls == 3


def test_read_timeout_reconnects_then_succeeds(stub, capsys, fast_backoff):
    stub.call_plan = [("sleep", 1.5)]
    assert run(stub, extra=["--read-timeout", "0.4"]) == 0
    assert stub.tool_calls == 2
    assert fast_backoff == [1.0]
    assert len(json.loads(capsys.readouterr().out)["emails"]) == 10


def test_read_timeout_exhausted_exits_4(stub, fast_backoff):
    stub.call_plan = [("sleep", 1.5)] * 3
    assert run(stub, extra=["--read-timeout", "0.4"]) == 4
    assert stub.tool_calls == 3
    assert fast_backoff == [1.0, 2.0]


def test_bad_token_401_exits_2_without_retry(stub, fast_backoff):
    assert run(stub, token="wrong-token") == 2
    assert stub.tool_calls == 0
    assert fast_backoff == []
    # initialize is attempted once; no retry loop
    assert len(stub.auth_headers) <= 3


def test_401_on_tool_call_exits_2_without_retry(stub, fast_backoff):
    stub.call_plan = [("status", 401)]
    assert run(stub) == 2
    assert stub.tool_calls == 1
    assert fast_backoff == []


# --- Gmail search compatibility (tasks 18-21) --------------------------------


@pytest.fixture
def gmail_stub():
    with StubMcpServer() as server:
        server.gmail = True
        yield server


def test_gmail_selects_gmail_search_and_builds_query(gmail_stub, capsys):
    gmail_stub.search_mode = "full"
    assert run(gmail_stub) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["tool"] == "gmail_search"
    assert gmail_stub.call_args == [{"query": "in:inbox", "max_results": 10}]
    assert len(data["emails"]) == 10


def test_gmail_full_objects_trigger_no_read_calls(gmail_stub, capsys):
    gmail_stub.search_mode = "full"
    assert run(gmail_stub) == 0
    assert gmail_stub.read_calls == []


def test_gmail_ids_only_are_hydrated(gmail_stub, capsys):
    gmail_stub.search_mode = "ids"
    assert run(gmail_stub) == 0
    emails = json.loads(capsys.readouterr().out)["emails"]
    assert len(emails) == 10
    assert all(e["subject"] and e["sender"] and e["received_at"] for e in emails)
    assert emails[0]["id"] == "m09"
    assert len(gmail_stub.read_calls) == 10


def test_gmail_hydration_calls_bounded_by_count(gmail_stub, capsys):
    gmail_stub.email_count = 12
    assert run(gmail_stub, extra=["--count", "4"]) == 0
    assert len(gmail_stub.read_calls) <= 4


def test_gmail_hydration_concurrency_at_most_five(gmail_stub, capsys):
    gmail_stub.read_delay = 0.2
    assert run(gmail_stub) == 0
    assert 1 < gmail_stub.max_concurrent_reads <= 5


def test_gmail_partial_read_failure_keeps_stub(gmail_stub, capsys):
    gmail_stub.read_fail_ids = {"m03"}
    assert run(gmail_stub) == 0
    emails = {e["id"]: e for e in json.loads(capsys.readouterr().out)["emails"]}
    assert len(emails) == 10
    assert emails["m03"]["subject"] in (None, "")
    assert emails["m04"]["subject"] == "Subject 4"


def test_gmail_invalid_arguments_exits_4_with_empty_stdout(gmail_stub, capsys):
    gmail_stub.invalid_arguments = True
    assert run(gmail_stub) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "validFields" in captured.err or "query" in captured.err
    assert gmail_stub.read_calls == []


def test_gmail_label_tool_not_called(gmail_stub):
    assert run(gmail_stub) == 0
    assert "gmail_label_message" not in gmail_stub.called_tools
