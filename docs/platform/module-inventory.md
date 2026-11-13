# Module inventory: lifeos/ and the rest of the repo (LIFE-524)

Report only. Nothing was deleted or changed. Taken from origin/main at c7ad41a on Nov 9, 2026 (10:36 PM CT). Written by Claude Code (lane-edge-comm). Page-level facts that could not be checked are marked "unchecked".

## Read this first

* **One row is DEAD-PROVEN (lifeos/synthetic); no row is left unclassified (LIFE-591, Nov 11 2026).** The 11 rows that had no positive evidence were each checked in the repo, in the workflows, on the Oracle VM (system and user units, cron, installed trees, role files) and on this Mac (launchd, cron, processes). Nothing was deleted by LIFE-591; Jim then decided, and LIFE-608 deleted `lifeos/synthetic` and `tests/synthetic`.
* **Size correction.** The ticket says lifeos/ is about 66k lines in 35 modules. Measured: **48,457 lines of Python in 34 top-level units** (322 .py files; 34 other files are shell, JSON, Node, systemd and launchd files). tests/ is **56,713 lines**, not about 57k (fine). Largest are sources 8,832, jobs 8,564, platform 5,596, interview 5,159, edge 2,692.
* **Line-count target for follow-on tickets: none proposed.** The target would count only DEAD-PROVEN rows, and there are none. The first real saving is elsewhere (see Findings).

## Findings Jim may want to act on

1. **Workflow YAML is the biggest duplication, not Python.** The 22 workflow files are 5,130 lines. The `bitwarden/sm-action` load step and its retry step appear in all 44 jobs with identical secret lists (44 of 44 pairs): 88 steps, about 1,466 lines (29%). The three step types in LIFE-521 total only about 464 lines (9%). See LIFE-521 comment 12434.
2. **The ticker on Oracle is older than the repo** (ticker.py, install-ticker.sh, platform/alert_registry.py differ; LIFE-530 report). The repo changes of Nov 6 and 7 are not deployed.
3. **Code that runs on Oracle with no repo source:** Cloud Fit engine (8 files), resume builder (5), claude-handoff (3), Hermes launch scripts, granola_contract.py, lifeos-fit-deploy, two lifeos-secret helpers. Private by design for Fit and resume (LIFE-529); the others are undecided.
4. **Test-suite time.** AGENTS.md says the suite takes "about 2 s". Observed today on the Mac: **Ran 4185 tests in 122.6 s** (OK, 28 skipped). PR CI runs it in two parallel shards with a warning at 45 s and failure at 90 s; the last PR run passed. The Notion Platform Canon target is under 60 s. Three different numbers; I do not claim any target is met on one machine.
5. **The Mac worktree and branch counts do not reproduce.** The Mac report (comments 12401 and 12412) says 94 registered worktrees and 216 unmerged branches. From this checkout today I see **11 worktrees and 7 local branches not in main** (Appendix A). I do not know why (a different path, a prune since 8:25 PM CT, or a different counting method). Appendix A lists what I can see; the Mac report's numbers remain unconfirmed.

## Where each module runs (LIFE-620)

Generated from `lifeos/platform/runtime.json`; do not edit by hand. The drift check's surface list and the ticker installer's module list are tested against the same file.

<!-- runtime-manifest:begin (generated: edit lifeos/platform/runtime.json, then python -m tests.contracts.test_runtime_manifest --write) -->
| Module | Runs on | Installed as | Activated by |
|---|---|---|---|
| lifeos/agenda | github |  |  |
| lifeos/amazon | github |  |  |
| lifeos/bench | github |  |  |
| lifeos/bills | github |  |  |
| lifeos/buzz | oracle |  |  |
| lifeos/calendar_bridge | github |  |  |
| lifeos/dcc | github |  |  |
| lifeos/delivery | github |  |  |
| lifeos/edge | oracle |  |  |
| lifeos/hermes_requests.py | github |  |  |
| lifeos/hiring | github, oracle |  |  |
| lifeos/imessage | github, oracle, mac |  |  |
| lifeos/interview | github |  |  |
| lifeos/jira | github |  |  |
| lifeos/jobs | github |  |  |
| lifeos/megibow | github |  |  |
| lifeos/network | github |  |  |
| lifeos/oraclecloud | github, oracle |  |  |
| lifeos/outlook | github |  |  |
| lifeos/physmail | github |  |  |
| lifeos/platform | github, oracle |  |  |
| lifeos/probe.py | github |  |  |
| lifeos/recruiters | github |  |  |
| lifeos/run.py | github |  |  |
| lifeos/sources | github, oracle |  |  |
| lifeos/telegram | oracle |  |  |
| lifeos/whatsapp | oracle |  |  |
| lifeos/edge/ticker/ticker.py | oracle | /opt/lifeos-ticker/ticker.py | lifeos-ticker-watch.timer |
| lifeos/platform/gate.py | oracle | /opt/lifeos-ticker/lifeos/platform/gate.py | lifeos-ticker-watch.timer |
| lifeos/platform/alerts.py | oracle | /opt/lifeos-ticker/lifeos/platform/alerts.py | lifeos-ticker-watch.timer |
| lifeos/platform/alert_registry.py | oracle | /opt/lifeos-ticker/lifeos/platform/alert_registry.py | lifeos-ticker-watch.timer |
| lifeos/platform/alert_settings.py | oracle | /opt/lifeos-ticker/lifeos/platform/alert_settings.py | lifeos-ticker-watch.timer |
| lifeos/platform/redact.py | oracle | /opt/lifeos-ticker/lifeos/platform/redact.py | lifeos-ticker-watch.timer |
| lifeos/platform/drift.py | oracle | /opt/lifeos-ticker/lifeos/platform/drift.py | lifeos-ticker-watch.timer |
| lifeos/edge/backup/offsite_backup.py | oracle | /opt/lifeos-offsite/offsite_backup.py | lifeos-offsite-backup.timer |
| lifeos/edge/ticker/oracle/install-ticker.sh | oracle | /opt/lifeos-ticker/install-ticker.sh | lifeos-ticker-watch.timer |
| lifeos/edge/backup/oracle/install-offsite.sh | oracle | /opt/lifeos-offsite/install-offsite.sh | lifeos-offsite-backup.timer |
| lifeos/edge/oracle/close_release.py | oracle | /opt/lifeos-close/bin/close_release.py | lifeos-close-release.timer |
| lifeos/edge/oracle/install-close.sh | oracle | /opt/lifeos-close/bin/install-close.sh | lifeos-close-release.timer |
| lifeos/imessage/mac/ai.lifeos.imessage-attention.plist | mac | ~/Library/LaunchAgents/ai.lifeos.imessage-attention.plist | ai.lifeos.imessage-attention |
| lifeos/imessage/mac/ai.lifeos.imessage-relay.plist | mac | ~/Library/LaunchAgents/ai.lifeos.imessage-relay.plist | ai.lifeos.imessage-relay |
<!-- runtime-manifest:end -->

## A. lifeos/ (34 units)

Columns: lines = Python lines; tests = test files / test lines that mention the module; PII = handles personal data (judged from purpose and docs, not scanned); where it runs: the generated table above (LIFE-620); Class = LIVE / DEAD-PROVEN (every row has one; LIFE-591).

| Unit | Files | Lines | Last commit | Tests | PII | Class | Proposed action | Reason |
|---|---|---|---|---|---|---|---|---|
| lifeos/sources | 58 | 8,832 | 2026-11-08 | 63 / 14,533 | mixed | LIVE | keep; merge candidates inside (29 loose files) | 24 of 27 stages named in workflows; close_app runs on Oracle |
| lifeos/jobs | 65 | 8,564 | 2026-11-08 | 80 / 19,303 | yes | LIVE | keep | 22 of 30 stages named in workflows |
| lifeos/platform | 41 | 5,596 | 2026-11-09 | 127 / 33,719 | mixed | LIVE | keep; shared base | imported by nearly every package; gate runs on Oracle |
| lifeos/interview | 21 | 5,159 | 2026-11-08 | 39 / 11,544 | yes | LIVE | keep | 4 of 4 stages named in workflows |
| lifeos/edge | 48 | 2,692 | 2026-11-08 | 15 / 4,421 | mixed | LIVE | keep | ticker, backup, close, botstudio, granola helper run on Oracle (3 ticker files older there, see LIFE-530 report) |
| lifeos/jira | 12 | 1,617 | 2026-11-09 | 9 / 3,266 | no | LIVE | keep | 7 of 7 stages named in workflows |
| lifeos/imessage | 15 | 1,575 | 2026-10-26 | 9 / 2,342 | yes | LIVE | keep | 2 stages in workflows; alerts_endpoint.py on Oracle |
| lifeos/recruiters | 9 | 1,360 | 2026-10-25 | 7 / 2,119 | yes | LIVE | keep | 2 of 2 stages in workflows |
| lifeos/hiring | 10 | 1,355 | 2026-11-08 | 11 / 4,455 | yes | LIVE | keep | 1 stage in workflows; used by Oracle Close |
| lifeos/synthetic | 13 | 1,335 | 2026-11-09 | 9 / 1,547 | no | DEAD-PROVEN | DELETED by LIFE-608 on Jim's typed yes (five shared result codes kept in codes.py under platform) | no import outside itself and its tests, no stage, no workflow; none of the Oracle units, cron entries, installed trees or role files, nor this Mac's launchd, cron or processes names it; only strings in platform/codes.py and the credential list point at it. Rollback if wanted: the code stays in Git history |
| lifeos/amazon | 10 | 1,319 | 2026-10-26 | 8 / 3,417 | yes | LIVE | keep | 3 of 5 stages named in workflows |
| lifeos/network | 10 | 1,231 | 2026-11-08 | 9 / 2,978 | yes | LIVE | keep | imported by run.py and sources; its stages network-inspect, -import, -audit and -match are run by the manual workflow network.yml (input `stage`), network-surface and -layout by network.yml and (surface) by hourly.yml |
| lifeos/bills | 5 | 892 | 2026-10-26 | 4 / 1,789 | yes | LIVE | keep | 3 of 3 stages in workflows |
| lifeos/megibow | 11 | 822 | 2026-11-07 | 2 / 454 | yes | LIVE | keep | imported by sources/megibow.py; stage "megibow" named in a workflow |
| lifeos/attention | 5 | 721 | 2026-11-06 | 5 / 2,858 | yes | LIVE | keep | 2 of 2 stages in workflows |
| lifeos/physmail | 6 | 640 | 2026-10-25 | 2 / 1,982 | yes | LIVE | keep | 2 of 2 stages in workflows |
| lifeos/probe | 1 | 603 | 2026-10-26 | 2 / 143 | no | LIVE | keep | stage "probe" in a workflow |
| lifeos/agenda | 3 | 587 | 2026-10-19 | 2 / 1,552 | yes | LIVE | keep | 2 of 2 stages in workflows |
| lifeos/delivery | 3 | 361 | 2026-11-01 | 2 / 352 | no | LIVE | keep | its one stage, amazon-census, is the `census` option of the manual workflow amazon.yml (run line `amazon-$STAGE`); no Oracle or Mac caller (and none needed) |
| lifeos/whatsapp | 8 | 356 | 2026-10-25 | 3 / 699 | yes | LIVE | keep | Oracle report lists lifeos/whatsapp/oracle as live |
| lifeos/buzz_mcp | 1 | 333 | 2026-11-07 | 0 / 0 | no | LIVE | keep | Oracle report: lifeos.buzz_mcp live |
| lifeos/telegram | 2 | 332 | 2026-10-15 | 1 / 123 | yes | LIVE | keep | called by path from imessage/alerts_endpoint.py (LIFE-530 report); Jim: Telegram alerting |
| lifeos/oraclecloud | 5 | 328 | 2026-10-28 | 3 / 393 | no | LIVE | keep | Oracle report lists oraclecloud/oracle/stats.mjs; imported by sources/oracle_card.py |
| lifeos/dcc | 3 | 321 | 2026-10-29 | 3 / 1,621 | mixed | LIVE | keep | stage dcc-card in workflows |
| lifeos/buzz_relay | 1 | 285 | 2026-11-07 | 5 / 1,695 | no | LIVE | keep | Oracle report: buzz_relay is part of the live Buzz MCP |
| lifeos/run | 1 | 241 | 2026-11-06 | 20 / 9,546 | no | LIVE | keep | entry point of every stage (18 workflows) |
| lifeos/calendar_bridge | 3 | 230 | 2026-10-25 | 2 / 485 | yes | LIVE | keep | stage calendar-sync in workflows |
| lifeos/readiness | 1 | 216 | 2026-11-09 | 3 / 417 | no | LIVE | keep | used by readiness-live.yml and AGENTS.md gate |
| lifeos/decisions | 1 | 109 | 2026-10-20 | 2 / 412 | no | LIVE | keep | AGENTS.md: new decision numbers only from this tool |
| lifeos/hermes_requests | 1 | 101 | 2026-11-05 | 1 / 166 | no | LIVE | keep | stage "hermes" in a workflow |
| lifeos/codemap | 1 | 97 | 2026-10-05 | 1 / 20 | no | LIVE | keep | AGENTS.md: a test fails when the generated codemap is stale |
| lifeos/outlook | 3 | 91 | 2026-10-02 | 1 / 332 | yes | LIVE | keep (91 lines; could merge into platform.outlook) | stage outlook-probe in workflows |
| lifeos/buzz_nip98 | 1 | 79 | 2026-11-01 | 0 / 0 | no | LIVE | keep | Oracle report names buzz_nip98 as live; no repo test |
| lifeos/buzz_auth | 1 | 77 | 2026-11-02 | 1 / 478 | no | LIVE | keep | Oracle report names buzz_auth as live |

Evidence notes: "stages named in workflows" means the stage name from docs/codemap.json appears in at least one file under .github/workflows (a manual-only workflow counts as live, per the ticket). A string reference in lifeos/platform/codes.py is not counted as a caller. Invisible callers checked: the stage loader in lifeos/run.py, absolute-path calls in lifeos/imessage/alerts_endpoint.py, systemd units under the five oracle/ folders, the two Mac launchd plists (definitions only, loaded state unchecked), workflow schedules and dispatch, and lifeos/sources/web/registry.py (not mapped row by row: **unchecked**).

## B. .github/workflows (22 files, 5,130 lines)

| File | Lines | Triggers | Last commit | Class | Action | Reason |
|---|---|---|---|---|---|---|
| agenda.yml | 97 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| amazon.yml | 67 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| bills.yml | 79 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| branches.yml | 85 | workflow_dispatch, pull_request, workflow_run | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | runs on every PR/merge |
| calendar.yml | 77 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| diag.yml | 269 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| domains.yml | 1,747 | workflow_dispatch, workflow_run | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| fit-edge-test.yml | 73 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| granola-auto.yml | 100 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| granola.yml | 75 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| hermes-stage.yml | 63 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| hourly.yml | 1,558 | schedule, workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | scheduled |
| interview-proof.yml | 55 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| manual-job.yml | 105 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| network.yml | 112 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| outlook.yml | 114 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| pr-tests.yml | 47 | pull_request, push | 2026-11-09 | LIVE | keep | runs on every PR/merge |
| profile-sync.yml | 79 | workflow_dispatch, workflow_run | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| readiness-live.yml | 79 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| recruiters.yml | 82 | workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | manual only (not dead) |
| safety.yml | 98 | schedule, workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | scheduled |
| watchdog.yml | 69 | schedule, workflow_dispatch | 2026-11-08 | LIVE | keep; shared Bitwarden load/retry pairs are the merge target | scheduled |

Run history for the baseline is in section F.

## C. tools/, hermes/, widgets/, requirements, docs

| Item | Files | Lines | Last commit | PII | Host | Class | Proposed action | Reason |
|---|---|---|---|---|---|---|---|---|
| tools/orca-concord-room | 13 | 3,877 | 2026-11-09 | no | Mac (Orca plugin) | LIVE | keep | Node tests run on every PR; Mac inventory: Concord adapter LIVE |
| hermes/ (scripts, roles, plugins) | 20 | 943 | 2026-11-07 | no | Oracle (Hermes runs there) | LIVE | keep | all six role files (`main` and five profiles) are byte-identical to the live copies in the Hermes folder on the VM; the deploy scripts are described in docs/hermes-files.md and have tests under tests/hermes. The connector-allowlist plugin was not found in the VM's top-level plugin folder (profile-level plugin folders were not checked) |
| widgets/communications-attention | 1 | 113 | 2026-10-15 | yes (display only) | GitHub Pages | LIVE | keep | the hosting copy is the separate public `github-widgets` repository, whose Pages site is built (checked through the GitHub API); the Notion embeds that point at it were not checked |
| widgets/launch | 6 | 102 | 2026-10-21 | no | GitHub Pages | LIVE | keep | same hosting copy and same limit as the row above |
| widgets/world-clock | 1 | 435 | 2026-10-14 | no | GitHub Pages | LIVE | keep | same hosting copy and same limit as the row above (its README names this folder as the canonical source) |
| muse-persona-export/ | removed | | | yes | n/a | n/a | already deleted by LIFE-537 (PR 863) | moved to the Notion Product Requirements entry REQ-34 |
| requirements.txt | 1 | 2 | 2026-10-01 | no | GitHub | LIVE | keep | pinned requirements used by CI |
| requirements-browser.txt | 1 | 1 | 2026-10-01 | no | GitHub | LIVE | keep | source of requirements-browser.lock, which hourly.yml installs with `pip install --require-hashes`; tests/contracts/test_lockfiles.py pairs the two |
| requirements-fit.txt | 1 | 1 | 2026-10-01 | no | GitHub (semantic layer) | LIVE | keep; do not move (LIFE-529) | source of requirements-fit.lock, which hourly.yml installs for the optional semantic layer (lifeos/jobs/fit/semantic.py imports fastembed); the same pairing test covers it |
| docs/ (33 top-level files plus contracts/ and development-policy/) | | | | no | n/a | LIVE | keep; check stale paths in a later ticket | read by agents and listed in INDEX.md (the repo check enforces the index); one summary row; PRIVACY.md line 17 names tests/test_privacy.py, the real guard is tests/contracts/test_privacy.py (drift only, protected file, Jim approves any fix) |
| INDEX.md, AGENTS.md, CLAUDE.md | 3 | 222 | | no | every session | LIVE | keep | bootstrap files |

## D. Oracle-only code (not in the repo)

lifeos-fit (8 files), lifeos-resume (5), claude-handoff (3), Hermes launch scripts, granola_contract.py, lifeos-fit-deploy, two lifeos-secret helpers: all **LIVE, VM-only**. Cloud Fit and resume: private by design, do not move or delete (LIFE-529). Source: LIFE-530 report (89 installed files against main c9e7617: 58 same, 3 different, 28 no repo match).

## E. Scalability baseline (before)

**(a) Files changed to add one new source.** Example: commit bac7831 (scale_up_register). Needed: the source module, its test, one line in lifeos/run.py STAGES, error codes in lifeos/platform/codes.py, a workflow step, tests/contracts/test_workflow.py, docs/CODEMAP.md, docs/codemap.json. **8 files** minimum (the commit touched 12 because it added two other modules too).

**(b) Workflow durations, last 10 runs each, from the GitHub API (times in CT, runs started Nov 9 7:00 PM CT to 10:17 PM CT):**

| Workflow | Runs seen | success | failure | cancelled | skipped | median | p95 |
|---|---|---|---|---|---|---|---|
| hourly.yml | 10 | 3 | 1 | 6 | 0 | 269 s | 1,354 s |
| domains.yml | 10 | 4 | 0 | 0 | 6 | 3 s (skipped runs take 2 to 3 s; the 4 real runs took 1,087 to 1,143 s) | 1,143 s |

Durations are start to last update for completed runs, including cancelled and skipped. "Expected runs": hourly has three schedule slots an hour and a gate that lets about one through, so 3 hours should give about 3 gated runs plus dropped slots; I saw 10 runs of which 6 were cancelled, which I did not investigate (concurrency cancel is the likely cause; unverified). Only a 3-hour window; no weekly view.

**(c) Bootstrap size:** AGENTS.md 125 + CLAUDE.md 27 + INDEX.md 70 = **222 lines** before starting work.

**(d) Full deterministic test suite:** observed **122.6 s** for 4,185 tests on the Mac today (single process). AGENTS.md says about 2 s. PR CI warns at 45 s and fails at 90 s (two shards). The Notion Platform Canon target is under 60 s. These disagree; no target is claimed met.

## Appendix A: git worktrees and local branches seen from this checkout (Nov 9, 2026, 10:36 PM CT)

Listed only; none proposed for deletion. Counts differ from the Mac report (94 worktrees, 216 unmerged); see Finding 5.

| Worktree (home folder shown as ~) | Last commit | Tip in main | Folder exists | Branch |
|---|---|---|---|---|
| ~/Developer/LIFEOS/life-os-v7 | 2026-11-09 | yes | yes | life/LIFE-524/module-inventory |
| ~/.concord-pilot/wt-council | 2026-11-09 | no | yes | life/LIFE-411/platform-core-os-1-0-0/platform-core-os/concord-room-council |
| ~/.concord-pilot/wt-round | 2026-11-09 | no | yes | council-round |
| ~/.concord-pilot/wt-run1 | 2026-11-09 | no | yes | council-run1 |
| ~/.concord-pilot/wt-run2 | 2026-11-09 | no | yes | council-0190 |
| ~/Developer/LIFEOS/worktrees/v7-life-249 | 2026-11-03 | yes | yes | (detached) |
| ~/orca/workspaces/life-os-v7/demo-orca-setup-hook-20261108 | 2026-11-08 | yes | yes | jimmarkunas/demo-orca-setup-hook-20261108 |
| ~/orca/workspaces/life-os-v7/demo-orca-setup-hook-python312-20261108 | 2026-11-08 | yes | yes | jimmarkunas/demo-orca-setup-hook-python312-20261108 |
| ~/orca/workspaces/life-os-v7/hooks | 2026-10-19 | yes | yes | jimmarkunas/hooks |
| ~/orca/workspaces/life-os-v7/life-417-add-agent-inbox-ask-and-verified-comple | 2026-11-08 | yes | yes | jimmarkunas/life-417-add-agent-inbox-ask-and-verified-comple |
| ~/orca/workspaces/life-os-v7/life-419-proof | 2026-11-08 | yes | yes | jimmarkunas/life-419-proof |

| Local branch not in main | Last commit |
|---|---|
| council-0190 | 2026-11-09 |
| council-round | 2026-11-09 |
| council-run1 | 2026-11-09 |
| life/LIFE-411/platform-core-os-1-0-0/platform-core-os/concord-room | 2026-11-08 |
| life/LIFE-411/platform-core-os-1-0-0/platform-core-os/concord-room-council | 2026-11-09 |
| life/LIFE-411/platform-core-os-1-0-0/platform-core-os/concord-room-reachability | 2026-11-09 |
| life/LIFE-537/remove-persona-export | 2026-11-09 |

Note: life/LIFE-537/remove-persona-export shows as "not in main" only because the PR was squash-merged; its change is in main as c7ad41a.

## Not checked

* Mac launchd, cron, processes and ports (needs Jim's Terminal report `mac-readonly-live-inventory-prompt.md`); so no DEAD-PROVEN row is possible.
* lifeos/sources/web/registry.py rows mapped to loaders.
* Which hermes/ scripts and widgets/ pages are still used by Jim.
* The 94 / 216 Mac counts.
* Docs stale-path scan beyond the PRIVACY.md note.

