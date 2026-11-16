# Simplify LIFE OS: measured baseline and scoped plan (LIFE-763, Nov 16 2026)

Plan only: nothing here is implemented. Numbers were measured on main at 2444e44d plus the runs listed.

## Measured baseline

| What | Number |
|---|---|
| Python in `lifeos/` | 53,104 lines |
| Python in `tests/` | 73,554 lines (46 files, 5,626 lines in `tests/contracts/`; `test_workflow.py` alone is 1,343) |
| Workflows | 23 files, 5,320 lines; `domains.yml` 1,820 lines, 17 jobs, 174 steps; `hourly.yml` 1,509 lines, 7 jobs, 87 steps |
| `lifeos.run` step references | domains 38, hourly 29, 15 other workflow files 1 to 6 each |
| Stage budgets | `stage_budgets.json`: 110 stage rows plus 25 rule rows, default 600 s |
| Per-stage registry spread | code in `lifeos/run.py` (200 lines), `stage_budgets.json`, `timeouts.md` (260 lines), `stage-isolation.md` (161 lines), `docstyle.py`, `runtime.json`, `module-inventory.md`, plus a workflow step; `tools/regen.py` writes the rows but tests fail until it is run |
| AGENTS.md | 15,360 bytes (the cap); 11 sections; 11 policy parts plus manifest in `docs/development-policy/`; `DECISIONS.md` 1,100 lines |
| `lifeos/jira/` | 1,424 lines in 12 modules (card 320, rollover 229, fill_defaults 201, readiness_record 162, snapshot 142, release 103); `tests/jira/` 1,497 lines |
| CI, pull request | `unit-tests` job 53 to 105 s (71 s on the latest main push). CI already selects tests from `tests.testindex` and shards the full suite three ways |
| CI, merge to main | `unit-tests` 71 s plus a `test-index` job of 254 s (the longest job anywhere in the PR path) plus a `unit-tests-second-date` job |
| Local full suite | about 5,400 tests, minutes; this is what the workers wait on |

Finding 1: item C is mostly already built. Pull requests run `testindex select` plus `tests/contracts`, and the full suite only on main. What is left is the 254 s `test-index` job on main, the fail-open to FULL, and no local equivalent (`preflight.sh`, LIFE-768 scope below). The measured pain is local and rework, not PR wall-clock.

Finding 2: the parked packages are cleanly bounded. `jobs`, `interview`, `hiring`, `recruiters`, `megibow` import only `lifeos.platform` (jobs also imports `lifeos.run`). They are imported only by `lifeos/sources` (the registered stages) and `lifeos/run.py` (jobs). Together they are 17,616 lines of `lifeos/` (33 percent) and 26,488 lines of tests (36 percent). Outside their own test folders, only `tests/contracts` (callout stamps, kit domain free, guardrails, source registry, timeouts), `tests/test_run.py`, `tests/sources`, `tests/network`, `tests/edge/test_close_release.py` and `tests/amazon` reach into them.

## The end state

A stage is one entry in one manifest. The workflow job, the budget rows, the isolation row, the docstyle and runtime rows and the test expectations are all generated from it or derived from it. Agents read a 4 KB page. CI and the local gate run what the change can affect. One scripted path closes a ticket. The job search and Interview OS code sits behind a boundary so a platform change does not run its tests.

## Items, tickets, order

| Item | Ticket | Size | Protected |
|---|---|---|---|
| A. Declarative stage manifest | LIFE-768 | L | Yes for the first generated workflow that adds or changes a permission block (needs-approval, one yes, shared with LIFE-764) |
| B. A few generic contract checks over the manifest | LIFE-769 | L | No |
| C. Changed-only CI, plus preflight on the same selector | LIFE-770 | M | No unless a workflow permission changes (none planned) |
| D. One-page agent guide | LIFE-766 | M | No |
| E. One scripted ticket path; list the rest for deletion | LIFE-771 (plus LIFE-767 list) | M | Deleting anything is Jim's yes; this ticket deletes nothing |
| F. Park job search and Interview OS behind a boundary | LIFE-772 | L | No (moves and a package boundary, no deletes, no spend); the directory move of `lifeos/jobs` needs no approval but changes `runtime.json` host mapping only by name |
| 764 Split `domains.yml` | LIFE-764 | M | Yes: new permission blocks (existing label `needs-approval`) |
| 765 Tests derive defaults from code | LIFE-765 | M | No; absorbs LIFE-758 |

### A. Declarative stage manifest (LIFE-768)

Files: new `lifeos/platform/stages.json` (name, module, schedule, timeout, budget seconds, required, secrets, isolation single or guarded, workflow, job); a generator `tools/gen_workflows.py`; `lifeos/run.py` reads the manifest instead of a hand-kept dict for platform stages; `tools/regen.py` becomes `gen --check`. Workflows keep a generated block per stage between markers, hand-written jobs stay. Never a rewrite of `hourly.yml`; stages migrate in batches, domain by domain, starting with `jira` (all 12 modules, one workflow).
Acceptance: adding a stage is one manifest entry plus the module; `python tools/gen.py --check` is clean; the generated step is byte-identical to the current step for every migrated stage (golden diff test); zero edits to `stage_budgets.json`, `timeouts.md`, `stage-isolation.md`, `runtime.json` by hand; full suite and the contract tests pass.
Risk: a generator that emits a different step silently changes a schedule. Mitigation: golden diff per stage before any switch, dry-run on one domain first.
Conflicts: `domains.yml` (PR 1261 now, LIFE-752 next, then LIFE-764), `stage_budgets.json`, `test_workflow.py` and the pinned counts (LIFE-758/765). Start after those merge.

### B. Generic contract checks (LIFE-769)

Today 46 files, 5,626 lines. Inventory by what they protect: stage rules (timeouts, isolation, budgets, workflow shape, codes, shards, settings, checkout step, source registry, readiness), policy and docs (development_policy_split, agent_files, privacy, borrowed_code, boundaries), runtime and hygiene (lockfiles, node_install, frozen_clock, suite_timing, ruff/mypy advisory), and small one-offs (callout stamps 159, delivery_rules 35, typed_high_fan_in 43). Target: about 6 modules. `test_stages.py` loops the manifest and asserts every stage has a timeout, a budget, isolation, a workflow job, secrets named only from the allowed list, and permissions no wider than the job. The workflow-shape and codes tests become loops over all workflow files. Keep every distinct invariant as a named assertion; delete only duplicate per-stage pins, and list each removed test with the check that now covers it in the PR description.
Acceptance: mutation proof for each removed class (introduce the violation, the new check fails); contracts runtime does not go up.
Risk: losing a real guard. Mitigation: the mutation list above is the gate.
Conflicts: `tests/contracts/test_workflow.py`, `test_timeouts.py`, `test_stage_isolation.py`; order after A for the stage loops and after LIFE-765 for pinned counts. The non-stage files (policy, privacy, hygiene) can merge into fewer files at any time.

### C. Changed-only CI (LIFE-770)

Already there: `testindex select` on pull requests. Remaining: (1) the 254 s `test-index` job on main (cache restore and incremental rebuild instead of a full rebuild; or drop to nightly, since it only feeds pull requests); (2) `unit-tests-second-date` on every main push moves to nightly; (3) path filters on heavy non-test jobs where they exist (docs-only changes skip the suite except docs contracts); (4) `tools/preflight.sh` (worker in flight) uses the same selector so local and CI agree; the failure classes it must reproduce are the six CI caught this week; (5) a measured fail-open rate (how often selection says FULL) logged in the step summary.
Acceptance: main push to green drops from the current 330 s to under 120 s; a docs-only PR runs only the docs contracts; selector falls back to FULL on any doubt.
Risk: a path filter that skips a needed test. Mitigation: FULL on main nightly and on any change to `tests/testindex.py`, workflows, `requirements*`.
Conflicts: `.github/workflows/pr-tests.yml` only; no overlap with 1261; runs parallel to everything except the preflight PR (same selector code).

### D. Agent boot page (LIFE-766, rewritten)

End state: `AGENTS.md` about 4,000 bytes: the delivery flow in 10 lines, where truth lives, the four commands (`preflight.sh`, `automerge.sh`, closeout helper, `setup-dev.sh`), the protected list, and one pointer to `docs/index.md`. Everything else moves verbatim to `docs/reference/` files named by the rule they carry. A test (`test_agent_files`, `test_development_policy_split`) keeps asserting that each moved rule exists once and is linked from the index; byte cap drops from 15,360 to 4,500. Nothing is deleted and no rule changes.
Conflicts: `AGENTS.md` and `CLAUDE.md` are edited by the preflight worker; run D after that PR merges. Size M. Not protected.

### E. One scripted ticket path (LIFE-771, rewrites the intent of LIFE-767)

Measured: 1,424 lines in 12 modules. Keep as the one path: create or claim, the closeout helper (stamp, receipt, transition, read-back), `promote`, `fill_defaults`. Candidates listed for Jim's deletion decision (not deleted here): `rollover` (229), `readiness_record` (162), `snapshot` (142), `card` (320), `watch` (70), `stalled` (50), `release` (103), `boards` (35), `search` (20), `store` (17), plus their stages and the 1,497 test lines. Each row says which stage, which workflow step and which test it would remove, and what it costs to keep. The one scripted flow: `python -m lifeos.jira.flow <KEY>` runs claim, then after merge receipt, Done and read-back, with the CT stamp from the helper.
Acceptance: the flow closes a real ticket end to end with one command; the candidate list is in a Jira comment on LIFE-767 for Jim.
Conflicts: the closeout-helper PR (in flight) owns `lifeos/jira/` additions; this item waits for it. Size M. Not protected; deletions are.

### F. Park job search and Interview OS behind a boundary (LIFE-772)

Measured boundary: `jobs`, `interview`, `hiring`, `recruiters` and `megibow` import only `lifeos.platform` (and `jobs` imports `lifeos.run`); only `sources` and `run` import them. Plan, in order: (1) break `jobs -> run` (move the shared helper into `platform`); (2) add a `parked` list to `lifeos/platform/runtime.json` and a contract that no non-parked package imports a parked one except through the source registry; (3) default test selection skips parked tests unless a parked path, `lifeos/platform`, or a shared contract changed (the test index already maps paths to tests, so this is a selector rule, not a move); (4) full suite on main and nightly still runs them; (5) later, only if Jim wants it, a separate package or repository (this one is the protected, larger step, not part of this ticket).
Acceptance: a change under `lifeos/inbox` or `lifeos/jira` runs none of the 26,488 parked test lines; a change under `lifeos/platform` runs them; import contract green.
Risk: hidden coupling through `tests/contracts` (callout stamps, kit domain free, guardrails, source registry, timeouts all read parked packages). Mitigation: those contracts stay in the always-run set.
Conflicts: `lifeos/run.py` and `runtime.json`; after A. Size L. Not protected.

## Order and parallelism

1. Now, in parallel, separate worktrees: LIFE-765 (with LIFE-758, already in progress, waiting on 1261), LIFE-770 (pr-tests.yml only), LIFE-767 list.
2. After PR 1261 and LIFE-752 merge: LIFE-768 step one (manifest and generator for the `jira` domain).
3. After the preflight PR merges: LIFE-766 (AGENTS.md).
4. After the closeout helper PR merges: LIFE-771 (one flow).
5. After LIFE-768 lands the manifest: LIFE-769 (generic checks) and LIFE-772 (park); these two touch different files and can run in parallel.
6. LIFE-764 (split `domains.yml`) last of the workflow changes; its permission blocks need Jim's one yes, and after LIFE-768 most of the file is generated, so the split is a generator option, not a hand edit.

## Needs Jim's yes

- LIFE-764 and the first LIFE-768 generated workflow that adds or changes a permission block.
- Any deletion: the LIFE-767 and LIFE-771 candidate list.
- Splitting job search or Interview OS into a separate package or repository (not planned in this series).
