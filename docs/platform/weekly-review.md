# Weekly LIFE OS Review (LIFE-945)

V7 is the only writer of the Weekly Reviews database (the ChatGPT "LIFE OS Daily Runs" generator was retired under LIFE-933, Nov 23 2026). Decisions: Jim, Nov 24 2026, Jira LIFE-945 comments 14132 and 14134.

**When.** The `weekly-review` stage runs in its own `weekly_review` job of `domains.yml` (after `dcc`, on the same ticks), because the Drive lane needs the tailnet. The first run at or after **Friday 10:00 PM CT** makes the row for that Friday (Week Ending = that Friday; review window Saturday to Friday; planning window the next Saturday to Friday). A run that missed Friday catches up until Sunday 10:00 PM CT (48 hours). The week ending Fri Nov 20 2026 is a one-time **backfill** (made from the sources as they are on the day it runs; the page says so; never Current once a newer week exists).

**One row per Week Ending, always.** The row is found by Week Ending. Present means nothing new is made. Two rows for one week stop the stage (`duplicate_weeks`) and write nothing. A row still `Degraded` inside 48 hours of its first write is rebuilt in place from fresh reads (every lane is read again).

**Six lanes, all read-only; each shows Healthy or the exact reason.** Jira, Calendar, Gmail and Outlook, and Notion read the V7 cards on the Daily Report (what the hourly stages already wrote; Jira must be 30 minutes old or less, the others 3 hours or less). Automation reads the `hourly.yml` and `domains.yml` runs of the review week through the GitHub API with the workflow's own token. Drive reads through the governed Oracle reader: the job joins the tailnet as the ephemeral `tag:v7-runner` node (the policy admits it only to Oracle TCP 47341) and sends one `drive1 <days>` line to the private endpoint, which forwards it to a read-only helper (`lifeos/edge/drive/`, installed by `lifeos/edge/drive/oracle/install.sh`; it loads the three Drive credential files Oracle already holds for the governed MCP, adds none, lists file metadata only and answers counts only). If Oracle cannot be reached only the Drive lane is Degraded, with its reason. Source Health is `Healthy` only when all six are.

**Writes.** Only: the new row and its page body, the Current flag on the previous Current row, and the one Dashboard pointer line. Status is always `Generated`; `Reviewed` and `Applied` are Jim's. The Decision Queue comes from fixed rules over the lanes (no model call, no spend); `Open Decisions` is its length.

**Settings (Runtime Configuration):** `WEEKLY_REVIEW` (switch; `on` or the stage does nothing), `NOTION_WEEKLY_REVIEWS_DATA_SOURCE_ID`, `WEEKLY_REVIEW_PAGE_ID`, `WEEKLY_REVIEW_POINTER_BLOCK_ID`.

**Rollback.** Set `WEEKLY_REVIEW` to off: the stage reads and writes nothing. Archive the created row in Notion. Put the pointer line back to its last text (the previous line is in the PR proof comment on LIFE-945).

**Oracle install and rollback.** `sudo bash lifeos/edge/drive/oracle/install.sh install <dir>` puts the helper tree in `/opt/lifeos-drive`, the socket and service units in `/etc/systemd/system`, backs up and replaces the private endpoint, and runs one smoke call through the socket (it must answer `ok`, or the script rolls the endpoint back). `sudo bash install.sh rollback` stops the helper and restores the previous endpoint.
