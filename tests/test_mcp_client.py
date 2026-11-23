"""Task 7: MCP client wrapper against a minimal in-process stub MCP server."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from recent_mail_fetcher.exitcodes import EXIT_AUTH, FailError
from recent_mail_fetcher.mcp_client import (
    StaticTokenProvider,
    TokenProvider,
    default_http_client_factory,
    open_session,
)

TOKEN = "dapi-secret-token"


class StubServer:
    def __init__(self) -> None:
        self.auth_headers: list[str | None] = []
        app = Starlette(routes=[Route("/mcp", self._handle, methods=["GET", "POST", "DELETE"])])
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
        auth = request.headers.get("authorization")
        self.auth_headers.append(auth)
        if auth != f"Bearer {TOKEN}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if request.method != "POST":
            return Response(status_code=405)
        msg = await request.json()
        if "id" not in msg:  # notification
            return Response(status_code=202)
        if msg["method"] == "initialize":
            result = {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {},
                "serverInfo": {"name": "stub", "version": "0"},
            }
        else:
            result = {}
        return JSONResponse({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    def __enter__(self) -> "StubServer":
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("stub server did not start")

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def test_initialize_completes_and_sends_bearer_header() -> None:
    async def run() -> None:
        async with open_session(stub.url, StaticTokenProvider(TOKEN)) as session:
            assert session is not None

    with StubServer() as stub:
        asyncio.run(run())
        assert stub.auth_headers, "no requests reached the stub"
        assert all(h == f"Bearer {TOKEN}" for h in stub.auth_headers)


def test_bad_token_exits_auth_without_retry() -> None:
    async def run() -> None:
        async with open_session(stub.url, StaticTokenProvider("wrong")):
            pass

    with StubServer() as stub:
        with pytest.raises(FailError) as ei:
            asyncio.run(run())
        assert ei.value.code == EXIT_AUTH
        assert len(stub.auth_headers) == 1  # no retry


def test_empty_token_fails_before_network() -> None:
    async def run() -> None:
        async with open_session("http://127.0.0.1:1/mcp", StaticTokenProvider("")):
            pass

    with pytest.raises(FailError) as ei:
        asyncio.run(run())
    assert ei.value.code == EXIT_AUTH


def test_injectable_parts() -> None:
    assert isinstance(StaticTokenProvider("x"), TokenProvider)
    assert "x" not in repr(StaticTokenProvider("x"))
    seen: list[tuple] = []

    def factory(headers, connect, read):
        seen.append((dict(headers), connect, read))
        return default_http_client_factory(headers, connect, read)

    async def run() -> None:
        async with open_session(
            stub.url,
            StaticTokenProvider(TOKEN),
            connect_timeout=5,
            read_timeout=7,
            http_client_factory=factory,
        ):
            pass

    with StubServer() as stub:
        asyncio.run(run())
    assert seen == [({"Authorization": f"Bearer {TOKEN}"}, 5, 7)]
