# Runtime Configuration source: cutover map and rollback (LIFE-844)

**Status: code only.** `lifeos/platform/runtime_config.py` is merged but called by nothing. `lifeos/platform/settings.py` and every workflow are unchanged. Nothing below is done, approved or scheduled; each protected step needs Jim's explicit yes on the concrete diff, and the LIFE-686 stability freeze stays in force until then.

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

## Cutover order (every step waits for Jim)

1. LIFE-686 stability window ends, or Jim lifts the freeze for this change.
2. Create and share the data source (above). Fill it with `runtime_config.export_rows(<accepted map>)`, then edit by hand.
3. Dry comparison: run `runtime_config.compare(old_page_values, new_source_values)`; `blocking: false` is required. Any difference stops here.
4. Bitwarden: add `V7_RUNTIME_CONFIG_SOURCE_ID`. Workflow PR: declare it in the reading steps and call `runtime_config.apply` next to `settings.apply` in compare-only mode (apply from the old page, report counts from the new). Natural runs for 24 hours with every name matched.
5. Switch: `settings.apply` uses the new source first (`remember=True`), the old page stays as the second source for 24 more hours.
6. Retire the old page reader and REQ-52 only on Jim's separate yes.

## Rollback (works at every step before 6)

1. Empty or remove `V7_RUNTIME_CONFIG_SOURCE_ID` in Bitwarden. The new reader returns `off` and reads nothing; `settings.apply` keeps reading the V7 settings page exactly as today (`test_rollback_is_removing_the_bootstrap_id_and_the_page_reader_is_unchanged`).
2. If the source is unreadable or empty, the last accepted map is used and the receipt says `unreadable:<CODE>` and `last_accepted`.
3. Revert the workflow PR. Nothing is deleted before step 6, so there is no data to restore; the data source can stay or be archived.

## Verification receipt (code PR)

Filled in on the PR: same-head CI run IDs, `tools/preflight.sh` result, and the test names for each acceptance criterion of LIFE-844 (reader by ID, refused and duplicate names, dual-read parity, fallback, title change, rollback).
