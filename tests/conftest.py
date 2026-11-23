"""Shared fixtures: reset the package logger around each test.

Keeps tests hermetic by clearing handlers and restoring default propagation on
the ``recent_mail_fetcher`` logger before and after every test. Tests that use
logging call ``setup_logging`` inside the test body; this fixture just prevents
state from one test (or a prior run of the CLI) leaking into another.
"""

from __future__ import annotations

import logging

import pytest

from recent_mail_fetcher.log import LOGGER_NAME


@pytest.fixture(autouse=True)
def _clean_package_logger() -> None:
    lg = logging.getLogger(LOGGER_NAME)
    lg.handlers.clear()
    lg.setLevel(logging.NOTSET)
    lg.propagate = True
    yield
    lg.handlers.clear()
    lg.setLevel(logging.NOTSET)
    lg.propagate = True
