"""Configuration loading for recent-mail-fetcher.

Merges environment variables with CLI flags (CLI flags win), validates the
Databricks token is present, and supplies defaults. The auth-failure exit code
is imported from the centralized ``exitcodes`` module (task #4) so the codebase
has a single source of truth for exit codes; it is re-exported here so existing
callers and tests that import ``EXIT_AUTH`` from ``config`` keep working.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from recent_mail_fetcher.exitcodes import EXIT_AUTH

# Service endpoint used when GMAIL_MCP_URL is not configured.
DEFAULT_URL = (
    "https://dbc-870c40d6-c4b7.cloud.databricks.com/ai-gateway/"
    "mcp-services/system.ai.gmail"
)
DEFAULT_COUNT = 10
DEFAULT_LABEL = "INBOX"
DEFAULT_READ_TIMEOUT = 60

# Auth-failure exit code is defined once in exitcodes.py (task #4) and imported
# above as the single source of truth, then re-exported from this module so the
# dispatch layer (task #11) and existing callers/tests can import ``EXIT_AUTH``
# from ``config`` unchanged.


class ConfigError(Exception):
    """Raised when configuration is invalid (e.g. a missing token).

    Carries the process exit code the caller should use. The default mirrors
    the auth-failure code so the CLI dispatcher can ``sys.exit(err.code)``.
    """

    def __init__(self, message: str, code: int = EXIT_AUTH) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Config:
    """Merged application configuration consumed by the dispatch layer."""

    token: str
    url: str
    count: int
    label: str
    tool: str | None
    allow_write_tools: bool
    read_timeout: float
    verbose: bool
    list_tools: bool
    json: bool


def _resolve(flag_value: Any, env_value: str | None, default: Any) -> Any:
    """Pick a flag (if explicitly given) over env over default.

    A flag counts as "given" when it is not ``None`` — argparse is configured
    with ``default=None`` precisely so we can distinguish "not provided" from
    "provided as empty". An environment value counts as "set" only when it is a
    non-empty string; a missing or empty env var falls through to the default.
    """
    if flag_value is not None:
        return flag_value
    if env_value:
        return env_value
    return default


def _parse_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid integer for {label}: {value!r}") from exc


def _parse_float(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid number for {label}: {value!r}") from exc


def load_config(args: Any, env: Mapping[str, str] | None = None) -> Config:
    """Merge environment variables and CLI flags into a validated ``Config``.

    Precedence for each option is: CLI flag (when explicitly provided) >
    environment variable (when set and non-empty) > built-in default.
    ``DATABRICKS_TOKEN`` is env-only — it is never exposed as a CLI flag — and
    must be non-empty; a missing or blank token raises :class:`ConfigError`
    (exit code 2) before any network call could be made.

    ``args`` is the argparse ``Namespace`` produced by ``build_parser``. ``env``
    defaults to ``os.environ``; tests pass an explicit mapping for hermetic
    behavior.
    """
    env_map: Mapping[str, str] = os.environ if env is None else env

    # Token is env-only. Strip so a stray trailing newline doesn't silently break
    # auth; a missing/empty/whitespace-only token is a hard, fast failure.
    token = (env_map.get("DATABRICKS_TOKEN") or "").strip()
    if not token:
        raise ConfigError(
            "DATABRICKS_TOKEN is missing or empty; set it in the environment "
            "before running recent-mail-fetcher."
        )

    url = _resolve(getattr(args, "url", None), env_map.get("GMAIL_MCP_URL"), DEFAULT_URL)

    count_raw = _resolve(getattr(args, "count", None), env_map.get("GMAIL_MCP_COUNT"), None)
    count = DEFAULT_COUNT if count_raw is None else _parse_int(count_raw, "GMAIL_MCP_COUNT/--count")

    label = _resolve(getattr(args, "label", None), env_map.get("GMAIL_MCP_LABEL"), DEFAULT_LABEL)

    tool = _resolve(getattr(args, "tool", None), env_map.get("GMAIL_MCP_TOOL"), None)
    tool = tool or None  # an empty/blank tool name means "no override"

    timeout_raw = _resolve(
        getattr(args, "read_timeout", None), env_map.get("HTTP_TIMEOUT_READ"), None
    )
    if timeout_raw is None:
        read_timeout = float(DEFAULT_READ_TIMEOUT)
    else:
        read_timeout = _parse_float(timeout_raw, "HTTP_TIMEOUT_READ/--read-timeout")

    return Config(
        token=token,
        url=url,
        count=count,
        label=label,
        tool=tool,
        allow_write_tools=bool(getattr(args, "allow_write_tools", False)),
        read_timeout=read_timeout,
        verbose=bool(getattr(args, "verbose", False)),
        list_tools=bool(getattr(args, "list_tools", False)),
        json=bool(getattr(args, "json", False)),
    )
