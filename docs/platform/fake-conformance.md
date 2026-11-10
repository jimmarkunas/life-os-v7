# Fake conformance (LIFE-549)

A fake that drifts from the thing it stands in for lets tests pass while production breaks. `tests/kit/conformance/` holds behavior cases as **data** (`cases/<family>.json`), written once and run against the fake **and** against ground truth. Any difference fails a test.

## Families and status (Nov 10 2026)

| Family (fake) | Ground truth | Status | Drifts found and fixed |
|---|---|---|---|
| http (`kit/http.py` Response, http_error) | the real urllib over a loopback server (runs in the normal suite) | covered | 2: `read(n)` ignored the size; error headers were a case-sensitive dict |
| db (`kit/db.py` FakeCursor, `kit/mysqlite.py`) | the real pymysql Cursor API; MySQL's documented results | covered | 1 (both fakes): `execute()` returned None, pymysql returns the affected-row count. 2 documented gaps (below) |
| notion (`kit/notion.py` FakeBlocks) | scrubbed recorded response shapes (`recordings/notion_blocks.json`) | covered | 4: list missed `object` and `next_cursor`; DELETE and PATCH returned `{}` instead of the block; append list missed the paging fields |
| clients (`kit/gmail.py` FakeGmail, `kit/gcal.py` CalendarEvents) | the real Gmail and GoogleCalendar clients (signatures) | covered | 1: `list_events` lacked `private_property` |
| `kit/notion.py` other classes (DataSourcePages, PaidBillTracker, region fakes), `kit/tree.py`, `kit/comms.py` | none: scripted tests doubles of OUR OWN scenarios, not imitations of one service | not covered | by design |
| `kit/shared.py` (Reply, Pushes, Clock, Terminal, Page, ...) | none: call recorders and tiny helpers | not covered | by design |

Documented gaps (a gap that stops differing fails, so none goes stale): FakeCursor has no `executemany`/`close`; MySQLite has no `close`; SQLite counts matched rows on UPDATE, MySQL counts changed rows.

## Add a case
1. Add an object to `cases/<family>.json`: `name`, `applies_to` (subjects that run it), the inputs (`ops`, `method`, `kind`, ...) and `expect`.
2. Make sure every subject that should pass does. If a fake legitimately differs, add it under `gaps.<subject>.<case name>` with the reason.
3. Run `python -m unittest tests.kit.conformance.test_<family>`.

## Refresh a recording
Replace the response in `recordings/<name>.json` with a scrubbed live one: swap every id, email, token, name and message text for an obviously fake value, keep field names and nesting. `tests/contracts/test_conformance_recordings.py` fails on an email, a token-like string, a long digit run, a UUID or a key prefix. Never commit an unscrubbed response, and never put a credential in a test or workflow: live checks must be skipped without the credential and run only in a separate manual job.

## Rule
A new fake in `tests/kit` that imitates a service needs conformance cases for it (or a line in the table above saying why not).
