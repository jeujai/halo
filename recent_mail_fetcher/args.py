"""Schema-driven tool argument construction (Task 9).

Builds ``tools/call`` arguments from a tool's ``inputSchema``. ``count`` and
``label`` are injected only when the schema declares a matching property
(by a known alias); undeclared fields are never added.
"""

from __future__ import annotations

from typing import Any

from recent_mail_fetcher.exitcodes import EXIT_TOOL_FAILURE, fail

# Priority order: first declared property wins (names compared normalized).
COUNT_ALIASES: tuple[str, ...] = (
    "max_results", "limit", "count", "page_size", "maxresults", "top",
)
LABEL_ALIASES: tuple[str, ...] = (
    "label", "labels", "label_ids", "labelids", "label_id", "labelid",
    "folder", "mailbox", "box",
)


def _norm(name: str) -> str:
    return name.lower().replace("-", "_")


def _properties(schema: Any) -> dict[str, Any]:
    if isinstance(schema, dict):
        props = schema.get("properties")
        if isinstance(props, dict):
            return props
    return {}


def find_property(schema: Any, aliases: tuple[str, ...]) -> str | None:
    """Return the declared property name matching the first alias hit, else None."""
    props = _properties(schema)
    by_norm = {_norm(k): k for k in props}
    for alias in aliases:
        if alias in by_norm:
            return by_norm[alias]
    return None


def _types(prop: Any) -> list[str]:
    if not isinstance(prop, dict):
        return []
    t = prop.get("type")
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str)]
    for key in ("anyOf", "oneOf"):
        if isinstance(prop.get(key), list):
            out: list[str] = []
            for sub in prop[key]:
                out.extend(_types(sub))
            return out
    return []


def _count_value(prop: Any, count: int) -> Any:
    types = _types(prop)
    if types and "integer" not in types and "number" not in types:
        if "string" in types:
            return str(count)
    return count


def _label_value(prop: Any, label: str) -> Any:
    types = _types(prop)
    if "array" in types and "string" not in types:
        return [label]
    return label


def build_query(label: str | None) -> str:
    """Gmail query for a label: INBOX -> ``in:inbox``, else ``label:<LABEL>``."""
    lab = (label or "INBOX").strip() or "INBOX"
    if lab.upper() == "INBOX":
        return "in:inbox"
    if any(ch.isspace() for ch in lab) or '"' in lab:
        lab = '"' + lab.replace('"', '\\"') + '"'
    return f"label:{lab}"


def _check_required(schema: Any, args: dict[str, Any]) -> None:
    """Exit 4 (before any call) naming the first required property left unfilled."""
    required = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(required, list):
        return
    for name in required:
        if isinstance(name, str) and name not in args:
            fail(
                EXIT_TOOL_FAILURE,
                f"Tool requires parameter {name!r}, which cannot be filled from the available options",
            )


def build_args(schema: Any, count: int | None = None, label: str | None = None) -> dict[str, Any]:
    """Build the argument map for a tool call from its ``inputSchema``."""
    props = _properties(schema)
    args: dict[str, Any] = {}
    if count is not None:
        name = find_property(schema, COUNT_ALIASES)
        if name:
            args[name] = _count_value(props[name], count)
    name = find_property(schema, LABEL_ALIASES)
    if name:
        if label:
            args[name] = _label_value(props[name], label)
    else:
        qname = find_property(schema, ("query",))
        if qname:
            args[qname] = build_query(label)
    _check_required(schema, args)
    return args


def tool_input_schema(tool: Any) -> Any:
    """Return a tool's input schema.

    ``mcp`` 1.x exposes it as ``Tool.inputSchema``; ``mcp`` 2.x renamed the
    attribute to ``Tool.input_schema`` (``inputSchema`` is only the wire alias).
    """
    schema = getattr(tool, "inputSchema", None)
    if schema is None:
        schema = getattr(tool, "input_schema", None)
    return schema


def build_args_for_tool(tool: Any, count: int | None = None, label: str | None = None) -> dict[str, Any]:
    """Convenience wrapper taking an MCP ``Tool``."""
    return build_args(tool_input_schema(tool), count, label)
