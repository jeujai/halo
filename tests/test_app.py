"""End-to-end tests for the CLI dispatch layer using a fake MCP session."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from recent_mail_fetcher import app
from recent_mail_fetcher.__main__ import build_parser, main

TOOL = SimpleNamespace(
    name="list_messages",
    description="List recent messages",
    inputSchema={"type": "object", "properties": {"count": {"type": "integer"}, "label": {"type": "string"}}},
)


class FakeSession:
    def __init__(self, n):
        self.n = n

    async def list_tools(self):
        return SimpleNamespace(tools=[TOOL])

    async def call_tool(self, name, args):
        msgs = [
            {"id": f"m{i:02d}", "subject": f"s{i}", "from": "a@x.com",
             "date": f"2025-01-{i + 1:02d}T10:00:00Z"}
            for i in range(self.n)
        ]
        block = SimpleNamespace(type="text", text=json.dumps({"messages": msgs}))
        return SimpleNamespace(content=[block], isError=False)


def patch_session(monkeypatch, n):
    @asynccontextmanager
    async def fake_open(url, provider, **kw):
        yield FakeSession(n)

    monkeypatch.setattr(app, "open_session", fake_open)


def run(argv, env):
    return app.run(build_parser().parse_args(argv), env)


def test_prints_ten_newest(monkeypatch, capsys):
    patch_session(monkeypatch, 15)
    assert run([], {"DATABRICKS_TOKEN": "tok"}) == 0
    out = capsys.readouterr().out
    assert "m14" in out and "m05" in out and "m04" not in out


def test_json_valid_and_fewer_than_count(monkeypatch, capsys):
    patch_session(monkeypatch, 3)
    assert run(["--json"], {"DATABRICKS_TOKEN": "tok"}) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["emails"]) == 3 and data["tool"] == "list_messages"


def test_empty_prints_no_messages(monkeypatch, capsys):
    patch_session(monkeypatch, 0)
    assert run([], {"DATABRICKS_TOKEN": "tok"}) == 0
    assert "no messages" in capsys.readouterr().out.lower()


def test_list_tools(monkeypatch, capsys):
    patch_session(monkeypatch, 1)
    assert run(["--list-tools"], {"DATABRICKS_TOKEN": "tok"}) == 0
    assert "list_messages" in capsys.readouterr().out


def test_missing_token_exits_2(monkeypatch):
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    assert run([], {}) == 2
    assert main([]) == 2
