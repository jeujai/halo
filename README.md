# Recent-Mail Fetcher

A standalone Python CLI that connects to a Databricks-hosted Gmail MCP service
(Streamable HTTP, bearer-token auth) and prints the most recent emails. It is
read-only: tools that look like write/send/delete operations are blocked unless
you pass `--allow-write-tools`.

Default endpoint:
`https://dbc-870c40d6-c4b7.cloud.databricks.com/ai-gateway/mcp-services/system.ai.gmail`

## Requirements

- Python 3.10 or newer
- A Databricks token (personal access token or service-principal token) that is
  allowed to use the Gmail MCP service. The token must have a scope broader than
  `ai-gateway:read`: MCP calls use HTTP POST, so a token limited to
  `ai-gateway:read` is rejected with HTTP 403 (see Troubleshooting).

## Install

From the project root (the directory containing `pyproject.toml`):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs `mcp` and `httpx` and provides the `recent-mail-fetcher` command.
You can also run it as `python -m recent_mail_fetcher`.

## Configuration

Settings come from environment variables and CLI flags. A flag overrides the
matching environment variable. Empty environment variables are treated as unset.

| Variable | Required | Matching flag | Default |
|----------|----------|---------------|---------|
| `DATABRICKS_TOKEN` | yes | none (env only, to keep it out of shell history) | none |
| `GMAIL_MCP_URL` | no | `--url` | the endpoint above |
| `GMAIL_MCP_LABEL` | no | `--label` | `INBOX` |
| `GMAIL_MCP_COUNT` | no | `--count` | `10` |
| `GMAIL_MCP_TOOL` | no | `--tool` | auto-select (see below) |
| `HTTP_TIMEOUT_READ` | no | `--read-timeout` | `60` (seconds) |

Set the token in your shell:

```bash
export DATABRICKS_TOKEN='<your token>'
```

The application does not load `.env` files itself. If you keep variables in a
file, export them before running, for example
`set -a; . ./.env; set +a` in bash or zsh.

The token is never printed or logged; it is redacted from log output.

## Usage

```
recent-mail-fetcher [--list-tools] [--json] [--count N] [--label NAME]
                    [--tool NAME] [--url URL] [--allow-write-tools]
                    [--read-timeout SECONDS] [-v | --verbose]
```

| Flag | Meaning |
|------|---------|
| `--list-tools` | Print every tool the service exposes (name, description, input schema) and exit 0. |
| `--json` | Print a JSON envelope instead of a table. |
| `--count N` | Number of messages to fetch. |
| `--label NAME` | Label/folder to read. |
| `--tool NAME` | Use this tool instead of auto-selecting one. |
| `--url URL` | Override the MCP endpoint. |
| `--allow-write-tools` | Allow tools whose names match `send`, `create`, `delete`, `update`, `reply`, `forward`, `trash`, `modify`. |
| `--read-timeout SECONDS` | HTTP read timeout. |
| `-v`, `--verbose` | More detail on stderr. |

Results go to stdout only. Logs go to stderr, so `--json` output can be piped.

### Examples

```bash
# 10 most recent messages from INBOX as a table
recent-mail-fetcher

# 5 messages as JSON, piped to jq
recent-mail-fetcher --count 5 --json | jq '.emails[].subject'

# A different label, with a specific tool
recent-mail-fetcher --label STARRED --tool <tool-name>

# Search-based tool (recommended for the Gmail service)
recent-mail-fetcher --tool gmail_search --label INBOX --count 5
```

### Query and label behavior

When the chosen tool has a `query` property and no label-like property (as with
`gmail_search`), `--label` is turned into a Gmail search query:

| `--label` | `query` sent |
|-----------|--------------|
| `INBOX` (default, case-insensitive) | `in:inbox` |
| `STARRED` | `label:STARRED` |
| `My Label` (contains spaces) | `label:"My Label"` |

Use `--tool gmail_search` (or `GMAIL_MCP_TOOL=gmail_search`) to select this
tool explicitly. The count is sent as `max_results` when the tool declares it.

### Search, then hydrate

`gmail_search` may return only message ids. The app handles this in two steps:

1. Call the search tool once.
2. If results lack subject, sender or `received_at`, call `gmail_read_message`
   once per incomplete entry and merge the missing fields. At most `--count`
   reads are made, at most 5 at a time. Complete entries trigger no reads.

If a read fails, that message stays in the output as an id-only entry and a
warning is logged; the run continues. If no read tool is found, id-only entries
are printed with a warning.

Only the first page of results is fetched. If the search returns a cursor
indicating more messages exist (and fewer than `--count` were obtained), a
warning is logged and pagination is not followed.

### Table output

Columns are `SUBJECT`, `SENDER`, `RECEIVED_AT`, `ID`, sorted newest first
(ties broken by id). If there are no messages, it prints `No messages.` and
exits 0. If fewer than `--count` messages exist, all of them are shown.

### JSON output

```json
{
  "service": "system.ai.gmail",
  "fetched_at": "2025-01-20T13:40:00Z",
  "tool": "list_messages",
  "count": 10,
  "emails": [
    {
      "id": "...",
      "subject": "...",
      "sender": "...",
      "received_at": "...",
      "snippet": "...",
      "label_ids": ["INBOX", "UNREAD"],
      "is_unread": true
    }
  ]
}
```

`count` is the requested count. Each email may also contain other fields (such
as `thread_id`, `recipients`, `has_attachments`); fields the service did not
return are `null` or empty.

## First run: discover the service's tools

The exact tool name and input schema exposed by `system.ai.gmail` were an open
question in the requirements (`.kiro/specs/recent-mail-fetcher/requirements.md`,
Open question 1). Resolve it with `--list-tools`:

```bash
recent-mail-fetcher --list-tools
```

Output is one block per tool:

```
<tool name>
  description: <description>
  inputSchema: { ...JSON schema... }
```

Then:

1. Pick the tool that lists or searches messages. Note the property names in
   its `inputSchema`.
2. Check whether the schema has a count property and a label property. The app
   adds `--count` and `--label` to the call only when the schema declares a
   matching property. Recognized count names: `max_results`, `limit`, `count`,
   `page_size`, `maxresults`, `top`. Recognized label names: `label`, `labels`,
   `label_ids`, `labelids`, `label_id`, `labelid`, `folder`, `mailbox`, `box`.
   If the schema has no label property but has `query`, the label is sent as a
   query (see Query and label behavior). Otherwise the value is not sent.
3. Run without `--tool` first. If the auto-selected tool (shown with `-v`) is
   wrong, pin the right one:

   ```bash
   export GMAIL_MCP_TOOL=<tool name>
   # or: recent-mail-fetcher --tool <tool name>
   ```

Auto-selection ignores write-looking tools and picks the tool whose name or
description best matches `list`, `recent`, `search`, `messages`, `emails`
(name matches weigh more than description matches). The other open questions
(runtime target, definition of "most recent", output destination, token source)
are answered in this version as: standalone CLI, newest by received date in
`INBOX` by default, stdout only, and a token from `DATABRICKS_TOKEN`.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success, including an empty result and `--list-tools`. |
| 2 | Auth failure: missing or empty `DATABRICKS_TOKEN`, 401/403, or permission denied. Also returned for invalid numeric config values (for example a non-integer `GMAIL_MCP_COUNT`). |
| 3 | No matching read tool found, the `--tool` name does not exist, or the chosen tool is a write tool and `--allow-write-tools` was not given. |
| 4 | Tool call failed (arguments rejected, or retries exhausted). Also used for unexpected errors. |
| 5 | The result could not be parsed as JSON. Raw text is printed as a fallback. |
| 130 | Interrupted with Ctrl-C. |

## Troubleshooting

**`DATABRICKS_TOKEN is missing or empty` (exit 2).** Export the token in the
same shell you run the command from. No network request is made in this case.

**Authentication failure (exit 2).** The gateway returned 401/403. Check that
the token is valid and not expired, belongs to the right workspace, and has
permission to use the Gmail MCP service. This is not retried.

**HTTP 403 with a valid token (exit 2).** A token limited to the
`ai-gateway:read` scope gets 403 because MCP requests use POST. Create a PAT
with a broader scope and set it as `DATABRICKS_TOKEN`.

**`No matching read tool found` or `Requested tool ... not found` (exit 3).**
The log lists the available tools. Run `--list-tools` and set `--tool` to a
real tool name. If the tool you need has a write-style name, add
`--allow-write-tools`.

**Tool call failed (exit 4).** Transient errors (429, 5xx, connection reset,
read timeout) are retried with exponential backoff, at most 3 attempts. If they
keep failing, check the endpoint with `--url`, your network, and try a larger
`--read-timeout`. If the tool rejects its arguments, the app retries once
without the count argument. Run with `-v` and compare the tool's `inputSchema`
(from `--list-tools`) with the arguments logged.

**`invalidArguments` / missing `query` (exit 4).** The tool reported that a
required `query` argument was missing and listed the valid fields (`cursor`,
`max_results`, `query`). This typically happens when auto-selection picks
`gmail_search_threads` or another tool whose arguments were not filled as
expected. Use `--tool gmail_search`, which accepts `query` and `max_results`
and receives the label as a query.

**Unparseable result (exit 5).** The tool returned text that was not JSON. The
raw text is printed (table mode) and a warning is logged. Run with `-v` and
check which tool was selected; another tool may return structured results.

**Empty output.** `No messages.` means the label has no messages, or the label
name is not what the service expects. Check the label property in the tool's
`inputSchema`.

**Need more detail.** Add `-v`. Logs are on stderr, results on stdout, so you
can redirect them separately: `recent-mail-fetcher -v 2> debug.log`.

## Development

```bash
pip install -e . pytest
pytest
```

A Databricks notebook variant lives in `notebook/recent_mail_fetcher_nb.py`.
