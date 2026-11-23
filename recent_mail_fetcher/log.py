"""Structured, token-redacting stderr logger (Task 4).

All application logging goes to stderr so stdout stays reserved for results
(the JSON envelope or the human-readable table), keeping JSON pipeable. A
redacting formatter scrubs the bearer token from every emitted line so no
secret reaches logs, satisfying requirements.md ("SHALL NOT print, log, or
persist the token") and design.md's redacting-logger requirement.

Usage::

    from recent_mail_fetcher.log import setup_logging, get_logger
    setup_logging(verbose=args.verbose, token=cfg.token)
    logger = get_logger()          # or logging.getLogger(__name__) in modules

The package logger ``recent_mail_fetcher`` owns a single
``StreamHandler(stderr)`` with a :class:`RedactingFormatter`. Child loggers
(e.g. ``recent_mail_fetcher.parser``) propagate to it and inherit the
redaction. ``propagate`` is set ``False`` on the package logger so records
never reach the root logger's ``lastResort`` handler, which would emit
unredacted to stderr. ``setup_logging()`` is idempotent: it clears previously
installed handlers before adding a fresh one, so it can be called again with an
updated token/verbosity (e.g. once the token is known after config load).

This module is importable and side-effect-free at import time: nothing is
configured until :func:`setup_logging` is called explicitly.
"""

from __future__ import annotations

import logging
import re
import sys

LOGGER_NAME = "recent_mail_fetcher"

# One structured line per record on stderr; stdout is never touched.
_DEFAULT_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def redact_text(text: str, token: str | None = None) -> str:
    """Scrub bearer tokens from a string.

    Two layers of defense:

    1. If a specific token is known (passed to :func:`setup_logging`), every
       occurrence of that exact string is replaced with ``***``. This catches
       the token anywhere it appears (bare, in a header, in a URL, ...).
    2. Any ``Authorization: Bearer <creds>`` header or bare ``Bearer <creds>``
       phrase is masked to ``Bearer ***`` regardless of whether the token is
       known, so an unexpected bearer value (e.g. a refreshed token) is still
       scrubbed.

    The known-token replacement runs first; the generic bearer regex then sees
    ``Bearer ***`` (already safe) rather than the real token. The generic regex
    is intentionally broad (any non-space run after "Bearer") because anything
    following "Bearer" in these logs is a credential, not meaningful prose.
    """
    if token:
        text = text.replace(token, "***")
    # Mask "Authorization: Bearer <creds>" headers (any case).
    text = re.sub(
        r"(?i)authorization:\s*bearer\s+\S+", "Authorization: Bearer ***", text
    )
    # Mask any remaining "Bearer <creds>" phrase.
    text = re.sub(r"(?i)\bbearer\s+\S+", "Bearer ***", text)
    return text


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the bearer token from the final rendered line.

    Redaction is applied to the fully formatted string (timestamp + level +
    name + message) so the token cannot hide in any part of the log line. The
    token is captured at :func:`setup_logging` time; when it is ``None`` only
    the generic bearer-pattern redaction applies.
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        token: str | None = None,
    ) -> None:
        super().__init__(fmt or _DEFAULT_FMT, datefmt)
        self._token = token

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return redact_text(rendered, self._token)


def setup_logging(verbose: bool = False, token: str | None = None) -> logging.Logger:
    """Configure the package logger to emit redacted, structured lines to stderr.

    - ``verbose=False`` (default): WARNING and above reach stderr.
    - ``verbose=True``: DEBUG and above (diagnostics: tool selection, retries,
      available tools, raw parse warnings) reach stderr.

    stdout is never written to. The token (when provided, e.g. ``cfg.token``)
    is handed to the formatter so the exact secret is scrubbed in addition to
    the generic bearer-pattern redaction. Idempotent: previously installed
    handlers are cleared first, so re-calling with an updated token/verbosity
    is safe.
    """
    logger = logging.getLogger(LOGGER_NAME)
    level = logging.DEBUG if verbose else logging.WARNING
    logger.setLevel(level)
    logger.propagate = False  # never reach root's lastResort (unredacted)

    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(RedactingFormatter(token=token))
    logger.addHandler(handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger in the package tree.

    With no argument returns the package logger ``recent_mail_fetcher``; with a
    name returns ``recent_mail_fetcher.<name>``. Modules may instead use
    ``logging.getLogger(__name__)`` directly, which yields the same child tree
    and inherits the redacting handler via propagation.
    """
    if name is None:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


# --- convenience helpers ---------------------------------------------------
# Operate on the package logger so a single setup_logging() call configures
# them. Resolved at call time from the logger object (not a captured copy), so
# level changes from setup_logging() take effect.

_logger = logging.getLogger(LOGGER_NAME)


def log_debug(msg: str, *args: object) -> None:
    """Log at DEBUG (visible only under --verbose)."""
    _logger.debug(msg, *args)


def log_info(msg: str, *args: object) -> None:
    """Log at INFO (visible only under --verbose)."""
    _logger.info(msg, *args)


def log_warn(msg: str, *args: object) -> None:
    """Log at WARNING (visible by default)."""
    _logger.warning(msg, *args)


def log_error(msg: str, *args: object) -> None:
    """Log at ERROR (visible by default)."""
    _logger.error(msg, *args)
