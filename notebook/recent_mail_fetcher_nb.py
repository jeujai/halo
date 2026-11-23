# Databricks notebook source
"""Databricks notebook adapter for recent-mail-fetcher (Task 12).

Reuses the shared core (discovery, arg injection, invoker, parser, renderer)
and swaps only the notebook-specific pieces through the existing injectable
interfaces:

* token source  -> ``dbutils`` API token (:class:`NotebookTokenProvider`)
* HTTP client   -> ``httpx2.AsyncClient`` (:func:`httpx2_client_factory`)
* event loop    -> ``nest_asyncio.apply()`` before ``asyncio.run()``

Usage in a notebook (the cell with ``%pip`` must run first, then restart Python)::

    # COMMAND ----------
    %pip install mcp httpx2 nest_asyncio /path/to/recent_mail_fetcher
    dbutils.library.restartPython()

    # COMMAND ----------
    from recent_mail_fetcher_nb import list_tools, fetch_recent
    list_tools(dbutils)            # prints tool names, descriptions, schemas

    # COMMAND ----------
    fetch_recent(dbutils)          # prints the 10 most recent emails
    fetch_recent(dbutils, count=5, as_json=True)

Core behavior is unchanged; nothing here modifies the ``recent_mail_fetcher``
package.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from recent_mail_fetcher.args import build_args_for_tool
from recent_mail_fetcher.app import _find_fail
from recent_mail_fetcher.config import (
    DEFAULT_COUNT,
    DEFAULT_LABEL,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_URL,
)
from recent_mail_fetcher.discovery import discover_and_select
from recent_mail_fetcher.exitcodes import (
    EXIT_PARSE_FAILURE,
    EXIT_SUCCESS,
    EXIT_TOOL_FAILURE,
)
from recent_mail_fetcher.invoker import call_tool
from recent_mail_fetcher.log import setup_logging
from recent_mail_fetcher.mcp_client import open_session
from recent_mail_fetcher.parser import parse_result
from recent_mail_fetcher.render import render

logger = logging.getLogger(__name__)


class NotebookTokenProvider:
    """Token source backed by the notebook's ``dbutils`` API token."""

    def __init__(self, dbutils: Any) -> None:
        self._dbutils = dbutils

    def get_token(self) -> str:
        ctx = self._dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        return (ctx.apiToken().get() or "").strip()

    def __repr__(self) -> str:  # never expose the secret
        return "NotebookTokenProvider(token='***')"


def httpx2_client_factory(
    headers: dict[str, str], connect_timeout: float, read_timeout: float
) -> Any:
    """Build an authenticated ``httpx2.AsyncClient`` (Databricks Runtime)."""
    import httpx2

    timeout = httpx2.Timeout(read_timeout, connect=connect_timeout, read=read_timeout)
    return httpx2.AsyncClient(headers=headers, timeout=timeout)


def _run(coro: Any) -> Any:
    """Run ``coro`` inside a notebook (which already has a running loop)."""
    import nest_asyncio

    nest_asyncio.apply()
    return asyncio.run(coro)


async def _fetch(
    dbutils: Any,
    *,
    url: str,
    count: int,
    label: str,
    tool_name: str | None,
    allow_write_tools: bool,
    read_timeout: float,
    as_json: bool,
    list_only: bool,
) -> int:
    provider = NotebookTokenProvider(dbutils)
    async with open_session(
        url,
        provider,
        read_timeout=read_timeout,
        http_client_factory=httpx2_client_factory,
    ) as session:
        tool = await discover_and_select(
            session,
            requested=tool_name,
            allow_write_tools=allow_write_tools,
            list_only=list_only,
        )
        if tool is None:  # tools already printed
            return EXIT_SUCCESS
        schema = getattr(tool, "inputSchema", None)
        call_args = build_args_for_tool(tool, count=count, label=label)
        result = await call_tool(session, tool.name, call_args, input_schema=schema)

    parsed = parse_result(getattr(result, "content", None), count)
    if parsed.exit_code == EXIT_PARSE_FAILURE:
        logger.warning("Result was not parseable as JSON; falling back to raw text")
        if parsed.emails:
            render(parsed.emails, as_json=as_json, tool=tool.name, count=count)
        elif not as_json:
            for text in parsed.raw_text:
                print(text.rstrip("\n"))
        return EXIT_PARSE_FAILURE
    render(parsed.emails, as_json=as_json, tool=tool.name, count=count)
    return EXIT_SUCCESS


def _main(dbutils: Any, *, verbose: bool = False, **kwargs: Any) -> int:
    setup_logging(verbose=verbose)
    try:
        token = NotebookTokenProvider(dbutils).get_token()
        setup_logging(verbose=verbose, token=token or None)
        return _run(_fetch(dbutils, **kwargs))
    except BaseException as exc:  # noqa: BLE001 - map to an exit code
        found = _find_fail(exc)
        if found is not None:
            return found.code
        logger.error("Unexpected failure: %s: %s", type(exc).__name__, exc)
        return EXIT_TOOL_FAILURE


def list_tools(
    dbutils: Any,
    *,
    url: str = DEFAULT_URL,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    verbose: bool = False,
) -> int:
    """Print every tool's name, description and input schema. Returns exit code."""
    return _main(
        dbutils,
        verbose=verbose,
        url=url,
        count=DEFAULT_COUNT,
        label=DEFAULT_LABEL,
        tool_name=None,
        allow_write_tools=False,
        read_timeout=float(read_timeout),
        as_json=False,
        list_only=True,
    )


def fetch_recent(
    dbutils: Any,
    *,
    count: int = DEFAULT_COUNT,
    label: str = DEFAULT_LABEL,
    tool: str | None = None,
    url: str = DEFAULT_URL,
    as_json: bool = False,
    allow_write_tools: bool = False,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    verbose: bool = False,
) -> int:
    """Print the ``count`` most recent emails (default 10). Returns exit code."""
    return _main(
        dbutils,
        verbose=verbose,
        url=url,
        count=count,
        label=label,
        tool_name=tool,
        allow_write_tools=allow_write_tools,
        read_timeout=float(read_timeout),
        as_json=as_json,
        list_only=False,
    )
