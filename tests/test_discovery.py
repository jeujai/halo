import asyncio
import io
from types import SimpleNamespace

import pytest

from recent_mail_fetcher.discovery import (
    discover_and_select,
    is_write_tool,
    select_tool,
)
from recent_mail_fetcher.exitcodes import EXIT_NO_TOOL, FailError


def tool(name, description=None, schema=None):
    return SimpleNamespace(name=name, description=description, inputSchema=schema or {})


class FakeSession:
    def __init__(self, tools):
        self._tools = tools

    async def list_tools(self):
        return SimpleNamespace(tools=self._tools)


def test_heuristic_prefers_read_tool():
    tools = [tool("get_profile"), tool("list_recent_messages"), tool("search_emails")]
    assert select_tool(tools).name == "list_recent_messages"


def test_heuristic_matches_description():
    tools = [tool("foo"), tool("fetch", "List the latest emails")]
    assert select_tool(tools).name == "fetch"


def test_tie_prefers_first():
    tools = [tool("list_a"), tool("list_b")]
    assert select_tool(tools).name == "list_a"


def test_tool_override_wins_over_heuristic():
    tools = [tool("list_messages"), tool("custom_fetch")]
    assert select_tool(tools, requested="custom_fetch").name == "custom_fetch"


def test_tool_override_unknown_exits_3():
    with pytest.raises(FailError) as ei:
        select_tool([tool("list_messages")], requested="nope")
    assert ei.value.code == EXIT_NO_TOOL == 3


def test_write_tools_skipped_by_heuristic():
    tools = [tool("send_message", "list and send messages"), tool("search_messages")]
    assert select_tool(tools).name == "search_messages"


def test_only_write_tools_exits_3():
    with pytest.raises(FailError) as ei:
        select_tool([tool("send_email"), tool("delete_messages")])
    assert ei.value.code == 3


def test_write_tool_override_blocked_without_flag():
    with pytest.raises(FailError) as ei:
        select_tool([tool("send_email")], requested="send_email")
    assert ei.value.code == 3


def test_write_tool_override_allowed_with_flag():
    t = tool("send_email")
    assert select_tool([t], requested="send_email", allow_write_tools=True) is t


def test_is_write_tool():
    assert is_write_tool("Delete_Message")
    assert not is_write_tool("list_messages")


def test_no_match_exits_3_and_logs_available_tools(caplog):
    with caplog.at_level("ERROR"):
        with pytest.raises(FailError) as ei:
            select_tool([tool("ping"), tool("status")])
    assert ei.value.code == 3
    assert "ping, status" in caplog.text


def test_no_tools_exits_3():
    with pytest.raises(FailError) as ei:
        select_tool([])
    assert ei.value.code == 3


def test_list_tools_output():
    tools = [tool("list_messages", "Lists mail", {"type": "object"}), tool("ping")]
    out = io.StringIO()
    result = asyncio.run(discover_and_select(FakeSession(tools), list_only=True, out=out))
    assert result is None
    text = out.getvalue()
    assert "list_messages" in text and "Lists mail" in text
    assert '"type": "object"' in text
    assert "ping" in text and "(no description)" in text


def test_discover_and_select_returns_tool():
    tools = [tool("ping"), tool("list_messages")]
    chosen = asyncio.run(discover_and_select(FakeSession(tools)))
    assert chosen.name == "list_messages"


def test_discover_and_select_no_match_exits_3():
    with pytest.raises(FailError) as ei:
        asyncio.run(discover_and_select(FakeSession([tool("ping")])))
    assert ei.value.code == 3


# --- Gmail search compatibility (task 18) ------------------------------------

from recent_mail_fetcher.discovery import select_read_message_tool  # noqa: E402

GMAIL_TOOLS = [
    "gmail_search_threads", "gmail_get_thread", "gmail_read_message", "gmail_get_attachment",
    "gmail_list_labels", "gmail_list_drafts", "gmail_label_message", "gmail_search",
]


def test_gmail_search_ranked_first():
    tools = [tool(n) for n in GMAIL_TOOLS]
    assert select_tool(tools).name == "gmail_search"


def test_thread_tool_chosen_only_when_nothing_else_matches():
    tools = [tool(n) for n in GMAIL_TOOLS if n != "gmail_search"]
    assert select_tool(tools).name == "gmail_search_threads"


def test_other_message_level_beats_thread_level():
    tools = [tool("gmail_search_threads"), tool("search_messages")]
    assert select_tool(tools).name == "search_messages"


@pytest.mark.parametrize("name", ["gmail_label_message", "gmail_unlabel_message"])
def test_label_tools_are_write_tools(name):
    assert is_write_tool(name)
    with pytest.raises(FailError) as ei:
        select_tool([tool(name)], requested=name)
    assert ei.value.code == EXIT_NO_TOOL


def test_non_search_tools_excluded_from_search_role():
    tools = [tool("gmail_read_message", "read messages"), tool("gmail_list_labels"),
             tool("gmail_list_drafts"), tool("gmail_get_attachment", "list messages")]
    with pytest.raises(FailError) as ei:
        select_tool(tools)
    assert ei.value.code == EXIT_NO_TOOL


def test_tool_override_still_wins_over_gmail_search():
    tools = [tool(n) for n in GMAIL_TOOLS]
    assert select_tool(tools, requested="gmail_search_threads").name == "gmail_search_threads"


def test_read_message_tool_resolution():
    assert select_read_message_tool([tool("get_message"), tool("gmail_read_message")]).name == "gmail_read_message"
    assert select_read_message_tool([tool("get_message"), tool("read_message")]).name == "read_message"
    assert select_read_message_tool([tool("get_message")]).name == "get_message"
    assert select_read_message_tool([tool("gmail_search")]) is None
