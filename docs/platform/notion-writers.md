# Notion writers on the Daily Report page (LIFE-688)

One card owns one region of the Daily Report page and writes only its own blocks, by block id. This file is the list of every writer. `tests/contracts/test_notion_writers.py` reads it: a module that writes the page but is not listed here fails CI, and so does a whole-page digest caller that is not listed.

## The rules

1. **One owner per region.** `lifeos/platform/router.py` `OWNERS` names the owner of each region. A region has exactly one writer row below.
2. **Own blocks only, enforced before the write is sent.** Every writer goes through `report_region.ScopedClient` (directly, or inside `report_region.replace_text` and `replace_tree`). A PATCH, POST or DELETE aimed at a block the card has not found under its own region is refused with `<CARD>_WRITE_OUT_OF_SCOPE` and never reaches Notion (LIFE-586, 613, 656; the Jira card joined in LIFE-688).
3. **No before-and-after comparison of the whole page.** Writers do not compare other cards' regions around their write: a legitimate write by another card at the same moment looked like a violation (LIFE-656). The only whole-page reads are listed in "Whole-page reads" below, and none compares before and after on the Daily Report page.
4. **A failed card write keeps the last accepted card.** A write that is refused or fails leaves the last card Notion accepted; nothing is invented. A write that fails after its heading stamp puts the old stamp back (LIFE-722).
5. **A new Notion writer adopts this by adding two entries:** one `OWNERS` entry in `lifeos/platform/router.py` and one row in the table below (module, region constant, owner, job and stage, how it finds its block, guard). The writer calls `ScopedClient` (or `replace_text`), so rule 2 holds. The contract test then passes. Source-health alerts for such a writer go through the alert gateway with a state key (LIFE-690).

## Daily Report writers

| module | region | owner | job.stage | how it finds its block | guard |
|---|---|---|---|---|---|
| lifeos/jira/card.py | JIRA_REGION | v7-jira | jira.jira-card | JIRA_CARD_BLOCK_ID callout; lists its own children | ScopedClient |
| lifeos/agenda/card.py | CALENDAR_REGION | v7-calendar | agenda.agenda-card | CALENDAR_CARD_BLOCK_ID callout | ScopedClient |
| lifeos/bills/card.py | BILLS_REGION | v7-bills | bills.bills-card | BILLS_CARD_BLOCK_ID callout | replace_text |
| lifeos/amazon/card.py | AMAZON_REGION | v7-amazon | amazon.amazon-card | AMAZON_CARD_BLOCK_ID callout | replace_text |
| lifeos/physmail/card.py | MAIL_ALERTS_REGION | v7-mail-alerts | mail.mail-card | MAIL_ALERTS_CARD_BLOCK_ID callout | replace_text |
| lifeos/hiring/card.py | HIRING_REGION | v7-hiring | hiring.hiring | HIRING_CARD_BLOCK_ID heading; its table found next to it | ScopedClient |
| lifeos/recruiters/outreach_card.py | RECRUITER_OUTREACH_REGION | v7-recruiter-outreach | recruiters.recruiter-outreach | heading and table found by one read of the page (the table block id names the page) | ScopedClient |
| lifeos/imessage/comms_card.py | COMMS_REGION | v7-comms | imessage.imessage-comms | the Communications callout's heading and Messages row, found on the page that JIRA_CARD_BLOCK_ID is on | ScopedClient |
| lifeos/oraclecloud/card.py | ORACLE_REGION | v7-oracle | imessage.oracle-card | its heading and one paragraph, found under the page | ScopedClient |
| lifeos/dcc/card.py | DCC_REGION | daily-command-center | dcc.dcc-card | DCC_CARD_BLOCK_ID callout | ScopedClient |
| lifeos/dcc/header.py | - | - | dcc.dcc-card | the page's date line: one top-level paragraph of a fixed shape, found among the page's top-level blocks; created at the top only if none exists | page-level, read back |
| lifeos/jobs/fit/capacity_card.py | - | - | capacity-card.capacity-card | the AI Capacity callout, found among the page's top-level blocks by its table header; inserted after the last divider if absent; off unless V7_AI_CAPACITY_CARD is on | in place, read back |

The two rows with `-` are not regions in `OWNERS`: the date line is the page header and the AI Capacity card is a top-level callout outside the half-width column. Both are page-level writes (a top-level read and, at most, one insert); they run in jobs of their own (`dcc` runs last, after every card job; `imessage` also holds the Communications and Oracle cards, which write inside their own callouts), so their page-level operations do not overlap another card's blocks.

## Other writers of Notion pages (not the Daily Report)

| module | page | note |
|---|---|---|
| lifeos/platform/branches.py | the Weekly LIFE OS Review (REPO_REGION) | one writer; compares the page before and after because nothing else writes that page |
| lifeos/dcc/weekly_archive.py | new child pages under the Weekly Reports Archive (WEEKLY_ARCHIVE_PAGE_ID); the Daily Report's Notes callout, only if the page has one | creates pages only; the one edit to the Daily Report is the Monday Notes reset, after the prior week's snapshot re-reads exactly equal; every write is read back |
| lifeos/sources/pipeline/job_page_outreach.py | each job's page in the Job Ledger | one writer per job page; compares the page before and after for the same reason |
| lifeos/accountability/region.py | the This week, Quarterly goals and Quarterly review callouts on Jim & Matt V3, found by the V3 block ID register (jim.thisweek, matt.goals, ...) | each callout and its summary page is read and written by block id inside the callout (ScopedClient) and read back |
| lifeos/megibow/card.py, lifeos/megibow/v3.py | the MegIBOW callouts, found by MEGIBOW_BLOCK_ID and the V3 block ids | each callout is read and written by block id and read back |

## Whole-page reads

`report_region.page_digest` walks a page. It is allowed in these places only; the contract test fails on any other caller.

| module | page | use |
|---|---|---|
| lifeos/hiring/card.py | Daily Report | once, before the write, to prove the Hiring heading is unique; nothing is compared afterwards |
| lifeos/recruiters/outreach_card.py | Daily Report | once, before the write, to prove the Recruiter Outreach heading is unique; nothing is compared afterwards |
| lifeos/sources/pipeline/job_page_outreach.py | a job page | before and after one write, single writer |
| lifeos/platform/branches.py | the Weekly Review | before and after one write, single writer |

A walk can meet a block another job just rewrote (Notion answers 404). `page_digest` therefore walks the page again once (LIFE-680) and fails with the same fixed code only if the second walk fails too.

## What was measured (production logs, Nov 12 to Nov 14 2026)

The last 120 `domains` runs (63 ran, 57 were skipped by the due table) and the last 120 `hourly` runs (59 ran). Fixed codes and counts only; each cell is the number of runs in which the code surfaced in that job's log.

| job | code | runs before the LIFE-680 and LIFE-682 merges (Nov 14, 1:02 PM and 1:36 PM CT) | runs after |
|---|---|---|---|
| hiring | PAGE_WALK_NOTION_HTTP_404 | 7 | 0 |
| mail | NOTION_HTTP_409 | 4 | 0 |
| recruiters | PAGE_WALK_NOTION_HTTP_404 | 4 | 0 |
| recruiters | PROTECTED_REGION_CHANGED | 4 | 0 |
| agenda | NOTION_HTTP_409 | 3 | 0 |
| imessage | NOTION_HTTP_429 | 2 | 0 |
| bills | NOTION_HTTP_409 | 1 | 0 |
| jira | NOTION_HTTP_429 | 1 | 0 |

Since the 1:36 PM CT merge, 12 of 12 domains runs succeeded with none of these codes. The `hourly` runs show `NOTION_HTTP_404` and `NOTION_HTTP_502` only in the `prep` job, which runs the unit tests (their fixtures print those codes); no hourly stage writes the Daily Report page. On the Oracle instance (lifeos-edge-01, read-only) no unit writes the Daily Report page: the units are Close, Granola, resume, WhatsApp, the ticker and the backup.

Reading: the 409 and 404 codes came from retries not yet in place (LIFE-654, 680, 682) and from walks that met a rewritten block; the cards themselves already wrote inside their own regions.

## Serialization: why there is no concurrency group

The ticket preferred a GitHub concurrency group around the Notion write step. Three facts decide against it:

- GitHub concurrency is per job or per workflow, never per step, so it would serialize the whole job, not just its write.
- A concurrency group keeps one running and one waiting run; a newer waiting run replaces the older one. With nine writer jobs in one group, waiting jobs would be cancelled and skipped, the failure that `domains.yml` already warns about for its own run group.
- In-run serialization is possible only with `needs:` chains. The writer jobs' measured medians (seconds) are jira 82, hiring 93, imessage 112, recruiters 48, megibow 35, agenda 31, dcc 30, bills 27, mail 27, amazon 19. Chained, they take about 470 seconds on the critical path, close to the 506 seconds the longest full run already takes (the `interview` job is 441 seconds), so the added time is estimated at 25 to 70 seconds. That adds a failure chain (one red job would delay or skip later cards unless every job keeps `always()`).

**Decided by Jim (Nov 15 2026, about 12:05 AM CT): option B.** Only the hiring and recruiters writer jobs are chained (the two that walk the page to prove their heading is unique); the other writer jobs stay in parallel. Workflow PR #1191 makes that change (rule recorded: option B chosen by Jim Nov 15 2026, rule: any Notion write conflict in a day moves the Daily Report writers to the full chain (option A)). Option A is the full chain of every writer job.

- Option B chains only the two jobs that read the whole Daily Report page: `hiring`, then `recruiters`. One `needs:` edge is added (`recruiters` needs `gate`, `jira`, `hiring`). Both jobs keep their own `if:`; `recruiters` keeps `always() && needs.gate.result != 'failure'`, so a red `hiring` job never stops it, and `hiring` still waits only for `gate`.
- Order and why: `recruiters` already waited for `jira`, and `hiring` has the longest whole-page walk (median 93 s against 48 s), so it starts first with the others, and `recruiters` is the one that waits. Replaying the last 20 domains runs with the new edge, the added time is 0 s in the median and the 90th percentile, and 25 s in the single worst run.
- Option A (the full chain jira, agenda, bills, mail, amazon, recruiters, hiring, imessage) stays prepared as the next step if the rule above fires.
- No permissions change: the watchdog token stays `actions: read`.

## The daily write-conflict count and the rule (LIFE-688)

The daily check-in (the GitHub watchdog's once-a-day push, `lifeos/platform/gate.py`) carries one line:

`Notion write conflicts in the last 24 h: N (hiring a, recruiters b); rate limits: M.`

- **A write conflict** is one of these fixed codes surfacing in a domains-run job log in the last 24 hours: `NOTION_HTTP_409`, a page-walk 404 (`PAGE_WALK_NOTION_HTTP_404`, with or without a job's prefix such as `HIRING_`), or `PROTECTED_REGION_CHANGED`. Each (job, code) counts once per run, so a retry loop that prints a code twice is one conflict. N is the total; the parentheses give N per job.
- **A rate limit** is `NOTION_HTTP_429`. It is counted apart (M) and is never a write conflict.
- **Counts per job by fixed code only.** No log line, name, id or message is kept or shown.
- **Read-only and bounded.** The count reads the logs of the domains runs finished in the last 24 hours through the GitHub API with the watchdog's existing token (`actions: read`; no permission change): newest first, at most 30 runs, at most 40 million bytes of log archive and at most 90 seconds in all (the watchdog job's own timeout is 3 minutes and its slowest run 34 seconds), one archive per run. One unreadable run is counted under `WRITE_CONFLICT_LOGS_UNREADABLE` (`lifeos/platform/isolate.py`) and the other runs are still counted; a reached cap says `WRITE_CONFLICT_CAP_REACHED`; runs that could not be listed say `WRITE_CONFLICT_COUNT_UNAVAILABLE`. These codes appear only in the check-in text: the count never pages by itself and never turns the check-in to DEGRADED (it is an INFO report under the health model's `daily_check` key).
- **The rule.** When N is above 0 the line says so plainly and ends with: `rule: any write conflict moves the Daily Report writers to the full chain (option A, LIFE-688).` That is the standing decision: one conflict is enough to change from option B to option A. When N is 0 the check-in shows the count and nothing else in it changes.

- **Counting starts at the decision.** Runs before Jim's decision (Nov 15 2026, 12:05 AM CT) are history, so a conflict from before the fixes cannot trigger the rule. Until 24 hours have passed since then the window is shorter, and the line ends with `Counted since the writer-chain decision (Nov 15 2026, 12:05 AM CT).` After that the count is a plain 24-hour window and says nothing about it (`gate.CONFLICT_COUNT_FROM`).

The line is also listed in `docs/platform/health-model.md` (the `daily_check` row).

## The stamp is written first, and put back on failure (LIFE-722)

`replace_text` (used by the Bills, Amazon and Mail Alerts cards) sets the heading stamp first and the body after. Before LIFE-722, a write that failed after the stamp PATCH left the new stamp over the old body: a stale card looked fresh. Now, when anything after the stamp write fails (a refused body write, a read-back that does not match), `replace_text` puts the previous stamp back, once, best effort, and the caller still sees the failure the card already reported. If Notion refuses the restore too, the caller sees the original failure and the stamp stays as written (the same as before the fix).

Chosen over "write the stamp last" because it changes nothing when every write succeeds (the same requests in the same order, the same card; no request is added) and is a small local change, while moving the stamp would reorder the writes of three cards and add a read-back request. Every other card writer (Jira, Calendar, Communications, Oracle Cloud) and the floating-region writers (Hiring, Recruiter Outreach, job-page outreach) already write the heading after the body; the floating writers also mark a half-written heading DEGRADED. A body that was written in part (some lines rewritten before a later one was refused) stays as it is; only the stamp is put back, so the card shows an old time over a partly new body rather than claiming to be current.

## Agent Inbox (LIFE-791)

Not a Daily Report writer. Agents and people write rows into one database (the inline "Agent Inbox list" on Jim's Agent Inbox page); V7 only adds rows through `send_to_jim` (`lifeos/inbox/agent_inbox.py`) and rewrites the five tile numbers of that page (`recount_page`). It does not use `report_region` or `ScopedClient`, so the rules above do not apply to it; see `docs/platform/agent-inbox.md`.

## Agent Inbox tiles on the merged page (LIFE-938)
| module | region | owner | job.stage | how it finds its block | guard |
|---|---|---|---|---|---|
| lifeos/platform/agent_inbox.py `recount_page` | the Now and Today box and the This week, FYI and Snoozed tiles | agent-inbox | domains `agent-inbox` and every `send_to_jim` | the one live page titled `Daily LIFE OS Report (Hourly)` whose top blocks hold the four-column tile row (else the old `Agent Inbox` page); no id is committed | patches only the box's own children and the three tile numbers, only when they differ; a changed row shape is the fixed refusal `tiles_shape_changed`; none or two pages is `page_not_found` or `page_ambiguous` |

