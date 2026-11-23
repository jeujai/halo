"""Unit tests for centralized exit codes and fail() (Task 4).

Validates: design.md "Exit codes" map (0/2/3/4/5), the FailError/.code control-
flow contract, and that fail() logs a redacted reason to stderr before raising.
"""

from __future__ import annotations

import logging

import pytest

from recent_mail_fetcher.exitcodes import (
    EXIT_AUTH,
    EXIT_NO_TOOL,
    EXIT_PARSE_FAILURE,
    EXIT_SUCCESS,
    EXIT_TOOL_FAILURE,
    FailError,
    fail,
)
from recent_mail_fetcher.log import setup_logging


def test_exit_code_constants_match_design_map():
    assert EXIT_SUCCESS == 0
    assert EXIT_AUTH == 2
    assert EXIT_NO_TOOL == 3
    assert EXIT_TOOL_FAILURE == 4
    assert EXIT_PARSE_FAILURE == 5


def test_config_reexports_exit_auth():
    # config.py must still expose EXIT_AUTH (re-exported from exitcodes).
    from recent_mail_fetcher.config import EXIT_AUTH as cfg_auth

    assert cfg_auth == EXIT_AUTH == 2


def test_failerror_carries_code_and_message():
    err = FailError("boom", EXIT_TOOL_FAILURE)
    assert err.code == EXIT_TOOL_FAILURE
    assert "boom" in str(err)


def test_fail_raises_failerror_with_code():
    with pytest.raises(FailError) as exc_info:
        fail(EXIT_NO_TOOL, "no read tool found")
    assert exc_info.value.code == EXIT_NO_TOOL
    assert "no read tool found" in str(exc_info.value)


def test_fail_logs_redacted_reason_to_stderr(capsys):
    token = "dapi-fail-secret"
    setup_logging(verbose=True, token=token)
    with pytest.raises(FailError):
        fail(EXIT_NO_TOOL, "no tool (auth was Bearer %s)" % token)
    err = capsys.readouterr().err
    assert token not in err
    assert "Bearer ***" in err


def test_fail_logs_even_when_token_not_configured(capsys):
    # Without a known token, the generic bearer regex still masks the header.
    setup_logging(verbose=True, token=None)
    with pytest.raises(FailError):
        fail(EXIT_AUTH, "got Authorization: Bearer dapi-mystery")
    err = capsys.readouterr().err
    assert "dapi-mystery" not in err
    assert "Bearer ***" in err
