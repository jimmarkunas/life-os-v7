# Employer boards inside the hourly tick (LIFE-905)

One crawler owns the employer boards at a time. The `BOARDS_IN_TICK` switch (`lifeos/platform/switches.json`) names it: **off** = the four daily windows (`boards.yml`, LIFE-854), **on** = the hourly Jobs tick's `boards-tick` step. The release PR ships with it **off**.

## How the two stand down

- Tick owns: the window gate (`lifeos.platform.boards_window`) skips every scheduled or tick-started run (`tick_owns`), and the tick's dispatch starts no window. A manual `boards.yml` run is still a manual run.
- Windows own: the `boards-tick` step prints `owner: windows, skipped` and opens no store session.
- Overlap guard: while the tick owns the boards it still skips the pass when any `boards.yml` run is queued or running, or when that cannot be read (`window_running`, fail closed). So a flip made while a window runs never gives two crawlers.

## The tick pass

The same reader as a window (`lifeos.sources.web.boards.run`): boards already read once, due ones only, oldest due first; the same cap (1,800), 4 workers, the 3-answers-of-429 stop per ATS, the held store connection, the six-hour hot-board refresh. A 4-minute soft deadline inside the 5-minute step budget keeps the receipt; what is left is first in the next hourly pass.

## Flip and rollback

- Flip: a reviewed PR changing `"state"` of `BOARDS_IN_TICK` to `on`, after three clean natural ticks of the shipped step observed (see LIFE-905). Flipped Nov 23, 2026, 11:23 AM CT, after ten clean no-op ticks.
- Rollback: a PR setting it back to `off` (or reverting). The windows resume from the same state tables; a window the tick era left unrun is a catch-up (leftovers first), and a board is read at most once per due time, so no frontier row is written twice.

## Production Contract amendment (draft for Jim)

> **Employer boards.** Employer boards are crawled hourly, due boards only, inside the Jobs tick, by exactly one crawler owner at a time. A board that lists a target job is read again within six hours; any other COMPLETE board within 24 hours; a FAILED board is retried per its backoff and is never a removal. The frontier moves only on a COMPLETE read. Provider protections are unchanged (4 workers, 3 answers of 429 stop that ATS for the run). A tick that finds a boards run in progress, or cannot tell, leaves boards to the next tick. The four daily board windows (1:00 AM, 7:00 AM, 1:00 PM, 7:00 PM Central) are retired once three consecutive natural ticks show due-only reads, freshness within six hours, no overlap and no save timeout; until then they are the fallback owner and the rollback target.
