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
| 2 | `tests/interview/test_stage.py`, `StageTests.test_cli_uses_interview_owned_stage` (8 lines) | `STAGES["interview"]` and `STAGES["interview-probe"]` call the evidence run and the probe with the same `(limit, live)` arguments (it asserted only mock call arguments) | `tests/interview/test_acceptance.py` `Delegation.test_stage_is_registered_and_normal_stages_stay_evidence_free` makes the same two delegation checks plus the registration. Break check 1 below. |
| 3 | `tests/interview/test_acceptance.py`, `Delegation.test_dry_harness_delegates_live_false_with_one_pair` (5 lines) | The acceptance harness hands the stage live=False and exactly one evidence pair (mock call arguments) | `Delegation.test_output_carries_no_evidence_and_dry_run_only_reads` runs the harness for real and asserts `writes_planned == 1`, `writes == 0` and GET-only calls; `test_live_flag_delegates_live_true` keeps the live flag. Break check 2 below. |

## Speed (no test removed)

* `tests/edge/backup/test_restore_check.py` and `test_mac_push.py` generated a new throwaway gpg key and re-sealed two archives in almost every test. The key is now generated once per run and shared, and the built archives are built once per key (they are only read). Same 31 tests; the two modules run in **12.3 s instead of 19.5 s**.

## Not done / not measured

* Coverage before and after was **not measured**: the coverage tool is not in the locked requirements, and the only test removed never ran, so coverage cannot have changed. If Jim wants the number, say so and I will install the tool locally and report it.
* The ticket asks for a break check on 3 removed groups; only 1 group was removed, so 1 is shown.

## Pass 2 (Nov 10 2026, LIFE-527 re-released): method and result

Method: the whole suite was run once with per-test coverage contexts (4,265 tests; 89.1% of `lifeos/` lines). A test became a candidate only if (a) every `lifeos/` line it runs is also run by at least two other tests, (b) at least half its assertions are mock-call, call-count or output-text checks, and (c) its name and body contain none of the keep words (refuse, deny, never, fail, unknown, invalid, secret, privacy, auth, corrupt, missing, integrity, recover, rollback, shape, readiness). `tests/contracts/` and `tests/kit/` were excluded.

Result: **16 candidates (175 lines, 0.3% of the test code).** I read the ones that cover the least production code and removed **2** (rows 2 and 3). The other 14 stay: they check retry timing on a real relay process, replay-is-a-no-op, no-secret-in-text, paging that loses nothing, or a push that carries counts only. Removing any of them would lose a behavior the ticket tells us to keep.

Sample break checks (changed locally, not committed, restored afterwards):

| Break | Surviving test that failed |
|---|---|
| `interview-probe` pointed at `stage.run` instead of `stage.probe` | `test_acceptance.Delegation.test_stage_is_registered_and_normal_stages_stay_evidence_free` |
| The acceptance harness passes no evidence pair to the stage | `test_acceptance.Delegation.test_output_carries_no_evidence_and_dry_run_only_reads` |
| Pass 1 row 1: Greenhouse `for=` only read when first in the query | `test_enrich.FirstPartyRelinkTests.test_the_board_name_is_read_when_for_is_the_first_query_parameter` |

Honest conclusion: the suite is not padded. The real size lever is not removal but sharing: about 1,300 lines of duplicated fakes (LIFE-544).
