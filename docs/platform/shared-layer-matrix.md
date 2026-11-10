# Shared layer semantics matrix (LIFE-526)

Written first, before any code moved. Taken from main at 2026-11-10; every row comes from code I read (file named in the row). **Rule applied:** a piece is shared only when it is the same logic in at least two real current consumers. Anything that differs on purpose (backoff, idempotency, side effects, privacy handling, transaction behaviour) stays where it is. A write that may or may not have happened is never retried automatically.

Method for "identical": an AST comparison of every function of 5+ lines in lifeos/sources, lifeos/jobs and lifeos/platform (docstrings ignored) found exactly **2 groups of byte-identical bodies**; I then read the retry, socket, read-back and write patterns by hand for the near-identical ones. Counts are lines of Python in sources / jobs / platform before the change: 8,832 / 8,564 / 5,578.

## A. Fetch

| Pattern | Consumer file | Retry allowed? | Safe to repeat? | Side effects | Privacy handling | Transaction | Decision |
|---|---|---|---|---|---|---|---|
| HTTP GET with redirect follow (max 8) | `platform/http.fetch`, used by sources/linkedin_search, sponsor_register, scale_up_register, web/lister; jobs/ats_detail, enrich, linkcheck, resolve/aggregators/dice | No retry inside; returns a `Fetched` with a failure word (timeout, network, too_many_redirects) | Yes (GET) | none | returns the body to the caller; callers keep counts and fixed codes | n/a | **Already shared.** Not changed. |
| Browser-impersonating GET, up to 4 rounds | `platform/impersonate.fetch` | Yes, rounds on a blocked or short page | Yes (GET) | none | no body logged | n/a | Already in platform; one purpose-built consumer. Not changed. |

## B. Retry

| Pattern | Consumer file | Retry allowed? | Safe to repeat? | Side effects | Privacy handling | Transaction | Decision |
|---|---|---|---|---|---|---|---|
| REST call with attempts and sleep | `platform/rest.call`, used by `platform/jira` (4 attempts for GET, 1 for a write unless the caller says it is idempotent) and `platform/gcal` (4 for GET/PUT/DELETE, 2 for insert/patch) | Reads yes; a write only when marked idempotent | By caller's flag | none beyond the call | fixed codes only | n/a | **Already shared.** Not changed. |
| Notion request pacing and 429 | `platform/notion_client._call` / `call_once` | 429 only, honouring Retry-After; `call_once` is never retried (uncertain write outcome) | Reads yes; writes only through `call_once` | Notion page/block changes | fixed `NOTION_*` codes | none | Already shared. Not changed. |
| Notion append, retry only on 429 | `network/surface._write` | 429 only (Notion did not apply it), 5 tries, 2 to 10 s | Yes, because a 429 means nothing was written | Notion block append | fixed codes | none | One consumer; **kept.** |
| Gmail list/get with exponential backoff and jitter | `platform/gmail.py` | transient and rate-limited errors, 2 to 4 tries | Reads | none | fixed log codes, no URL/id/body | n/a | Differs on purpose (error classification, jitter); **kept.** |
| Database connection with a wait ladder | `platform/db.connect` | The connect only (no statement has run); waits from `RETRY_WAITS`, quieter ladder after an SSH timeout, route switch to the jump host | Yes: nothing executed | opens a tunnel | no credentials printed | none until connected | Differs on purpose; reads and uncertain writes are treated differently; **kept.** |
| Oracle socket request: retry the CONNECT only | `jobs/fit/edge.exchange` and `interview/granola_oracle.exchange` | The connect, 2 tries, 3 s apart; **never after any byte was sent** | Yes for the connect (nothing sent); the exchange itself is never repeated | one request to Oracle | fixed codes only | none | **SHARED** (2 consumers, same logic; see C). |
| Other Oracle socket calls | `oraclecloud/card`, `imessage/comms_card`, `imessage/stage`, `imessage/openclaw_imessage` | No retry (single attempt, `with create_connection`) | n/a | read/send once | fixed codes | none | Differ (no connect retry); **kept.** |
| Rate-limit pacing sleeps | `sources/linkedin_search` (pause between searches), `jobs/resolve/aggregators/linkedin` (pause), `.../dice` (wait for remaining quota) | Not a retry: pacing | n/a | none | n/a | n/a | Domain pacing; **kept.** |
| Read-back wait: read again up to 4 times, 3 s apart, until the state matches | `jobs/fit/capacity_card.write`, `megibow/card.write`, `platform/report_region` (verify step) | Reads only; the WRITE before it is never repeated | Yes (reads) | none | fixed codes (`VERIFY_FAILED`, `CARD_VERIFY_FAILED:...`) | none | **SHARED** (3 consumers, same loop; see C). |

## C. What gets shared (the layer)

1. `platform/readback.py`: `confirmed(read, matches, attempts=4, pause=3, sleep=None)`. Only repeats a READ. Three consumers.
2. `platform/socket_exchange.py`: `exchange(...)`: connect retried, request sent once, read to the server's close. Two consumers, kept as thin wrappers with the same names and signatures.
3. `jobs/private_report` uses `platform/stored_pages` for its two helpers (`_existing`/`_remember` are byte-identical to `find`/`remember`, same table `v7_private_reports`, same error code).

## D. Record writing

| Pattern | Consumer file | Retry? | Safe to repeat? | Side effects | Privacy | Transaction | Decision |
|---|---|---|---|---|---|---|---|
| Remember a created page id under a key, read back | `jobs/private_report._remember` and `platform/stored_pages.remember` | No | Replace with read-back check | one private-store row | key and page id only | `with connect()` per call | **Identical (AST): one copy kept** (stored_pages). |
| Find the stored live page id | `jobs/private_report._existing` and `platform/stored_pages.find` | No | Yes (read) | none (a dry run never creates the table) | page id only | `with connect()` | **Identical: one copy kept.** |
| Notion block PATCH writers (cards) | `capacity_card`, `megibow/card`, `report_region`, `network/surface` | PATCH once; a failed or uncertain write is **not** repeated; then a read-back | no (not auto-retried) | block text/table rows | counts and fixed codes | none | Domain-specific; **kept.** Only the read-back loop after them is shared. |
| Snapshot store | `platform/snapshot_store` | n/a | n/a | private-store row | n/a | per connection | Already shared. Not changed. |

## E. Error-code mapping

| Pattern | Where | Decision |
|---|---|---|
| Fixed-code exception class `__init__(self, code): super().__init__(code); self.code = code` | `edge/secrets/lifeos_secret_use.Refused`, `edge/runner/runner.Refuse`, `interview/granola_bridge.Stop`, `interview/granola_oracle.OracleError` | 4 identical shapes, but none is in sources or jobs, and two are code that runs as installed copies on Oracle (held under LIFE-528). **Not merged here.** |
| The error-code registry and alert gateway | `platform/codes.py` and the gateway | Already the target; every code emitted must be registered (a contract test enforces it). The shared modules emit no new code of their own. |

## F. Schema check

Schema-neutral. No on-disk format, table or serialized record changes: `platform/stored_pages` already writes the same table (`v7_private_reports`) with the same key column and the same snapshot dict as `jobs/private_report` did. The round-trip test (old record in, same page id out) is in the PR.

## G. Not covered (stated, not skipped silently)

I did not read every call site in sources/ and jobs/ (125 files); I searched for sleep, retry, backoff, attempt, socket connect and read-back patterns and ran the AST comparison. A pattern that does not use those words or that differs in shape would not appear here.
