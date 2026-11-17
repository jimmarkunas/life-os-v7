# Codebase value audit (LIFE-806)

Read-only. Nothing was deleted, merged or changed. Measured on main at 576518f7, 2026-11-17.
Every Delete/Merge below is a **proposal**; rollback for each is `git revert` of its PR. Nothing is removed without Jim's yes per item.

## Headline numbers

| What | Number |
|---|---|
| Product code (`lifeos/`) | 54,137 lines, 25 packages |
| Tests (`tests/`) | 75,866 lines, ~5,500 tests (tests are 1.4x the code) |
| Workflows (`.github/workflows` + `workflows-src`) | 7,387 lines |
| Docs / policy | 82 files, 7,693 lines (DECISIONS.md alone 1,100 lines, 253 entries) |
| Stages in the hourly run | 111 |
| CI test time (sum of shard weights) | 154.7 s over 306 modules |

Honest read: **the "theater" is real but mostly friction, not seconds.** Test seconds are dominated by `edge` (56.9 s) and `buzz` (29.1 s), which are Oracle-host code and not guardrails. All `contracts` tests together are 17.2 s (11%). What contracts cost is red builds and agent turns, not CI minutes. Only about 4% of all lines are clean delete/merge candidates (table at the end).

## Per package (code lines / files, tests lines / count, CI s, consumer, verdict)

| Package | Code | Tests | CI s | What consumes it | Verdict |
|---|---|---|---|---|---|
| sources | 9,659 / 65 | 5,776 / 440 | 5.1 | Job Ledger pages, many stages; imported by probe | Keep |
| jobs | 8,525 / 62 | 11,898 / 942 | 24.2 | Job pipeline, capacity card (off by default) | Keep; capacity_card is Ask Jim (off) |
| platform | 8,522 / 74 | 8,012 / 709 | 2.9 | Imported by 24 packages; Weekly LIFE OS Review | Keep |
| interview | 5,144 / 21 | 6,052 / 404 | 2.5 | Interview rows; ~430 s hourly | Keep, Ask Jim (Q2) |
| edge | 4,321 / 29 | 7,978 / 553 | 56.9 | Oracle host services | Keep (biggest CI cost, but it is the live host) |
| inbox | 2,908 / 24 | 3,469 / 272 | <1 | Agent Inbox, Followup lane (live) | Delete 733 dead lines (below) |
| imessage | 1,545 / 6 | 1,920 / 133 | 4.3 | Comms card; used by edge | Keep |
| jira | 1,536 / 14 | 1,653 / 139 | 1.9 | Jira card in Daily Report; ticket tooling | Keep card.py; Ask Jim on the rest |
| recruiters | 1,362 / 10 | 2,824 / 234 | <1 | Outreach card; ~756 s hourly | Keep, Ask Jim (Q1) |
| hiring | 1,355 / 10 | 4,790 / 352 | <1 | Hiring card | Keep |
| amazon | 1,263 / 9 | 1,343 / 102 | <1 | Amazon card; retention ~275 s hourly | Keep, Ask Jim (Q2) |
| megibow | 1,230 / 13 | 955 / 72 | <1 | Megibow card / V3 | Keep |
| network | 1,222 / 10 | 1,739 / 142 | 1.0 | Imported by run, sources | Keep |
| buzz | 787 / 5 | 1,708 / 117 | 29.1 | Concord/Buzz channel; used by hermes_requests | Keep; tests are slow (29 s for 787 lines): speed-up candidate, not delete |
| accountability | 768 / 10 | 408 / 21 | <1 | Jim & Matt V3 page | Keep |
| bills | 663 / 5 | 670 / 48 | <1 | Bills card | Keep |
| physmail | 626 / 6 | 845 / 60 | <1 | Mail alerts card | Keep |
| agenda | 426 / 3 | 420 / 37 | <1 | Calendar card | Keep |
| whatsapp | 378 / 1 | 512 / 35 | <1 | Comms | Keep |
| dcc | 369 / 3 | 762 / 54 | <1 | DCC card + header | Keep |
| oraclecloud | 360 / 4 | 473 / 35 | <1 | Oracle card | Keep |
| telegram | 333 / 2 | 123 / 11 | <1 | Alerts | Keep (last change Nov 13) |
| calendar_bridge | 230 / 3 | 437 / 32 | <1 | Imported by run | Keep |
| outlook | 89 / 2 | 302 / 17 | <1 | Imported by run | Keep |
| delivery | 89 / 2 | 64 / 6 | <1 | One stage | Keep |
| kit (tests only) | 0 | 1,456 / 12 | <1 | 22 files for 12 tests (mostly fixtures) | Ask Jim: look for dead fixtures |

"Last meaningful change": everything was touched in the last ~5 days, so recency does not separate live from dead. Import evidence does, and that is what the Inbox finding below uses.

## Dead code found (evidence: no stage, no workflow, no other module imports it)

`lifeos/inbox`, modules unreachable from any running stage (checked by following imports from the 6 stage entry points, the other packages, `.github` and `tools`):

| Module | Lines | Origin |
|---|---|---|
| notion_binding | 180 | cancelled governed-actions design |
| policy | 162 | same |
| actions | 120 | same |
| adapters | 95 | same |
| concord_source | 78 | same |
| inbox | 62 | same |
| access | 36 | same |
| **Total** | **733** | plus ~900 test lines (test_access, test_actions, test_durable, test_inbox, test_notion_binding, part of test_concord_followup) |

Nothing Jim sees depends on them. **Verdict: Delete (needs Jim's yes, Q4). Rollback: git revert.**

Separate: `followup_deliver` (142) and `followup_classify` (46) are still reachable (merged in PR #1308 for the cancelled LIFE-716). They run, so deleting them changes behavior. **Ask Jim.**

## Guardrail layer (audited separately)

| Piece | Size | What it protects | Simpler check? | Verdict |
|---|---|---|---|---|
| `tests/contracts` | 52 files, 6,194 lines, 435 tests, 17.2 s | Mixed: workflow shape, docs text, policy wording | See below | Keep the 27 that read workflows/code; Merge the doc-pin ones |
| Doc-pin tests: test_buzz_roadmap 58, delivery_rules 35, net_size_rule 57, development_policy_split 119, agent_files 94, health_model 58, scheduled_checks 42, readiness 164, ruff_advisory 81, mypy_advisory 108 | 816 lines | A sentence still exists in a doc after an old incident | One test that checks the 10 doc anchors from a list | Merge into one `test_doc_anchors.py` (~100 lines): saves ~700 lines |
| `test_workflow.py` | 1,361 lines, 71 tests | Shape of the hourly/domains workflows (the bug class that took the hourly run down) | Partly: many assert generated YAML equals its source fragments | Keep; Merge the "generated equals source" ones into the generator's own check |
| Meta-linter / dependency tests | 395 lines | Dependency bumps, linter config | None simpler | Keep |
| `DECISIONS.md` + `DECISIONS_INDEX.md` | 1,100 + 259 lines, 253 entries | Why a rule exists | Agents read it on every task | Merge: archive entries older than 90 days into the index only (Ask Jim, Q6) |
| Policy docs | development-policy 11 parts, 484 lines; AGENTS.md 4 KB; CLAUDE.md 5.6 KB | Agent behavior | Fine size now | Keep |
| `tools/` | preflight 47, automerge 32, workflow_gate 163, regen 219, test.sh 94 | Fast gate, merge on green, approvals gate | Already minimal | Keep |
| Registry/doc generators (`regen.py`) | 219 lines | Stops docs and manifest drifting | One check already | Keep |

Each contract test cites the LIFE ticket for its incident in its docstring, so any one can be traced and reverted individually.

## Duplication that is not dead code

Workflow YAML: the Bitwarden load/retry pair is repeated across 44 jobs (per `docs/platform/module-inventory.md`). A shared composite action would remove an estimated 600-900 lines. The composite actions `bws-load` and `bws-plain` already exist, so this is a merge, not new design. **Verdict: Merge into the composite action. Estimate, not measured.**

## Totals if every Delete and Merge below is approved

| Item | Lines saved (code+tests) | CI s | Hourly s |
|---|---|---|---|
| Delete 7 dead Inbox modules + tests | ~1,630 | <1 | 0 |
| Merge 10 doc-pin tests into 1 | ~700 | ~1 | 0 |
| Revert followup_deliver/classify (if Jim says yes) | ~560 | <1 | small (stage runs) |
| Simplify ticket-management code in `lifeos/jira` (rollover, readiness_record, snapshot, watch, stalled, release, boards, search, store = 828 + about 900 tests) | ~1,700 | ~1 | ~80 s (jira stage) at most |
| Workflow YAML Bitwarden dedupe (estimate) | ~700 | 0 | 0 |
| **Total** | **~5,300 of ~130,000 (about 4%)** | **~3-4 s of 154.7** | **~80 s at most** |

So deleting everything safe does **not** make the hourly run noticeably faster. Hourly seconds live in the stages Jim may or may not use: recruiters ~756 s, interview ~430 s, boards ~294 s, amazon retention ~275 s, imessage 159 s, agenda 141 s. That is why those are questions below, not deletes.

## Questions for Jim (outputs only he can judge; default in bold)

1. **Recruiters outreach card** (~756 s of every hourly run): do you act on it weekly? A) **Yes, keep it** B) Barely, remove it C) Keep, but only the cheap half.
2. **Interview prep (~430 s) and Amazon retention (~275 s)**: still useful? A) **Keep both** B) Remove Amazon retention C) Remove interview prep D) Remove both.
3. **Capacity card** (built, switched off): A) **Delete it** B) Keep it off C) Turn it on.
4. **Delete the 7 dead Inbox files (733 lines + their tests)**: nothing runs them. A) **Yes** B) No.
5. **Ticket-management code in `lifeos/jira`** (rollover, snapshot, readiness record, stalled, watch): A) **Keep only what the Daily Report card needs, merge the rest into one status check** B) Keep as is.
6. **Old DECISIONS.md entries** (253 entries agents re-read): A) **Move entries older than 90 days to the index only** B) Keep all.
