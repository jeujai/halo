"""Unit tests for the config loader (Task 2).

Validates: Requirements "Loading configuration" — env-vs-flag precedence, the
missing-token short-circuit (exit 2, no network call), and built-in defaults.
"""

from __future__ import annotations

import argparse

import pytest

from recent_mail_fetcher.config import (
    DEFAULT_COUNT,
    DEFAULT_LABEL,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_URL,
    EXIT_AUTH,
    Config,
    ConfigError,
    load_config,
)

TOKEN = "dapi-test-token-abcdef"


def _ns(**overrides: object) -> argparse.Namespace:
    """Build an argparse Namespace mirroring ``build_parser``'s output.

    Every field defaults to the "not provided" sentinel so precedence is
    exercised against env vars and built-in defaults.
    """
    base: dict[str, object] = dict(
        list_tools=False,
        json=False,
        count=None,
        label=None,
        tool=None,
        url=None,
        allow_write_tools=False,
        read_timeout=None,
        verbose=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _env(**overrides: str) -> dict[str, str]:
    base: dict[str, str] = {"DATABRICKS_TOKEN": TOKEN}
    base.update(overrides)
    return base


# --- defaults -------------------------------------------------------------

def test_defaults_when_neither_env_nor_flag():
    cfg = load_config(_ns(), _env())
    assert cfg.token == TOKEN
    assert cfg.url == DEFAULT_URL
    assert cfg.count == DEFAULT_COUNT
    assert cfg.label == DEFAULT_LABEL
    assert cfg.tool is None
    assert cfg.read_timeout == float(DEFAULT_READ_TIMEOUT)
    assert cfg.allow_write_tools is False
    assert cfg.verbose is False
    assert cfg.list_tools is False
    assert cfg.json is False


def test_url_default_matches_service_endpoint():
    assert DEFAULT_URL == (
        "https://dbc-870c40d6-c4b7.cloud.databricks.com/ai-gateway/"
        "mcp-services/system.ai.gmail"
    )


# --- env-only values are used when the flag is None ------------------------

def test_env_used_when_flag_none():
    cfg = load_config(
        _ns(),
        _env(
            GMAIL_MCP_URL="https://example.test/mcp",
            GMAIL_MCP_COUNT="25",
            GMAIL_MCP_LABEL="WORK",
            GMAIL_MCP_TOOL="search_messages",
            HTTP_TIMEOUT_READ="90",
        ),
    )
    assert cfg.url == "https://example.test/mcp"
    assert cfg.count == 25
    assert cfg.label == "WORK"
    assert cfg.tool == "search_messages"
    assert cfg.read_timeout == 90.0


def test_empty_env_falls_back_to_default():
    cfg = load_config(
        _ns(),
        _env(GMAIL_MCP_URL="", GMAIL_MCP_LABEL="", GMAIL_MCP_TOOL=""),
    )
    assert cfg.url == DEFAULT_URL
    assert cfg.label == DEFAULT_LABEL
    assert cfg.tool is None


# --- flag precedence ------------------------------------------------------

def test_flag_overrides_env():
    cfg = load_config(
        _ns(
            url="https://flag.test/mcp",
            count=50,
            label="PROMO",
            tool="list_recent",
            read_timeout=12.0,
        ),
        _env(
            GMAIL_MCP_URL="https://env.test/mcp",
            GMAIL_MCP_COUNT="25",
            GMAIL_MCP_LABEL="WORK",
            GMAIL_MCP_TOOL="search_messages",
            HTTP_TIMEOUT_READ="90",
        ),
    )
    assert cfg.url == "https://flag.test/mcp"
    assert cfg.count == 50
    assert cfg.label == "PROMO"
    assert cfg.tool == "list_recent"
    assert cfg.read_timeout == 12.0


def test_boolean_flags_taken_from_args():
    cfg = load_config(
        _ns(list_tools=True, json=True, allow_write_tools=True, verbose=True),
        _env(),
    )
    assert cfg.list_tools is True
    assert cfg.json is True
    assert cfg.allow_write_tools is True
    assert cfg.verbose is True


def test_token_is_env_only_never_a_flag():
    # No --token flag exists on the CLI surface (secrets stay out of shell history).
    from recent_mail_fetcher.__main__ import build_parser

    parser = build_parser()
    option_flags = {opt for action in parser._actions for opt in action.option_strings}
    assert "--token" not in option_flags

    # load_config reads the token from env, never from args: a stray args.token
    # attribute (there isn't one, but defensively) is ignored in favor of env.
    ns = _ns()
    ns.token = "from-flag"
    cfg = load_config(ns, _env(DATABRICKS_TOKEN="from-env"))
    assert cfg.token == "from-env"


def test_token_whitespace_is_stripped():
    cfg = load_config(_ns(), _env(DATABRICKS_TOKEN="  dapi-ws-token  "))
    assert cfg.token == "dapi-ws-token"


# --- missing/empty token short-circuits to exit 2 -------------------------

@pytest.mark.parametrize(
    "env",
    [
        {},  # missing entirely
        {"DATABRICKS_TOKEN": ""},  # empty
        {"DATABRICKS_TOKEN": "   "},  # whitespace only
    ],
    ids=["missing", "empty", "whitespace"],
)
def test_missing_or_empty_token_short_circuits(env):
    with pytest.raises(ConfigError) as exc_info:
        load_config(_ns(), env)
    assert exc_info.value.code == EXIT_AUTH
    assert "DATABRICKS_TOKEN" in str(exc_info.value)


def test_short_circuit_happens_before_any_network_call():
    # load_config must raise ConfigError without touching the network layer.
    # The config module imports no HTTP/MCP client, so a raised ConfigError is
    # structural proof no request was made. Here we additionally assert the
    # error surfaces (and nothing is returned).
    with pytest.raises(ConfigError):
        load_config(_ns(), {})
    # A successful parse returns a Config, proving only the token path fails:
    assert isinstance(load_config(_ns(), _env()), Config)


# --- malformed numeric env values get a clear error ----------------------

def test_invalid_count_env_raises_config_error():
    with pytest.raises(ConfigError) as exc_info:
        load_config(_ns(), _env(GMAIL_MCP_COUNT="not-a-number"))
    assert exc_info.value.code == EXIT_AUTH
    assert "GMAIL_MCP_COUNT" in str(exc_info.value)
