"""Unit tests for the redacting logger (Task 4).

Validates: requirements.md "Authenticating to the gateway" (SHALL NOT print,
log, or persist the token) and design.md's redacting logger — logging a bearer
token emits "Bearer ***" and no secret reaches output. Also covers verbosity
gating, stderr-only output, and setup idempotency.

Validates: Requirements (Authenticating to the gateway; Output/observability).
"""

from __future__ import annotations

import logging

from recent_mail_fetcher.log import (
    LOGGER_NAME,
    RedactingFormatter,
    get_logger,
    log_debug,
    log_error,
    log_info,
    log_warn,
    redact_text,
    setup_logging,
)


# --- redact_text (pure) ----------------------------------------------------

def test_redact_known_token_in_bearer_phrase():
    assert redact_text("Bearer dapi-abc123", "dapi-abc123") == "Bearer ***"


def test_redact_known_bare_token():
    assert redact_text("token=dapi-abc123 done", "dapi-abc123") == "token=*** done"


def test_redact_known_token_in_authorization_header():
    assert (
        redact_text("Authorization: Bearer dapi-abc123", "dapi-abc123")
        == "Authorization: Bearer ***"
    )


def test_redact_generic_bearer_header_without_known_token():
    assert redact_text("Authorization: Bearer someopaquevalue", None) == (
        "Authorization: Bearer ***"
    )


def test_redact_generic_bearer_phrase_without_known_token():
    assert redact_text("Bearer someopaquevalue", None) == "Bearer ***"


def test_redact_case_insensitive_bearer():
    assert redact_text("bearer xyz", None) == "Bearer ***"
    assert redact_text("AUTHORIZATION: BEARER xyz", None) == "Authorization: Bearer ***"


def test_redact_leaves_innocent_text_unchanged():
    assert redact_text("no secrets here", None) == "no secrets here"
    # "Bearer" not followed by whitespace+token is left alone.
    assert redact_text("the 'Bearer' scheme", None) == "the 'Bearer' scheme"


def test_redact_known_token_absent_is_noop_for_that_token():
    # A token arg that doesn't appear -> only the generic bearer regex applies.
    assert redact_text("hello world", "not-present") == "hello world"


# --- RedactingFormatter ----------------------------------------------------

def test_formatter_redacts_full_line():
    fmt = RedactingFormatter(token="dapi-xyz")
    record = logging.LogRecord(
        name="recent_mail_fetcher.x",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Authorization: Bearer %s",
        args=("dapi-xyz",),
        exc_info=None,
    )
    rendered = fmt.format(record)
    assert "dapi-xyz" not in rendered
    assert "Bearer ***" in rendered


def test_formatter_redacts_without_known_token():
    fmt = RedactingFormatter(token=None)
    record = logging.LogRecord(
        name="recent_mail_fetcher.x",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Authorization: Bearer dapi-unknown",
        args=(),
        exc_info=None,
    )
    rendered = fmt.format(record)
    assert "dapi-unknown" not in rendered
    assert "Bearer ***" in rendered


# --- setup_logging end-to-end (stderr capture) -----------------------------

def test_setup_logging_redacts_known_token_on_stderr(capsys):
    token = "dapi-super-secret-999"
    setup_logging(verbose=True, token=token)
    log_error("Authorization: Bearer %s", token)
    err = capsys.readouterr().err
    assert token not in err
    assert "Bearer ***" in err


def test_setup_logging_redacts_bare_token_on_stderr(capsys):
    token = "dapi-bare-42"
    setup_logging(verbose=True, token=token)
    log_error("connecting with token=%s", token)
    err = capsys.readouterr().err
    assert token not in err
    assert "***" in err


def test_setup_logging_generic_redaction_without_known_token(capsys):
    setup_logging(verbose=True, token=None)
    log_error("Authorization: Bearer dapi-unknown")
    err = capsys.readouterr().err
    assert "dapi-unknown" not in err
    assert "Bearer ***" in err


def test_setup_logging_never_writes_to_stdout(capsys):
    setup_logging(verbose=True, token="x")
    log_error("err")
    log_warn("w")
    log_info("i")
    log_debug("d")
    out = capsys.readouterr().out
    assert out == ""


def test_verbose_emits_debug(capsys):
    setup_logging(verbose=True)
    log_debug("debug-line")
    err = capsys.readouterr().err
    assert "debug-line" in err


def test_non_verbose_suppresses_debug(capsys):
    setup_logging(verbose=False)
    log_debug("debug-line")
    log_warn("warn-line")
    err = capsys.readouterr().err
    assert "debug-line" not in err
    assert "warn-line" in err


def test_setup_logging_idempotent_single_line(capsys):
    setup_logging(verbose=True, token="t1")
    setup_logging(verbose=True, token="t2")  # second call must replace, not stack
    log_error("only-once")
    err = capsys.readouterr().err
    assert err.count("only-once") == 1


def test_get_logger_returns_package_tree():
    assert get_logger().name == LOGGER_NAME
    assert get_logger("parser").name == f"{LOGGER_NAME}.parser"


def test_propagate_disabled_after_setup():
    setup_logging(verbose=False)
    assert logging.getLogger(LOGGER_NAME).propagate is False
