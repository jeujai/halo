# Recent-Mail Fetcher — Requirements

## Context

A standalone Python application that connects to the Databricks-hosted Gmail MCP service and retrieves the 10 most recent emails, read-only.

- **MCP endpoint:** `https://dbc-870c40d6-c4b7.cloud.databricks.com/ai-gateway/mcp-services/system.ai.gmail`
- **Transport:** Streamable HTTP.
- **Auth:** Databricks bearer token (PAT or service-principal token).

The exact tool name and input schema the service exposes are not yet confirmed; requirements are written to be tool-discovery-driven and must be revisited once the tool list is known (see Open questions).

## User stories

1. As a developer, I want to run one command that prints my 10 most recent emails so I can review new mail quickly.
2. As a developer, I want JSON output so I can pipe the results into other tools.
3. As a developer, I want to inspect the service's available tools so I can configure the correct read tool.
4. As an operator, I want the app to authenticate with a token from the environment so secrets are never committed or logged.

## Configuration & Authentication

### Loading configuration

WHEN the application starts
THE SYSTEM SHALL read `DATABRICKS_TOKEN` and option values from environment variables and CLI flags, with CLI flags taking precedence over environment variables.

WHEN `DATABRICKS_TOKEN` is missing or empty
THE SYSTEM SHALL fail with a clear error message and exit code 2 without making any network request.

WHEN `GMAIL_MCP_URL` is not set
THE SYSTEM SHALL default to `https://dbc-870c40d6-c4b7.cloud.databricks.com/ai-gateway/mcp-services/system.ai.gmail`.

WHEN `GMAIL_MCP_COUNT` is not set and `--count` is not supplied
THE SYSTEM SHALL default to 10.

### Authenticating to the gateway

WHEN establishing the MCP connection
THE SYSTEM SHALL send an `Authorization: Bearer <token>` header and SHALL NOT print, log, or persist the token.

## Connection & tool discovery

### Establishing the MCP session

WHEN connecting to the service
THE SYSTEM SHALL open a Streamable HTTP MCP client and initialize the MCP `ClientSession`.

WHEN the gateway returns 401 or 403
THE SYSTEM SHALL report an authentication failure and exit code 2 without retrying.

WHEN the gateway returns 429, a 5xx, or a connection reset
THE SYSTEM SHALL retry with exponential backoff up to 3 times and, if still failing, exit code 4.

WHEN a read exceeds the configured HTTP timeout
THE SYSTEM SHALL abort the in-flight request and treat it as a retryable error.

### Discovering tools

WHEN the user runs with `--list-tools`
THE SYSTEM SHALL print every tool the service exposes, including name, description, and input schema, then exit 0.

WHEN selecting a tool to retrieve messages
THE SYSTEM SHALL use the tool named by `--tool` if provided, otherwise select the first tool whose name or description matches read keywords (`list`, `recent`, `search`, `messages`, `emails`).

WHEN no matching read tool is found
THE SYSTEM SHALL log the available tools to aid diagnosis and exit code 3.

WHEN the selected tool's name matches a write/send/delete pattern (`send`, `create`, `delete`, `update`, `reply`, `forward`, `trash`, `modify`)
THE SYSTEM SHALL refuse to call it unless `--allow-write-tools` is explicitly set.

## Retrieving emails

### Fetching recent messages

WHEN invoking the selected tool
THE SYSTEM SHALL pass a count/limit argument equal to `--count` when the tool's input schema declares such a property, otherwise omit it.

WHEN `--label` is specified and the tool's schema accepts a label or folder parameter
THE SYSTEM SHALL pass that value to the tool; the default label is `INBOX`.

### Parsing results

WHEN the tool returns content
THE SYSTEM SHALL parse it into a normalized `Email` list containing at least id, sender, recipients, subject, `received_at`, snippet, `label_ids`, and `is_unread`.

WHEN the result content is not parseable as JSON
THE SYSTEM SHALL fall back to raw text per content block, emit a warning, and exit code 5.

WHEN the result contains more than `--count` messages
THE SYSTEM SHALL sort by `received_at` descending, breaking ties by message id, and truncate to `--count`.

WHEN the result is empty
THE SYSTEM SHALL print a "no messages" notice and exit 0.

WHEN fewer than `--count` messages exist
THE SYSTEM SHALL print all available messages without error.

## Output

### Rendering results

WHEN `--json` is set
THE SYSTEM SHALL emit valid JSON matching the envelope: `service`, `fetched_at`, `tool`, `count`, and `emails`.

WHEN `--json` is not set
THE SYSTEM SHALL print a human-readable table sorted newest first, including subject, sender, `received_at`, and id.

## Edge cases & error handling

WHEN the selected tool rejects the supplied arguments
THE SYSTEM SHALL log the tool schema and the arguments tried, retry once without the count argument, and exit code 4 if it still fails.

WHEN the token is valid but lacks permission for the service
THE SYSTEM SHALL surface the gateway's error, exit code 2, and not retry.

## Gmail search compatibility

Observed against the live service: it exposes `gmail_search`, `gmail_read_message`, `gmail_search_threads`, `gmail_get_thread`, `gmail_list_drafts`, `gmail_list_labels`, `gmail_get_attachment`, and write tools (`gmail_send_email`, `gmail_create_draft`, `gmail_update_draft`, `gmail_forward_email`, `gmail_create_label`, `gmail_label_message`, `gmail_unlabel_message`, `gmail_label_thread`, `gmail_unlabel_thread`, and `*_with_attachments` variants). The server instructs clients to use `gmail_search` / `gmail_read_message` for message-level reads. These tools take a Gmail `query` string and `max_results`, and have no label parameter. The auto-selected `gmail_search_threads` failed with `invalidArguments` (missing `query`; valid fields `cursor`, `max_results`, `query`), and the app still exited 0. The requirements below supersede the earlier label/count injection rules where they conflict.

### Building the query

WHEN the selected tool's input schema declares a `query` property and declares no label or folder property
THE SYSTEM SHALL pass `query` equal to `label:<LABEL>` for the configured label.

WHEN the configured label is `INBOX` (the default) and the schema rule above applies
THE SYSTEM SHALL pass `query` equal to `in:inbox`.

### Mapping count

WHEN the selected tool's input schema declares `max_results`, `limit`, `count`, `page_size`, `maxResults`, or `top`
THE SYSTEM SHALL pass `--count` as the value of the first declared property in that order.

### Required parameters

WHEN a property listed as required in the selected tool's input schema cannot be filled from count, label, or query building
THE SYSTEM SHALL exit code 4 with a message naming the property, without calling the tool.

### Tool preference

WHEN `--tool` is not provided and several read tools match
THE SYSTEM SHALL prefer message-level search tools (`gmail_search`) over thread-level tools (`gmail_search_threads`, `gmail_get_thread`).

WHEN selecting a tool automatically
THE SYSTEM SHALL select only tools that are read-only per the write-pattern block list, excluding `gmail_read_message`, `gmail_get_attachment`, `gmail_list_labels`, and `gmail_list_drafts` from the search role.

### Tool-reported errors

WHEN a tool result has `isError` set to true, or its parsed JSON object has `success` equal to false
THE SYSTEM SHALL write the tool's error message to stderr, exit code 4, and write nothing to stdout.

WHEN the tool-reported error names invalid arguments
THE SYSTEM SHALL include the tool's valid field list in the stderr message.

### Hydrating search results

WHEN a search result contains message ids without subject, sender, or `received_at`
THE SYSTEM SHALL call the read-message tool (`gmail_read_message`) once per id to obtain the missing fields.

WHEN hydrating search results
THE SYSTEM SHALL issue at most `--count` read calls and at most 5 concurrent calls.

WHEN a single read call fails
THE SYSTEM SHALL log a warning, keep the id-only entry, and continue with the remaining ids.

WHEN a search result already contains subject, sender, and `received_at` for every message
THE SYSTEM SHALL skip hydration.

WHEN the read-message tool is not found and hydration is needed
THE SYSTEM SHALL print the id-only entries and log a warning.

### Pagination

THE SYSTEM SHALL NOT paginate with `cursor` when `--count` does not exceed the tool's page size, and SHALL issue a single search call.

WHERE `--count` exceeds the number of messages returned in the first page and the result contains a `cursor`, THE SYSTEM SHALL treat multi-page retrieval as out of scope and print the messages returned, with a warning that more exist.

## Open questions (to resolve before/while implementing)

1. Exact tool name and input schema exposed by `system.ai.gmail` — run `--list-tools` (or the existing `gmailmcp.py`) and capture output.
2. Runtime target confirmation: standalone CLI (assumed) vs. Databricks notebook job vs. both.
3. "10 most recent" definition confirmation: newest by received date in `INBOX` (assumed) vs. a different label vs. newest by sent date.
4. Output destination confirmation: stdout only (assumed) vs. also writing a JSON file.
5. Token source confirmation: PAT via env (assumed) vs. OAuth/M2M vs. notebook context.
6. Exact result shape of `gmail_search` (full email objects vs. ids only, snippet presence, `cursor` key) — capture with a live call.
7. Exact result shape of `gmail_read_message` and its id parameter name (`message_id` vs. `id`).
8. PAT scope: a token limited to `ai-gateway:read` receives HTTP 403 because MCP uses POST; confirm the minimal broader scope that works.
