# Recent-Mail Fetcher — Tasks

Implementation plan derived from `requirements.md` and `design.md`. Tasks are numbered; mark `[x]` when complete. `Depends on` lists task numbers — Kiro builds the execution graph (parallel waves) from these. Tasks marked `(optional)` can be deferred without blocking the core deliverable.

Shared assumptions to confirm before or during task 8/10 (see requirements.md Open questions): the exact tool name and input schema of `system.ai.gmail`. Tasks 8, 9, 10 are written to be schema-discovery-driven so they hold even if the schema differs from the assumed `list_messages`/count/label shape.

---

## Foundation

- [x] 1. Project scaffold
  - Create a Python 3.10+ package (`recent_mail_fetcher/`) with `pyproject.toml` pinning `mcp`, `httpx` (standalone) and `python-dotenv` (optional, local `.env`). Add `__main__.py` entry point and a minimal `argparse` skeleton that prints `--help`.
  - Files: `pyproject.toml`, `recent_mail_fetcher/__init__.py`, `recent_mail_fetcher/__main__.py`.
  - Verify: `pip install -e .` succeeds; `python -m recent_mail_fetcher --help` exits 0.
  - Depends on: —

- [x] 2. Config loader
  - Merge environment variables with CLI flags (flags win). Validate `DATABRICKS_TOKEN` non-empty (short-circuit exit 2, no network call). Defaults: URL = service endpoint, count = 10, label = `INBOX`, read timeout = 60.
  - Files: `recent_mail_fetcher/config.py`.
  - Verify: unit assertions on env-vs-flag precedence and the missing-token short-circuit.
  - Depends on: #1

- [x] 3. Email data model + JSON envelope
  - `Email` dataclass per design.md. Parse `received_at` from ISO-8601 or RFC-2822. Build the JSON output envelope (`service`, `fetched_at`, `tool`, `count`, `emails`).
  - Files: `recent_mail_fetcher/models.py`.
  - Verify: a populated `Email` serializes to the expected envelope JSON.
  - Depends on: #1

- [x] 4. Redacting logger + exit-code helpers
  - Structured stderr logger with a `--verbose` level; mask the token (`Bearer ***`). Centralized exit-code constants (0/2/3/4/5) and a small `fail(code, msg)` helper.
  - Files: `recent_mail_fetcher/log.py`, `recent_mail_fetcher/exitcodes.py`.
  - Verify: logging a bearer token emits `Bearer ***`; no secret in output.
  - Depends on: #1

## Core

- [x] 5. Renderer
  - Table (default) and JSON (`--json`) renderers, both sorted newest-first. Output goes to stdout only.
  - Files: `recent_mail_fetcher/render.py`.
  - Verify: JSON matches the §Interfaces envelope; table shows subject/sender/received_at/id sorted descending.
  - Depends on: #3

- [x] 6. Parser
  - Normalize `CallToolResult.content` blocks into `Email[]`: parse text blocks as JSON, fall back to raw text + warn (exit 5). Sort by `received_at` desc, tiebreak `id` asc, truncate to `--count`.
  - Files: `recent_mail_fetcher/parser.py`.
  - Verify: fixture payloads (valid JSON, nested, malformed, empty, mixed blocks) yield the expected `Email[]` and exit codes.
  - Depends on: #3

## MCP layer

- [x] 7. MCP client wrapper
  - Open `streamable_http_client` with an authenticated `httpx.AsyncClient` (`Authorization: Bearer <token>`) and initialize `ClientSession`. Isolate token source and HTTP client behind an injectable interface so the notebook variant can swap them later.
  - Files: `recent_mail_fetcher/mcp_client.py`.
  - Verify: against a stub MCP server, `initialize()` completes and the bearer header is present on requests.
  - Depends on: #1, #4

- [x] 8. Tool discovery + selection
  - `list_tools()`; `--list-tools` prints name + description + input schema then exits 0. Select read tool by `--tool` or heuristic keyword match (`list`, `recent`, `search`, `messages`, `emails`). Block names matching `send|create|delete|update|reply|forward|trash|modify` unless `--allow-write-tools`. Exit 3 if none found, logging available tools.
  - Files: `recent_mail_fetcher/discovery.py`.
  - Verify: with a fake session, correct tool is selected; `--list-tools` prints all schemas; write tools blocked; no-match exits 3.
  - Depends on: #7

- [x] 9. Arg injection
  - Build call arguments from the tool's `inputSchema`: inject `count`/`label` only when the schema declares a matching property; never add undeclared fields.
  - Files: `recent_mail_fetcher/args.py`.
  - Verify: schema fixtures (with/without count/label props) produce the correct argument maps.
  - Depends on: #7

- [x] 10. Invoker
  - `call_tool(name, args)` with retry/backoff (≤3) on 429/5xx/connection reset/read timeout; 401/403 non-retry (exit 2); tool-rejected-args retries once without the count arg (exit 4 if still failing). Surface permission-denied as exit 2 without retry.
  - Files: `recent_mail_fetcher/invoker.py`.
  - Verify: stub returning 429/5xx/401 drives the correct retry/exit behavior.
  - Depends on: #7, #9

## Wiring

- [x] 11. CLI end-to-end
  - Full argparse surface (`--list-tools`, `--json`, `--count`, `--label`, `--tool`, `--url`, `--allow-write-tools`, `--read-timeout`, `-v`). Dispatch through config → connect → discover → call → parse → render, with the exit-code map. Empty result prints "no messages" (exit 0); fewer than count prints all available.
  - Files: `recent_mail_fetcher/__main__.py` (extended), `recent_mail_fetcher/app.py`.
  - Verify: end-to-end run prints 10 newest; `--list-tools` lists tools; missing/bad token exits 2; `--json` is valid JSON.
  - Depends on: #2, #5, #6, #8, #10

- [x] 12. Notebook variant adapter (optional)
  - Reuse the shared core from a Databricks notebook: token from `dbutils...apiToken().get()`, `httpx2` for the HTTP client, `nest_asyncio.apply()` before `asyncio.run()`. No changes to core behavior.
  - Files: `notebook/recent_mail_fetcher_nb.py`.
  - Verify: a notebook cell lists tools and prints the 10 most recent emails.
  - Depends on: #11

## Tests

- [x] 13. Unit tests — parser
  - Cover valid/nested/malformed JSON, empty, mixed block types, sort + truncate + tiebreak, exit 5 fallback.
  - Files: `tests/test_parser.py`.
  - Verify: `pytest tests/test_parser.py` green.
  - Depends on: #6

- [x] 14. Unit tests — discovery, selection, arg injection
  - Heuristic selection, `--tool` override, write-tool blocking, no-match exit 3, `--list-tools` output; arg injection for schemas with/without count/label.
  - Files: `tests/test_discovery.py`, `tests/test_args.py`.
  - Verify: `pytest` green for both.
  - Depends on: #8, #9

- [x] 15. Unit tests — config, model, renderer
  - Env-vs-flag precedence, missing-token short-circuit, defaults; envelope serialization; table/JSON rendering sorted newest-first.
  - Files: `tests/test_config.py`, `tests/test_models.py`, `tests/test_render.py`.
  - Verify: `pytest` green for all three.
  - Depends on: #2, #3, #5

- [x] 16. Integration smoke test
  - Stub MCP server exercising: retry/backoff on 429/5xx, read timeout, 401 non-retry path, empty result, fewer-than-count result, a successful 10-email fetch.
  - Files: `tests/integration/test_smoke.py`, `tests/integration/stub_server.py`.
  - Verify: `pytest tests/integration` green.
  - Depends on: #7, #10, #11

## Docs

- [x] 17. README + runbook
  - Install, environment configuration, CLI usage, the `--list-tools` discovery flow for resolving Open questions, exit-code reference, troubleshooting.
  - Files: `README.md`.
  - Verify: a new user can configure env and run the app to completion from the README alone.
  - Depends on: #11

## Gmail search compatibility

Follow-up from running against the live `system.ai.gmail` service (see requirements.md "Gmail search compatibility" and design.md "Search then hydrate"). Result shapes of `gmail_search` and `gmail_read_message` are unconfirmed, so tasks 20–21 must handle both full objects and ids only.

- [x] 18. Prefer message-level search tool in selection
  - Rank candidates: exact `gmail_search` first, other message-level search/list tools next, thread-level tools (`*_threads`, `*_thread`) last. Exclude `gmail_read_message`, `gmail_get_attachment`, `gmail_list_labels`, `gmail_list_drafts` from the search role. Extend the write block pattern to cover `label` and `unlabel`. Add a resolver for the read-message tool (`gmail_read_message`, else `read_message`/`get_message`). `--tool` still overrides.
  - Files: `recent_mail_fetcher/discovery.py`.
  - Verify: with the observed tool list, `gmail_search` is selected; `gmail_search_threads` is chosen only if nothing else matches; `gmail_label_message` is blocked.
  - Depends on: #8

- [x] 19. Query building, count aliases, required-param check
  - When the schema declares `query` and no label/folder property, set `query` to `label:<LABEL>` (INBOX → `in:inbox`; quote labels containing spaces). Map count via aliases `max_results`, `limit`, `count`, `page_size`, `maxResults`, `top` in that order. If any `required` property is unfilled, raise an error that maps to exit 4 and names the property, before calling the tool.
  - Files: `recent_mail_fetcher/args.py`.
  - Verify: schema fixtures from `gmail_search_threads` produce `{"query": "in:inbox", "max_results": 10}`; a schema with an unknown required property fails with exit 4 and names it.
  - Depends on: #9, #10

- [x] 20. Tool-reported error detection
  - Treat `isError: true` or a parsed JSON object with `success: false` as failure: message (plus `validFields` when present) to stderr, nothing to stdout, exit 4. Keep the existing retry-once-without-count behavior for invalid-argument errors.
  - Files: `recent_mail_fetcher/invoker.py`, `recent_mail_fetcher/parser.py`.
  - Verify: the observed `invalidArguments` body yields exit 4 with an empty stdout; a normal result is unaffected.
  - Depends on: #9, #10

- [x] 21. Hydrate ids via read-message tool
  - Parse search results into full `Email` objects or id stubs. For stubs missing subject/sender/`received_at`, call the read-message tool per id, using the id parameter name from its schema, with at most `--count` calls and `asyncio.Semaphore(5)`. A failed read keeps the stub and logs a warning. Skip hydration when all entries are complete. If the read tool is missing, print stubs and warn. Warn when a `cursor` indicates more results; do not paginate. Wire into the app flow after the search call.
  - Files: `recent_mail_fetcher/hydrate.py` (new), `recent_mail_fetcher/parser.py`, `recent_mail_fetcher/app.py`.
  - Verify: ids-only search results render with subject/sender/date; full-object results trigger zero read calls; concurrency never exceeds 5.
  - Depends on: #18, #19, #20

- [x] 22. Tests for Gmail search compatibility
  - Selection ranking and write blocking (task 18), query/alias/required-param cases (19), error detection and empty stdout (20), hydration cases: ids only, full objects, partial failure, call bound, concurrency bound (21). Extend the stub server to serve `gmail_search` and `gmail_read_message`, and to return the `invalidArguments` body on demand.
  - Files: `tests/test_discovery.py`, `tests/test_args.py`, `tests/test_parser.py`, `tests/integration/test_smoke.py`, `tests/integration/stub_server.py`.
  - Verify: `pytest` green, including `pytest tests/integration`.
  - Depends on: #18, #19, #20, #21

- [x] 23. README update
  - Document the query/label behavior (`label:<LABEL>`, default `in:inbox`), using `--tool gmail_search`, and search-then-hydrate. State the PAT scope requirement: a token limited to `ai-gateway:read` gets HTTP 403 because MCP uses POST, so use a PAT with a broader scope. Add troubleshooting entries for the `invalidArguments` / missing `query` error (exit 4) and the 403 case.
  - Files: `README.md`.
  - Verify: README covers each new behavior and both troubleshooting entries.
  - Depends on: #22
