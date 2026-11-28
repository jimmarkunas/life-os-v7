# Scheduled checks and the test clock (LIFE-692)

Two things used to rot without anyone being told: a scheduled workflow that kept failing (the weekly safety check was red from Oct 19 to Nov 9 and nothing paged), and a test that quietly read the real clock (a date-bound test stopped the jobs pipeline from publishing on Nov 6, LIFE-349). This page is the list of both, and the rules that keep them from going quiet again.

## Scheduled workflows

Every workflow with a `cron:` trigger is in this table. `tests/contracts/test_scheduled_checks.py` fails if a scheduled workflow is missing from it, or from the central check in `lifeos/platform/gate.py`.

| Workflow | Cadence | What it checks | Failure alert |
|---|---|---|---|
| `hourly.yml` | three slots an hour (7, 27, 47) | the pipeline tick: the gate picks one run an hour | Covered already: `pipeline_failing`, `pipeline_stalled` and `run_failed` page for it (`SCHEDULED_COVERED`). Its scheduled runs are ticks, so a red tick is not a second alert. |
| `capacity-card.yml` | hourly (55), and the hourly tick starts it when its last run is 40 minutes or more old (LIFE-957: GitHub's own cron fired it only every 5 to 10 hours) | the AI Capacity card, moved out of the hourly imessage job (LIFE-793) | Covered already (`SCHEDULED_COVERED`): the one stage only warns; no second alert. |
| `v3-pages.yml` | after each hourly domains run (LIFE-1000); the cron `40 * * * *` is only a configured fallback, because GitHub fires it about twice a day (Nov 26 2026) | the five V3 accountability page stages, moved out of the hourly jira job (LIFE-793) | Covered already (`SCHEDULED_COVERED`): the stages only warn (LIFE-730), so the run does not go red from a stage; no second alert. |
| `watchdog.yml` | hourly (17, 47) | the pipeline-stale issue, the alert sweep and the central scheduled check | `scheduled_red_watchdog` (PAGE). A watchdog cannot see its own failure from inside, so the Oracle ticker's copy of `gate.watch` runs the same check and covers it. Closes after 3 finished scheduled passes in a row. |
| `safety.yml` | cron `17 13 * * 1` (UTC) = Mondays 7:17 AM CST, 8:17 AM CDT. GitHub starts it late: the scheduled runs of Nov 9, Nov 16 and Nov 23 began 9 h 41 min, 9 h 35 min and 11 h 32 min after the slot | the smoke and golden checks | `scheduled_red_safety` (PAGE). Closes on one finished scheduled pass (weekly: three passes would take three weeks). |

`boards.yml` is not in this table: it has no `schedule:` or cron (retired by LIFE-905, Nov 23 2026). The hourly tick crawls the due boards itself and no longer dispatches the workflow; `boards.yml` is only a manual run. Its old failure alert, `scheduled_red_boards`, has no scheduled run left to watch.

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

CI runs the second date in the job `unit-tests-second-date` of `.github/workflows/pr-tests.yml` with `LIFEOS_TEST_NOW=2027-03-09` (LIFE-1100 rule):

* **On a pull request** (a docs-only change skips it, LIFE-770) it runs only the date-sensitive test modules listed in `tests/second-date-tests.txt`, about 20 s of tests. `tests/contracts/test_second_date_list.py` keeps that list a superset of every test module that imports `tests.kit.clock`, reads the real clock (the allowlist below) or imports an allowlisted helper such as `tests/jobs/fakes_jobsdb.py`. Add a module to the list whenever its result can depend on today's date.
* **On a merge to main** it runs the whole suite as 4 parallel jobs of 4 of 16 shards (each under 60 s). The job is `continue-on-error` there: a red second date on main shows in the job but never turns the run red, because the Close release and the test gate read that run's verdict and only the required `unit-tests` job decides it. Fix a red one as a Bug.
* It is an additional check, not the required `unit-tests` check, so branch protection is unchanged (`docs/platform/branch-protection.md`); a red pull request run names the date that broke it.

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
- `tests/um/test_um_retrieval_final.py` — builds a fabricated gate token whose `exp` is `time.time()` plus a lifetime; the gate under test checks it against the real clock.
- `tests/um/test_um_retrieval_live.py` — same fabricated token, `exp` set against `time.time()`; the live gate under test reads the real clock.
- `tests/workflows/test_workflow_gate.py` — sets a saved Jira read's file mtime relative to now; the gate under test compares it with the real clock.
