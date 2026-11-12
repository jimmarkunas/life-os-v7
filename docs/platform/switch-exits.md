# Switches and their exits (LIFE-604 item 14)

Every switch that can still change how the pipeline runs, where it lives (one place), its default, and the condition under which it is removed. A switch without a written exit is not added (the freeze in LIFE-604).

| Switch | Default | The one place | Exit condition | State on Nov 12 2026 |
| --- | --- | --- | --- | --- |
| `TICK_CONCURRENCY` | removed (stages of a group always run side by side) | none | done | Removed in LIFE-604 approval 5; a test fails if the name returns. |
| `BOARDS_BACKFILL` | off | `lifeos/platform/switches.json` (state and bounds; turning it on is a reviewed PR) | the first pass is drained (about 10,500 never-read boards at 450 per tick, then it hibernates at zero): delete the stage and the entry | The workflow no longer passes a repository variable. The variable existed with value `off`; it has no reader left and is deleted after this PR merges (read-back below the table in the PR and the ticket). |
| `BROWSER_CACHE_BUST` | unset (no suffix on the two browser cache keys) | repository variable, passed to `.github/actions/browser` as `cache_bust` by both jobs that install the browser | set it only when the log says `BROWSER_LIBS_CACHE_FAILED`; delete it the day after the rebuilt copy is used (it currently holds an old value from LIFE-576 and still names the live cache keys, so deleting it makes one cold run) | Kept. Exit written here and in the action. |
| `NETWORK_SURFACE_SKIP` | on (skip pages proven earlier and untouched since) | repository variable read as `${{ vars.NETWORK_SURFACE_SKIP \|\| 'on' }}` in `hourly.yml`; code default is also on | remove the switch and the full-read branch after 30 days of green ticks with the skip on (LIFE-572 measured 11 green ticks, step 7c about 68 s) | Kept as a kill switch. The variable does not exist today. Removing the switch also removes the full-read path that the replay tests in `tests/network/test_surface.py` use, so that is its own change. |
| `HIRING_AUTO_CLOSE` | on | `lifeos/platform/switches.json` | none planned: it is a product behaviour, bounded in the file | Unchanged. |

## The legacy skip rule in `network_surface.py`

The ticket says to remove the legacy skip rule only after every Ledger page has a recorded edit time (the trust limit forces a full read of each page otherwise). **Not removed in this change.** What was checked: the counts line of recent ticks reports `edit_table: true`, `pages_unread: 0` and `published_pages` about 2,550 with 1 to 3 pages changed per tick; that shows the edit table is being filled, not that every page has an entry. Proving "every page" needs a read of the store's edit table, which this change did not do. The rule stays until that count is read.
