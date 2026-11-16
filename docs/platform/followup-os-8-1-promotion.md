# Followup OS 8.1: minimal Action Inbox slice and Production Contract promotion proposal (LIFE-737, Nov 16 2026)

Status: decided (D258 in docs/DECISIONS.md, Jim, Nov 16 2026); the Production Contract text still needs his approval (LIFE-750). Planning only: no stage, scheduler, storage, credential or contract text is changed by this file. Sources read (read-only): the Notion pages "Followup OS" (product canon, incl. the production/runtime handoff contract) and "Domain: Followup OS (machine page)", and the merged `lifeos/inbox/followup.py` (LIFE-710). Why: LIFE-710 found no active Followup owner, so the Action Inbox sixth feed (LIFE-696) is synthetic until one exists.

## 1. The minimal slice (what the Inbox needs) versus roadmap

| In the slice | Stays roadmap |
|---|---|
| Four loop classes: waiting on someone, deliverable owed, post-interaction value-add, short-lived outcome thread | Act now, drafting, email send, Calendar proposals, Jira/Todoist promotion (canon "action-execution contract") |
| Derived states only: Needs Action Today, Waiting/Aging, Upcoming; Review Needed kept apart and out of counts | Review/Confirm/Dismiss UI surface in the Daily Command Center |
| Checkpoint: source-backed or visibly Suggested; a Jim override never edits source due dates | Broad source scans, history backfill, relationship workflows, escalation |
| Suppression fingerprints for dismissed/closed evidence (no resurrection without new evidence) | Any CRM, mail archive, task database or second scheduler (canon refuses all) |
| Thread object fields only: id, outcome, class, owner, next move, checkpoint + provenance, state, source refs, confidence, resolution state, last trustworthy observation | Copied bodies, canonical due dates, a history ledger |
| Missing/stale/denied evidence is DEGRADED with "last accepted" time, never zero; Personal/Bytalos isolated, TGP excluded | Dashboard-framework work |

The derivation rules already exist as the pure `project()` in `lifeos/inbox/followup.py` (synthetic, 28 tests). The gap is a real owner, real evidence reads and one persistence owner.

## 2. The promotion change (the exact proposal)

| Item | Proposed value |
|---|---|
| Component | One `followup` stage (current-state lane), registered in `lifeos/platform/source_registry.json` with the existing stage pattern; no new workflow file, cron, watchdog or poller |
| Owner/scheduler | **Decision A (recommended): V7's existing hourly tick runs it.** V7 must run with ChatGPT unavailable (D54) and D25/D58 make V7's workflow the sole scheduler for V7-owned work. The canon text names `LIFE OS Daily Runs`; **Decision B** keeps that, at the cost of the Inbox depending on ChatGPT. Either way: no third scheduler |
| Cadence | Every hourly tick is one full reconciliation of every approved source (Jim, Nov 16 2026: "full reconciliation needs to be every hour"): each source is read from its last accepted checkpoint and every thread is re-derived and written back. No delta-only runs, no hour-specific full runs. Stalling or staggering jobs to save time is deferred to a later decision |
| Sources read (approved adapters only) | Gmail/Outlook delta, Calendar, Jira, Todoist, Notion source references, Interview OS context; through the existing LIFE-306 broker and per-client grants. Asana is excluded from the first slice (view-only elsewhere) |
| Storage | The existing V7 MySQL store (`lifeos.platform.db.connect`), one new table family `v7_followup_*` (threads, source checkpoints, overrides, suppression fingerprints), one code path owns writes: validate, atomic write, read-back, equality check. This is a schema change and needs Jim's approval per AGENTS.md before it is created |
| DEGRADED | Component-local. Failed source: keep last accepted state, show "DEGRADED, last accepted <time CT>", do not advance that source checkpoint. Failed write or read-back: keep known-good state. Unrelated components keep rendering |
| Output | One component-state payload (PASS or DEGRADED, counts, thread projections, review list outside counts, provenance ids) read by the Action Inbox and, if wanted, the Daily Command Center |
| Contract text to change at promotion | Followup as an authorized lane; which branches run it; its state location and single mutation owner; component-local freshness; where it renders; the live acceptance below; "no second scheduler". This is the eight-item list in the canon handoff; the contract itself is edited only after Jim approves this file |
| Natural-run acceptance | One real scheduled hourly run proves: slot stamp, contract load, bounded read, reconcile, persist, read-back, projection, and a second idempotent run creating no duplicate thread. A manual run or unit test is not acceptance |

## 3. Child tickets (dependency order; each one PR, planning-only until Agent Go)

1. **Decision record.** Done when Jim records A or B, the schema approval for `v7_followup_*`, and the source list; the choice is written once in `docs/DECISIONS.md`.
2. **Followup state store.** Done when the tables, single write path, read-back check and tests for replay, suppression, checkpoint-only-after-accept and failed-write preservation pass on synthetic data.
3. **Bounded source readers.** Done when each approved source has a delta reader behind the broker with tests that the hourly path reads only authorized deltas and a failed source yields DEGRADED, not zero.
4. **Reconciler and stage.** Done when `followup` is registered, derives state from the store plus deltas using `project()`, and the Action Inbox `FollowupSource` swaps its `followup_projection_missing` refusal for this reader.
5. **Production Contract change.** Done when the eight contract items are reviewed and landed and the readiness check passes.
6. **Natural-run acceptance.** Done when one real scheduled run meets section 2's acceptance, read back, with no unauthorized scan or second scheduler. Then LIFE-716 and LIFE-719 resume.

## 4. Decisions for Jim

1. Scheduler: A (V7 tick, recommended) or B (ChatGPT Daily Runs).
2. Approve a new `v7_followup_*` table family in the V7 MySQL store (schema change).
3. Approve the first-slice source list (Gmail/Outlook delta, Calendar, Jira, Todoist, Notion references, Interview OS).
4. Move children 1 to 6 to Agent Go one at a time; nothing here activates anything.
