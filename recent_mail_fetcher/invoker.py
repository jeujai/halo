"""Tool invoker with retry/backoff and error classification (Task 10).

``call_tool(session, name, args)`` wraps ``session.call_tool`` and applies the
design.md error-handling table:

* 401/403 or permission denied: ``EXIT_AUTH``, never retried.
* 429 / 5xx / connection reset / read timeout: exponential backoff, at most
  ``max_attempts`` (3) attempts, then ``EXIT_TOOL_FAILURE``.
* Tool rejects the arguments (JSON-RPC invalid params or an ``isError``
  result): log schema and tried args, retry once without the count argument,
  ``EXIT_TOOL_FAILURE`` if it still fails.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable

from recent_mail_fetcher.args import COUNT_ALIASES, _norm
from recent_mail_fetcher.exitcodes import (
    EXIT_AUTH,
    EXIT_TOOL_FAILURE,
    FailError,
    fail,
)
from recent_mail_fetcher.mcp_client import _status_code
from recent_mail_fetcher.parser import detect_tool_error

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BASE_DELAY = 1.0

_AUTH = "auth"
_RETRY = "retry"
_REJECTED = "rejected"

_AUTH_RE = re.compile(
    r"\b(401|403)\b|unauthori[sz]ed|forbidden|permission[ _-]?denied|"
    r"not permitted|access denied",
    re.I,
)
_RETRY_RE = re.compile(
    r"\b(429|5\d\d)\b|too many requests|rate.?limit|connection reset|"
    r"read timed? ?out|timed out|bad gateway|service unavailable|"
    r"gateway timeout|internal server error",
    re.I,
)
_RETRY_EXC_NAMES = {
    "ReadTimeout", "ConnectTimeout", "ConnectError", "ReadError",
    "RemoteProtocolError", "WriteError", "PoolTimeout", "TimeoutException",
}


def _classify_text(text: str) -> str | None:
    if _AUTH_RE.search(text):
        return _AUTH
    if _RETRY_RE.search(text):
        return _RETRY
    return None


def _classify_exception(exc: BaseException) -> str:
    status = _status_code(exc)
    if status in (401, 403):
        return _AUTH
    if status == 429 or (status is not None and 500 <= status < 600):
        return _RETRY
    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return _RETRY
    if any(c.__name__ in _RETRY_EXC_NAMES for c in type(exc).__mro__):
        return _RETRY
    for sub in getattr(exc, "exceptions", ()) or ():
        if _classify_exception(sub) != _REJECTED:
            return _classify_exception(sub)
    by_text = _classify_text(str(exc))
    if by_text:
        return by_text
    return _REJECTED


def _result_text(result: Any) -> str:
    parts = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _count_key(args: dict[str, Any]) -> str | None:
    for key in args:
        if _norm(key) in COUNT_ALIASES:
            return key
    return None


class _Failure(Exception):
    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def _new_http_status(session: Any, start: int) -> int | None:
    """Most significant auth/transient HTTP status recorded since ``start``."""
    statuses = getattr(session, "http_statuses", None)
    if not isinstance(statuses, list):
        return None
    new = [s for s in statuses[start:] if isinstance(s, int)]
    for s in new:
        if s in (401, 403):
            return s
    for s in new:
        if s == 429 or 500 <= s < 600:
            return s
    return None


async def _attempt(session: Any, name: str, args: dict[str, Any]) -> Any:
    statuses = getattr(session, "http_statuses", None)
    start = len(statuses) if isinstance(statuses, list) else 0
    try:
        result = await session.call_tool(name, args)
    except FailError:
        raise
    except Exception as exc:  # noqa: BLE001 - classified below
        status = _new_http_status(session, start)
        if status in (401, 403):
            raise _Failure(_AUTH, f"HTTP {status}: {exc}") from exc
        if status is not None:
            raise _Failure(_RETRY, f"HTTP {status}: {exc}") from exc
        raise _Failure(_classify_exception(exc), f"{type(exc).__name__}: {exc}") from exc
    if getattr(result, "isError", False):
        text = _result_text(result)
        raise _Failure(_classify_text(text) or _REJECTED, text or "tool returned an error")
    reported = detect_tool_error(getattr(result, "content", None))
    if reported is not None:
        raise _Failure(_classify_text(reported) or _REJECTED, reported)
    return result


async def _with_retries(
    session: Any,
    name: str,
    args: dict[str, Any],
    max_attempts: int,
    base_delay: float,
    sleep: Callable[[float], Awaitable[Any]],
) -> Any:
    """Run one call, retrying transient failures. Raises _Failure(REJECTED) upward."""
    for attempt in range(1, max_attempts + 1):
        try:
            return await _attempt(session, name, args)
        except _Failure as f:
            if f.kind == _AUTH:
                fail(EXIT_AUTH, f"Authentication/permission failure calling '{name}': {f.detail}")
            if f.kind == _RETRY:
                if attempt >= max_attempts:
                    fail(
                        EXIT_TOOL_FAILURE,
                        f"Tool '{name}' failed after {max_attempts} attempts: {f.detail}",
                    )
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Transient failure calling '%s' (attempt %d/%d): %s; retrying in %.1fs",
                    name, attempt, max_attempts, f.detail, delay,
                )
                await sleep(delay)
                continue
            raise
    raise AssertionError("unreachable")  # pragma: no cover


async def call_tool(
    session: Any,
    name: str,
    args: dict[str, Any] | None = None,
    *,
    input_schema: Any = None,
    max_attempts: int = MAX_ATTEMPTS,
    base_delay: float = BASE_DELAY,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> Any:
    """Call ``name`` with ``args`` on ``session``; return the ``CallToolResult``.

    Exits via :class:`FailError` (codes 2 or 4) per the module docstring.
    """
    args = dict(args or {})
    try:
        return await _with_retries(session, name, args, max_attempts, base_delay, sleep)
    except _Failure as f:
        logger.error(
            "Tool '%s' rejected arguments %r (schema: %r): %s",
            name, args, input_schema, f.detail,
        )
        key = _count_key(args)
        if key is None:
            fail(EXIT_TOOL_FAILURE, f"Tool '{name}' rejected arguments: {f.detail}")
        reduced = {k: v for k, v in args.items() if k != key}
        logger.warning("Retrying '%s' once without '%s' argument", name, key)
        try:
            return await _with_retries(session, name, reduced, max_attempts, base_delay, sleep)
        except _Failure as f2:
            fail(
                EXIT_TOOL_FAILURE,
                f"Tool '{name}' still failed without '{key}': {f2.detail}",
            )
