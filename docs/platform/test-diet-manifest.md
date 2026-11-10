# Test diet manifest (LIFE-527)

Written Nov 10, 2026. Rules applied: `tests/contracts/` is untouched (including `test_privacy.py`); docs/PRIVACY.md and .claude/settings.json untouched; a test is removed only if it asserts an internal detail or duplicates another test, and every removal names the surviving proof.

## What I looked at (4,071 test methods outside tests/contracts, found by AST)

| Search | Result | Decision |
|---|---|---|
| Test methods defined twice in the same class (Python runs only the last, so the first never runs) | **1** | Removed (row 1 below). |
| Byte-identical test bodies (3+ lines) across files | 3 groups, 4 copies, 18 lines: `test_a_success_closes_an_open_alert_and_says_so_once` (calendar_bridge and jira watch tests), `test_no_restart_command_in_the_code` (3 hermes script tests), and the one in row 1 | The first two are NOT duplicates: the body only calls `self.` helpers, and each class's setup points them at a different module or script. Kept. |
| Tests that are mostly mock-interaction assertions (call counts, call order, `assertLogs`) | 27 tests, 182 lines | Sampled; every one I read asserts a user- or system-visible behavior ("push only for new items, with counts", "a refused post asks nothing"). Kept. |
| Identical test helper classes repeated across files | 4 groups, 51 lines (`FakeDb` 26, `Reply` 12, `Pushes` 7, `Clock` 6) | Not changed: moving them to a shared kit touches 8 files to save about 50 lines and risks no protection gain. Left for Jim to ask for. |
| Slowest tests (unittest `--durations`) | The 25 slowest total about 50 s of 140 s; my own gpg key tests were the biggest cost | Sped up without removing a test (below). The Bot Studio installer tests run the real shell installers (about 1.5 s each): that IS what they test. Kept. |

**Finding, stated plainly:** the suite is not padded with implementation-detail tests. I found **one** test that never ran, and nothing else that can be removed without losing a protection. Reaching a large cut would mean removing behavior tests, which the rules forbid.

## Removal manifest

| # | Removed | Behavior it checked | Surviving proof |
|---|---|---|---|
| 1 | `tests/jobs/test_enrich.py`, class `FirstPartyRelinkTests`, the FIRST definition of `test_the_board_name_is_read_when_for_is_the_first_query_parameter` (7 lines) | A Greenhouse embed URL with `for=` as the first query parameter or the second yields the same board API address | The second, identical definition of the same test in the same class (the only one Python ever ran). Break check: with the `for=` pattern in `lifeos/jobs/enrich.py` made to ignore a leading `for=`, the surviving test FAILS (shown locally, not committed). |

## Speed (no test removed)

* `tests/edge/backup/test_restore_check.py` and `test_mac_push.py` generated a new throwaway gpg key and re-sealed two archives in almost every test. The key is now generated once per run and shared, and the built archives are built once per key (they are only read). Same 31 tests; the two modules run in **12.3 s instead of 19.5 s**.

## Not done / not measured

* Coverage before and after was **not measured**: the coverage tool is not in the locked requirements, and the only test removed never ran, so coverage cannot have changed. If Jim wants the number, say so and I will install the tool locally and report it.
* The ticket asks for a break check on 3 removed groups; only 1 group was removed, so 1 is shown.
