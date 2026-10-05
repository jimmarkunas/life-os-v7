# LIFE OS V7: the one plan (plain words)

Last updated: October 5, 2026 (night). This file is the source; Notion shows the same operating plan.

## How we work

- **Done means:** merged where code is involved, run live once, a counts-only or equivalent acceptance check passes, and Jim says it is done.
- **Status values:** 🔵 To-Do, 🟡 In Progress, 🔴 Blocked, 🟢 Done.
- **Status mapping:** To-Do = not started but actionable; In Progress = actively building, validating, live-checking, or awaiting Jim's final Done confirmation after required proof is available; Blocked = cannot advance because of a dependency, prerequisite, credential, decision, time-bound proof point, or explicit hold; Done = only after the Done rule above is satisfied.
- **One owner per row:** every roadmap row is owned by exactly one of `ChatGPT`, `Jim`, or `Claude Code`. If ownership changes between steps, those are separate rows in delivery order.
- **Order rule:** the roadmap is topological: prerequisite work appears before the work it unlocks. Blocked dependent work sits immediately after its unmet prerequisite where practical.
- **Weekly hardening:** every week a small, fixed amount of work goes to things that stop silent bugs (below, `Safety net`).
- **Safety rules:** private data never goes in this public repository; a failed step says so instead of showing old numbers as fresh; consequential database/secret/schema changes retain their existing approval gates.

## Hard date: Wednesday, October 7, 3 PM CT (meeting with Matt)

MegIBOW is **🔴 Blocked** until the first required Monday rollover proof point. The weekly table fills itself every hour, Cumulative is seeded from the old sheet, warnings show, review items go to the MegIBOW Review list, and a failed refresh says DEGRADED. Still to prove after the rollover becomes available: the Monday rollover (first freeze Monday Oct 12), a real review item answered end to end, and the sixteen acceptance scenarios on the real dashboard. The 1:30 PM and 2:30 PM CT phone alerts fire only when a Wednesday refresh is failing.

## Execution order — prerequisite first

### Active proof / decisions

1. **MegIBOW — Monday rollover + final acceptance** — Owner: `ChatGPT` — 🔴 Blocked until Monday rollover evidence exists. After it exists, run the real review-item path and dashboard acceptance checks; Jim gives final Done confirmation.
2. **Network inspector — first-run counts + importer design** — Owner: `ChatGPT` — 🟡 In Progress. Real run: 3,453 source rows; 3,346 deterministic profile identities; 3,244 initial position-eligible rows; 107 no-URL identity collisions held unresolved. The importer direction is accepted: Door B, person identity and position eligibility are separate, ambiguous rows are counted holds, and NET-1 uses only the three already-approved tables. Await Jim's final Done confirmation after the corrected spec/read-back is complete.
3. **Network importer — bounded Notion-attachment importer** — Owner: `Claude Code` — 🔵 To-Do. Reuse the existing private Notion attachment; import all 3,346 deterministic people, create initial positions only for the 3,244 rows with usable employer evidence, hold the 107 ambiguous no-URL rows without guessing, drop email, allow nullable title, chunk/resume safely, and make replay a no-op. `v7_network_people`, `v7_network_positions`, and `v7_network_batches` were already approved by Jim on October 5; no new Jim approval blocks NET-1.
4. **Scoping documents — reconcile current scopes into planning input** — Owner: `ChatGPT` — 🟡 In Progress.
5. **Scheduled runs since Fit clean-up — production verification** — Owner: `ChatGPT` — 🟡 In Progress.
6. **Bills recurrence — real weekly/bi-weekly/quarterly/yearly cases** — Owner: `ChatGPT` — 🟡 In Progress.
7. **Bills overdue count — reconcile 24 overdue bills** — Owner: `ChatGPT` — 🟡 In Progress.
8. **Amazon missing-field behavior — real orders with no date/total** — Owner: `ChatGPT` — 🟡 In Progress.
9. **Outlook tagging — real tagged example exists** — Owner: `Jim` — 🟢 Done. Existing Bytalos/TGP messages in `james.markunas@outlook.com` already carry the `LIFE OS Attention` category; no manual tagging action remains.
10. **Outlook tagging — verify V7 detects the tag in the intended mailbox** — Owner: `ChatGPT` — 🟡 In Progress. Use the existing tagged messages as the fixture and prove the V7 Outlook path detects the category end to end; do not ask Jim to retag mail.

### Next delivery chain

11. **Safety net — weekly hardening slice** — Owner: `Claude Code` — 🔵 To-Do.
12. **Mail Alerts — create/restore Notion presentation surface** — Owner: `ChatGPT` — 🔵 To-Do.
13. **Mail Alerts — V7 runtime wiring + authoritative read-back** — Owner: `Claude Code` — 🔴 Blocked on row 12.
14. **Hiring Pipeline — create/restore Notion presentation surface** — Owner: `ChatGPT` — 🔵 To-Do.
15. **Hiring Pipeline — V7 runtime wiring + authoritative read-back** — Owner: `Claude Code` — 🔴 Blocked on row 14.
16. **Recruiters — add `Needs review` property and configure the Notion view** — Owner: `ChatGPT` — 🔵 To-Do.
17. **Recruiters — V7 runtime wiring** — Owner: `Claude Code` — 🔴 Blocked on row 16.
18. **Delivery tracking — step 1 tracking-number census from mail** — Owner: `Claude Code` — 🔵 To-Do.
19. **Jobs OS clean-up — named ranking/data-quality defects** — Owner: `Claude Code` — 🔵 To-Do.

### Later dependency chains

20. **Delivery — carrier credentials / developer access if the chosen carriers require them** — Owner: `Jim` — 🔴 Blocked until row 18 determines the real carrier/API requirement.
21. **Delivery — steps 2–7 carrier-aware lifecycle** — Owner: `Claude Code` — 🔴 Blocked on rows 18 and 20 where credentials are required.
22. **Network refresh and matching** — Owner: `Claude Code` — 🔴 Blocked on row 3.
23. **Recruiters/Hiring Pipeline — mail-evidence routing** — Owner: `Claude Code` — 🔴 Blocked on rows 13, 15, and 17.
24. **Physical Mail tracking — forwarding/tracking/delivered lifecycle** — Owner: `Claude Code` — 🔴 Blocked on the delivery/mail prerequisites.
25. **Company cockpit — Notion operating surface / product presentation** — Owner: `ChatGPT` — 🟡 In Progress.
26. **Company cockpit — V7 live synthesis from accepted sources** — Owner: `Claude Code` — 🔴 Blocked until row 25 and the required source path are accepted.
27. **Oracle VM prerequisite for Edge Gateway** — Owner: `Jim` — 🔵 To-Do.
28. **Edge Gateway — implementation on the approved VM** — Owner: `Claude Code` — 🔴 Blocked on row 27.
29. **Lensa product/source decision** — Owner: `Jim` — 🔵 To-Do.
30. **Reed parser** — Owner: `Claude Code` — 🔵 To-Do.
31. **Accountability — product/surface definition** — Owner: `ChatGPT` — 🔵 To-Do.
32. **Accountability — runtime implementation** — Owner: `Claude Code` — 🔴 Blocked on row 31.
33. **Drive Index** — Owner: `Claude Code` — 🔵 To-Do.
34. **Recruiter-mail brief** — Owner: `Claude Code` — 🔴 Blocked on row 23.
35. **Interview OS promotion** — Owner: `Claude Code` — 🔴 Blocked until its prerequisite live lifecycle proof is complete.

## Safety net (small, every week)

Backups with a tested restore; one switch that stops all Notion writes; a review of which integrations can write; every step fails alone; clean-up of the Node 20 warning and a short runbook.

## Jim's own to-dos

Only the rows explicitly owned by Jim above plus final Done confirmation after proof. Agent-capable Notion/configuration/review work must not be pushed to Jim.

## Agent-owned actions — do not push these to Jim

ChatGPT owns Notion presentation/schema/view changes that the connector can perform, validation/reconciliation, inspection, product/cockpit definition, and evidence review. Claude Code owns repository/runtime implementation and repair packages. When ownership changes, use separate roadmap rows.

## On hold (do not do)

36. **Contract branch in `life-os-automation` — reactivate or keep frozen** — Owner: `Jim` — 🔴 Blocked by explicit hold.
37. **LI Connection page scope-clause change — reactivate or keep frozen** — Owner: `Jim` — 🔴 Blocked by explicit hold.
38. **ChatGPT `Network Lookups` module text — reactivate or keep frozen** — Owner: `Jim` — 🔴 Blocked by explicit hold.
39. **Any other production-contract change outside an approved package** — Owner: `Jim` — 🔴 Blocked unless Jim explicitly authorizes it.
