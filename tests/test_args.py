from recent_mail_fetcher.args import build_args


def schema(**props):
    return {"type": "object", "properties": props}


def test_both_declared():
    s = schema(count={"type": "integer"}, label={"type": "string"})
    assert build_args(s, 5, "INBOX") == {"count": 5, "label": "INBOX"}


def test_neither_declared_adds_nothing():
    s = schema(subject={"type": "string"})
    assert build_args(s, 5, "INBOX") == {}


def test_missing_or_empty_schema():
    assert build_args(None, 5, "INBOX") == {}
    assert build_args({}, 5, "INBOX") == {}


def test_aliases_and_array_label():
    s = schema(maxResults={"type": "integer"}, label_ids={"type": "array", "items": {"type": "string"}})
    assert build_args(s, 3, "STARRED") == {"maxResults": 3, "label_ids": ["STARRED"]}


def test_only_count_declared():
    s = schema(limit={"type": "integer"})
    assert build_args(s, 7, "INBOX") == {"limit": 7}


def test_string_count_and_none_values_skipped():
    s = schema(page_size={"type": "string"}, folder={"type": "string"})
    assert build_args(s, 4, None) == {"page_size": "4"}


def test_only_label_declared():
    s = schema(label={"type": "string"})
    assert build_args(s, 5, "INBOX") == {"label": "INBOX"}


def test_never_adds_undeclared_fields():
    s = schema(query={"type": "string"}, count={"type": "integer"})
    result = build_args(s, 5, "INBOX")
    assert set(result) <= set(s["properties"])
    assert result == {"count": 5, "query": "in:inbox"}


def test_gmail_search_threads_schema():
    s = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}, "cursor": {"type": "string"}},
        "required": ["query"],
    }
    assert build_args(s, 10, "INBOX") == {"query": "in:inbox", "max_results": 10}


def test_query_label_and_quoting():
    s = schema(query={"type": "string"})
    assert build_args(s, None, "Work") == {"query": "label:Work"}
    assert build_args(s, None, "My Label") == {"query": 'label:"My Label"'}


def test_count_alias_priority():
    s = schema(top={"type": "integer"}, limit={"type": "integer"}, max_results={"type": "integer"})
    assert build_args(s, 2, None) == {"max_results": 2}


def test_unfillable_required_exits_4_and_names_property():
    import pytest
    from recent_mail_fetcher.exitcodes import EXIT_TOOL_FAILURE, FailError

    s = {"type": "object", "properties": {"query": {"type": "string"}, "foo": {"type": "string"}}, "required": ["query", "foo"]}
    with pytest.raises(FailError) as ei:
        build_args(s, 5, "INBOX")
    assert ei.value.code == EXIT_TOOL_FAILURE
    assert "foo" in str(ei.value)


# --- Gmail search compatibility (task 19) ------------------------------------

import pytest  # noqa: E402

from recent_mail_fetcher.args import build_query  # noqa: E402
from recent_mail_fetcher.exitcodes import EXIT_TOOL_FAILURE, FailError  # noqa: E402


@pytest.mark.parametrize(
    "label,expected",
    [
        ("INBOX", "in:inbox"),
        ("inbox", "in:inbox"),
        (None, "in:inbox"),
        ("STARRED", "label:STARRED"),
        ("My Work", 'label:"My Work"'),
    ],
)
def test_build_query(label, expected):
    assert build_query(label) == expected


def test_query_set_from_label_when_no_label_property():
    s = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    assert build_args(s, 5, "My Work") == {"query": 'label:"My Work"'}


def test_label_property_wins_over_query():
    s = schema(query={"type": "string"}, label={"type": "string"})
    assert build_args(s, None, "INBOX") == {"label": "INBOX"}


@pytest.mark.parametrize(
    "alias", ["max_results", "limit", "count", "page_size", "maxResults", "top"]
)
def test_each_count_alias(alias):
    assert build_args(schema(**{alias: {"type": "integer"}}), 3, None) == {alias: 3}


def test_count_alias_priority_order():
    s = schema(top={"type": "integer"}, limit={"type": "integer"}, max_results={"type": "integer"})
    assert build_args(s, 3, None) == {"max_results": 3}
    s = schema(top={"type": "integer"}, count={"type": "integer"})
    assert build_args(s, 3, None) == {"count": 3}


def test_unfilled_required_property_exits_4_naming_it():
    s = {"type": "object", "properties": {"query": {"type": "string"}, "folder_id": {"type": "string"}},
         "required": ["query", "folder_id"]}
    with pytest.raises(FailError) as ei:
        build_args(s, 5, "INBOX")
    assert ei.value.code == EXIT_TOOL_FAILURE == 4
    assert "folder_id" in str(ei.value)


def test_required_property_filled_passes():
    s = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    assert build_args(s, 5, "INBOX") == {"query": "in:inbox"}
