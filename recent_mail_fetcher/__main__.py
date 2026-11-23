"""CLI entry point for recent-mail-fetcher.

Defines the argparse surface and delegates to ``app.run`` for the pipeline
(config -> connect -> discover -> call -> parse -> render).
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser mirroring the spec's surface."""
    parser = argparse.ArgumentParser(
        prog="recent-mail-fetcher",
        description=(
            "Fetch the most recent emails from a Databricks-hosted Gmail MCP "
            "service. Read-only by construction."
        ),
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print every tool the service exposes (name, description, input "
        "schema) then exit.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit results as a JSON envelope instead of a human-readable table.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Maximum number of messages to fetch. Overrides GMAIL_MCP_COUNT. "
        "Default: 10.",
    )
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="Label or folder to read. Overrides GMAIL_MCP_LABEL. Default: INBOX.",
    )
    parser.add_argument(
        "--tool",
        type=str,
        default=None,
        help="Force a specific tool name (skip heuristic discovery). "
        "Overrides GMAIL_MCP_TOOL.",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="MCP service endpoint URL. Overrides GMAIL_MCP_URL.",
    )
    parser.add_argument(
        "--allow-write-tools",
        action="store_true",
        help="Permit calling tools that match write/send/delete patterns. "
        "Off by default for read-only safety.",
    )
    parser.add_argument(
        "--read-timeout",
        type=float,
        default=None,
        help="HTTP read timeout in seconds. Overrides HTTP_TIMEOUT_READ. "
        "Default: 60.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Increase log detail on stderr.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    from recent_mail_fetcher.app import run

    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
