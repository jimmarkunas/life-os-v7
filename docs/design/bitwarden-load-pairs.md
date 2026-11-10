# Design: collapse the Bitwarden load and retry step pairs (LIFE-540)

Design only. No workflow was changed by this document. Measured on main at 893a0aa (Nov 9, 2026, 10:59 PM CT), 22 workflow files, 5,130 lines. Names and counts only; no secret values, no secret ids.

## 1. The problem

Every job that needs Bitwarden secrets has two `bitwarden/sm-action` steps: a load, and one retry that runs only if the load failed (D209, one retry, no fallback). The retry repeats the load's `with:` block word for word. A composite action cannot replace the pair as is, because jobs read the secrets as named step outputs (`steps.<id>.outputs.NAME || steps.<id>_retry.outputs.NAME`), and a composite action must declare its outputs by name in advance.

## 2. Facts

| Measure | Value |
|---|---|
| `sm-action` steps | 88 = 44 pairs (load + retry) in 21 workflow files |
| Lines in all load steps | 733 |
| Lines in all retry steps | 733 (together 1,466 = 29% of workflow YAML) |
| Lines in the retry steps' `with:` blocks (the repeated part) | 557 |
| Retry-pause steps (`sleep $((30 + RANDOM % 45))`) | 44 steps, 132 lines |
| Retry `with:` identical to its load `with:` | 44 of 44 pairs |
| Distinct secret-name sets across the 88 steps | 33 |

Pairs grouped by identical secret-name set (a set used by more than one pair):

| Pairs using the set | Secrets in the set | Where |
|---|---|---|
| 4 | 19 | hourly.yml (4 jobs) |
| 3 | 7 | diag.yml, outlook.yml, profile-sync.yml |
| 3 | 1 | diag.yml, hourly.yml, interview-proof.yml |
| 3 | 1 | domains.yml, hourly.yml, watchdog.yml |
| 2 | 14 | domains.yml |
| 2 | 2 | hermes-stage.yml, readiness-live.yml |
| 1 each | 1 to 21 | the other 27 sets |

So a shared composite or reusable block would help only 6 of the 33 sets across more than one pair; most pairs have a set of their own.

Another fact: 6 pairs (amazon, three in diag, safety, watchdog) do not set `set_env: false`. The other 38 do, so they expose secrets only as masked step outputs. I did not check what the action does by default when `set_env` is left out.

## 3. Options

| # | Option | Lines saved (about) | Workflow files touched | Secrets reach steps | Contract tests | Rollback |
|---|---|---|---|---|---|---|
| A | **YAML anchor on the load's `with:` and an alias in the retry's `with:`** (`with: &bws_x` ... then `with: *bws_x`) | **513** (557 lines become 44 alias lines) = 10% of workflow YAML | 21 | **Unchanged**: same steps, same ids, same masked step outputs, same `||` reads | `yaml.safe_load` expands aliases, so structural tests still pass. Text-regex tests that count each secret mapping twice (the "load and its identical retry" checks, e.g. in tests/contracts/test_workflow.py) would count once and must be rewritten. Better version: assert that the parsed retry `with` equals the parsed load `with`. | Revert the PR |
| B | Composite action with declared outputs | small | up to 21 | Changes (secrets re-exported through the action's outputs); redaction of secret-looking outputs through a composite was **not tested** | Digest-pin tests need new pins | Revert |
| C | Reusable workflow | n/a | n/a | Job-level only. From GitHub's documentation as I remember it (**not tested**), secret values in job outputs are redacted, so this would break the output reads | n/a | n/a |
| D | One load step that exports to the environment (`set_env: true`) and retries inside a wrapper | about 800 to 865 (the retry steps and the pause steps go) | 21 | **Changes**: secrets become environment variables for every later step of the job, including third-party actions (a wider exposure than masked step outputs). Needs Jim's yes (the STOP line) | Most secret-path tests rewritten | Revert |
| E | YAML merge key `<<` to share a block with overrides | n/a | n/a | n/a | n/a | **Tested: not supported.** GitHub rejected the whole workflow file as soon as `<<: *x` was added (no job ran). |
| F | Leave as is | 0 | 0 | unchanged | unchanged | n/a |

## 4. Test record for option A and E (done for this design)

On a throwaway branch (`scratch/LIFE-540-anchor-test`, a workflow with no secrets and no network, triggered only by pushes to that branch, never merged):
* Plain anchor and alias of a mapping (`env: &x` ... `env: *x`): **works** (run 42758904449, success; the second step checked the aliased values).
* Alias of a mapping that holds a multi-line string like the secrets list: **works** (run 42759094637, success; the second step compared a hash of the text).
* Merge key `<<: *x`: **fails** (run 42758963801: GitHub started no job; the file was removed from the test and the rest passed).
* Not tested on a real `uses: bitwarden/sm-action` step (that would need the runtime token). The shape is the same (a mapping under `with:`), but this is the one thing an implementation PR must prove first, on a manual dry run of one workflow.

## 5. Recommendation: option A

* Same secrets path, same step ids, same masking; nothing about how secrets reach steps changes, so the STOP line is not triggered.
* Saves about 513 lines (10%) and removes the chance of the retry drifting from the load.
* Easy to revert; the edit is mechanical and can be checked by a script (parse each file, require retry `with` equals load `with`).
* Leaves the retry-pause steps (132 lines) alone; they are three lines each.
* Option D saves more but widens secret exposure; take it only if Jim wants that trade.

## 6. If Jim picks A: how the implementation ticket should run (not done here)

1. One PR, one commit per workflow file group, branch `life/LIFE-nnn/bitwarden-aliases`.
2. A script rewrites each pair and then proves, by parsing, that the retry `with` equals the load `with` and that no step id, name, `if:` or output read changed.
3. Rewrite the text-count assertions in the contract tests to the parsed comparison; do not delete a test in the same PR as the code it covers (add the stronger check first).
4. Manual dry run of one affected workflow and one scheduled run observed green before closing.

## 7. Not checked

* An alias on a real `sm-action` step (needs a live run).
* What `set_env` defaults to when omitted (the 6 pairs above).
* The remaining text-regex assertions in tests/contracts/test_bws_helper.py, test_guardrails.py and tests/jobs/fit/test_backlog_workflow.py (I found the doubled-mapping checks in test_workflow.py only).
* Option B's redaction behavior and option C's output behavior (from documentation memory, not tested).
