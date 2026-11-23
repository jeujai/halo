"""Centralized process exit codes and a small fail() helper (Task 4).

Single source of truth for the exit-code map in design.md ("Exit codes") and
the error-handling sections of requirements.md. Other modules import these
named constants instead of hard-coding integers, so the codes live in one
auditable place. ``config.py`` imports ``EXIT_AUTH`` from here (and re-exports
it) so the codebase no longer defines the auth code in two spots.

``fail(code, message)`` pairs a logged reason with a control-flow signal: it
logs the message at ERROR level, then raises :class:`FailError` carrying the
exit code. This mirrors how ``config.ConfigError`` carries a ``.code`` for the
CLI dispatcher (task #11) to ``sys.exit(err.code)``. The dispatcher catches
``ConfigError`` and ``FailError``; for ``FailError`` it only exits (``fail()``
already logged the reason), for ``ConfigError`` it logs ``str(err)`` then exits,
so every failure path emits exactly one error line.

The log call inside ``fail()`` uses stdlib ``logging.getLogger(__name__)`` (a
child of the ``recent_mail_fetcher`` logger). Redaction of the bearer token is
a cross-cutting concern applied by the handler ``log.setup_logging()`` installs
on the parent logger, so even an error message that accidentally contains the
token is scrubbed before it reaches stderr. The application calls
``setup_logging()`` with the token right after config load, before any
``fail()`` is invoked, so redaction is always active when ``fail()`` runs.
"""

from __future__ import annotations

import logging

# Exit code constants — mirror design.md "Exit codes".
EXIT_SUCCESS = 0
"""Success, including an empty result."""

EXIT_AUTH = 2
"""Auth failure: missing/invalid token, 401/403, or permission denied."""

EXIT_NO_TOOL = 3
"""No matching read tool found during discovery."""

EXIT_TOOL_FAILURE = 4
"""Tool call failed (rejected args, or retries exhausted)."""

EXIT_PARSE_FAILURE = 5
"""Result not parseable as JSON after the raw-text fallback was attempted."""


class FailError(Exception):
    """Raised by :func:`fail` to signal a process exit with a specific code.

    Carries the exit code the CLI dispatcher should use, mirroring
    ``config.ConfigError``'s ``.code`` attribute, so the dispatcher can do
    ``sys.exit(err.code)`` without hard-coding integers. The human-readable
    message is available via ``str(err)`` and is also logged by :func:`fail`.
    """

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def fail(code: int, message: str) -> None:
    """Log an error message and raise :class:`FailError` carrying the exit code.

    The message is logged at ERROR level via this module's logger (a child of
    ``recent_mail_fetcher``); the redacting formatter installed by
    :func:`log.setup_logging` scrubs any bearer token from the emitted line.
    Then ``FailError(message, code)`` is raised so the CLI dispatcher can exit
    with ``code``. Callers that want to log extra diagnostic context (e.g. the
    available tool list before exiting ``EXIT_NO_TOOL``) should log that
    context at DEBUG/INFO before calling :func:`fail`.
    """
    logging.getLogger(__name__).error(message)
    raise FailError(message, code)
