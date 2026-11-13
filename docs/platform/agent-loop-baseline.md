# Agent-loop baseline (LIFE-556)

Written Nov 10 2026, 3:00 PM CT, from data that already exists: the last 20 merged pull requests titled `[LIFE-nnn]` and their `pr-tests` runs on GitHub, plus the Jira move "To Do -> Agent Go" (the start of agent work) and "Agent Go -> Done" for the tickets behind them. Produced by `python -m lifeos.profile_tickets --last 20 --jira jira.json` (read-only; `lifeos/profile_tickets.py`, test `tests/test_profile_tickets.py`). Keys, counts and times only: no ticket text, log text or personal data is copied here. This report does NOT repeat LIFE-548's pull-request check time or tests-touched measurements.

## The 20 pull requests

| PR | Ticket | Files | + / - | Commit to merge (min) | CI runs | Failed before green | First run to green (min) | Jira start to Done (min) | Packages |
|---|---|---|---|---|---|---|---|---|---|
| 902 | LIFE-528 | 12 | +686 / -678 | 4.7 | 1 | 0 | 4.2 | n/a | contracts, edge, other:tests, platform, sources |
| 900 | LIFE-528 | 1 | +3 / -3 | 2.0 | 1 | 0 | 1.8 | n/a | platform |
| 898 | LIFE-548 | 8 | +838 / -8 | 6.7 | 1 | 0 | 1.3 | 93.9 | contracts, docs, other:tests, workflows |
| 899 | LIFE-528 | 4 | +118 / -7 | 1.9 | 1 | 0 | 1.4 | n/a | contracts, platform |
| 896 | LIFE-549 | 23 | +615 / -10 | 4.3 | 2 | 1 | 3.9 | 76.8 | contracts, docs, kit |
| 895 | LIFE-528 | 7 | +589 / -3 | 5.8 | 3 | 1 | 5.3 | n/a | jobs, platform, sources |
| 894 | LIFE-553 | 1 | +1 / -0 | 1.8 | 1 | 0 | 1.4 | 25.8 | other:AGENTS.md |
| 893 | LIFE-544 | 35 | +108 / -68 | 2.0 | 1 | 0 | 1.7 | 56.9 | amazon, contracts, docs, hiring, jobs, kit, network, recruiters, sources |
| 892 | LIFE-544 | 33 | +642 / -506 | 1.9 | 1 | 0 | 1.5 | 56.9 | edge, hiring, imessage, interview, jira, jobs, oraclecloud, recruiters, sources |
| 891 | LIFE-544 | 14 | +100 / -87 | 1.6 | 1 | 0 | 1.3 | 56.9 | calendar_bridge, edge, jira, jobs, kit, platform, sources |
| 890 | LIFE-527 | 3 | +18 / -13 | 1.9 | 1 | 0 | 1.1 | 453.3 | docs, interview |
| 889 | LIFE-531 | 10 | +224 / -8 | 2.0 | 1 | 0 | 1.5 | n/a | oraclecloud, platform |
| 887 | LIFE-542 | 2 | +78 / -5 | 1.6 | 1 | 0 | 1.2 | 431.7 | edge |
| 886 | LIFE-542 | 3 | +69 / -1 | 2.2 | 1 | 0 | 1.5 | 431.7 | edge |
| 885 | LIFE-528 | 12 | +405 / -2 | 2.3 | 1 | 0 | 1.5 | n/a | contracts, docs, edge, platform |
| 884 | LIFE-527 | 5 | +98 / -27 | 1.7 | 1 | 0 | 1.3 | 453.3 | docs, edge, jobs |
| 883 | LIFE-526 | 16 | +317 / -114 | 8.4 | 1 | 0 | 1.6 | 388.1 | docs, interview, jobs, megibow, platform, sources |
| 881 | LIFE-519 | 3 | +90 / -0 | 2.1 | 1 | 0 | 1.7 | n/a | contracts, workflows |
| 880 | LIFE-519 | 9 | +40 / -40 | 3.9 | 1 | 0 | 1.6 | n/a | amazon, edge, hiring, interview, jira, jobs, network |
| 879 | LIFE-519 | 1 | +28 / -3 | 2.6 | 1 | 0 | 1.7 | n/a | contracts |
"First run to green" is from the first `pr-tests` run on the branch to the finish of the first green run on the final head. "Jira start to Done" is per TICKET (a ticket with three PRs repeats its value on each row); `n/a` means the ticket was not Done yet or its Jira times were not read.

## Median and worst

* Files changed per PR: median 7.5, worst 35 (n=20)
* Lines added: median 104, worst 838 (n=20); lines removed: median 8, worst 678 (n=20)
* First commit to merge, minutes: median 2.05, worst 8.4 (n=20)
* CI runs per PR: median 1, worst 3 (n=20); failed before the first green: median 0, worst 1 (n=20)
* First CI run to first green, minutes: median 1.5, worst 5.3 (n=20)
* Jira Agent Go to Done, minutes, one value per ticket: median 93.9, worst 453.3 (n=7)
* PRs whose first CI run failed: 2 of 20. PRs that touched more than one package (generated files ignored): 15 of 20.

Plain words: once a branch is pushed, a PR is green in about a minute and a half and merged in about two minutes. The long part of a ticket is not in the pull request. A ticket takes a median of about 94 minutes from Agent Go to Done, and the slowest three (388, 432 and 453 minutes) were released at 4:29 AM and finished around noon behind other work.

## Why the first CI run failed (2 of 20)

* PR 895 (credential list): the privacy-style scan in the credentials test caught a secret-shaped string. One failing test, fixed in the next push.
* PR 896 (fake conformance tests): one failing test (failures=1). I did not recover the test name from the log; counts only.
* Hidden failure: PR 902 failed once on the suite's 90-second time limit and passed when the same CI job was rerun. A rerun reuses the same run, so this data shows 0 failures for it. The 90-second limit is close to the real time (86 s on the rerun).

## Files outside what the ticket named

Not measurable from this data: it needs the text of each ticket to compare with the files changed, and the rules here keep ticket text out of the report. As a proxy, 15 of 20 PRs touched more than one package, but most of those are the test-layer and privacy sweeps, which name "every package" on purpose. Treat 15 as an upper bound, not a count of surprises.

## Control set: 10 comparable PRs

Chosen as the smallest PRs with no workflow change. Only 8 of the 20 are truly small and one-package; the last two (marked *) are looser, because the sample has no more.

| PR | Ticket | Kind of change |
|---|---|---|
| 879 | LIFE-519 | test (contract) |
| 894 | LIFE-553 | doc (one file) |
| 900 | LIFE-528 | platform (one data entry) |
| 887 | LIFE-542 | platform (edge fix) |
| 886 | LIFE-542 | platform (edge feature, with test) |
| 890 | LIFE-527 | test + doc |
| 899 | LIFE-528 | test (contract) + platform |
| 884 | LIFE-527 | test + doc |
| 895 * | LIFE-528 | platform (new module and screen section; 3 packages) |
| 889 * | LIFE-531 | stage (Oracle card watch; 2 packages) |

## Three repeatable benchmark tasks

Run each on a clean checkout of main by one agent, write down the three times, and run the same task again after the lint (LIFE-558), type (LIFE-559) and config (LIFE-560) tickets land. Do not run them here.

1. **Add an environment variable read to a stage.** Pick a stage in `lifeos/jobs` that reads one variable; add a second optional variable with a default, use it, add a test. Time: (a) to the first green local test of the changed test file, (b) to a pushed branch, (c) to the first green `pr-tests` run. Count: files touched and commands run.
2. **Add a field to a platform record.** In `lifeos/platform`, add one optional field to a record that is already written and read (for example in `credentials.py`'s row), update every reader, add a test. Same three times and counts.
3. **Add a registered error code with its test.** Add one new fixed code to its package's `codes.json` (LIFE-644; it was `lifeos/platform/codes.py`) for a stage that already raises a similar one, use it at one call site and test it. Same three times and counts; also note whether the contract tests (codes, code map, test index) needed a separate regeneration step.

## Scorecard numbers asked for in LIFE-522 comment 12549

* **Files touched to add one new source:** 8 (LIFE-526 receipt). Not re-measured here; nothing in these 20 PRs added a source.
* **Share of the epic's children that went green with no relay back to Jim:** the epic has 32 children: 19 Done, 1 Cancelled, 12 open. Done / all non-cancelled = 19 of 31 (61%). Of 7 tickets whose Jira history I read (526, 527, 542, 544, 548, 549, 553), all 7 have a single Agent Go to Done move with no reopen; 3 of the 7 (548, 549, 553) were also clean in the stricter sense of one PR line, no follow-up PR, no correction ticket. The other 12 Done children were not read, so the strict share for the whole epic is not known; recompute it from the changelogs at the end of the epic.

## What cannot be measured from this data

* Model tokens and cost per ticket; time spent reading files; time spent waiting for Jim or for another agent's merge (the Jira and GitHub times include it).
* The work before the first commit: agents commit just before they push, so "first commit to merge" (about 2 minutes) only measures the check and merge wait, not the building. The Jira Agent Go to Done time is the only whole-ticket number here, and it also includes queueing.
* Failure reasons beyond job and test counts; failures hidden by a rerun of the same CI run.
* Anything on the Mac or the Oracle VM that never became a pull request.
