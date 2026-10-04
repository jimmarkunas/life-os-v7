# Scheduler authority: the conflict and the fix (approved and made; the contract edit is on a branch until Jim merges it)

Status: **approved by Jim and made on October 4, 2026.** The production contract (version 2.15.12) and the Development Policy note were edited in `life-os-automation` on the branch `claude/multi-carrier-delivery-tracking-lpaytv` (commit 2fa8eb6), docs only, no pull request. The edit takes effect for the ChatGPT automation only when Jim merges that branch. V7 recorded the same rule as decision D123. No code, workflow or test changed.

## 1. The conflict, in plain words

There are two descriptions of "who runs things on a schedule", and they cannot both be read literally.

- **The old production contract** (in `life-os-automation`) says the ChatGPT task `LIFE OS Daily Runs` is the **only** scheduler, that no GitHub cron, watchdog or helper scheduler is allowed, and that it writes the Daily Report.
- **V7's own decisions** say GitHub Actions is the only scheduler **for V7**, an outside timer may tick it, a watchdog may raise an alarm, and the ChatGPT task is a separate scheduler that runs only ChatGPT-owned modules.

Both are Jim's. What is actually running today matches V7's description, so the contract text is out of date for V7.

## 2. What each side says (verified)

| Where | Statement |
|---|---|
| Contract header, `docs/life-os-production-contract.md` (v2.15.11) | Owner: "the single enabled ChatGPT automation `LIFE OS Daily Runs`"; schedule hourly at minute 00 Chicago; "No second recurring task is authorized." |
| Contract, the operating-override list | "The existing one-hourly `LIFE OS Daily Runs` scheduler remains the sole scheduler. No new scheduler, workflow family, datastore, watchdog, dispatcher, or orchestration layer is authorized." |
| Contract, Global production invariants | "Exactly one recurring production automation may be enabled: `LIFE OS Daily Runs`. No helper scheduler, recurring GitHub cron, watchdog, retry task, mailbox monitor ... is authorized." And only Jim may disable, pause or replace it; if found disabled, re-enable it in place. |
| Contract, hourly cadence | Full source branches at 6 AM, 9 AM, 12 PM and 6 PM CT (midnight is a mail and status branch), other hours mail routing plus a presentation render. |
| Development Policy, `docs/life-os-development-policy.md`, "non-mutable execution constitution" item 2 (a locked rule: "Only Jim may supersede these locked rules") | GitHub Actions in the public **`life-os-v2`** repository are an approved stateless executor when triggered by Daily Runs; "Daily Runs remains the sole recurring scheduler; no GitHub `schedule`/cron ... or second orchestration layer is authorized." |
| Lane documents (Amazon Orders, Interview canon, accountability sync, run reliability, mail routing) | Name `LIFE OS Daily Runs` as the single owner or scheduler of their lane. |
| V7 D25 (Jim, 2026-10-01) | "GitHub Actions cron (`hourly.yml`, driving `lifeos.run`) is the sole recurring scheduler for V7. No ChatGPT/LLM-hosted schedule, no second scheduler. Any wider LIFE OS 'Daily Runs' scheduling that conflicts is superseded for V7 (the canon page needs updating on the Notion side)." |
| V7 D30 and D31 | Three crons an hour with a gate; one outside timer may tick the same workflow, holding no logic or data. |
| V7 D52 | An alarm-only watchdog; it never runs the pipeline. |
| V7 D58 (2026-10-03) | "Two independent schedulers exist and stay independent": V7's own hourly workflow, and the ChatGPT `LIFE OS Daily Runs` task, which "orchestrates only ChatGPT-owned modules ... and is not a replacement for V7 runtime work." Daily Report regions have exactly one owner, set by `lifeos/platform/router.py`. |
| V7 `tests/contracts/test_guardrails.py` | Pins exactly two workflow files with a `schedule:` (`hourly.yml`, `watchdog.yml`), three crons in `hourly.yml`, and a watchdog that never runs `lifeos.run`. |
| V7 `domains.yml` | No `schedule:`; every job runs after an hourly tick through `workflow_run`, so a V7 domain job is a stage of V7's tick, not a scheduler. |

**Four concrete clashes.**
1. "Sole scheduler" (contract, policy) against D25, D31, D58.
2. "No recurring GitHub cron, watchdog or helper scheduler" against V7's three crons and its watchdog.
3. "The ChatGPT task writes the Daily Report" against D58's regions owned by V7's code (Jira, Calendar, Bills, Amazon today; Deliveries, Mail Alerts, Hiring Pipeline and the MegIBOW block proposed).
4. Cadence: the contract's four full-source slots against V7 acquiring on every tick. Several proposed features (the company cockpit, MegIBOW, Deliveries) are written against "the contract owns run times", which cannot be satisfied for V7-owned work.

**A real risk, not only a tidiness issue.** A future agent that reads the contract as written could treat V7's cron or watchdog as unauthorized and remove it, or the ChatGPT task could classify the world as `PRODUCTION SCHEDULER DEGRADED`. The contract is read live at the start of each ChatGPT run.

## 3. The proposed resolution

Make "sole scheduler" mean **sole within its own scope**, with two scopes and one shared rule:

1. **Two scopes, each with exactly one scheduler.**
   - **ChatGPT scope:** `LIFE OS Daily Runs` is the sole scheduler for the ChatGPT-owned modules and for whatever the V2 lanes it still runs. Nothing about its slots, branches or duties changes.
   - **V7 scope:** V7's GitHub Actions workflow is the sole scheduler for V7-owned work: the hourly tick (three crons with one gate, D30), the outside timer's `workflow_dispatch` tick (D31), the alarm-only watchdog (D52), and the `domains.yml` jobs that run after each tick. No ChatGPT-hosted schedule drives V7 work.
2. **No third scheduler, ever.** The "no new scheduler, workflow family, datastore, watchdog, dispatcher or orchestration layer" invariant stays and binds both scopes. New V7 work (MegIBOW, Deliveries, the cockpit, Physical Mail) is a stage inside V7's tick, never its own schedule.
3. **Each Daily Report region has one owner**, as `router.OWNERS` records (D58). Where a lane document says `LIFE OS Daily Runs` owns something, it means the ChatGPT scope; a region that `router.OWNERS` assigns to V7 is written by V7 and read-only to ChatGPT.
4. **Only Jim may disable or replace either scheduler** (the contract's rule extended to V7's workflow, not only to Daily Runs).
5. **Run times for V7-owned work belong to V7's decisions.** Features that today say "the Production Contract owns run times" read as "the owner's scheduler owns run times": V7's tick for V7-owned work.

## 4. The smallest edit that does it

One scope paragraph and three one-sentence changes, not a rewrite of every document that mentions Daily Runs:

- **Contract, Authority model:** add the paragraph below once. It also makes the lane documents' "Owner: the single existing `LIFE OS Daily Runs`" lines read correctly without editing five files.
- **Contract, the operating-override bullet and the Global invariants bullet:** append "within the ChatGPT scope (see Scheduler scopes)" to "sole scheduler" and to "exactly one recurring production automation".
- **Development Policy, execution constitution item 2:** it is already about `life-os-v2`; append "(this item governs `life-os-v2`; V7 is governed by its own decisions D25, D31, D52 and D58)".
- **Contract footer:** a version line, 2.15.12, recording Jim's date and the change.

Draft paragraph:

> **Scheduler scopes.** LIFE OS has two schedulers, each the sole scheduler within its own scope, and no third is authorized. (1) `LIFE OS Daily Runs` is the sole scheduler for ChatGPT-owned modules and for the lanes this contract assigns to it. (2) The `life-os-v7` GitHub Actions workflow (its hourly tick, the outside timer's tick, the alarm-only watchdog, and the domain jobs that run after each tick) is the sole scheduler for V7-owned work, as recorded in V7 decisions D25, D31, D52 and D58. A domain job in V7 is a stage of V7's tick, not a scheduler. Every Daily Report region has exactly one owner, as recorded in V7's `lifeos/platform/router.py`; where this contract or a lane document names `LIFE OS Daily Runs` as owner of something, it means ChatGPT-owned scope and does not reach a region or lane that V7 owns. No new scheduler, workflow family, datastore, watchdog, dispatcher or orchestration layer is authorized in either scope, and only Jim's explicit current instruction may disable, pause, stop, replace or reschedule either scheduler.

Draft footer line:

> This v2.15.12 file preserves the complete v2.15.11 production authority and adds Jim's explicit October 2026 scheduler-scope rule: two schedulers, each sole within its own scope (`LIFE OS Daily Runs` for ChatGPT-owned work, the `life-os-v7` workflow for V7-owned work), no third, one owner per Daily Report region.

**Not changed by this edit:** Daily Runs' slots, branches, evidence rules, ownership of recovery evidence, continuity state, or any lane's behavior. The other contract changes already queued (tracking numbers in private storage; Physical Mail's unread-state rule, Attention duplication and region ownership; the cockpit cadence) are separate edits and are listed here only so they can be batched if Jim prefers.

## 5. The V7 side

V7 needs a decision entry, not a code change: a consolidated record that D25's "sole scheduler for V7" and D58's "two independent schedulers" are the same rule at different scopes, and that the contract was updated to say so. Draft text for `docs/DECISIONS.md`, to be added when the contract edit is approved:

> **D1xx — Scheduler scopes (Jim, 2026-10-xx).** D25 and D58 are one rule. V7's GitHub Actions workflow is the sole scheduler for V7-owned work; `LIFE OS Daily Runs` is the sole scheduler for ChatGPT-owned modules; no third scheduler; a domain job is a stage of V7's tick. `tests/contracts/test_guardrails.py` keeps pinning exactly two scheduled workflow files. The production contract (v2.15.12) states the same split.

No test changes. The existing pin already enforces the V7 half.

## 6. What was done, and what is left

**Done.**
- `life-os-automation`, branch `claude/multi-carrier-delivery-tracking-lpaytv`: the "Scheduler scopes" section was added to the contract exactly as drafted in section 4; "sole scheduler" and "exactly one recurring production automation" were qualified as "within the ChatGPT scope"; the header version and the provenance footer became 2.15.12; the Development Policy's execution-constitution item 2 now says it governs `life-os-v2` and points to V7's decisions. The contract-related tests I could find (Interview canon, Amazon orders, Gmail acquisition) pass. The repository's own static check (`scripts/execution_constitution_check.py`) reports FAIL, and it did before my edit: it flags an artifact upload in that repository's `life-os-runtime.yml`, which is unrelated to scheduling.
- V7: decision D123 and its index line, using the draft text in section 5. The decision-index test passes.

**Left for Jim.**
- **Merge the branch** when he is happy with the diff; until then the live contract still says the old thing. I did not open a pull request.
- **Platform Canon (Notion).** I found the page, and it already carries a V7 amendment from October 1 ("for the V7 Jobs pipeline the recurring scheduler is GitHub Actions ... supersedes the Daily Runs and no-GitHub-cron lines above for V7 only"). It is narrower than the new rule (it names only the Jobs pipeline, while the domain jobs and the new V7 features also run on V7's tick). Widening that one phrase to "V7-owned work" would align it. I have not edited it.
- **Not included, on purpose:** the other contract changes I had listed as optional (carrier tracking numbers in private storage, Physical Mail's unread-state rule and region ownership, the cockpit cadence). They were never drafted or decided, and the contract is the live instruction file of an unattended automation, so each needs its own decision and its own hunk.
