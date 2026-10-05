# LIFE OS V7: the one plan (plain words)

Last updated: October 5, 2026 (night). One Claude Code session builds everything below, one pull request at a time. Jim approves each merge ("Merge PR N"). This file is the source; Notion shows the same table (ChatGPT keeps it in step).

## How we work

- **Done means:** merged, run live once, a counts-only check passes, and Jim says it is done.
- **Status words:** To-Do, In Progress, Done, Blocked.
- **Status mapping:** To-Do = not started but actionable; In Progress = building, merged-but-not-yet-confirmed, live-checking, or validation underway; Blocked = cannot advance because of a dependency, prerequisite, credential, decision, or explicit hold; Done = only after the Done rule above is satisfied.
- **RAG mapping:** 🔵 To-Do; 🟡 In Progress; 🔴 Blocked; 🟢 Done. RAG mirrors Status exactly; it is not a separate health score.
- **Weekly hardening:** every week a small, fixed amount of work goes to things that stop silent bugs (below, "Safety net").
- **Safety rules:** private data never goes in this public repository; a failed step says so instead of showing old numbers as fresh; anything that changes a database, a secret or a Notion layout stops for Jim's yes.

## Hard date: Wednesday, October 7, 3 PM CT (meeting with Matt)

MegIBOW is **In Progress**: the weekly table fills itself every hour, Cumulative is seeded from the old sheet, warnings show, review items go to the MegIBOW Review list, and a failed refresh says DEGRADED. Still to prove: the Monday rollover (first freeze Monday Oct 12), a real review item answered end to end, and the sixteen acceptance scenarios on the real dashboard. The 1:30 PM and 2:30 PM CT phone alerts fire only when a Wednesday refresh is failing.

## Order

**Now**
1. MegIBOW: watch the first rollover and Wednesday's refresh; Jim confirms.
2. Network inspector: Merged and run once; read the counts and decide the importer.
3. Scoping documents: Merged on `main`.

**Next, in this order**
4. Safety net: Fit re-checks a named company first after a rule change; a test that formats every database statement the way the real driver does; a saved set of real jobs every Fit change must pass; a phone alert when a job source stops delivering.
5. Mail Alerts box (Physical Mail) and Hiring Pipeline box on the Daily Report.
6. Recruiters table (after the "Needs review" column exists).
7. Delivery tracking (first step: find tracking numbers in mail and count them; no database change).
8. Network importer (after the inspector's first run).
9. Jobs OS clean-up: the Jobright "work mode unknown" flood, Jobright's date for ranking, Veramed and Bluestonex, Plentific and Floww, a second Fit test round, company names on old Notion pages.

**Later**
Delivery steps 2 to 7, Network refresh and matching, mail evidence for Recruiters and Hiring Pipeline, Physical Mail tracking, the company cockpit, the edge gateway (after the Oracle VM exists), Lensa decision, Reed parser, Accountability, Drive Index, recruiter-mail brief, Interview OS promotion. The Daily Command Center stays ChatGPT's.

## Safety net (small, every week)
Backups with a tested restore; one switch that stops all Notion writes; a review of which integrations can write; every step fails alone; clean-up of the Node 20 warning and a short runbook.

## Not yet confirmed (marked, not done)
The scheduled runs since the Fit clean-up; Bills weekly, bi-weekly, quarterly and yearly cases on real rows; the 24 overdue bills; Amazon orders with no date or total; Outlook tagging (V7 reads two accounts and found nothing tagged yet).

## Jim's own to-dos
Say "Merge PR N"; finish the Oracle upgrade; carrier developer credentials (Delivery); check the MegIBOW table after Monday's rollover and say if it is Done; answer review items as they appear; make any true product/source decisions that require Jim.

## Agent-owned actions — do not push these to Jim
Create/repair Notion surfaces when the connector can do it; add the Recruiters `Needs review` column; run validation/reconciliation checks; inspect Network inspector counts and propose the importer; perform evidence review; prepare bounded implementation/repair packages; report only true approval/credential/decision/manual blockers back to Jim.

## On hold (do not do)
Merging the contract branch in `life-os-automation`; the Notion scope clause on the LI Connection page; ChatGPT's "Network Lookups" module text; any other contract change.
