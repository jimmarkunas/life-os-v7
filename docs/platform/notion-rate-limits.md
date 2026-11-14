# Notion API limits and the one rule (LIFE-682)

Read from Notion's own documentation on Nov 14 2026, 1:20 PM CT. When the pages change, change this file and `lifeos/platform/limits.py` together: a contract test (`tests/contracts/test_limits.py`) fails when they disagree.

Sources: https://developers.notion.com/reference/request-limits and https://developers.notion.com/reference/status-codes (no "last updated" date is shown on either page).

## What Notion publishes

* **Per connection (one integration token):** a fixed budget of requests per 60-second window: 180 requests per minute (an average of 3 per second) on plans below Business; 600 per minute (10 per second) on Business and Enterprise. The budget can be spent at any pace inside the window, including one burst.
* **Data source queries:** a separate limit of about 3 requests per second per connection.
* **Per workspace:** a separate limit shared by all of the workspace's connections and scaled to the plan. A request can be limited even when each connection is under its own limit.
* **429 `rate_limited`:** "This request exceeds the number of requests allowed. Slow down and try again." `additional_data.rate_limit_reason` names the limit (`public_api_request_rate_limit` per connection, `public_api_space_request_rate_limit` per workspace).
* **`Retry-After`:** an integer number of seconds; at most 60 for a per-connection limit, longer for a per-workspace limit. The body repeats it as `additional_data.retry_after`.
* **Client guidance:** handle 429 and 529, respect `Retry-After`, queue outgoing requests, use exponential backoff with jitter, set a retry limit, and avoid independent retry loops in each worker.
* **529 `service_overload`:** respect `Retry-After` and retry the same way as 429.
* **500, 502, 503, 504:** retry only when the request is idempotent (GET or DELETE) unless the application has its own idempotency protection; a write that got a 503 may have been saved, so check its state first.
* **409 `conflict_error`:** "The transaction could not be completed, potentially due to a data collision." Make sure the parameters are up to date and try again.
* **Size limits:** 1000 block elements and 500 KB per payload; 100 elements in any array of blocks or rich text; 2000 characters per `text.content` and per URL.

## The rule in our code (every Notion call goes through `lifeos/platform/notion_client.py`)

1. **429 and 529, every call (reads, writes, `call_once`; the one exception: a `call_once` POST, a create, is a single attempt unless its caller passes `rate_retries`):** wait the `Retry-After` seconds (60 at most) plus up to 50% jitter, then retry, up to 6 times. No header: 2, 4, 8 seconds and so on, with the same jitter. A `Retry-After` over 60 (a per-workspace wait) or total waits over 180 seconds in one call: stop with the fixed code `NOTION_HTTP_429` (or `_529`).
2. **Slow down:** after any 429 the process doubles its gap between requests for the rest of the run (0.4 s, up to 3.2 s).
3. **409:** retried with a short jittered backoff on any method (the request was not applied).
4. **500/502/503/504:** retried only on GET or DELETE; a write is never repeated blindly: its caller reads it back first.
5. **Pacing:** one process spends at most 2.5 requests per second (under the published 3 per second average). Parallel GitHub jobs are separate processes that share one workspace limit; they cannot queue behind each other without a shared limiter (a new service, which is not approved), so the defence is rule 1 with jitter, rule 2 and the job order in the workflows (`needs:`), not more parallelism.
6. **No other path:** no module under `lifeos/` may call `api.notion.com` itself; a contract test fails if one does.

## Constants the code uses (a test compares these lines with `limits.py`)

```
NOTION_REQUESTS_PER_SECOND = 3
NOTION_BUDGET_PER_MINUTE = 180
NOTION_RATE_RETRIES = 6
NOTION_RETRY_AFTER_CAP_SECONDS = 60
NOTION_RATE_WAIT_BUDGET_SECONDS = 180
NOTION_MAX_CHILD_BLOCKS = 100
NOTION_MAX_RICH_TEXT_CHARS = 2000
```
