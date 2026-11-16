# DRAFT for Jim's approval: Production Contract text for the Followup lane (LIFE-750, Nov 16 2026)

Status: **DRAFT. Nothing is edited at the contract's owner.** The contract is `docs/life-os-production-contract.md` in the `life-os-automation` repository (v2.15.15 on Nov 16 2026). This file only proposes the text. After Jim approves it in this PR, the edit is a separate PR to `life-os-automation` (not V7), and the V7 readiness check must still pass. Authority: Jim, chat, Nov 16 2026: "V7" (scheduler), the `v7_followup_*` table family "Approved", the first source list "Approved", and "Approved, promote Followup OS 8.1" (D258 in `docs/DECISIONS.md`; proposal in `docs/platform/followup-os-8-1-promotion.md`).

**One correction to the earlier proposal.** The promotion doc offered "Decision B: keep ChatGPT `LIFE OS Daily Runs`". That is moot: the contract (v2.15.15) records Daily Runs as RETIRED since Oct 20, 2026, and V7's tick is the only scheduled executor. Decision A (V7's hourly tick) is the only option, which is what Jim chose.

## 1. Where the text goes (insertion points)

Three small edits to the contract, all in the style of its existing V7 grants.

**(a) New section**, placed after `INTERVIEW CANON OWNERSHIP OVERRIDE` (the last V7 ownership grant) and before `NEWSLETTER + US REMOTE OPERATING OVERRIDE`. It follows the same pattern as the two sections it sits beside:

> `## MAIL ALERTS OWNERSHIP OVERRIDE — October 5, 2026` ... "**V7 is the sole live owner and writer of the Daily Report `Mail Alerts` region.**" ... "This creates no new scheduler, workflow family, orchestration layer, or duplicate datastore." ... "Missing, stale, partial, failed, or unaccepted V7 Mail Alerts evidence is `DEGRADED` for any consuming ChatGPT presentation."
>
> `## INTERVIEW CANON OWNERSHIP OVERRIDE — October 20, 2026 CT` ... "**Activation order (one writer at a time):** (1) this contract is merged on `life-os-automation/main` ... (2) only then Jim sets the V7 repository variable ... Until both have happened V7 runs the stage as a dry run and writes nothing."

**(b) One row** in the `DAILY RUNS RETIREMENT OVERRIDE` status table (the table of formerly ChatGPT-owned functions), after the `Interview Canon writer` row. Before: the table ends with `| Interview Canon writer (scripts/interview_canon_runtime.py) | RETIRED — write gate INTERVIEW_CANON_WRITE_AUTHORIZED: false preserved |`. After: the same table plus the row in section 3 below.

**(c) Version line and provenance.** The header `**Version:**` line gains `Followup lane granted to V7 (disabled until acceptance)`, the version becomes 2.15.16, and the Provenance section gains one sentence (section 4).

Not touched: `Global production invariants`, `Authority model`, `Shared Platform Runtime`, `Daily Report — RETIRED RENDERING AUTHORITY` (Followup is not a Daily Report region), cadence text and every lane document.

## 2. Drop-in text for (a)

```
## FOLLOWUP LANE OWNERSHIP OVERRIDE — <approval date> CT

- **V7 is the sole owner and writer of the Followup derived state** that feeds the Action Inbox: open loops (waiting on someone, deliverable owed, post-interaction value-add, short-lived outcome thread), their derived states (Needs Action Today, Waiting/Aging, Upcoming, with Review Needed kept apart), checkpoints and suppression fingerprints. Jim authorized the lane on November 16, 2026 (LIFE-737; decision D258 in `jimmarkunas/life-os-v7`). `LIFE OS Daily Runs` is retired and does not read, write or repair this state.
- **Execution path.** One `followup` stage inside V7's existing hourly tick and `domains.yml` continuation (`lifeos.run followup`). This creates no new scheduler, workflow family, cron, watchdog, orchestration layer, or datastore beyond the table family named below. Only Jim may reverse this ownership, and only by editing this section.
- **Cadence.** Every tick the stage reads only bounded deltas (new evidence since each source's last accepted checkpoint, Jim's explicit captures, checkpoints that come due by time alone). A full reconciliation of all sources runs only at the hours this section names: <6:00 AM, 9:00 AM, 12:00 PM and 6:00 PM CT>. A tick with no due work never rescans a source.
- **Sources (read-only, approved adapters only).** Gmail/Outlook delta, Calendar, Jira, Todoist, Notion source references and Interview OS context, each through the existing secrets broker and its own per-client grant. Asana is not read. Personal and Bytalos grants, identities, records and caches are separate; TGP is excluded. This section grants no new credential, OAuth scope or provider access; each grant is a separate step that needs Jim's explicit approval.
- **State and the single mutation owner.** The one authorized datastore is the `v7_followup_*` table family (threads, source checkpoints, overrides, suppression fingerprints) in the existing V7 MySQL store, approved by Jim on November 16, 2026. The `accept()` write path in `lifeos/inbox/followup_store.py` is the only writer: it validates the whole batch, writes it in one transaction, reads it back and compares before committing. A Jim checkpoint override lives in its own table and never edits a source's due date. Creating the tables in production is a separate step that needs Jim's go.
- **Freshness and DEGRADED are component-local.** A failed, missing, stale, denied, truncated or unaccepted source makes Followup `DEGRADED` with a "last accepted" time in America/Chicago, keeps the last accepted threads, and does not advance that source's checkpoint. A failed write or read-back keeps the known-good state. Followup never presents a trustworthy empty state or `0`, and an unrelated component keeps rendering when Followup is degraded. Strong proof that an outcome ended closes a thread and the same evidence cannot reopen it; an intermediate Jira or Todoist task, a chase email or an ended Calendar block never closes one.
- **Where it renders.** One component-state payload (PASS or DEGRADED, counts, thread projections, a review list outside the counts, provenance) read by the Action Inbox. This grants no Daily Report region; the Daily Command Center may read the payload only as a consumer and may not refresh or repair it. No consumer falls back to the retired executor or reads a source itself.
- **Live acceptance.** The lane is DISABLED — NOT DELIVERED until one natural scheduled hourly run shows the slot stamp, the contract load, a bounded read, the reconciliation, the write, the read-back and the projection, and a second run creates no duplicate thread, with no unauthorized source scan. A manual run or a unit test is not acceptance. Until then the stage may run only as a dry run that writes nothing.
- **Activation order (one step at a time):** (1) this contract is merged on `life-os-automation/main`; (2) Jim approves creating the `v7_followup_*` tables; (3) Jim approves each source credential grant; (4) the `followup` workflow step is added to V7 with no new `permissions`; (5) the natural run above; (6) only then Jim records delivery in the status table. Followup actions (confirm resolution, update checkpoint) and every Followup OS 8.1 roadmap capability (act now, drafting, email send, Calendar proposals, Jira/Todoist promotion) stay NOT DELIVERED.
```

## 3. Drop-in row for (b)

```
| Followup lane (Action Inbox sixth feed) | DISABLED — NOT DELIVERED until natural-run acceptance; then V7 only, as granted in `FOLLOWUP LANE OWNERSHIP OVERRIDE` |
```

## 4. Drop-in text for (c)

Header, after the existing items: `/ Followup lane granted to V7 (disabled until acceptance)`, version `2.15.16`.

Provenance sentence, appended after the v2.15.15 paragraph:

```
This v2.15.16 file records Jim's November 16, 2026 grant of the Followup lane to V7 as a single `followup` stage in V7's existing hourly tick, with one new `v7_followup_*` table family in V7's existing MySQL store. The lane stays DISABLED — NOT DELIVERED until its natural scheduled-run acceptance; the grant creates no scheduler, workflow family, orchestration layer or other datastore.
```

## 5. What this does NOT change

- No scheduler, workflow, cron, `permissions` block, credential, OAuth scope, source access or table. The V7 schedule is unchanged.
- The Daily Report regions, Interview OS and Mail Alerts grants, the retirement of `LIFE OS Daily Runs`, and every lane document.
- Followup OS 8.1 roadmap capabilities and every Action Inbox write action (LIFE-719).

## 6. Protected steps that stay separate, each needing Jim's own go

1. Creating the `v7_followup_*` tables in production (schema change).
2. Each source credential grant (Gmail/Outlook, Calendar, Jira, Todoist, Notion, Interview OS), through the broker.
3. The `followup` step in `.github/workflows/domains.yml`, and any `permissions` change.
4. Any live read, and turning `LIVE_READS_ENABLED` on.
5. Recording delivery in the status table.

## 7. What Jim approves in this PR

1. The drop-in section in section 2, as the exact contract text.
2. The status-table row in section 3 (lane stays DISABLED — NOT DELIVERED until acceptance).
3. **Cadence decision:** the angle-bracket hours (6 AM, 9 AM, 12 PM, 6 PM CT) for full reconciliation. V7's tick has no such gate today (those slots were the retired Daily Runs'), so this is a new stage-internal gate, not a schedule. Keep these hours, or name others.
4. The version bump to 2.15.16 and the provenance sentence in section 4.
5. That the edit is made as a separate PR to `life-os-automation` once approved, with the approval date filled into the heading. Merging this draft PR changes no contract.
