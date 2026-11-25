# Runtime Configuration source: cutover map and rollback (LIFE-844)

**Status (LIFE-859, Nov 23, 2026): step 6 is done. The old settings page reader, its seeding and the fallback to that page are removed from code; the Runtime Configuration source is the one settings source. REQ-52 stays in Product Requirements as history, Superseded by this source. The recovery route is the last accepted map (below). The compare-only reader is now a read-only health check; the older notes below are history.**

**Status (LIFE-892, Nov 22, 2026): Phase 2 (the switch) is in code: `settings.apply` reads the new source first and the old page second; rollback is an empty `V7_RUNTIME_CONFIG_SOURCE_ID`. Phase 1 (compare-only, LIFE-858) still runs next to it as the parity check.** `lifeos/platform/runtime_config.py` is called next to `settings.apply` in the `lifeos.run` steps that hold the bootstrap ID and, with LIFE-858, in the hourly tick and four domains jobs. It changes no environment and writes nothing. `lifeos/platform/settings.py` still reads the old page only and decides every value. Cutover steps 5 and 6 are protected: each needs Jim's explicit yes on the concrete diff.

## What the reader does

| Rule | Where it comes from |
|---|---|
| The source is found by the stable ID in `V7_RUNTIME_CONFIG_SOURCE_ID`, never by a title search and never through Product Requirements. | `runtime_config.source_id`, `fetch` |
| Rows: `Name` (title), `Value` (text), `Kind` (select: address or switch), `Revision` (number, 0 or more), `Active` (checkbox). An inactive row is skipped. | `runtime_config.REQUIRED` |
| Only names in `settings.MOVABLE` are applied. A name in `SAFETY` or ending `_TOKEN`, `_SECRET`, `_KEY`, `_PASSWORD` is refused and counted. | `settings.MOVABLE`, `settings.SAFETY` (reused, not copied) |
| An address must look like an address; `Kind` must match the name; a value with a line break or no value is invalid. | `runtime_config.parse` |
| A name with two active rows is applied from neither row (`repeated`). | `runtime_config.parse` |
| The shadow rule is unchanged: Notion fills a name the step declares empty and only compares one GitHub still holds. | `settings._fill` (reused) |
| No source, no read: `off`. An unreadable, malformed or empty source: the existing private `settings:last-accepted` map (`unreadable:<CODE>`, `last_accepted`); with no store access or no saved map the environment stays as it was. | `settings._fallback` (reused) |
| The new reader does not write the last-accepted map unless `remember=True`, which only the cutover step sets, so two sources never overwrite each other's snapshot. | `runtime_config.apply` |
| Fixed codes: `RUNTIME_CONFIG_SOURCE_ID_MISSING`, `RUNTIME_CONFIG_SOURCE_ID_INVALID`, `RUNTIME_CONFIG_UNREADABLE`, `RUNTIME_CONFIG_EMPTY`. Counts and names only, never a value. | `runtime_config` |
| `compare(old, new)` is the dual-read gate: any mismatch, a name only the old page holds or a name only the new source holds blocks the cutover. | `runtime_config.compare` |

## Consumers (found in `origin/main`, Nov 19, 2026)

* **One Python call site:** `lifeos/run.py` calls `settings.apply(os.environ)` for every stage. 20 workflows run stages through `lifeos.run`: amazon, bills, capacity-card, agenda, calendar, domains, granola, granola-auto, diag, interview-proof, fit-edge-test, manual-job, safety, profile-sync, network, hourly, outlook, hermes-stage, v3-pages, recruiters.
* **Five workflows with the `python -m lifeos.platform.settings` pre-step** (writes `$GITHUB_ENV` and `$GITHUB_OUTPUT`): branches, profile-sync, domains, readiness-live, v3-pages.
* **Notion tokens the readers use today:** `NOTION_JIRA_TOKEN` and `NOTION_API_TOKEN` (may also seed the page), plus read-only `NOTION_INTERVIEW_TOKEN`, `NOTION_RECRUITERS_TOKEN`, `NOTION_AMAZON_TOKEN`, `NOTION_BILLS_TOKEN`, `NOTION_FIT_SYNC_TOKEN` (the page is readable by them because its root page is shared with every connection).

## Permission map for the new source (all protected; none done)

| What | Who | Needed |
|---|---|---|
| Create the Runtime Configuration data source, outside Product Requirements, with the five properties above | Jim or an approved writer | Jim's yes on the parent page and schema |
| Share it (read) with the integrations behind the seven tokens above; edit only for the people who change settings | Jim | Parent chosen so those seven can already read it, or each is added by name; no other sharing, no guest access |
| Write `V7_RUNTIME_CONFIG_SOURCE_ID` (an address, not a secret) to Bitwarden, project GitHub Actions | Jim | Bitwarden writes are Jim's or the secrets service's |
| Add the name to the env of the steps that read settings (the 20 and 5 above) | one workflow PR | Protected-file approval; no new `secrets.` or `vars.` name (the frozen-list test stays green) |
| Create no new scheduler, host, SSH or database credential | | None needed: the reader uses the existing Notion client and the existing private store |

Seeding: the new reader never writes the source. While both sources run, the old page keeps its seeding; after the cutover nothing seeds, and rows are edited in Notion.

## Phase 1 evidence map (LIFE-858)

Where the compare-only read runs (a `bws_runtime_config` step loads only the bootstrap ID, `continue-on-error`, `set_env: false`; an address, not a secret; if it fails the step runs exactly as before):

| Path | Step | Notion tokens it holds (and so proves) |
|---|---|---|
| `lifeos.jobs.tick` (`hourly.yml`, finish job, the real hourly path) | the tick, read in a background thread: the stages do not wait for it | `NOTION_API_TOKEN` |
| `domains.yml` bills job | `bills-snapshot` | `NOTION_BILLS_TOKEN` |
| `domains.yml` amazon job | `amazon-orders` | `NOTION_AMAZON_TOKEN` |
| `domains.yml` recruiters job | `recruiters` | `NOTION_RECRUITERS_TOKEN`, `NOTION_API_TOKEN` |
| `domains.yml` interview job | `interview` | `NOTION_INTERVIEW_TOKEN`, `NOTION_API_TOKEN`, `NOTION_RECRUITERS_TOKEN` |
| ten workflows wired by PR #1335 (`lifeos.run`) | unchanged | `profile-sync` proves `NOTION_JIRA_TOKEN` and, with this change, `NOTION_FIT_SYNC_TOKEN` |

Receipt: `counts.runtime_config` in a `lifeos.run` line, or the `runtime_config:` line after `tick:` in the tick. Fields: `status`, `matched`, `mismatched`, `only_old`, `only_new`, `blocking`, `rows` (old and new counts), `kinds`, `seconds` (the read's own cost), `tokens` (`PASS` or `FAIL:<CODE>` for each of the seven names the step holds; a name it does not hold is not listed and is UNVERIFIED for that run), `new_counts` (names of rows refused, invalid or repeated, only when there are any) and, on a manual dispatch only, `names` (both sides' setting names; never a value).

**Step count (current code).** Before LIFE-858 the compare ran in 10 `lifeos.run` steps in 10 workflows (amazon, bills, calendar, granola-auto, granola, hermes-stage, interview-proof, manual-job, profile-sync, recruiters); PR #1335's title said "14 steps in 11 workflows", which the merged diff does not support. With LIFE-858 it also runs in the hourly tick and four domains jobs (15 steps in 12 workflows).

Run IDs, the seven-token matrix and the 59-versus-58 explanation are posted on LIFE-858. Nothing here proves Phase 2; the switch waits for Jim's separate approval.

## Cutover order (every step waits for Jim)

1. Jim lifted the LIFE-686 freeze on November 21, 2026; this cutover is released as its own `Agent Go` ticket (LIFE-844).
2. Create and share the data source (above). Fill it with `runtime_config.export_rows(<accepted map>)`, then edit by hand.
3. Dry comparison: run `runtime_config.compare(old_page_values, new_source_values)`; `blocking: false` is required. Any difference stops here.
4. Bitwarden: add `V7_RUNTIME_CONFIG_SOURCE_ID`. Workflow PR: declare it in the reading steps and call `runtime_config.shadow` next to `settings.apply` in compare-only mode (apply from the old page, report counts from the new). Natural runs for 24 hours with every name matched. **Done (PRs #1334, #1335); extended to the hourly tick and the domains jobs by LIFE-858 Phase 1.**
5. Switch (LIFE-892, Jim approved Nov 22, 2026, 6:14 PM CT): `settings.apply` reads the new source first (`remember=True`, receipt `source: new`), the old page stays as the second source (`new_source: unreadable:<CODE>` when it is used because the new source failed). **In progress: code merged, then 24 hours of natural runs.**
6. **Done (LIFE-859, Jim's yes on Nov 23, 2026, 3:48 PM CT, comment 13958):** the old page reader, its seeding and the empty-ID fallback to that page are removed; REQ-52 is Superseded, not deleted.

## Rollback and recovery (after step 6)

The old rollback (empty `V7_RUNTIME_CONFIG_SOURCE_ID` returns to the old page) no longer exists; its safe replacement is the last accepted map, proved in `tests/platform/test_settings.py::OldPageRetired`:

1. **A source that cannot be read** (unreadable, empty, malformed, or no `V7_RUNTIME_CONFIG_SOURCE_ID`): the environment is filled from the private `settings:last-accepted` map, written by the last good read (`remember`). The receipt says `status: unreadable:<CODE>` or `no_source_id`, and `last_accepted` with the time (CT); it never says `source: new`. With no saved map or no store access, the environment stays as it was.
2. The last accepted map never holds a safety name or credential, and nothing reads or writes the retired page.
3. To undo this change, revert the LIFE-859 PR (the old reader returns with its tests); the page itself was never edited or deleted.

## Verification receipt (code PR)

Filled in on the PR: same-head CI run IDs, `tools/preflight.sh` result, and the test names for each acceptance criterion of LIFE-844 (reader by ID, refused and duplicate names, dual-read parity, fallback, title change, rollback).

## MEJ and JFM cutover rows and the permanent manual runs (LIFE-918)

**Five rows, all off until the LIFE-918 activation** (registered in `settings.MOVABLE`, owned by `lifeos/accountability/config.txt`, read through `lifeos/accountability/switches.py`, which fails closed: only the exact value `on` turns a switch on).

| Row | Kind | Meaning | Default |
| --- | --- | --- | --- |
| `MEJ_SPRINT_AUTOMATION` | switch | JFM and MEJ sprint automation, on and off as one unit | off |
| `V3_WEEK_VIEW` | switch | the new V3 weekly sprint view for both boards, as native blocks to the approved wireframe: a 2 x 2 grid of counter tiles, Task commitments with Sub-tasks under their parent, blocked and overdue sections, a Done toggle with `auto-closed` marks, and the next four short sprint names. Jim (JFM) is the left callout, Matt (MEJ) the right, as the V3 page already pairs them | off |
| `V2_QUARTER_SHADOW` | switch | the v2 quarterly reader runs as a shadow beside the official legacy figures | off |
| `V2_PROMOTION` | switch | the promotion kill switch: only `on` lets the first reconciliation run at or after Fri Jan 1 2027, 8:00 AM CT decide | off |
| `V2_OFFICIAL` | switch | v2 quarterly figures are official; set only by the promotion run | off |

**The v2 preview inside the Quarterly Goals & Review blocks (LIFE-918 AC4/AC5; Jim, comment 14216: no separate preview block, so there is no `V3_PREVIEW_BLOCK_ID` row; the blocks are found by the existing V3 block ID register rows `jim.goals` and `matt.goals`).** With `V2_QUARTER_SHADOW` `on`, the goals stage upgrades each person's own table in place to Lane, Progress (ten blocks: green at 70% or more, orange 30% to 69%, red under 30%), Done / all, New count (preview) and Status. The legacy figures stay official. JFM shows a dash and a green OK. Matt shows the v2 count per lane when an approved frozen quarter list exists; otherwise a dash and a red `NEEDS Q1 MEMBER LIST` (not approved) or `DATA MISSING` (the list and Jira disagree). Notion cannot widen a table, so the first live run creates the new table under the stamp, removes the old one and reads the rows back; the title, lanes and their Epic links, the Last refreshed line, the open-tasks note and the Quarterly Reviews toggle are kept. A v2 problem never fails the stage. Off, the two-column table is untouched. See it without Notion or Jira: `python -m lifeos.accountability.preview --synthetic`.

Rollback: restore the before rows (all off), or revert the PR that reads them. These rows add no scheduler, credential or store, and the existing explicit-argument gates (`mej_metric_version`, `mej_task_focus`, `mej_selective`) are unchanged by this PR.

**Permanent manual runs** use the one existing entry point (no new workflow, scheduler or test platform):

| Mode | How | Writes |
| --- | --- | --- |
| Synthetic | `rollover.run(limit, live, environ=ENV, client=<fake>, now=<fixed date>)` in a test (`tests/jira/test_manual_modes.py`, `tests/jira/test_mej_selective_rollover.py`) | none; no network, no real client built |
| Dry run (real reads, zero writes) | `python -m lifeos.run jira-rollover` (no `--live`), or the `domains.yml` manual run with the `jira` input `rollover` and `V7_LIVE` not true | none |
| Live | `python -m lifeos.run jira-rollover --live`, or the same manual run with `V7_LIVE=true`; the scheduled path is `jira-rollover-scheduled` | only behind the existing `V7_LIVE` gates |

**The weekly view blocks (LIFE-918 AC3).** With `V3_WEEK_VIEW` `on`, the week stage writes the body of each person's existing week callout (the one-paragraph body is replaced once, then compared and left alone when unchanged): it creates the new blocks right under the stamp, removes the old body, and reads the whole tree back before success. The heading, the `Summary for Claude:` line (its page keeps the same plain lines) and every block outside the callout are untouched; a body holding any other kind of block is refused (`ACCOUNTABILITY_SHAPE`). See it without Notion or Jira: `python -m lifeos.accountability.weekview --synthetic`.
