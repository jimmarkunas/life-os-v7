# Runtime Configuration source: cutover map and rollback (LIFE-844)

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
6. Retire the old page reader and REQ-52 only on Jim's separate yes.

## Rollback (works at every step before 6)

1. Empty or remove `V7_RUNTIME_CONFIG_SOURCE_ID` in Bitwarden. The new reader returns `off` and reads nothing; `settings.apply` keeps reading the V7 settings page exactly as today (`test_rollback_is_removing_the_bootstrap_id_and_the_page_reader_is_unchanged`).
2. If the source is unreadable or empty, the last accepted map is used and the receipt says `unreadable:<CODE>` and `last_accepted`.
3. Revert the workflow PR. Nothing is deleted before step 6, so there is no data to restore; the data source can stay or be archived.

## Verification receipt (code PR)

Filled in on the PR: same-head CI run IDs, `tools/preflight.sh` result, and the test names for each acceptance criterion of LIFE-844 (reader by ID, refused and duplicate names, dual-read parity, fallback, title change, rollback).
