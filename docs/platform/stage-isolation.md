# Stage isolation: one bad item never fails a stage (LIFE-689)

The rule, for every stage in `lifeos/run.py` (the stages of the registered sources included):

1. A stage fails only if **every item failed** or **the stage itself could not do its job** (list its items, write its result).
2. Items that succeeded are always kept and written.
3. A failed item is recorded under a **fixed code**, never under an error's text.

The shared helper is `lifeos/platform/isolate.py`: `run_items(items, work, code_of=...)` runs each item, never raises for a single item, and returns the successes and the failures with their codes; `record(counts, isolated)` adds `items`, `item_failed` and `item_codes` to a stage's counts **only when at least one item failed** (a run where every item succeeds prints exactly the counts it printed before); `stage_fails(counts)` is the rule. `lifeos.run.main` applies it to a stage in `PARTIAL_FAILURE` that reports an item outcome: some items failed and the stage did its job exits 0 with the failures in the counts; all items failed, or `stage_failed` (the stage itself failed), exits 1 with the stage's own fixed code. A stage that reports no item outcome keeps its own rule. A new Notion writer (for example the Action Inbox view) adopts the helper by running its items through `run_items` and calling `record`.

## Status

* `helper`: the stage's items run through the helper.
* `guarded`: the stage loops over external items with error handling inside the loop, so one failed item does not stop the others; the failure is counted in the stage's own way and the stage is not in `PARTIAL_FAILURE`, so it does not exit 1 on a partial failure.
* `single`: no loop over external items (one read or write unit): its failure is the stage's own failure.
* `exempt`: a loop or failure that must fail the stage, with the reason.
* `partial-red`: per-item failures are counted but a single failed item still exits 1 through `PARTIAL_FAILURE`. It breaks the rule and is on the list to move; no stage is marked "aborts".

Method: every stage target module was scanned for loops over accounts, sources, boards, projects, meetings, threads, messages, files, pages and similar items whose body calls a client; each loop without a `try` inside was read by hand (table at the end). The contract test `tests/contracts/test_stage_isolation.py` runs the same scan, so a new stage, or a new unguarded loop, fails until it is classified here. The scan finds loops by name and call shape; it cannot prove a stage's behavior, which is why the hand-read table and the stage tests exist.

## Every stage

This table is rendered from `lifeos/platform/stages.json` (LIFE-768): edit a stage's status or reason there, then run `python tools/regen.py`.

| Stage | Module | Status | Why | Exits 1 on any failed item |
| --- | --- | --- | --- | --- |
| `accountability-artifacts` | `accountability.artifacts` | `guarded` | One loop over two people (Jim, Matt); each person is handled on its own and a failed Jira read is recorded for that person only, then the stage fails after both were tried. | no |
| `accountability-changelog` | `accountability.changelog` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `accountability-goals` | `accountability.goals` | `guarded` | One loop over two people (Jim, Matt); each person is handled on its own and a failed Jira read is recorded for that person only, then the stage fails after both were tried. | no |
| `accountability-review` | `accountability.review` | `guarded` | One loop over two people (Jim, Matt); each person is handled on its own and a failed Jira read is recorded for that person only, then the stage fails after both were tried. | no |
| `accountability-week` | `accountability.week` | `guarded` | One loop over two people (Jim, Matt); each person is handled on its own and a failed Jira read is recorded for that person only, then the stage fails after both were tried. | no |
| `agenda-card` | `agenda.card` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `agenda-snapshot` | `agenda.snapshot` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `alerts` | `jobs.alerts` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `alerts-open` | `jobs.alertstate` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `amazon-backfill` | `amazon.backfill` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `amazon-card` | `amazon.card` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `amazon-census` | `delivery.census` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `amazon-orders` | `amazon.stage` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `amazon-retention` | `amazon.retention` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `amazon-review-reset` | `amazon.reset` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `audit` | `jobs.audit` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `audit-fresh` | `jobs.audit` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `audit-ready` | `jobs.audit` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `bills-card` | `bills.card` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `bills-paid` | `bills.paid` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `bills-snapshot` | `bills.snapshot` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `board-check` | `sources.web.boardcheck` | `guarded` | Loops over its items; each loop without error handling is reviewed below. | no |
| `boards` | `sources.web.boards` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `boards-backfill` | `sources.web.boards` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `boards-preview` | `sources.web.boards` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `boards-tick` | `sources.web.boards` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `calendar-sync` | `calendar_bridge.watch` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `capacity-card` | `jobs.fit.capacity_card` | `exempt` | One reading of Oracle's limits: a failed read is the stage's own failure and the old card stays until a fresh reading is verified. | yes |
| `dcc-card` | `dcc.card` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `discover-jobs` | `sources.web.discover_jobs` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `drain-audit-ready` | `jobs.audit` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `drain-enrich` | `jobs.enrich` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `drain-fit` | `jobs.fit.stage` | `exempt` | D214: the same stage as fit, run again in the drain pass. | no |
| `drain-publish` | `jobs.publish` | `exempt` | D212: the same stage as publish, run again later in the tick. | no |
| `enrich` | `jobs.enrich` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `expire-holds` | `jobs.hold` | `guarded` | Loops over its items; each loop without error handling is reviewed below. | no |
| `explain` | `jobs.explain` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `fit` | `jobs.fit.stage` | `exempt` | D214: only an enabled cloud shadow can degrade, and that is the stage's own state, not one item's. | yes |
| `fit-cloud-backlog` | `jobs.fit.backlog` | `exempt` | D214: the same cloud-shadow degrade rule as fit, for the manual backlog batch. | yes |
| `fit-edge-test` | `jobs.fit.edge` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `fit-profile-sync` | `jobs.fit.profile_sync` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `fit-review` | `jobs.review` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `followup` | `inbox.followup_stage` | `helper` | Scopes (workspace and account) run through `isolate.run_items`; partial failure keeps the stage green, all-failed fails it. | no |
| `followup-schema-apply` | `inbox.followup_apply` | `exempt` | A one-time DDL apply: a failed statement or a failed read-back must fail the stage, and no item may be skipped, because a half-applied schema is not a result. | no |
| `followup-schema-check` | `inbox.followup_schema` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `funnel` | `jobs.funnel` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `granola` | `interview.granola_bridge` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `granola-auto` | `sources.interview.granola_auto` | `partial-red` | Each meeting that is left on Oracle with `failed` counts toward the stage's `failed`, and a stage-level stop (a Notion error, a deadline, a store failure) is a second path into the same count; one state machine, so moving it needs its own change (next ticket). | yes |
| `hermes` | `hermes_requests` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `hiring` | `sources.pipeline.hiring` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `hiring-outcomes` | `hiring.outcomes` | `guarded` | Loops over its items; each loop without error handling is reviewed below. | no |
| `imessage-alerts` | `imessage.stage` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `imessage-comms` | `imessage.comms_card` | `guarded` | Loops over its items; each loop without error handling is reviewed below. | no |
| `inbox-schema-apply` | `inbox.apply_schema` | `exempt` | A one-time DDL apply: a failed statement or a failed read-back must fail the stage, and no item may be skipped, because a half-applied schema is not a result. | no |
| `inbox-schema-check` | `inbox.schema` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `inbox-view` | `inbox.agent_inbox_stage` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `interview` | `sources.interview.interview_evidence` | `partial-red` | Its `failed` adds unreadable threads, blocked pages and sweep failures together, which LIFE-690 turns into state-change alerts; moving it onto the helper before that would change what pages, so it waits for LIFE-690. | yes |
| `interview-acceptance` | `interview.acceptance` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `interview-mail` | `sources.interview.mail_snapshot` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `interview-probe` | `interview.stage` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `interview-ui-probe` | `interview.ui_probe` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `jira-card` | `jira.card` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `jira-fill` | `jira.fill_defaults` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `jira-probe` | `jira.snapshot` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `jira-release` | `jira.release` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `jira-rollover` | `jira.rollover` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `jira-rollover-scheduled` | `jira.rollover` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `jira-rollover-watch` | `jira.watch` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `jira-snapshot` | `jira.snapshot` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `jira-stalled` | `jira.stalled` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `job-page-outreach` | `sources.pipeline.job_page_outreach` | `helper` | Items run through `isolate.run_items`; partial failure keeps the stage green, all-failed or an unreadable list fails it. | yes |
| `jobs-backlog` | `jobs.backlog` | `exempt` | One private report page: a failed write is the stage's own failure. | yes |
| `linkedin-search` | `sources.discovery.linkedin_search` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `mail-alerts` | `physmail.stage` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `mail-card` | `physmail.card` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `mail-filing` | `sources.cards.mail_filing` | `helper` | Items run through `isolate.run_items`; partial failure keeps the stage green, all-failed or an unreadable list fails it. | yes |
| `manual-job` | `sources.pipeline.manual_job` | `exempt` | One job per run: its failure is the stage's failure. | yes |
| `megibow` | `sources.cards.megibow` | `exempt` | One card: its failure is the stage's failure. | yes |
| `network-audit` | `network.importer` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `network-import` | `network.importer` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `network-inspect` | `network.inspect` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `network-layout` | `sources.network.ledger_layout` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `network-match` | `sources.network.network_leads` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `network-surface` | `sources.network.network_surface` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `newsletter-trace` | `sources.newsletters.cardtrace` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `notion-sandbox-access` | `sources.proofs.notion_sandbox_access` | `exempt` | A one-shot proof of one sandbox page. | yes |
| `notion-sandbox-outreach-proof` | `sources.proofs.notion_sandbox_outreach_proof` | `exempt` | A one-shot proof of one sandbox page. | yes |
| `oracle-card` | `sources.cards.oracle_card` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `outlook-auth` | `run (custom)` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `outlook-probe` | `outlook.stage` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `probe` | `probe` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `publish` | `jobs.publish` | `exempt` | D212: a page that does not read back fails the stage on purpose: a write that cannot be verified is not a write. | yes |
| `publish-fresh` | `jobs.publish` | `exempt` | D212: the same stage as publish, run again later in the tick. | no |
| `purge` | `jobs.retention` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `recruiter-outreach` | `recruiters.outreach_card` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `recruiters` | `recruiters.stage` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `report-archive` | `dcc.weekly_archive` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `resolve-dice` | `jobs.resolve.aggregators.dice` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `resolve-jobright` | `run (custom)` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `resolve-linkedin` | `run (custom)` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `safety-fit-capture` | `jobs.safety` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `safety-fit-golden` | `jobs.safety` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `safety-sql-smoke` | `jobs.safety` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `scale-up-boards` | `sources.web.boardstate` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `scale-up-exclusion-review` | `jobs.exclusions` | `exempt` | One private report page: a failed write is the stage's own failure. | yes |
| `scale-up-exclusions` | `jobs.exclusions` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `scale-up-holds` | `jobs.holds` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `scale-up-links` | `jobs.linkcheck` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `scale-up-register` | `sources.discovery.scale_up_register` | `exempt` | One private report page: a failed write is the stage's own failure. | yes |
| `sponsors` | `sources.discovery.sponsor_register` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `sync-seen` | `jobs.repost` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |
| `web` | `sources.web.run` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `web-scale-up` | `sources.web.run` | `single` | No loop over external items: one read or write unit, so its failure is the stage's own. | no |
| `web-titlewatch` | `sources.web.titlewatch` | `guarded` | Loops over its items with error handling inside; one failed item does not stop the others. | no |

## Reviewed loops

Loops over external items with no `try` inside, read by hand on Nov 14 2026. None aborts a stage on one item.

| Module | Function | Iterable | Verdict |
| --- | --- | --- | --- |
| `inbox.followup_apply` | `apply` | `TABLES` | The read-back after the create: a mismatch must fail the stage (FOLLOWUP_SCHEMA_VERIFY_FAILED). |
| `inbox.apply_schema` | `apply` | `schema.TABLES` | The read-back after the create: a mismatch must fail the stage (INBOX_SCHEMA_VERIFY_FAILED). |
| `hiring.outcomes` | `contacts` | `sorted(messages, key=_received)` | Pure: it attributes already-read messages to rows; no client is called (the scan matched `search`, a regex search). |
| `imessage.comms_card` | `write` | `rows.items()` | The read-back after the writes: a mismatch must fail the stage, because a write that cannot be verified is not a write. |
| `jobs.fit.stage` | `score_rows` | `rows` | Pure scoring of rows already read (`list` here is a list operation). |
| `jobs.hold` | `mismatch_release` | `cursor.fetchall()` | Rows of one store in one connection: a store error is the stage's own failure (it cannot write its result). |
| `jobs.hold` | `run` | `RETIRED.items()` | A fixed constant table, one store query each: a store error is the stage's own failure. |
| `jobs.retention` | `purge_notion` | `pages` | Rows of one store in one connection: a store error is the stage's own failure; the Notion calls are read before this loop. |
| `probe` | `teamtailor` | `registry.load(registry.PATHS['Scale-Up'])` | A manual diagnostic probe (not scheduled); `fetch` returns a status object, it does not raise for one source. |
| `sources.interview.granola_auto` | `run` | `granola_clients.rows_where(client, granola_clients.UNFILED, True)` | Jim's triage rows; `fetch(meeting)` returns None on failure and the loop continues. |
| `sources.interview.granola_auto` | `triage` | `granola_clients.rows_where(client, granola_clients.UNFILED, True)` | Jim's triage rows; `fetch(meeting)` returns None on failure and the loop continues. |
| `sources.interview.interview_evidence` | `matches` | `sorted(records, key=lambda m: m['received'])` | Pure matching of already-read messages to requests. |
| `sources.interview.interview_evidence` | `recruiter_threads` | `urls` | The try wraps the whole loop: one failed read of the Recruiters source marks only the requests that depend on it unreadable; the rest of the stage still runs. |
| `sources.pipeline.job_page_outreach` | `outreach_rows` | `range(store.MAX_PAGES)` | Paging through the one Recruiters source the stage reads; an unreadable source fails the whole run on purpose (tests: an unreadable recruiters source fails the whole run). |
| `sources.web.boardcheck` | `run` | `chosen` | A manual board-check tool: `lister.list_source` returns a status object per source, never raises for one board; a store error is the tool's own failure. |

## Measured before this change (Nov 13 to Nov 14 2026, last 120 runs of each workflow)

Whole-run failures: `domains` 8 of 120, `hourly` 12 of 120. By fixed code: Recruiter Outreach page read `..._PAGE_WALK_NOTION_HTTP_404` 3 and `..._PROTECTED_REGION_CHANGED` 2 (a block rewritten under the read), Hiring `..._PAGE_WALK_NOTION_HTTP_404` 1, `INTERVIEW_OS_DEGRADED` 2 (one blocked item), `ORACLE_WRITE_FAILED` 2 and `COMMS_WRITE_FAILED` 1 (the iMessage cards' Notion writes), `JIRA-CARD NOTION_HTTP_429` 1, `BILLS-CARD NOTION_HTTP_409` 1, `PUBLISH_RECORDS_FAILED` 1, `WEB FAILED: BoardsFailing` 7 (a code that no longer exists in the repository) and `STORE_SSH_*` on 4 stages in one run (the Oracle store was unreachable: the stages could not write). Most are Notion page-level races and write limits (LIFE-680, 682, 688), not one bad item; the one pattern that is "one item reds a stage" is `INTERVIEW_OS_DEGRADED`, listed above as `partial-red`.

