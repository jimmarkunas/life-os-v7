# Scheduled checks and the test clock (LIFE-692)

Two things used to rot without anyone being told: a scheduled workflow that kept failing (the weekly safety check was red from Oct 19 to Nov 9 and nothing paged), and a test that quietly read the real clock (a date-bound test stopped the jobs pipeline from publishing on Nov 6, LIFE-349). This page is the list of both, and the rules that keep them from going quiet again.

## Scheduled workflows

Every workflow with a `cron:` trigger is in this table. `tests/contracts/test_scheduled_checks.py` fails if a scheduled workflow is missing from it, or from the central check in `lifeos/platform/gate.py`.

| Workflow | Cadence | What it checks | Failure alert |
|---|---|---|---|
| `hourly.yml` | three slots an hour (7, 27, 47) | the pipeline tick: the gate picks one run an hour | Covered already: `pipeline_failing`, `pipeline_stalled` and `run_failed` page for it (`SCHEDULED_COVERED`). Its scheduled runs are ticks, so a red tick is not a second alert. |
| `boards.yml` | four windows a day: 1:00 AM, 7:00 AM, 1:00 PM and 7:00 PM CT (two UTC cron lines per window, daylight-saving safe, as a backup: GitHub's own cron never fired for this workflow, so the hourly tick starts each window with `boards_window dispatch`) | the employer-board crawl, moved out of the hourly tick (LIFE-854): cap 1,800 boards and a 15-minute step timeout per window, 2,900 and 25 minutes for a catch-up window; one receipt per window | `scheduled_red_boards` (PAGE). A red window makes the next one a catch-up (leftovers first). Closes on one finished scheduled pass. |
| `capacity-card.yml` | hourly (55), and the hourly tick starts it when its last run is 40 minutes or more old (LIFE-957: GitHub's own cron fired it only every 5 to 10 hours) | the AI Capacity card, moved out of the hourly imessage job (LIFE-793) | Covered already (`SCHEDULED_COVERED`): the one stage only warns; no second alert. |
| `v3-pages.yml` | after each hourly domains run (LIFE-1000), cron hour :40 as the fallback | the five V3 accountability page stages, moved out of the hourly jira job (LIFE-793) | Covered already (`SCHEDULED_COVERED`): the stages only warn (LIFE-730), so the run does not go red from a stage; no second alert. |
| `watchdog.yml` | hourly (17, 47) | the pipeline-stale issue, the alert sweep and the central scheduled check | `scheduled_red_watchdog` (PAGE). A watchdog cannot see its own failure from inside, so the Oracle ticker's copy of `gate.watch` runs the same check and covers it. Closes after 3 finished scheduled passes in a row. |
| `safety.yml` | Mondays 8:17 AM CT | the smoke and golden checks | `scheduled_red_safety` (PAGE). Closes on one finished scheduled pass (weekly: three passes would take three weeks). |

## The central check

One check in `gate.watch`, not an edit to each workflow file (workflow files are protected, and a step added to each one is a step that can be forgotten or break).

- It lists each scheduled workflow's runs with the watchdog's read-only token (`actions: read`; no `actions: write` is ever added). One list call per workflow, `event=schedule`, newest first.
- The signal is the newest **finished** scheduled run: failed (`failure`, `timed_out`, `startup_failure`) is red; the last N finished runs passed is green; anything else is no change. Cancelled, skipped, unfinished and manual runs are ignored.
- It goes through the alert gateway with a state key: `scheduled_red_<name>` pages **once** on red, a second red run pages nothing, and one `scheduled_recovered_<name>` push goes out on recovery (health model rules 2 and 3; `docs/platform/health-model.md`). The incident is one GitHub issue per workflow, label `scheduled-red-<name>`, so the watchdog and the ticker copy never both page.
- If the workflow already phoned the failure itself (`safety.yml`'s `Phone alert` step succeeded), the check stays quiet: one alert per failure.
- If the runs cannot be read, it records the fixed code `SCHEDULED_RUNS_UNREADABLE=<names>` in the watchdog line and never pages.

## The test clock

`tests/kit/clock.py` is the one clock a test may read: a fixed moment, or the moment in the environment variable `LIFEOS_TEST_NOW` (an ISO date or time, UTC when no zone is given). `FrozenDateTime` and `FrozenDate` replace `datetime` and `date` in a module that asks them for now.

`tests/contracts/test_frozen_clock.py` fails when a test file calls the real clock (`datetime.now`, `datetime.utcnow`, `datetime.today`, `date.today`, `time.time`) and is not in the allowlist below. The allowlist can only shrink: a file listed here that no longer calls the real clock fails the same test, so it is removed in the PR that converts it.

To prove a result does not depend on today's date, run the suite twice with two dates:

```
LIFEOS_TEST_NOW=2026-11-14 sh tools/test.sh --all
LIFEOS_TEST_NOW=2027-03-09 sh tools/test.sh --all
```

CI runs the second date on every pull request that changes more than docs (a docs-only change skips it, LIFE-770): the job `unit-tests-second-date` in `.github/workflows/pr-tests.yml` runs the same tests as `unit-tests` (the tests the change can affect plus `tests/contracts`, or the whole suite in three shards) with `LIFEOS_TEST_NOW=2027-03-09`. It runs beside `unit-tests`, so the wall time of the check does not grow; it costs one extra runner job per pull request run (about 87 s median, 136 s at most, measured on the last 33 pull request runs of `unit-tests`). It is an additional check, not the required `unit-tests` check, so branch protection is unchanged (`docs/platform/branch-protection.md`): a red run shows on the pull request and names the date that broke it. It runs on pull requests only, so a failure there can never turn a merge-to-main run red or hold back the Close release.

### Allowlist: test files that still read the real clock

Each one is here because the **production code under test** reads the real clock itself, so moving only the test's side of the date would make the test depend on the distance between the fixture date and today. They move to the fixture when that production clock can be injected. Format: `` `path` — reason ``.

- `tests/edge/test_close_auto_sim.py` — dates a wait file with `time.time()`; the close gate under test compares it with the real clock.
- `tests/imessage/test_relay.py` — `lifeos/imessage/openclaw_imessage.py` reads `datetime.now(timezone.utc)` inline; the test patches only its `NOW` window.
- `tests/jira/test_readiness_mcp.py` — sets file mtimes relative to now; the code under test compares them with the real clock.
- `tests/jobs/fakes_jobsdb.py` — fake of the MySQL `UTC_TIMESTAMP()`: it has to answer with the real moment, as the database does.
- `tests/jobs/support.py` — fake mailbox's `newer_than:` window, read against the real clock by the code under test.
- `tests/jobs/test_enrich.py` — posting dates read by the enrich stage against the real clock.
- `tests/jobs/test_newsletter_golden_path.py` — newsletter ingest and finalize read the real clock.
- `tests/jobs/test_us_remote_golden_path.py` — boards and enrich read the real clock for the same run.
- `tests/sources/newsletters/test_cardtrace.py` — ingest cards are aged against the real clock.
- `tests/sources/newsletters/test_ingest.py` — ingest and tombstones are aged against the real clock.
- `tests/testindex.py` — the test-index tool, not a test: takes `now` as a parameter and defaults to the real clock; the test of it (`tests/test_testindex.py`) uses the fixture.
