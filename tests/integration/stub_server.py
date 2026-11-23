"""Scriptable stub Streamable-HTTP MCP server for integration smoke tests.

Runs a real uvicorn server on a loopback port in a background thread. It speaks
just enough JSON-RPC (initialize, tools/list, tools/call) for the real MCP
client. ``tools/call`` behaviour is scripted through ``call_plan``: each entry
is consumed by one call; when the plan is empty the call succeeds with
``email_count`` messages.

Plan entries:
    ("status", 429)    reply with that HTTP status
    ("sleep", secs)    delay the response (to trigger client read timeouts)

Gmail mode (``gmail = True``) serves ``gmail_search``, ``gmail_read_message``
and a thread-level ``gmail_search_threads`` instead of ``list_messages``:
    search_mode        "ids" (id stubs only) or "full" (complete objects)
    invalid_arguments  gmail_search returns the observed ``invalidArguments`` body
    read_fail_ids      ids for which gmail_read_message reports ``success: false``
    read_delay         seconds each read call is delayed (to overlap calls)
    search_cursor      cursor value included in search results, if any
``read_calls`` / ``max_concurrent_reads`` record hydration behaviour.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

TOKEN = "dapi-integration-token"

TOOL = {
    "name": "list_messages",
    "description": "List recent messages",
    "inputSchema": {
        "type": "object",
        "properties": {"count": {"type": "integer"}, "label": {"type": "string"}},
    },
}


GMAIL_TOOLS = [
    {
        "name": "gmail_search_threads",
        "description": "Search Gmail threads",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
                "cursor": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gmail_search",
        "description": "Search Gmail messages",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
                "cursor": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gmail_read_message",
        "description": "Read one Gmail message",
        "inputSchema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    },
    {
        "name": "gmail_label_message",
        "description": "Add a label to a message",
        "inputSchema": {"type": "object", "properties": {"message_id": {"type": "string"}}},
    },
]

INVALID_ARGUMENTS_BODY = {
    "errorType": "invalidArguments",
    "missing": ["query"],
    "success": False,
    "validFields": ["cursor", "max_results", "query"],
}


def make_messages(n: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"m{i:02d}",
            "subject": f"Subject {i}",
            "from": "alice@example.com",
            "date": f"2025-01-{i + 1:02d}T10:00:00Z",
        }
        for i in range(n)
    ]


class StubMcpServer:
    def __init__(self) -> None:
        self.email_count = 10
        self.call_plan: list[tuple[str, Any]] = []
        self.tool_calls = 0
        self.call_args: list[dict[str, Any]] = []
        self.auth_headers: list[str | None] = []
        self.gmail = False
        self.search_mode = "ids"
        self.invalid_arguments = False
        self.read_fail_ids: set[str] = set()
        self.read_delay = 0.0
        self.search_cursor: str | None = None
        self.called_tools: list[str] = []
        self.read_calls: list[str] = []
        self.max_concurrent_reads = 0
        self._active_reads = 0
        app = Starlette(
            routes=[Route("/mcp", self._handle, methods=["GET", "POST", "DELETE"])]
        )
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    async def _handle(self, request: Request) -> Response:
        import asyncio

        auth = request.headers.get("authorization")
        self.auth_headers.append(auth)
        if auth != f"Bearer {TOKEN}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if request.method != "POST":
            return Response(status_code=405)
        msg = await request.json()
        if "id" not in msg:  # notification
            return Response(status_code=202)

        method = msg["method"]
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stub", "version": "0"},
            }
        elif method == "tools/list":
            result = {"tools": GMAIL_TOOLS if self.gmail else [TOOL]}
        elif method == "tools/call":
            if self.gmail:
                return await self._gmail_call(msg)
            self.tool_calls += 1
            self.call_args.append(msg["params"].get("arguments", {}))
            if self.call_plan:
                kind, value = self.call_plan.pop(0)
                if kind == "status":
                    return JSONResponse({"error": "scripted"}, status_code=value)
                if kind == "sleep":
                    await asyncio.sleep(value)
            text = json.dumps({"messages": make_messages(self.email_count)})
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        else:
            result = {}
        return JSONResponse({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    @staticmethod
    def _text_result(msg: dict[str, Any], payload: Any) -> Response:
        result = {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}
        return JSONResponse({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    async def _gmail_call(self, msg: dict[str, Any]) -> Response:
        import asyncio

        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        self.called_tools.append(name)
        if name == "gmail_read_message":
            mid = args.get("message_id")
            self.read_calls.append(mid)
            self._active_reads += 1
            self.max_concurrent_reads = max(self.max_concurrent_reads, self._active_reads)
            try:
                if self.read_delay:
                    await asyncio.sleep(self.read_delay)
            finally:
                self._active_reads -= 1
            if mid in self.read_fail_ids:
                return self._text_result(msg, {"success": False, "message": "read failed"})
            full = next((m for m in make_messages(self.email_count) if m["id"] == mid), None)
            return self._text_result(msg, full or {"success": False, "message": "not found"})
        # search tools
        self.tool_calls += 1
        self.call_args.append(args)
        if self.invalid_arguments:
            return self._text_result(msg, INVALID_ARGUMENTS_BODY)
        msgs = make_messages(self.email_count)
        if self.search_mode == "ids":
            msgs = [{"id": m["id"]} for m in msgs]
        payload: dict[str, Any] = {"messages": msgs}
        if self.search_cursor:
            payload["cursor"] = self.search_cursor
        return self._text_result(msg, payload)

    def __enter__(self) -> "StubMcpServer":
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("stub server did not start")

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)
