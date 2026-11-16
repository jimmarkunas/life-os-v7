# PR CI cost and escapes (LIFE-744, Nov 16 2026)

Measured read-only with `gh api` on Nov 16 2026. Two sets: the last 30 merged PRs (the run for each merged head, 31 runs) and every `pr-tests` run since Nov 11 (400 runs; 57 completed both test jobs).

## Cost

| Job | Runs | Median | Slowest | Total job time |
|---|---|---|---|---|
| `unit-tests` | 31 | 83 s | 107 s | 2,298 s |
| `unit-tests-second-date` | 31 | 82 s | 106 s | 2,283 s |
| `test-index` | skipped on every PR (runs on a push to main only, about 3 minutes) | | | |

The two test jobs run in parallel, so the PR wall time does not double, but the job time does: the second date is about half of the test minutes on every PR. A run is about 45 to 55 s when the selector picks targets and 80 to 107 s when it picks everything.

## What the second date caught

In the 57 runs where both jobs finished, `unit-tests` failed 5 times and `unit-tests-second-date` failed 5 times, in the same 5 runs. It never failed alone and none of the 31 merged heads failed either job. So in these 5 days it caught nothing the main job did not.

Decision: the job stays on the PR path for now. The obvious move, running it on a push to main, would make a failure there turn main's run red, and the Close release waits for main's own run (see the comment above the job in `pr-tests.yml`). Moving it needs its own design (a separate workflow or a result the release ignores) and a decision from Jim, so this ticket records the data and does not change the workflow.

## Why PR 1235 passed and main failed docstyle

PR 1235 added two files under `lifeos/inbox/` with module docstrings over the 1,200 character limit. The PR run uses the test selector (`tests/testindex.py`): a production file the index does not know falls back to the mirror test folder, `tests/inbox`, plus `tests/contracts`. The docstyle check is `tests/platform/test_docstyle.py`, which scans every package but lives outside both, so it was never selected. The push to main runs everything, so main failed 20 minutes later and a fix PR (1237) was needed.

Fix: `REPO_WIDE` in `tests/testindex.py` lists tests that scan every production file (today only `tests.platform.test_docstyle`). `select()` adds them whenever a `.py` file under `lifeos/` changes, so the same case now fails on its own PR. Test: `tests/test_testindex.py`.
