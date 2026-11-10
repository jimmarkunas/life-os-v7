# Lint baseline (LIFE-558)

Ruff is an **advisory** check: it runs on the Python files a pull request changes, prints counts only, and cannot fail the job. Nothing below was fixed or reformatted; this is the starting count.

- Tool and rules: ruff 0.17.4 (pinned with a hash in `requirements-test.lock`), configured in `pyproject.toml`: pycodestyle errors (E), pyflakes (F) and bugbear (B); line length (E501) is ignored because existing lines run to 661 characters.
- Scope of this baseline: `lifeos/` and `tests/`, Nov 10 2026, run with `ruff check --no-cache --statistics lifeos tests`.
- Local run time over the whole repo: 0.09 seconds.
- Total: **577 findings** (158 in `lifeos/`, 419 in `tests/`). Counts only; no file content is recorded here.

## Count per rule

| Rule | Name | Count | Auto-fixable |
|---|---|---:|---|
| F401 | unused-import | 120 | yes |
| E702 | ] | 98 | [ |
| E731 | ] | 89 | [ |
| E741 | ] | 59 | [ |
| E701 | ] | 47 | [ |
| B905 | ] | 43 | [ |
| B023 | ] | 26 | [ |
| B007 | ] | 26 | [ |
| F841 | unused-variable | 19 | unsafe only |
| E401 | multiple-imports-on-one-line | 16 | yes |
| E402 | ] | 12 | [ |
| B904 | ] | 7 | [ |
| B017 | ] | 4 | [ |
| F811 | redefined-while-unused | 4 | unsafe only |
| B033 | duplicate-value | 2 | yes |
| F541 | f-string-missing-placeholders | 1 | yes |
| B008 | ] | 1 | [ |
| F402 | ] | 1 | [ |
| B006 | ] | 1 | [ |
| E703 | useless-semicolon | 1 | yes |

## Count per package

| Package | Findings |
|---|---:|
| `tests/interview` | 181 |
| `tests/jobs` | 43 |
| `tests/sources` | 32 |
| `tests/platform` | 26 |
| `lifeos/interview` | 25 |
| `lifeos/sources` | 25 |
| `lifeos/jobs` | 22 |
| `tests/edge` | 18 |
| `tests/buzz` | 16 |
| `lifeos/platform` | 14 |
| `tests/network` | 14 |
| `tests/hiring` | 13 |
| `tests/amazon` | 11 |
| `lifeos/edge` | 10 |
| `tests/hermes` | 10 |
| `tests/contracts` | 9 |
| `tests/recruiters` | 9 |
| `lifeos/megibow` | 8 |
| `tests/imessage` | 8 |
| `lifeos/network` | 7 |
| `lifeos/recruiters` | 7 |
| `lifeos` | 6 |
| `lifeos/imessage` | 6 |
| `tests/bills` | 5 |
| `lifeos/hiring` | 4 |
| `lifeos/jira` | 4 |
| `lifeos/synthetic` | 4 |
| `tests/oraclecloud` | 4 |
| `tests/physmail` | 4 |
| `tests/synthetic` | 4 |
| `lifeos/dcc` | 3 |
| `lifeos/delivery` | 3 |
| `tests/jira` | 3 |
| `lifeos/agenda` | 2 |
| `lifeos/attention` | 2 |
| `lifeos/bills` | 2 |
| `lifeos/telegram` | 2 |
| `tests/attention` | 2 |
| `tests/dcc` | 2 |
| `lifeos/amazon` | 1 |
| `lifeos/physmail` | 1 |
| `tests` | 1 |
| `tests/agenda` | 1 |
| `tests/kit` | 1 |
| `tests/megibow` | 1 |
| `tests/outlook` | 1 |

## How to read and refresh

- On a pull request, the `pr-tests` workflow prints the same rule counts for the changed Python files only (step "Ruff advisory"). A number there is a hint, not a gate.
- To refresh this file after a clean-up: `pip install --require-hashes -r requirements-test.lock` in a throwaway environment, then run the command above and update the tables.
- Next step, only if Jim asks for it: fix one rule family at a time, auto-fixable rules first (F401 unused imports, E401, B033, F541, E703), each as its own reviewed pull request. Until then ruff never blocks a merge.
