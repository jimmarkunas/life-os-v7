# V7 roadmap and Codex prompts

Order: Codex builds, Claude reviews, Jim merges. One PR per item. Do not start the next item until the last one is merged.

1. Bills  2. Amazon Orders  3. Calendar in the Daily Report  4. Daily Command Center  5. Accountability  6. Drive Index
Side tracks: A. Dice/Reed parsers (Codex, running)  B. Recruiter mail (TL, prompt below)  C. Phase E (prompt below)

## Shared preamble (paste at the top of every prompt)

```
Repository: jimmarkunas/life-os-v7 (public). Branch from main. Open a PR, do not merge.
Read docs/DECISIONS.md (D54-D58) first. V1 (jimmarkunas/life-os-automation) and V2
(jimmarkunas/life-os-v2) are read-only references: extract the smallest proven mechanic only.
Rules: V7 must work with ChatGPT unavailable. platform/ imports nothing; OS packages import
only platform; sources may import any OS. Credentials: other secrets blanked, secrets only on
the step that needs them, continue-on-error plus a warning step. No sending or deleting
(tests forbid DELETE, /send, sendMail, /forward, permanentDelete). Privacy test checks
committed files only: `git add` before running the suite; no address-shaped strings, use
example.com. Fail closed: error or full result, never partial. Notion writes: add new
blocks, remove old, read back. Keep the diff small, no new frameworks.
Proof: full suite passes (look for a line starting "OK"); report counts only; add a short
D-entry to docs/DECISIONS.md and a docs/SETUP.md section for any new secret or input.
workflow_dispatch is at 25 inputs on hourly.yml: add no input there.
Stop and report if you need a new dependency, a schema change, or a V1/V2 port larger than
~300 lines.
```

## 1. Bills
Port V1 `scripts/notion_bill_snapshot.py` and `notion_bill_paid_snapshot.py` as a small `lifeos/bills/` package: read the Notion Bill Tracker fully (paginate; error on incomplete), return active recurring bills, paid state, due dates, stale-due unpaid recurring bills, amounts, PASS/DEGRADED with row counts. Read-only. Expose a stage in `lifeos/run.py` and a `bills` snapshot the Daily Report can consume. Own region heading "Bills". Tests with synthetic Notion pages.

## 2. Amazon Orders
Port V2 `lifeos/amazon_orders.py` (with V1 `amazon_orders/runtime.py` and `docs/amazon-orders-production-contract.md` as reference) onto V7's Gmail and Outlook readers. Events ORDERED/SHIPPED/DELIVERED reconciled per order id, never moving state backwards, with source message ids, total, item summary, canonical order link; upsert into the Notion Amazon Orders database with read-back. Gmail filing to an `Amazon` label only after the Notion read-back passes. Seen-table pattern as in newsletters. Tests with synthetic emails.

## 3. Calendar in the Daily Report
Extend `lifeos/platform/gcal.py` with a bounded, fully paginated `events(time_min, time_max)` (loop guard, error on incomplete), then a small `lifeos/calendar_bridge/agenda.py` returning today and tomorrow, with all-day events and the timed events in order. Renders into the "Calendar" region of the Daily Report only; fail closed. No writes.

## 4. Daily Command Center
Composition layer using `platform/router.py` (D58): regions, one owner each. V7 owns JIRA Execution, Calendar, Bills, Amazon, Jobs and Mail Alerts; ChatGPT-owned regions stay untouched. Order from V1: Calendar, Daily Jobs, Attention, Bills, Mail Alerts, Amazon, Notes, Hiring Pipeline, Jira/GTV. Add 6 AM baseline plus 9 AM, 12 PM and 6 PM change deltas. Render only; no new data sources. Must degrade per region (one module failing never blanks another).

## 5. Accountability
Read `docs/jim-matt-accountability-dashboard-prd.md`, `accountability-dashboard-sync.md` and `scripts/accountability_dashboard_gate.py` in V1. Rebuild small on the V7 Jira snapshot: JFM and MEJ separately, current sprint, next sprint, triage, blocked items excluded from capacity, target 8 and ceiling 10, GREEN/YELLOW/RED load, lane mix. Write to the Notion accountability page; keep the known-good state when degraded; read back. Skip the V1 acceptance ceremony.

## 6. Drive Index
Google Drive API read through the existing service account (no connector), file metadata normalized, upsert into the Notion Drive Index keyed on exact Drive File ID, with a bounded modified-since window. Read-only on Drive. Ask Jim which folders are in scope (secret `DRIVE_FOLDER_IDS`); if unset, stop with a clear code.

## B. Recruiter mail (hand to the TL)
```
Role: tech lead. Design first, then hand a Codex-ready build prompt back to Jim. No code yet.
Read V1 docs/mail-routing contract (automated_job_source / human_hiring / unrelated),
V2 Mail Router and classifier, and V7 sources/newsletters/outlook.py.
Deliver: (1) V7 classifier for human_hiring vs automated vs unrelated across Outlook
(both mailboxes) and Gmail; (2) the Notion "Recruiters & Human Outreach" write, with natural
key and read-back; (3) the rule for entering the Hiring Pipeline only when a hiring-manager
interview is scheduled; (4) an alert path (platform alerts layer, D53); (5) what is
Never Touched (Dice Private Email, replies, any send/delete); (6) proof plan and PR split.
Max ~2 pages. Flag any decision Jim must make.
```

## C. Phase E
```
Three small PRs. (1) ChatGPT-off drill: a manual workflow `drill.yml` that runs the full
hourly pipeline with ChatGPT-owned regions simulated absent and asserts that every V7-owned
region still renders. Counts only. (2) Outlook sign-in expiry reminder: when a refresh
token has not been used successfully for 60 days or refresh fails with a known expiry code,
raise an alert through the platform alerts layer naming the mailbox label only.
(3) Jira alerts: overdue and blocked tickets on boards from JIRA_BOARDS raise one alert
per run through the same layer, with counts only, deduplicated per day.
```
