# Action Inbox Notion view: row projection and reconciliation plan (LIFE-711, Nov 16 2026)

Synthetic only. Nothing in this change calls Notion, reads a provider, uses a credential or writes to Oracle; the writer is an injected callable that refuses by default. Code: `lifeos/inbox/view_rows.py` (projection), `lifeos/inbox/view_plan.py` (reconciliation plan), tests: `tests/inbox/test_view.py`. Origin LOS-86 / REQ-7; parent LIFE-696. Jim, Nov 16 2026: LIFE-716 no longer blocks this ticket; Followup shows as unavailable until its lane is live.

## A. The view's properties

One Notion database view ("Action Inbox"), one row per Inbox item, plus review rows and one health row per source and workspace. `view_rows.project_rows(inbox, now, last_ok, review)` returns plain dicts, property name to string or bool:

| Property | Items | Meaning |
|---|---|---|
| Record ID | all | the key: `source:workspace:account:native_id`; health `health:source:workspace`; review `review:followup:workspace:account:id` |
| Kind | all | `item`, `review` or `health` |
| Title, Link | items, review | the source's own title and the original record's https link; no description, body or recipient text is copied |
| Workspace | all | `Personal` or `Bytalos` on every row; the All, Personal and Bytalos filters are this property (All is the union); TGP never reaches a row |
| Source | all | Jira, Todoist, Concord, Followup, Calendar or Asana |
| Bucket | items, review | Needs me, Waiting, Approvals, Upcoming, or Review needed (Followup suggestions only) |
| Source status, Due, Source updated | items | the source's own status word and times (ISO, with the source's offset) |
| Owner cue, Checkpoint | Followup | You or Them; Source-backed, Suggested or Jim override (a Suggested checkpoint is visibly Suggested) |
| Freshness | items | `fresh`, or `stale` when the record is older than 15 minutes since its retrieval |
| Source health | all | OK, `DEGRADED, last accepted <CT time>`, `Unavailable: not provisioned`, or `Unavailable: <fixed reason word>` |
| Health state, Last refreshed, Last OK | health rows | `ok`, `degraded` or `unavailable`; the refresh time; the last time the source read cleanly |
| Counted | all | false for review and health rows: they are in no count |

Followup's own states map to the Inbox buckets: Needs Action Today to Needs me, Waiting/Aging to Waiting, Upcoming to Upcoming. Review Needed stays a separate kind and is excluded from every count. Separate Jira or Todoist work is never merged into a Followup row just because it points at the same outcome.

## B. Honesty rules (tested)

- A source that failed, is degraded, is stale or is not provisioned is shown as such. It is never an empty list: an expected source with no health entry shows `Unavailable: not provisioned`.
- `view_rows.summary` returns a count as a number only when every expected source of that workspace is OK. Otherwise a positive count means "at least n" (`complete` is false) and a zero is `None`, unknown, never `0`.
- One failed source (Followup included) never hides the other five. An exception message never reaches a row, only its fixed word.
- Personal and Bytalos are read with their own grants and credential names and share no cache; a workspace with no grant shows no rows and its sources show `Unavailable`.

## C. Reconciliation (`view_plan.plan(current, desired)`)

A pure function: the same inputs give the same ordered plan of `create` (full row), `update` (only the changed properties) and `archive`. Applying a plan and planning again gives nothing (no churn): the per-row time properties are the source's own, so unchanged data produces no update; only a health row's `Last refreshed` moves when the clock does.

- Rows are keyed by Record ID. An archived row that comes back is restored by an update.
- A row is archived only when its source read cleanly this run (health state `ok`). A failed, degraded or not-provisioned source keeps its last accepted rows, so a bad hour never empties the view.
- Health rows are never archived. A row whose key is not ours is left alone.
- `view_plan.execute(ops, write)` hands each operation to `write`; the default `refuse_write` raises `notion_writes_not_authorized`. No second task store, scheduler or frontend exists: this is a plan and an injected writer.

## D. What is not done, and the exact approvals still needed (Done-when item 4)

Item 4 ("verify Notion projection and rollback with a read-back receipt on an approved test page") is NOT done here. It needs, from Jim, each stated exactly:

1. **A named test page or database** in Notion (disposable, not the production Command Center), whose id goes in the environment under a name such as `ACTION_INBOX_VIEW_TEST` (`lifeos.platform.notion_ids`), never in the repo.
2. **The Notion integration and its token custody** for that page: which integration, which data scope (this page only), where the token lives (the existing Bitwarden layout), and who may read it.
3. **A writer scope:** one row for the writer in `docs/platform/notion-writers.md` and, if it owns a page region, one `OWNERS` entry in `lifeos/platform/router.py`, so the one-writer contract test passes. This ticket adds neither because it writes nothing.
4. **A release:** a stage in the V7 tick (or a manual run) and the host that runs it, approved separately; no live read is enabled by this change (`LIVE_READS_ENABLED` stays false).
5. **A rollback and a read-back receipt:** the plan's reverse (archive what was created), and a read of the page after the write that equals the projection.

Until then the projection and the plan are proven only on synthetic data, and the view does not count as live six-source acceptance (that stays with LIFE-720).

## E. Known gaps

- **Next move.** The `followup` stage's thread dicts carry no next-move field today, so the `Next move` property is empty. Adding it is a change to the stage's payload (LIFE-749 follow-up), not to this projection.
- **Followup is unwired in production** (LIFE-750 and LIFE-751 own that), so in a live run Followup shows `Unavailable: not provisioned` until its natural-run acceptance.
- **Expected sources** are the released set (Todoist Personal only, Asana Bytalos only; the rest in both workspaces); a different release needs `view_rows.EXPECTED` changed.

Test command (local, synthetic): `python3 -m unittest tests.inbox.test_view`.
