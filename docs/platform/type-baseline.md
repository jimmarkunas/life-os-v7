# Type-check pilot: baseline (LIFE-559)

Nov 10 2026. An **advisory** type checker on `lifeos/platform` and ten platform modules fully annotated. Types only: no behaviour change.

## The checker

* **mypy 2.3.1**, lenient mode (no strict flags), configured in `mypy.ini` (`files = lifeos/platform`). `pyproject.toml` stays ruff-only on purpose: `tests/contracts/test_ruff_advisory.py` pins it, and this ticket's Done-when says no existing test is edited.
* Chosen over pyright because mypy installs from hashed wheels with no download at run time; pyright's pip package fetches Node when it first runs, which would break "nothing is installed unpinned" (LIFE-536).
* Pinned in `requirements-test.txt` and `requirements-test.lock` with its dependencies (`ast_serialize`, `librt`, `mypy_extensions`, `pathspec`, `typing_extensions`), each with a sha256 for CPython 3.12 Linux x86_64.
* The pull-request step "Type check advisory" in `.github/workflows/pr-tests.yml` runs after the unit tests on the `lifeos/platform/*.py` files the change touches, prints counts only, is `continue-on-error`, and ends in `exit 0`. It cannot fail the job or change the required `unit-tests` check. `tests/contracts/test_mypy_advisory.py` pins all of this.

## Baseline (lenient mode, `python -m mypy`, 45 files in `lifeos/platform`)

| Module | Errors |
|---|---|
| `codes.py` | 2 (`Value of type "Collection[str]" is not indexable`, line 932) |
| every other module | 0 |

Lenient mode skips the bodies of functions that have no annotation, so the count is low because almost nothing is annotated. Typing more functions makes the checker look at more code. The two errors are left as they are (that module is not one of the ten).

## The ten typed modules (chosen by how many files import them, modules under about 250 lines)

`names` (23 importing files), `http` (13), `alerts` (12), `runtime` (10), `snapshot_store` (9), `notion_ids` (9), `jira` (9), `outlook` (8), `ledger_body` (7), `tinyfish` (6). Skipped by the ticket: `notion_client`, `gmail`, `db`. Skipped because they have no functions (`limits`), belong to another ticket (`credentials`, `credential_list`, `codes`), or cannot take an import: `router` (14 importing files) was in the first list, but `tests/platform/test_router.py` requires that module to import nothing, so typing it would mean editing that test; `tinyfish` took its place.

Annotated functions: **13 of 2,997 in `lifeos/` before; 92 after.** In `lifeos/platform`: 2 of 366 before; 81 after. All 72 public functions and methods of the ten modules are fully annotated (argument and return types); a contract test fails if one is not.

## Proof that nothing else changed

* The full test suite passes with no test edited.
* `ast` comparison against `main` for each of the ten files: identical once annotations (arguments, returns, bare `x: T` declarations) and the added standard-library typing imports are removed. Ten of ten identical.
* The annotations import only `typing`, `collections.abc` and `datetime` (standard library), and `from __future__ import annotations` in two modules, so the files shipped inside the Oracle Close tree and the ticker stay complete (`tests/edge/test_close_closure_complete.py` passes).

## Proof that the checker catches a wrong call

A scratch file (never committed) called `names.same_company(5, ["x"])`; the advisory step's exact command reported 2 findings (argument 1 and argument 2 have incompatible types) while the required check is unaffected, because the step cannot fail the job.

## What comes next (Jim's decision)

Making the checker blocking for the clean modules is a separate decision, after about two weeks of advisory runs.
