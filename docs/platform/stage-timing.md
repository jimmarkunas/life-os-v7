# Stage timing receipts (LIFE-855)

Every stage the hourly tick runs prints one `stage_timing: {json}` line to stderr beside its existing `stage <name>: <outcome> <seconds>s` line (that line is unchanged). A standalone stage (`lifeos.run` in a domains job) prints the same line when `STAGE_TIMING_FILE` is set; the domains workflow sets it for every job. Names and numbers only; no identity or content.

| Field | Meaning |
| --- | --- |
| `stage` | the stage name |
| `outcome` | `complete`, `degraded` (finished but reports failed items or says degraded) or `incomplete` (failed, timed out or skipped) |
| `result` | the tick's own word: `ok`, `failed`, `timeout`, `skipped` |
| `begin`, `end` | Central-time ISO stamps (`2026-11-22T19:02:00-06:00`) |
| `elapsed_s` | seconds the stage ran |
| `wait_s` | seconds it waited for its services' slots before its budget started, plus the seconds its own thread waited in `read_slots`. A wait in a worker thread a stage starts is counted under no stage (`stage_timing.unattributed()`), never guessed into one |
| `items` | the stage's own declared item count, or `null` (unknown, never zero). A stage declares its count by an `items_key` on its step; none is declared yet (the stage metadata files wait for LIFE-894's PR) |
| `run` | the GitHub run id |

The hourly `finish` job also appends each line to `stage-timing-<run id>.jsonl` and uploads it as the artifact `stage-timing-<run id>` (14 days; the pinned `actions/upload-artifact` v4.6.2; no new permission or secret).

## The report

`python tools/stage_timing_report.py --from 2026-11-23 --to 2026-11-24` (Central dates) downloads the artifacts of the hourly ticks in that range with `gh` (read only) and prints, per stage: runs, p50, p95, max, share of the run's wall time, wait p50, how often it was on the critical path and its critical seconds, how many runs were not complete and how many runs lack it. `--dir DIR` reads files already downloaded; `--steps` adds the `prep`, `finish` and `report` jobs from GitHub's own timestamps. A run with no artifact, or a stage a run lacks, is UNVERIFIED and left out of the numbers, never counted as zero. The critical path of a run is walked back from its latest end through the latest-ending stage that had ended when the previous one began.

## API calls per job (LIFE-1112)

A stage's row carries `api_calls` when it made outside calls: one count per service (`gmail`, `notion`, `jira`, `graph`, `todoist`, `gcal`) from `lifeos/platform/api_counts.py`, which the shared REST layer and the Notion and Gmail clients call once per HTTP attempt, plus the Gmail gate's own tallies (units, seconds waited, slowdowns, `gmail_lease_yield`, `gmail_lease_open`). `python tools/api_receipt.py RUN_ID [RUN_ID ...]` reads the runs' logs with `gh` (read only) and prints one table: a row per job, a column per service, a total row, and the gate tallies under it. A job that printed no count has no row (UNVERIFIED, never zero).

Rollback: revert the pull request; nothing else reads these lines.
