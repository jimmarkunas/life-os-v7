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

## D. The Notion writer and its QA proof (LIFE-711, Nov 16 2026)

Jim's recorded answers (chat, Nov 16 2026): **1** yes, the QA area (LIFE OS QA Home) is where the first real Notion write goes. **2** yes, the existing Notion integration (the one behind `NOTION_API_TOKEN`), no new integration or token. **3** yes, the writer may only create, update and archive the Agent Inbox rows it owns, tracked by a server-side row map, never any other page or block, and it is the only writer on that surface. **4** no to a separate reviewed change between QA and production: "This is unnecessary process theater. We should plan to retire QA as archive only, once this process is proved out." **5** yes, rollback is archiving the rows the writer created and restoring the "not connected" block text, with a read-back receipt each run.

What exists (`lifeos/inbox/view_writer.py`, `view_stage.py`, `view_synthetic.py`; tests `tests/inbox/test_view_writer.py`):

- **The view** is one Notion database, "Agent Inbox (V7 view)", created by the writer under LIFE OS QA Home (found by exact title, so no page id is stored anywhere). Its properties are exactly `view_writer.PROPERTIES`, the ones section A lists plus `Managed by`.
- **Ownership.** A row is ours only when it is in the view's data source and its `Managed by` property is `v7-inbox-writer`. `ViewWriter.current_rows()` returns only those rows and builds the row map (Record ID to page id). Updates and archives go through the map: a key outside it is refused with `INBOX_VIEW_WRITE_OUT_OF_SCOPE` before any request. A page in another data source, or a row someone added by hand, is never read into the map and never touched. Duplicate rows for one key keep the oldest and archive the rest.
- **Reconcile and read back.** `reconcile` plans with `view_plan.plan`, writes, reads the rows back and plans again: `readback_equal` is true only when the second plan is empty. The receipt is counts and flags, never a title, link or id.
- **TGP** is one fixed row, "Tracked, not connected". It is not a source and nothing is read for it.
- **The QA proof** is a manual run: Actions, workflow diag, stage `inbox-view-qa`, confirm `QA`. It writes synthetic rows (titles start with SYNTHETIC, links on example.com) twice: scenario A creates every row, scenario B updates one, archives one whose source read cleanly and keeps the row of a degraded source. The rows stay in place for Jim to look at. `inbox-view-qa-rollback` (confirm `QA`) archives every row the writer owns and reads back that none remain.
- **The hourly path** is the stage `inbox-view` behind the switch `ACTION_INBOX_VIEW` (switches.json), off. On, it still returns `unwired`: no synthetic row is ever written by the hourly path.

What remains for LIFE-711, in order: a live row source (the Followup feed, then the other five readers; `LIVE_READS_ENABLED` and the grants are released with LIFE-716 and LIFE-720, not here), then pointing the writer at the production Agent Inbox block on the LIFE OS home page in the same ticket (no second approval step, per answer 4), then archiving the QA area. Rollback on production is the same archive plus restoring the "not connected" text.

## E. Known gaps

- **Next move.** The `followup` stage's thread dicts carry no next-move field today, so the `Next move` property is empty. Adding it is a change to the stage's payload (LIFE-749 follow-up), not to this projection.
- **Followup is unwired in production** (LIFE-750 and LIFE-751 own that), so in a live run Followup shows `Unavailable: not provisioned` until its natural-run acceptance.
- **Expected sources** are the released set (Todoist Personal only, Asana Bytalos only; the rest in both workspaces); a different release needs `view_rows.EXPECTED` changed.

Test command (local, synthetic): `python3 -m unittest tests.inbox.test_view`.
