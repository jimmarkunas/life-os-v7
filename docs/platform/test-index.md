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

## CI

`.github/workflows/pr-tests.yml` (required check `unit-tests`, unchanged): the step "Pick the tests" runs the selector on pull requests only; on a push to main it writes FULL. A FULL pick runs the two parallel shards as before; a subset runs `python -m unittest <targets>`. Any trouble picking (the selector fails, the base is missing, the index is unreadable) means FULL. The 90 s warning and 120 s limit still apply.

**Where the index comes from:** the job `test-index` runs on every push to main after `unit-tests` passes: it installs the test lock (which holds `coverage`), runs `python -m tests.testindex build` and saves `docs/test-index.json` with the cache action under `test-index-v1-<hash of tests/testindex.py and requirements-test.lock>-<commit>`. A pull request restores the newest cache whose key starts with the same prefix (restore only, it never saves) before the selector runs. The first pull requests after a change to the builder or the lock, or after a failed build, find no index and run FULL.

## A wrong index cannot skip a test it should run

* **Different builder or tools:** the cache key includes a hash of `tests/testindex.py` and `requirements-test.lock`; an index made by other code or other pinned tools is never restored. A pull request that edits either file is FULL anyway.
* **Age:** `select` ignores an index whose `generated_at` is more than 7 days old (FULL). Main's own build is the freshness; nothing checks it hourly any more.
* **Missing or damaged:** no cache, a missing or non-JSON file, a wrong schema, missing keys or numbers outside the test list all print FULL (`tests/test_testindex.py SelectCommand`).
* **A lie about a file:** the tests named after a changed module (`tests/<package>/test_<module>*.py`) and every module in `tests/contracts` run whatever the index says; a file the index does not list runs its whole mirror folder, or FULL. Adding, renaming or deleting a source needs no rebuild (LIFE-588).
* **Poisoning:** pull requests only restore; the only job that saves is `test-index`, on a push to main.

## Limits (read before trusting a green subset)

* Coverage shows what a test *runs*, not everything it *depends on*: a test that only reads a file's text, or a change in a non-Python file the code loads, is not in the index. Those cases are covered by the always-run contracts, the mirror-folder rule and the FULL list above, and main always runs everything.
* Node tests (`tools/orca-concord-room`) run in their own step on every pull request, unchanged.
