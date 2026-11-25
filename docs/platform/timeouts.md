# Timeouts and stage budgets (LIFE-691)

Nothing in the pipeline can wait forever. Three layers, each measured, none left as "none":

1. **Every workflow job and long step has `timeout-minutes`** (table below). Measured over the last 100 runs of each workflow on Nov 14 2026, timed-out jobs included.
2. **Every network call in `lifeos/` has an explicit timeout** (table below; a contract test fails a call without one).
3. **Every stage has a time budget in seconds** (`lifeos/platform/stage_budgets.json`). A stage that spends it stops its own work, records the fixed code `STAGE_BUDGET_EXCEEDED` (the Jobs tick records its own `STAGE_TIMEOUT_<n>S`), and fails the run only if it is a required stage.

## How the job timeouts were set

The ticket's rule: twice the slowest run of the last 100, rounded up to whole minutes. Added on purpose, all of them only make a timeout safer:

* A timeout is never raised; it is only tightened.
* Floor of 5 minutes: a job's own set-up (checkout, Python, the Bitwarden helper that waits up to 400 s) varies.
* A job whose Bitwarden helper waits W seconds keeps at least 3 x W: `tests/contracts/test_bws_helper.py` already requires that the helper's retry never takes more than a third of the job's limit, so those jobs (most domain jobs) stay at 15 to 30 minutes. The stage budgets below are what stops a slow stage early inside such a job.
* A job whose slowest run reached 90% of its current limit keeps it and is listed for Jim below: it is at its limit and a smaller number would only cut real work.
* A job with fewer than 10 measured runs, or none, keeps its value: too little evidence to tighten.
* `amazon.yml` keeps 330 minutes: its backfill stage reads every historical month and runs for hours by design (D140).

**No timeout under 10 minutes was set for a job that has ever run longer than 10 minutes** (the ticket's stop rule): the slowest run in the sample is the base, so a value is always at least twice any run seen. Jobs set under 10 minutes and their slowest run are in the table.

The job timeouts in `.github/workflows` change in one separate workflow pull request (workflow files are protected). Until it lands, the jobs listed as `pending` in `lifeos/platform/timeouts.json` still have their old value; a test keeps that list honest and the workflow pull request empties it.

## Workflow jobs

| Workflow | Job | Now (min) | Target (min) | Runs measured | Median (s) | Slowest (s) | Basis |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| `agenda.yml` | `agenda` | 15 | 15 | 5 | 16 | 85 | kept: only 5 measured runs (fewer than 10), slowest 85 s |
| `amazon.yml` | `amazon` | 330 | 330 | 28 | 60 | 1037 | kept: the backfill stage reads every historical month and runs for hours by design (D140); the 330-minute limit is pinned on purpose |
| `bills.yml` | `bills` | 15 | 15 | 3 | 10 | 10 | kept: only 3 measured runs (fewer than 10), slowest 10 s |
| `branches.yml` | `archive` | 5 | 5 | 96 | 14 | 24 | twice the slowest run (24 s) rounded up to whole minutes, 5-minute floor |
| `branches.yml` | `report` | 10 | 10 | 0 | - | - | not measured: no completed run in the last 100; keeps its current value |
| `calendar.yml` | `calendar` | 15 | 15 | 6 | 12 | 18 | kept: only 6 measured runs (fewer than 10), slowest 18 s |
| `diag.yml` | `diag` | 10 | 10 | 33 | 14 | 97 | twice the slowest run (97 s) rounds to 10 min, not below the current 10: unchanged |
| `diag.yml` | `inbox_view_qa` | 10 | 10 | 0 | - | - | not measured: new job (LIFE-711); keeps its current value |
| `diag.yml` | `interview_ui` | 5 | 5 | 10 | 240 | 302 | slowest run 302 s reached 90% of the current limit: keeps 5 min and is listed for Jim (the job is at its limit) |
| `diag.yml` | `report` | 10 | 10 | 10 | 14 | 19 | twice the slowest run (19 s) rounds to 10 min, not below the current 10: unchanged |
| `diag.yml` | `sandbox` | 5 | 5 | 4 | 10 | 30 | kept: only 4 measured runs (fewer than 10), slowest 30 s |
| `diag.yml` | `trace` | 10 | 10 | 5 | 13 | 16 | kept: only 5 measured runs (fewer than 10), slowest 16 s |
| `domains.yml` | `agenda` | 15 | 15 | 45 | 32 | 87 | twice the slowest run (87 s) rounds to 15 min, not below the current 15: unchanged |
| `domains.yml` | `amazon` | 15 | 15 | 45 | 19 | 30 | twice the slowest run (30 s) rounds to 15 min, not below the current 15: unchanged |
| `domains.yml` | `amazon_retention` | 15 | 15 | 45 | 255 | 556 | twice the slowest run (556 s) rounds to 19 min, not below the current 15: unchanged |
| `domains.yml` | `bills` | 15 | 15 | 45 | 28 | 90 | twice the slowest run (90 s) rounds to 15 min, not below the current 15: unchanged |
| `domains.yml` | `chain` | 5 | 5 | 0 | 0 | 0 | kept: new job (LIFE-904), reads the Actions API only |
| `domains.yml` | `dcc` | 10 | 10 | 45 | 31 | 57 | twice the slowest run (57 s) rounds to 10 min, not below the current 10: unchanged |
| `domains.yml` | `gate` | 5 | 5 | 45 | 4 | 9 | twice the slowest run (9 s) rounds to 5 min, not below the current 5: unchanged |
| `domains.yml` | `granola` | 20 | 20 | 45 | 36 | 56 | twice the slowest run (56 s) rounds to 20 min, not below the current 20: unchanged |
| `domains.yml` | `hiring` | 15 | 15 | 46 | 92 | 124 | twice the slowest run (124 s) rounds to 15 min, not below the current 15: unchanged |
| `domains.yml` | `hiring_outcomes` | 30 | 30 | 31 | 18 | 22 | twice the slowest run (22 s) rounds to 30 min, not below the current 30: unchanged |
| `domains.yml` | `imessage` | 10 | 10 | 45 | 113 | 162 | twice the slowest run (162 s) rounds to 10 min, not below the current 10: unchanged |
| `domains.yml` | `interview_mail` | 30 | 30 | 0 | - | - | not measured: no completed run in the last 100; keeps its current value |
| `domains.yml` | `interview` | 30 | 30 | 31 | 442 | 678 | twice the slowest run (678 s) rounds to 30 min, not below the current 30: unchanged |
| `domains.yml` | `fit_cloud_backlog` | 58 | 58 | 12 | 58 | 107 | LIFE-856: moved from the hourly finish job unchanged; its batch ends by :55 and the 58 minute limit it had there |
| `domains.yml` | `followup` | 15 | 15 | 0 | - | - | not measured: no completed run in the last 100; keeps its current value |
| `domains.yml` | `jira` | 15 | 15 | 45 | 82 | 117 | twice the slowest run (117 s) rounds to 15 min, not below the current 15: unchanged |
| `domains.yml` | `mail` | 10 | 10 | 45 | 28 | 90 | twice the slowest run (90 s) rounds to 10 min, not below the current 10: unchanged |
| `domains.yml` | `megibow` | 20 | 20 | 45 | 36 | 334 | twice the slowest run (334 s) rounds to 20 min, not below the current 20: unchanged |
| `domains.yml` | `outlook` | 10 | 10 | 45 | 22 | 498 | twice the slowest run (498 s) rounds to 17 min, not below the current 10: unchanged |
| `domains.yml` | `network_surface` | 20 | 20 | 12 | 109 | 209 | LIFE-856: moved from the hourly finish job unchanged (109 s p50, 209 s slowest as Step 7c there); the 20 minute limit of the other late jobs kept |
| `domains.yml` | `recruiters` | 30 | 30 | 54 | 86 | 1501 | twice the slowest run (1501 s) rounds to 51 min, not below the current 30: unchanged |
| `domains.yml` | `report` | 5 | 5 | 8 | 8 | 10 | kept: only 8 measured runs (fewer than 10), slowest 10 s |
| `domains.yml` | `weekly_review` | 10 | 10 | 0 | - | - | new job (LIFE-945): not measured yet; 10 min like the other card jobs, the stage budget is 600 s |
| `fit-edge-test.yml` | `fit_edge` | 5 | 5 | 12 | 27 | 76 | twice the slowest run (76 s) rounds to 5 min, not below the current 5: unchanged |
| `granola-auto.yml` | `auto` | 20 | 20 | 10 | 43 | 109 | twice the slowest run (109 s) rounds to 20 min, not below the current 20: unchanged |
| `granola.yml` | `granola` | 15 | 15 | 4 | 28 | 41 | kept: only 4 measured runs (fewer than 10), slowest 41 s |
| `hermes-stage.yml` | `hermes` | 10 | 10 | 5 | 10 | 12 | kept: only 5 measured runs (fewer than 10), slowest 12 s |
| `hourly.yml` | `finish` | 58 | 58 | 100 | 116 | 815 | kept at Jim's decision (Nov 15 2026, approve, leave prep and finish): the tick's 35-minute budget and the D238 backlog batch to :55; revisit after a week of runs |
| `hourly.yml` | `interview` | 15 | 15 | 0 | - | - | not measured: no completed run in the last 100; keeps its current value |
| `hourly.yml` | `jobright` | 40 | 40 | 0 | - | - | not measured: no completed run in the last 100; keeps its current value |
| `hourly.yml` | `lensa` | 40 | 40 | 0 | - | - | not measured: no completed run in the last 100; keeps its current value |
| `hourly.yml` | `linkedin` | 40 | 40 | 0 | - | - | not measured: no completed run in the last 100; keeps its current value |
| `hourly.yml` | `prep` | 45 | 45 | 100 | 25 | 235 | kept at Jim's decision (Nov 15 2026, approve, leave prep and finish): up to 25 min gate wait for an older run (D210) plus the Bitwarden helper wait plus the tests; revisit after a week of runs |
| `hourly.yml` | `report` | 5 | 5 | 100 | 2 | 16 | twice the slowest run (16 s) rounds to 5 min, not below the current 5: unchanged |
| `hourly.yml` | `unit-tests` | 20 | 20 | 0 | - | - | new job (LIFE-856 change 1): the unit tests took 125 to 235 s inside `prep` (slowest prep run 235 s); 20 minutes keeps generous room; revisit after a week of runs |
| `interview-proof.yml` | `proof` | 30 | 30 | 18 | 506 | 1614 | twice the slowest run (1614 s) rounds to 54 min, not below the current 30: unchanged |
| `manual-job.yml` | `manual` | 15 | 15 | 4 | 47 | 71 | kept: only 4 measured runs (fewer than 10), slowest 71 s |
| `mariadb-race.yml` | `race` | 10 | 10 | 0 | - | - | new job (LIFE-903): a MariaDB container start (about 30 s), pip install and a 10 s race; 10-minute guard, no run measured yet |
| `network.yml` | `network` | 30 | 30 | 100 | 539 | 1818 | slowest run 1818 s reached 90% of the current limit: keeps 30 min and is listed for Jim (the job is at its limit) |
| `outlook.yml` | `outlook` | 20 | 20 | 20 | 11 | 227 | twice the slowest run (227 s) rounds to 20 min, not below the current 20: unchanged |
| `pr-tests.yml` | `test-index` | 11 | 11 | 39 | 247 | 305 | twice the slowest run (305 s) rounded up to whole minutes, 5-minute floor |
| `pr-tests.yml` | `unit-tests` | 5 | 5 | 99 | 105 | 136 | twice the slowest run (136 s) rounded up to whole minutes, 5-minute floor |
| `pr-tests.yml` | `unit-tests-second-date` | 5 | 5 | 0 | - | - | new job (LIFE-692): the same tests as `unit-tests` (slowest 136 s) with the test clock fixed at a second date, pull requests only; 5-minute floor; no run measured yet |
| `profile-sync.yml` | `settings` | 5 | 5 | 0 | - | - | new job (LIFE-703): reads the V7 settings page once for the Fit profile sync switch, the Bitwarden helper wait is 60 s; 5-minute floor; no run measured yet |
| `profile-sync.yml` | `sync` | 10 | 10 | 39 | 15 | 248 | twice the slowest run (248 s) rounds to 10 min, not below the current 10: unchanged |
| `readiness-live.yml` | `readiness` | 10 | 10 | 1 | 8 | 8 | kept: only 1 measured runs (fewer than 10), slowest 8 s |
| `recruiters.yml` | `recruiters` | 20 | 20 | 9 | 389 | 404 | kept: only 9 measured runs (fewer than 10), slowest 404 s |
| `safety.yml` | `safety` | 20 | 20 | 12 | 22 | 44 | twice the slowest run (44 s) rounds to 20 min, not below the current 20: unchanged |
| `suite-timing.yml` | `plan` | 2 | 2 | 4 | 3 | 4 | kept: only 4 measured runs (fewer than 10), slowest 4 s |
| `suite-timing.yml` | `report` | 5 | 5 | 4 | 11 | 18 | kept: only 4 measured runs (fewer than 10), slowest 18 s |
| `suite-timing.yml` | `timing` | 5 | 5 | 40 | 95 | 135 | twice the slowest run (135 s) rounded up to whole minutes, 5-minute floor |
| `boards.yml` | `window` | 5 | 5 | 0 | - | - | not measured: new job (LIFE-854): one read of this workflow's own run history; 5-minute floor |
| `boards.yml` | `boards` | 35 | 35 | 0 | - | - | not measured: new job (LIFE-854, Jim's sizing, lowered Nov 22 2026 after the measured 172 s per 1,036 boards): the catch-up window's 25-minute crawl step plus checkout, the Bitwarden helper wait and the tailnet join |
| `capacity-card.yml` | `capacitycard` | 10 | 10 | 0 | - | - | not measured: new job (LIFE-793); keeps its current value |
| `v3-pages.yml` | `v3pages` | 10 | 10 | 0 | - | - | not measured: new job (LIFE-793); keeps its current value |
| `watchdog.yml` | `watch` | 3 | 3 | 100 | 9 | 34 | already under the 5-minute floor; slowest run 34 s |
| `watchdog.yml` | `alarm` | 3 | 3 | 0 | n/a | n/a | new in LIFE-704: two GitHub API calls; kept at the 3-minute limit it is created with |

### At their limit (for Jim)

* `diag.yml::interview_ui`: slowest run 302 s against a limit of 5 min: this job reaches its limit. It keeps 5 min; whether it should run longer or do less is a decision for Jim.
* `network.yml::network`: slowest run 1818 s against a limit of 30 min: this job reaches its limit. It keeps 30 min; whether it should run longer or do less is a decision for Jim.

## Steps with their own timeout

| Workflow | Job | Step timeout (min) |
| --- | --- | ---: |
| `hourly.yml` | `linkedin` | 4 |
| `boards.yml` | `boards` | 15 (window), 25 (catch-up) |
| `hourly.yml` | `finish` | 15, 10 |

A step has no timeout of its own unless listed: it is bounded by its job's `timeout-minutes` and, for a stage, by its stage budget.

## Network calls

Every call that leaves the process carries an explicit timeout. `tests/contracts/test_network_timeouts.py` scans `lifeos/` and fails any call to `urlopen`, `create_connection`, `pymysql.connect`, `requests` or `httpx`, any subprocess call to `ssh`, `curl`, `gh`, `scp` or `tailscale`, and any socket `connect` without one.

| Where | Call | Timeout (seconds) |
| --- | --- | --- |
| `lifeos/edge/oracle/close_release.py` | `urlopen` | `20` |
| `lifeos/edge/secrets/lifeos_secret_use.py` | `urlopen` | `FETCH_WAIT` |
| `lifeos/imessage/comms_card.py` | `create_connection` | `WAIT` |
| `lifeos/imessage/openclaw_imessage.py` | `create_connection` | `TIMEOUT` |
| `lifeos/imessage/stage.py` | `create_connection` | `15` |
| `lifeos/oraclecloud/card.py` | `create_connection` | `WAIT` |
| `lifeos/platform/db.py` | `subprocess.run(net)` | `30 if jump else 20` |
| `lifeos/platform/gate.py` | `urlopen` | `30` |
| `lifeos/platform/gcal.py` | `urlopen` | `self._timeout` |
| `lifeos/platform/gmail.py` | `urlopen` | `self._timeout` |
| `lifeos/platform/notion_client.py` | `urlopen` | `30` |
| `lifeos/platform/outlook.py` | `urlopen` | `timeout` |
| `lifeos/platform/rest.py` | `urlopen` | `timeout` |
| `lifeos/platform/scheduled_jobs.py` | `urlopen` | `30` |
| `lifeos/platform/socket_exchange.py` | `create_connection` | `connect_timeout` |
| `lifeos/platform/tinyfish.py` | `urlopen` | `timeout` |
| `lifeos/platform/tinyfish_search.py` | `urlopen` | `timeout` |
| `lifeos/sources/devtools/profile_runs.py` | `subprocess.run(net)` | `120` |
| `lifeos/platform/db.py` | `pymysql.connect` (Oracle store through the tunnel) | connect 10, read 20, write 20 |
| `lifeos/sources/apps/resume_app.py`, `lifeos/imessage/alerts_endpoint.py` | unix socket `connect` | `settimeout(wait)` before `connect` |
| `lifeos/jobs/resolve/aggregators/jobright.py` | browser actions (`click`, `fill`, `wait_for`, `wait_for_load_state`) | each carries `timeout=` in milliseconds |
| `lifeos/edge/backup/*`, `lifeos/edge/granola/gran_helper.py` | `sqlite3.connect` | `timeout=` 10 to 30 (local files, not the network) |

## Stage budgets

`lifeos/platform/stage_budgets.json` has one row for each of the 116 stages in `lifeos/run.py`. `lifeos.run` arms an alarm of that many seconds around the stage. Rules:

* A stage within its budget behaves exactly as before.
* A stage over it stops, prints the fixed code `STAGE_BUDGET_EXCEEDED` with the two numbers (counts only), and **a required stage exits 1, an optional stage exits 0** so the run passes and the code stays visible in the stage's counts line.
* **Required** means the stage has a workflow step without `continue-on-error`; in the Jobs tick (`lifeos/jobs/tick.py`) every stage except the bulk drain stages. The Jobs tick keeps its own alarm and its fixed code `STAGE_TIMEOUT_<n>S`; its optional (bulk) stages now time out without failing the tick, a required stage still fails it.
* A budget is the stage's slowest measured step times two, rounded up to 30 s, at least 120 s, and never above its job's limit minus two minutes, so the stage records its code before GitHub ends the whole job.
* A stage no workflow runs gets the table default of 600 seconds.

The table below is rendered from `lifeos/platform/stages.json` (LIFE-768): edit a stage's budget there, then run `python tools/regen.py`.

| Stage | Budget (s) | Required | Basis |
| --- | ---: | --- | --- |
| `accountability-artifacts` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `accountability-changelog` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `accountability-goals` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `accountability-review` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `accountability-week` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `agenda-card` | 180 | no | twice its slowest measured step (77 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `agenda-snapshot` | 120 | no | twice its slowest measured step (58 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `alerts` | 120 | no | twice its slowest measured step (11 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `alerts-open` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `amazon-backfill` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `amazon-card` | 120 | no | twice its slowest measured step (15 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `amazon-census` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `amazon-orders` | 120 | no | twice its slowest measured step (5 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `amazon-retention` | 780 | no | twice its slowest measured step (547 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `amazon-review-reset` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `audit` | 180 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `audit-fresh` | 60 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `audit-ready` | 60 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `bills-card` | 120 | no | twice its slowest measured step (9 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `bills-paid` | 150 | no | twice its slowest measured step (61 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `bills-snapshot` | 120 | no | twice its slowest measured step (7 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `board-check` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `boards` | 1380 | yes | the employer-board windows (boards.yml, LIFE-854): a catch-up window's 25-minute step with the soft deadline 3 minutes before it, the hard alarm 1 minute after the soft deadline's margin; a window that cannot finish turns the run red (required) |
| `boards-backfill` | 600 | no | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `boards-preview` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `boards-tick` | 300 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `calendar-sync` | 240 | yes | twice its slowest measured step (113 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `capacity-card` | 120 | no | twice its slowest measured step (44 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `dcc-card` | 120 | no | twice its slowest measured step (41 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `discover-jobs` | 300 | no | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `drain-audit-ready` | 60 | no | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `drain-enrich` | 480 | no | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `drain-fit` | 240 | no | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `drain-publish` | 720 | no | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `enrich` | 480 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `expire-holds` | 120 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `explain` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `fit` | 240 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `fit-cloud-backlog` | 3000 | no | LIFE-41 (Nov 19 2026): the scheduled batch runs up to the 45-minute cap of cloud.BACKLOG_BUDGET_MAX (2700 s, D238) plus one in-flight call to settle (165 s), rounded to 3000 s; the tick job's limit is 58 minutes. The Nov 14 value of 180 s was the alarm cloud.remaining() honours, so every batch stopped after 2 calls |
| `fit-edge-test` | 120 | yes | twice its slowest measured step (23 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `fit-profile-sync` | 480 | yes | twice its slowest measured step (234 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `fit-review` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `followup` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `followup-schema-apply` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `followup-schema-check` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `funnel` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `granola` | 120 | yes | twice its slowest measured step (27 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `granola-auto` | 210 | yes | twice its slowest measured step (98 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `hermes` | 120 | yes | twice its slowest measured step (3 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `hiring` | 240 | no | twice its slowest measured step (107 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `hiring-outcomes` | 120 | no | twice its slowest measured step (8 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `imessage-alerts` | 120 | no | twice its slowest measured step (59 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `imessage-comms` | 120 | no | twice its slowest measured step (46 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `inbox-schema-apply` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `inbox-schema-check` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `inbox-view` | 600 | yes | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `interview` | 1680 | yes | twice its slowest measured step (1604 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `interview-acceptance` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `interview-mail` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `interview-probe` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `interview-ui-probe` | 300 | yes | twice its slowest measured step (296 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `jira-card` | 150 | no | twice its slowest measured step (70 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `jira-fill` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `jira-probe` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `jira-reconcile-daily` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `jira-release` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `jira-rollover` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `jira-rollover-scheduled` | 120 | no | twice its slowest measured step (4 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `jira-rollover-watch` | 120 | no | twice its slowest measured step (53 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `jira-snapshot` | 120 | no | twice its slowest measured step (29 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `jira-stalled` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `job-page-outreach` | 120 | no | twice its slowest measured step (5 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `jobs-backlog` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `linkedin-search` | 180 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `mail-alerts` | 120 | no | twice its slowest measured step (60 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `mail-card` | 120 | no | twice its slowest measured step (12 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `mail-filing` | 120 | no | twice its slowest measured step (3 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `manual-job` | 120 | yes | twice its slowest measured step (57 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `megibow` | 660 | no | twice its slowest measured step (318 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `network-audit` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `network-import` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `network-inspect` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `network-layout` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `network-match` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `network-surface` | 210 | no | twice its slowest measured step (100 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `newsletter-trace` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `notion-sandbox-access` | 120 | yes | twice its slowest measured step (1 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `notion-sandbox-outreach-proof` | 120 | yes | twice its slowest measured step (23 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `oracle-card` | 180 | no | twice its slowest measured step (82 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `outlook-auth` | 450 | yes | twice its slowest measured step (218 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `outlook-probe` | 120 | yes | twice its slowest measured step (2 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `probe` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `publish` | 720 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `publish-fresh` | 300 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `purge` | 480 | no | twice its slowest measured step (240 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `recruiter-outreach` | 180 | no | twice its slowest measured step (87 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `recruiters` | 1230 | yes | twice its slowest measured step (605 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `report-archive` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `resolve-dice` | 180 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `resolve-jobright` | 600 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `resolve-linkedin` | 420 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `safety-fit-capture` | 120 | yes | twice its slowest measured step (10 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `safety-fit-golden` | 120 | no | twice its slowest measured step (12 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `safety-sql-smoke` | 120 | no | twice its slowest measured step (19 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `scale-up-boards` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `scale-up-exclusion-review` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `scale-up-exclusions` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `scale-up-holds` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `scale-up-links` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `scale-up-register` | 600 | no | no workflow runs this stage (manual or developer use): the table default |
| `sponsors` | 120 | no | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `sync-seen` | 180 | no | twice its slowest measured step (86 s), rounded up to 30 s, at least 120 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |
| `web` | 180 | yes | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `web-scale-up` | 300 | no | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `web-titlewatch` | 240 | no | the Jobs tick plan (lifeos/jobs/tick.py): its own alarm per stage, optional when it is a bulk stage |
| `weekly-review` | 600 | no | no measured step: the table default 600 s; in each workflow never above that job's limit minus 2 minutes; the largest of its workflows' values |

## Stuck-run reaper

LIFE-580 made the gate and the ticker ignore a tick run that is queued or running for more than 40 minutes, so one stuck run cannot stop every later tick, and opened one incident (`tick_run_stuck`, INFO, state key in the alert registry). LIFE-691 adds the cancel: the watchdog (`lifeos/platform/gate.py`, run by the GitHub watchdog and by the Oracle ticker) asks GitHub to cancel each stuck tick run (`POST /actions/runs/<id>/cancel`).
* GitHub accepts the cancel and the run ends: no alert; the stuck incident never opens.
* The watchdog token stays `actions: read` (decided Nov 15 2026, no permissions change); until Jim grants more, GitHub refuses the cancel and the incident below is the signal.
* GitHub refuses (as it did on Nov 11 2026), or the run is still active 20 minutes after the stuck limit (a cancel the next watchdog cycle should have finished): the one `tick_run_stuck` incident opens, once, through the alert gateway; the skip rule keeps later ticks running.
* The incident closes when no tick run is stuck.

The Oracle ticker runs its own installed copy of `lifeos/platform/gate.py`, so the cancel takes effect there after the ticker install (docs/SETUP.md step 37).

