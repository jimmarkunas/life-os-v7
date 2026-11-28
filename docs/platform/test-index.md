# Test index and affected-test runs (LIFE-548)

A pull request used to run all ~4,300 tests. It now runs the tests its change can affect, plus every test under `tests/contracts`. A merge to main and the hourly run always run everything. The index is not committed: CI builds it on main and pull requests restore it (LIFE-644).

## The index

`docs/test-index.json` (git-ignored) says, for every production file under `lifeos/` (path relative to `lifeos/`), which tests run at least one of its lines. It is made by running the whole suite once under coverage.py with one coverage context per test.

* Make one locally (optional): `pip install coverage` (it is in `requirements-test.lock`) then `python -m tests.testindex build` (about 3 minutes; it writes the ignored `docs/test-index.json`). Never commit or hand-edit it. Without one, `sh tools/test.sh` and the selector run everything (FULL, about 80 s, cheaper than a build).
* Shape: `tests` (sorted test ids), `modules` (test module to file), `skipped` (indexes of skipped tests), `covers` (production file to indexes into `tests`), `uncovered` (production files no test runs), plus `schema`, `commit` and one `generated_at`. Only repo-relative paths and dotted test ids; no data, no absolute path, no secret.

## The selector

`python -m tests.testindex select --base HEAD^1` prints the unittest targets for the files changed since the base, or the single word `FULL`. When in doubt it says FULL.

| Changed file | Runs |
|---|---|
| `tests/kit/**`, `tests/*.py` harness files, any helper under `tests/`, `.github/**`, `.claude/**`, `requirements*`, `*.lock`, `lifeos/platform/**`, `docs/PRIVACY.md`, config files, any unknown file | everything (FULL) |
| a `lifeos/` file in the index | the tests the index maps to it, plus the tests named after the module in the mirror folder (`tests/<package>/test_<module>*.py`; the index can be days old) |
| a `lifeos/` file not in the index (new file) | its whole mirror folder `tests/<package>/`; no mirror folder means FULL |
| a `test_*.py` file | that whole file |
| docs and `*.md` files | nothing extra |
| always | every module in `tests/contracts` |

`tests/contracts` holds only the checks that scan the whole repository (privacy, size, generated indexes, registries, every workflow's structure, secret least-privilege). A test about one feature lives with it: `tests/workflows/` (one workflow's wiring and the workflow tools), `tests/tooling/` (the test runner, selector and CI setup), `tests/um/` (the memory pilots), or the package folder (`tests/buzz/`, `tests/jira/`, `tests/platform/`, `tests/sources/`, `tests/docs/`). The selector picks them by the usual rules when their feature's files change (LIFE-1098 step B).

## CI

`.github/workflows/pr-tests.yml` (required check `unit-tests`, unchanged): the step "Pick the tests" runs the selector on pull requests only; on a push to main it writes FULL. A FULL pick runs the two parallel shards as before; a subset runs `python -m unittest <targets>`. Any trouble picking (the selector fails, the base is missing, the index is unreadable) means FULL. The 90 s warning and 120 s limit still apply.

**Job graph and the 60 s rule (LIFE-1100):** `pr-tests.yml` is five jobs. `pick` chooses the tests and `python -m tests.splitsel` plans the matrix; `tests (n)` runs it: a FULL pick is 8 jobs of 4 of the 32 shards of `python -m tests.shard K 32`, a selected run is 1 to 4 jobs (about 10 CI seconds of tests each, split by `tests/shard-weights.json`) each running its group in four processes; `lint` runs ruff, mypy and the Node tests beside them; the last job, still named exactly `unit-tests` (the required check), runs always, fails unless pick, every test job and lint succeeded, and checks the whole-run wall time (earliest job start to the verdict). Every CI invocation must take 60 s or less: a test job over 60 s fails, the whole run over 60 s fails (message `TIME GATE ONLY` when every test passed), 45 s warns. At most 10 jobs of one run run together; GitHub Free allows 20 at once. Rebalance with `python -m tests.shard --from-logs <shard logs>`.

**Measured Nov 27 2026 (LIFE-1100):** a FULL run is 37 to 45 s from the earliest job start to the verdict (slowest test job 26 to 31 s, pick 5 to 7 s, lint 11 to 13 s); `tests.edge.whatsapp.test_whatsapp_row1_pack` was split in three (24 s to 11, 14 and 7 s) because it was the slowest module; the contracts alone are about 120 CI seconds of tests, so a selection that includes them runs as 4 jobs.

**Second date (LIFE-1100):** the job `unit-tests-second-date` does not use the selector. A pull request runs only the modules in `tests/second-date-tests.txt` (a superset, kept by `tests/contracts/test_second_date_list.py`, of every module that imports the clock fixture or reads the real clock); the full suite on the second date runs on a merge to main only. Rule and reasons: `docs/platform/scheduled-checks.md`.

**Where the index comes from (kept on purpose, not on any trigger):** the build is the workflow `.github/workflows/test-index.yml`, `workflow_dispatch` only, no schedule, no push, no `workflow_run`, contents read, no secret. Jim, Nov 28 2026: it is off every main run (the 60 s CI rule; a main push now ends at the `unit-tests` verdict) but kept so the functionality is not lost. Run it when you want faster, narrower selections for pull requests: `gh workflow run test-index.yml`. It restores the newest index and runs `python -m tests.testindex check --max-age-days 1`; a fresh one is reused, otherwise it installs the test lock (which holds `coverage`), runs `python -m tests.testindex build` (about 250 s) and saves `docs/test-index.json` with the cache action under `test-index-v1-<hash of tests/testindex.py and requirements-test.lock>-<commit>`. A pull request restores the newest cache whose key starts with the same prefix (restore only, it never saves) before the selector runs; with none, or one older than 7 days, the selector uses the import graph (`reaching`) instead of running everything. It is intentionally unreferenced by any trigger, so a dead-code scan must not delete it: `tests/workflows/test_test_index.py` fails if the workflow is missing, gains a trigger, or `tests/testindex.py` loses its `build` or `check` command.

## Parked packages (LIFE-772)

Job search and Interview OS (`lifeos/jobs`, `interview`, `hiring`, `recruiters`, `megibow`: about 18 % of the CI test seconds in `tests/shard-weights.json`) are imported only by `lifeos/sources` and the top-level entry points. So a pull request that changes any other package never selects their tests (measured with a built index: a change to `lifeos/inbox`, `jira` or `sources` selects none of them), and a change to `lifeos/platform`, `.github/**`, the shared test kit or a lock file is FULL, which runs them. `lifeos/run.py` selects the tests that cover it, which include some of theirs. `tests/contracts/test_parked_boundary.py` (always run) reads the real imports and fails if any other package starts importing one of the five, naming the importer, because then those tests must run when it changes. Main, the hourly run and `--all` still run everything.

## A wrong index cannot skip a test it should run

* **Different builder or tools:** the cache key includes a hash of `tests/testindex.py` and `requirements-test.lock`; an index made by other code or other pinned tools is never restored. A pull request that edits either file is FULL anyway.
* **Age:** `select` ignores an index whose `generated_at` is more than 7 days old (FULL). Main's own build is the freshness; nothing checks it hourly any more.
* **Missing or damaged:** no cache, a missing or non-JSON file, a wrong schema, missing keys or numbers outside the test list all print FULL (`tests/test_testindex.py SelectCommand`).
* **A lie about a file:** the tests named after a changed module (`tests/<package>/test_<module>*.py`) and every module in `tests/contracts` run whatever the index says; a file the index does not list runs its whole mirror folder, or FULL. Adding, renaming or deleting a source needs no rebuild (LIFE-588).
* **Poisoning:** pull requests only restore; the only job that saves is `test-index`, on a push to main.

## Limits (read before trusting a green subset)

* Coverage shows what a test *runs*, not everything it *depends on*: a test that only reads a file's text, or a change in a non-Python file the code loads, is not in the index. Those cases are covered by the always-run contracts, the mirror-folder rule and the FULL list above, and main always runs everything.
* Node tests (`tools/orca-concord-room`) run in their own step on every pull request, unchanged.

## The local quick gate follows imports (LIFE-763)

`tools/preflight.sh` (`tests/quickselect.py`) adds two source-derived rules to "the tests that name or mirror a changed file": (1) a change to a lifeos module selects every test that imports or names it, or any lifeos module that imports it, transitively (the credentials screen tests for a change to `credential_list.py`); a module reached by more than `HUB_CLOSURE` (40) modules, such as `db`, follows its direct importers only; (2) a changed non-Python file selects the tests whose source names its repo path or distinctive file name. Measured: a `credential_list.py` change went from 57 to 81 modules (13 s), a docs-only change stays 57 modules (11 s), the `db.py` hub is 125 modules (26 s). CI still runs the full suite.

**One shared selector now.** `testindex.select()` is the only selector: CI calls it with the cached coverage index and `tests/quickselect.py` (local preflight) calls it with the import graph. Local mode counts committed, staged, unstaged and untracked files, and a rename counts as its old and new path. An uncertain change (a workflow, a root config file, a helper under `tests/`, an unknown file type) runs every test module, locally too; the measured sizes above are from before this change, and a full local run takes about two minutes. A `tools/*.py` change also selects the tests that name the file, and the repo-wide scanners always run. The "tests that name the module" text match is local only. The cached index key hashes `tests/testindex.py`, so a change to the selector rebuilds it and pull requests before that rebuild run the full suite.
