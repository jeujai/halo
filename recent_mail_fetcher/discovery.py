"""Tool discovery and selection (Task 8).

Lists the tools exposed by the MCP service, prints them for ``--list-tools``,
and picks the read tool either from an explicit ``--tool`` name or by keyword
heuristic. Tools whose names look like write operations are blocked unless
``allow_write_tools`` is set. If nothing suitable is found, the available tools
are logged and the process exits with ``EXIT_NO_TOOL`` (3).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any, Iterable, TextIO

from recent_mail_fetcher.exitcodes import EXIT_NO_TOOL, fail

logger = logging.getLogger(__name__)

READ_KEYWORDS: tuple[str, ...] = ("list", "recent", "search", "messages", "emails")
WRITE_PATTERN = re.compile(r"send|create|delete|update|reply|forward|trash|modify|label|unlabel", re.I)
NON_SEARCH_TOOLS: frozenset[str] = frozenset(
    {"gmail_read_message", "gmail_get_attachment", "gmail_list_labels", "gmail_list_drafts"}
)
THREAD_PATTERN = re.compile(r"threads?$", re.I)
PREFERRED_SEARCH_TOOL = "gmail_search"



def is_write_tool(name: str) -> bool:
    """True if the tool name looks like a write/send/delete operation."""
    return bool(WRITE_PATTERN.search(name or ""))


def _schema(tool: Any) -> Any:
    from recent_mail_fetcher.args import tool_input_schema

    return tool_input_schema(tool)


def format_tools(tools: Iterable[Any]) -> str:
    """Render name, description and input schema for each tool."""
    blocks: list[str] = []
    for tool in tools:
        desc = getattr(tool, "description", None) or "(no description)"
        schema = json.dumps(_schema(tool), indent=2, sort_keys=True, default=str)
        blocks.append(f"{tool.name}\n  description: {desc}\n  inputSchema: {schema}")
    return "\n\n".join(blocks)


def print_tools(tools: Iterable[Any], out: TextIO | None = None) -> None:
    """Print all tools (the ``--list-tools`` output) to stdout."""
    (out or sys.stdout).write(format_tools(tools) + "\n")


def _score(tool: Any) -> int:
    name = (tool.name or "").lower()
    desc = (getattr(tool, "description", None) or "").lower()
    score = 0
    for kw in READ_KEYWORDS:
        if kw in name:
            score += 2
        if kw in desc:
            score += 1
    return score


def _tier(tool: Any) -> int:
    """Preference tier: exact gmail_search > other message-level > thread-level."""
    name = (tool.name or "").lower()
    if name == PREFERRED_SEARCH_TOOL:
        return 2
    if THREAD_PATTERN.search(name):
        return 0
    return 1


def select_read_message_tool(tools: Iterable[Any]) -> Any | None:
    """Resolve the read-message tool: gmail_read_message, else read_message/get_message."""
    by_name = {(t.name or "").lower(): t for t in tools}
    for name in ("gmail_read_message", "read_message", "get_message"):
        if name in by_name:
            return by_name[name]
    return None


def _no_tool(tools: list[Any], reason: str) -> None:
    logger.error("Available tools: %s", ", ".join(t.name for t in tools) or "(none)")
    for t in tools:
        logger.debug("Tool %s schema: %s", t.name, json.dumps(_schema(t), default=str))
    fail(EXIT_NO_TOOL, reason)


def select_tool(
    tools: Iterable[Any], requested: str | None = None, allow_write_tools: bool = False
) -> Any:
    """Choose the read tool. Exits with code 3 when none qualifies."""
    tools = list(tools)
    if requested:
        match = next((t for t in tools if t.name == requested), None)
        if match is None:
            _no_tool(tools, f"Requested tool {requested!r} not found.")
        if is_write_tool(match.name) and not allow_write_tools:
            _no_tool(
                tools,
                f"Tool {requested!r} looks like a write tool; "
                "pass --allow-write-tools to use it.",
            )
        return match

    candidates = [t for t in tools if allow_write_tools or not is_write_tool(t.name)]
    candidates = [t for t in candidates if (t.name or "").lower() not in NON_SEARCH_TOOLS]
    scored = [(_score(t), i, t) for i, t in enumerate(candidates)]
    scored = [s for s in scored if s[0] > 0]
    if not scored:
        _no_tool(tools, "No matching read tool found.")
    best = max(scored, key=lambda s: (_tier(s[2]), s[0], -s[1]))[2]
    logger.debug("Selected tool %s", best.name)
    return best


async def discover_tools(session: Any) -> list[Any]:
    """Call ``session.list_tools()`` and return the tool list."""
    result = await session.list_tools()
    return list(result.tools)


async def discover_and_select(
    session: Any,
    requested: str | None = None,
    allow_write_tools: bool = False,
    list_only: bool = False,
    out: TextIO | None = None,
    all_tools: list[Any] | None = None,
) -> Any | None:
    """List tools; in ``list_only`` mode print them and return ``None``
    (caller exits 0), otherwise return the selected tool. When ``all_tools``
    is given, the discovered tool list is appended to it."""
    tools = await discover_tools(session)
    if all_tools is not None:
        all_tools.extend(tools)
    if list_only:
        print_tools(tools, out)
        return None
    return select_tool(tools, requested, allow_write_tools)
