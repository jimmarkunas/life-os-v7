# Notion query conservation policy

**Status:** Canonical rule for how agents and scheduled runs read Notion. It is linked from the Development Policy (the paragraph on stops for Jim).

## Goal

Run within the current Notion plan without making metered Notion data-source rows or SQL queries a normal dependency.

## Query precedence

For any Notion read, use the lowest-cost method that can prove the required state, in this order:

1. **Exact fetch** of a known page, data source or view by its ID.
2. **Saved view** (`query-data-sources` with `mode=view`) when a filtered row set is needed and a saved view exists. This path is not metered.
3. **Repository-owned Python on the official Notion Public API** (the `lifeos` readers) for bulk reads, exact-key reconciliation, counts, duplicate checks and other repeated structured inspection, through an authorized execution path that already exists.
4. **Metered rows or SQL query**, only when none of the above can establish the fact safely and Jim has approved that specific query after hearing why it is needed and its exact scope.

Do not spend a metered query to find what a known saved view, a known page ID or a current snapshot already shows. Batch the fields you need into one API read instead of several narrow queries.

## SQL and rows approval gate

- **Scheduled runs never use SQL or rows queries.** If cheaper paths cannot prove the state, mark the lane `DEGRADED` and keep the known-good state.
- **Interactive work asks Jim first.** The request says why the query is needed, which cheaper paths were tried or why they are not enough, and the exact scope. His yes covers only that query or clearly bounded batch, never later queries, turns, chats or tasks. A general earlier "go" does not count.
- Before every `query-data-sources` call, check the mode. `mode=view` is allowed. `mode=sql`, `mode=rows`, or no mode when SQL is the default, needs the approval above. `query-multiple-data-sources` is SQL and needs it too.
- No diagnostic, QA, reconciliation, migration, repair, count or emergency is an implicit exception. Do not repeat a metered query in the same task unless the first answer was incomplete, nothing cheaper helps, and Jim is told why and approves the retry.
- A violation is reported as `GUARDRAIL VIOLATION - NOTION METERED QUERY PATH`, and no further SQL or rows query is attempted in that task unless Jim re-authorizes it after being told.

## Hard rules

- A quota or entitlement failure on any Notion read is `DEGRADED`, never zero, and known-good state is preserved.
- Do not add a workflow, schedule or recurring automation to avoid the approval gate.
- An API read run in GitHub Actions uses an already-approved workflow with no new cron. It reads only configured data sources, paginates safely, respects Notion's 429 `Retry-After`, writes only the minimum evidence, fails closed on permission, timeout, truncation or incomplete pagination, never logs the token or private payloads, and never writes to Notion unless the owning contract authorizes and verifies that write.
- Conserving queries never weakens exact-key de-duplication, preserved manual decisions, or the requirement for definitive closure evidence.
