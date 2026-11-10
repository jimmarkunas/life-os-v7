# Test index and affected-test runs (LIFE-548)

A pull request used to run all ~4,300 tests. It now runs the tests its change can affect, plus every test under `tests/contracts`. A merge to main and the hourly run always run everything.

## The index

`docs/test-index.json` says, for every production file under `lifeos/` (path relative to `lifeos/`), which tests run at least one of its lines. It is made by running the whole suite once under coverage.py with one coverage context per test.

* Make or refresh it (the generate command): `pip install coverage` then `python -m tests.testindex build` (about 3 minutes; it overwrites `docs/test-index.json`; commit that file). Never merge or hand-edit the index: if it conflicts, take main's and rebuild.
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

`.github/workflows/pr-tests.yml` (job and required check `unit-tests`, unchanged): the new step "Pick the tests" runs the selector on pull requests only; on a push to main it writes FULL. A FULL pick runs the two parallel shards as before; a subset runs `python -m unittest <targets>`. Any trouble picking (the selector fails, the base is missing) means FULL. The 45 s warning and 90 s limit still apply.

## Staleness (two checks)

**Every pull request:** `tests/contracts/test_test_index.py` fails when a production file under `lifeos/` exists but is not in the index, or the index lists a file that is gone. It has negative controls (a removed entry and a fake deleted file must each be caught). So a PR that adds, renames or deletes a production file must regenerate the index in the same PR; the failure message says how. This is what stops the index from drifting silently.

**Hourly full run (age):** `python -m tests.testindex check` fails when the index is more than 7 days old or when production files exist that are in neither `covers` nor `uncovered`. The hourly full run (`hourly.yml`, step "Unit tests") sets `LIFEOS_TEST_INDEX_FRESH=1`, which turns on one test, `tests/test_testindex.py CommittedIndexIsFresh`. If it fails, the hourly run fails and the existing "run failed" alert goes out through the alert gateway (no new sender). A pull request never fails because the index aged.

**If it is stale:** run `python -m tests.testindex build`, commit `docs/test-index.json`, open a PR. Until then pull requests keep working: files missing from the index fall back to their mirror folder or FULL.

## Limits (read before trusting a green subset)

* Coverage shows what a test *runs*, not everything it *depends on*: a test that only reads a file's text, or a change in a non-Python file the code loads, is not in the index. Those cases are covered by the always-run contracts, the mirror-folder rule and the FULL list above, and main always runs everything.
* Node tests (`tools/orca-concord-room`) run in their own step on every pull request, unchanged.
