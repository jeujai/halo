"""Dispatch layer (Task 11): config -> connect -> discover -> call -> parse -> render.

``run(args)`` returns the process exit code. stdout carries results only; all
logging goes to stderr through the redacting logger.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Mapping

from recent_mail_fetcher.args import build_args_for_tool, tool_input_schema
from recent_mail_fetcher.config import Config, ConfigError, load_config
from recent_mail_fetcher.discovery import discover_and_select, select_read_message_tool
from recent_mail_fetcher.exitcodes import (
    EXIT_PARSE_FAILURE,
    EXIT_SUCCESS,
    EXIT_TOOL_FAILURE,
    FailError,
    fail,
)
from recent_mail_fetcher.invoker import (
    BASE_DELAY,
    MAX_ATTEMPTS,
    _classify_exception,
    call_tool,
)
from recent_mail_fetcher.log import setup_logging
from recent_mail_fetcher.mcp_client import StaticTokenProvider, open_session
from recent_mail_fetcher.hydrate import hydrate_emails
from recent_mail_fetcher.models import sort_emails
from recent_mail_fetcher.parser import ParseResult, parse_result
from recent_mail_fetcher.render import render

logger = logging.getLogger(__name__)

# Backoff sleep for session reconnects; module-level so tests can shorten it.
_sleep = asyncio.sleep


def _find_fail(exc: BaseException) -> FailError | None:
    """Locate a FailError, possibly nested inside an exception group."""
    if isinstance(exc, FailError):
        return exc
    for sub in getattr(exc, "exceptions", ()) or ():
        found = _find_fail(sub)
        if found is not None:
            return found
    return None


async def _session_round(cfg: Config) -> tuple[Any, ParseResult | None]:
    """One connect -> discover -> search -> parse -> hydrate round.

    Returns ``(tool, parsed)``; ``tool`` is ``None`` when ``--list-tools``
    already printed the listing.
    """
    provider = StaticTokenProvider(cfg.token)
    async with open_session(
        cfg.url, provider, read_timeout=cfg.read_timeout
    ) as session:
        all_tools: list[Any] = []
        tool = await discover_and_select(
            session,
            requested=cfg.tool,
            allow_write_tools=cfg.allow_write_tools,
            list_only=cfg.list_tools,
            all_tools=all_tools,
        )
        if tool is None:
            return None, None

        schema = tool_input_schema(tool)
        call_args = build_args_for_tool(tool, count=cfg.count, label=cfg.label)
        logger.debug("Calling tool %s with args %r", tool.name, call_args)
        result = await call_tool(session, tool.name, call_args, input_schema=schema)

        parsed = parse_result(getattr(result, "content", None), cfg.count)
        if parsed.emails and parsed.exit_code == EXIT_SUCCESS:
            read_tool = select_read_message_tool(all_tools)
            if read_tool is not None and read_tool.name == tool.name:
                read_tool = None
            hydrated = await hydrate_emails(session, parsed.emails, read_tool, cfg.count)
            parsed.emails = sort_emails(hydrated)[: cfg.count]
        if parsed.cursor and len(parsed.emails) < cfg.count:
            logger.warning(
                "More messages exist (cursor returned) but pagination is not supported; "
                "showing %d", len(parsed.emails),
            )
    return tool, parsed


async def _round_with_reconnect(cfg: Config) -> tuple[Any, ParseResult | None]:
    """Retry the whole session when the transport itself dies.

    A read timeout or connection reset raised inside the MCP SDK's transport
    task group tears down the session, so the invoker's in-session retry
    cannot recover. Reconnect with exponential backoff, at most
    ``MAX_ATTEMPTS`` rounds, then exit ``EXIT_TOOL_FAILURE``.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await _session_round(cfg)
        except BaseException as exc:  # noqa: BLE001 - classified below
            if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            if _find_fail(exc) is not None or _classify_exception(exc) != "retry":
                raise
            detail = f"{type(exc).__name__}: {exc}"
            if attempt >= MAX_ATTEMPTS:
                fail(
                    EXIT_TOOL_FAILURE,
                    f"Session failed after {MAX_ATTEMPTS} attempts: {detail}",
                )
            delay = BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "Transport failure (attempt %d/%d): %s; reconnecting in %.1fs",
                attempt, MAX_ATTEMPTS, detail, delay,
            )
            await _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


async def _fetch(cfg: Config) -> int:
    tool, parsed = await _round_with_reconnect(cfg)
    if tool is None or parsed is None:  # --list-tools already printed
        return EXIT_SUCCESS

    if parsed.exit_code == EXIT_PARSE_FAILURE:
        logger.warning("Result was not parseable as JSON; falling back to raw text")
        if parsed.emails:
            render(parsed.emails, as_json=cfg.json, tool=tool.name, count=cfg.count)
        elif not cfg.json:
            for text in parsed.raw_text:
                sys.stdout.write(text.rstrip("\n") + "\n")
        return EXIT_PARSE_FAILURE

    render(parsed.emails, as_json=cfg.json, tool=tool.name, count=cfg.count)
    return EXIT_SUCCESS


def run(args: Any, env: Mapping[str, str] | None = None) -> int:
    """Run the CLI pipeline for parsed ``args``; return the exit code."""
    setup_logging(verbose=bool(getattr(args, "verbose", False)))
    try:
        cfg = load_config(args, env)
    except ConfigError as err:
        logging.getLogger(__name__).error(str(err))
        return err.code

    # Re-install logging with the token so it is scrubbed from every line.
    setup_logging(verbose=cfg.verbose, token=cfg.token)
    try:
        return asyncio.run(_fetch(cfg))
    except KeyboardInterrupt:
        logger.error("Interrupted")
        return 130
    except BaseException as exc:  # noqa: BLE001 - map everything to an exit code
        found = _find_fail(exc)
        if found is not None:
            return found.code  # fail() already logged the reason
        logger.error("Unexpected failure: %s: %s", type(exc).__name__, exc)
        return EXIT_TOOL_FAILURE
