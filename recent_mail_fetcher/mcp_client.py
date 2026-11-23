"""MCP client wrapper (Task 7).

Opens a Streamable HTTP MCP session with Bearer auth and yields an initialized
``mcp.ClientSession``. The token source and the HTTP client construction are
injectable (:class:`TokenProvider`, :data:`HttpClientFactory`) so the notebook
variant can swap them (``dbutils`` token, ``httpx2``) without touching callers.

The token is only placed in the ``Authorization`` header; it is never logged.
401/403 responses are mapped to ``EXIT_AUTH`` (no retry here).
"""

from __future__ import annotations

import inspect
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Protocol, runtime_checkable

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from recent_mail_fetcher.exitcodes import EXIT_AUTH, FailError, fail

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 60.0


@runtime_checkable
class TokenProvider(Protocol):
    """Source of the bearer token."""

    def get_token(self) -> str: ...


class StaticTokenProvider:
    """Token held in memory (e.g. from ``Config.token`` / DATABRICKS_TOKEN)."""

    def __init__(self, token: str) -> None:
        self._token = token

    def get_token(self) -> str:
        return self._token

    def __repr__(self) -> str:  # never expose the secret
        return "StaticTokenProvider(token='***')"


# (headers, connect_timeout, read_timeout) -> an httpx-style AsyncClient
HttpClientFactory = Callable[[dict[str, str], float, float], Any]


def _http_module() -> Any:
    """Pick the HTTP library the installed ``mcp`` SDK expects.

    Newer ``mcp`` releases annotate ``http_client`` as ``httpx2.AsyncClient``;
    older ones use ``httpx``.
    """
    try:
        ann = inspect.signature(streamable_http_client).parameters["http_client"].annotation
    except (KeyError, ValueError, TypeError):
        ann = ""
    if "httpx2" in str(ann):
        import httpx2

        return httpx2
    import httpx

    return httpx


def default_http_client_factory(
    headers: dict[str, str], connect_timeout: float, read_timeout: float
) -> Any:
    """Build an authenticated ``AsyncClient`` with the configured timeouts."""
    lib = _http_module()
    timeout = lib.Timeout(
        read_timeout, connect=connect_timeout, read=read_timeout
    )
    return lib.AsyncClient(headers=headers, timeout=timeout)


def _status_code(exc: BaseException) -> int | None:
    """Find an HTTP status code in an exception (or nested group), if any."""
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    if isinstance(code, int):
        return code
    for sub in getattr(exc, "exceptions", ()) or ():
        found = _status_code(sub)
        if found is not None:
            return found
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        return _status_code(cause)
    return None


@asynccontextmanager
async def open_session(
    url: str,
    token_provider: TokenProvider,
    *,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    http_client_factory: HttpClientFactory = default_http_client_factory,
) -> AsyncIterator[ClientSession]:
    """Yield an initialized :class:`ClientSession` over Streamable HTTP.

    The ``Authorization: Bearer <token>`` header is set on the HTTP client.
    HTTP 401/403 during connect/initialize exits with ``EXIT_AUTH`` (no retry);
    other connection errors propagate for the invoker's retry logic.
    """
    token = token_provider.get_token()
    if not token:
        fail(EXIT_AUTH, "Bearer token is missing or empty.")
    headers = {"Authorization": f"Bearer {token}"}
    client = http_client_factory(headers, connect_timeout, read_timeout)
    # The MCP SDK collapses HTTP errors into generic JSON-RPC errors, so record
    # response statuses ourselves to detect 401/403.
    statuses: list[int] = []

    async def _record_status(response: Any) -> None:
        statuses.append(response.status_code)

    hooks = getattr(client, "event_hooks", None)
    if isinstance(hooks, dict):
        hooks.setdefault("response", []).append(_record_status)
    logger.debug("Opening MCP session to %s", url)
    entered = False
    try:
        async with client:
            async with streamable_http_client(url, http_client=client) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    # Expose observed HTTP statuses so the invoker can classify
                    # tool-call failures (the SDK hides 401/429/5xx codes).
                    session.http_statuses = statuses  # type: ignore[attr-defined]
                    await session.initialize()
                    entered = True
                    yield session
    except BaseException as exc:  # noqa: BLE001 - classify then re-raise
        if entered or isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        if isinstance(exc, FailError):
            raise
        status = next((c for c in statuses if c in (401, 403)), None)
        if status is None:
            status = _status_code(exc)
        if status in (401, 403):
            fail(EXIT_AUTH, f"Authentication failed (HTTP {status}); not retrying.")
        raise
