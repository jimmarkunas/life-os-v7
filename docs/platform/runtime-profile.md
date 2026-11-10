# Production runtime profile (LIFE-555)

Where the hourly and domains workflows spend their time. Read-only: nothing here changes production. Measured Nov 10 2026, about 3:10 PM CT, from GitHub's own run records with `python -m lifeos.profile_runs` (job and step names and start/end seconds only; no log text, no data, no secret). Re-run it any time: `python -m lifeos.profile_runs --workflow hourly --last 10 --failed 5 --top 0` (same with `--workflow domains`).

**How to read the numbers.** p50 is the middle run, p95 the near-worst run. A group is the newest runs of one outcome and one kind: hourly runs are started by the Oracle ticker (`tick`) or by hand (`manual`); domains runs follow a tick (`after tick`) or are manual. Cancelled and skipped runs are left out. Wall time runs from the first job's start to the last job's end (time waiting for a runner is not included). Startup share is Set up job, checkout, Bitwarden, tailnet join, installs and caches as a share of all job-seconds.

## Samples

| Workflow | Group | Runs | Wall p50 | Wall p95 | Startup share | Unit-test share |
|---|---|---|---|---|---|---|
| hourly | failure / manual | 1 | 120 s | 120 s | 6% | 86% |
| hourly | failure / tick | 5 | 159 s | 1147 s | 14% | 14% |
| hourly | success / manual | 5 | 241 s | 244 s | 16% | 43% |
| hourly | success / tick | 10 | 918 s | 1186 s | 3% | 13% |
| domains | failure / after tick | 5 | 851 s | 1146 s | 36% | 0% |
| domains | success / after tick | 10 | 1103 s | 1147 s | 5% | 0% |
| domains | success / manual | 10 | 35 s | 80 s | 17% | 0% |

Only 5 successful and 1 failed hand-started hourly runs exist in the window, and there is no failed manual domains run, so those groups are small. The two groups that matter for speed are **hourly success / tick (10 runs)** and **domains success / after tick (10 runs)**.

## Hourly (the Oracle ticker's tick)

**Critical path:** prep (p50 152 s) > finish (p50 761 s) > report (p50 7 s) = about 920 s, against a measured wall p50 of 918 s and p95 of 1,186 s. The resolver jobs (jobright, linkedin, lensa) run beside finish and do not lengthen the path. A failed tick ends early or late depending on where it fails (wall p50 159 s, p95 1,147 s over 5 failures: most stop at the unit tests or an early lane, a few run the whole chain and fail at the end).

**Where the time goes (success / tick, p50 / p95):**

| Job / step | p50 | p95 | Share of the 918 s wall |
|---|---|---|---|
| finish (the whole job) | 761 s | 1,012 s | 83% |
| - Jobs tick, the whole chain in one process | 392 s | 616 s | 43% |
| - Network Leads blocks on Ledger pages (step 7c) | 267 s | 319 s | 29% |
| - Cloud Fit backlog batch (step 6a2) | 57 s | 74 s | 6% |
| - all other finish steps together | about 45 s | | 5% |
| prep (the whole job) | 152 s | 165 s | 17% |
| - Unit tests | 125 s | 137 s | 14% |
| - Stage: extract cards from mail | 8 s | 11 s | 1% |
| report (the whole job) | 7 s | 10 s | 1% |

**Startup (Bitwarden, tailnet join, installs) is only about 3% of the job-seconds** in a tick run, so it is not a target. The unit-test phase is 125 s (13% of all job-seconds, 14% of the wall) and sits on every hourly run's critical path.

**Slow every time vs only sometimes:** the Jobs tick (392 s p50, 616 s p95) and Network Leads (267 s, 319 s) are slow in every run, and the Jobs tick varies most (p95 is 1.6 times p50). Cloud Fit backlog varies with the allowance and the backlog (57 s p50, 74 s p95). Everything else is steady within 1 to 5 s.

## Domains (after a tick)

**Critical path:** jira > agenda > bills > amazon > mail > hiring > dcc > report = about 1,065 s of the measured wall p50 of 1,103 s (p95 1,147 s). The jobs are chained on purpose: the card jobs write the same Daily Report page and each verifies the others' regions (see the `needs:` comments in domains.yml), so they run one after another. Longest jobs by p50: recruiters 764 s (off the critical path), amazon 335 s, mail 204 s, imessage 203 s, agenda 180 s, dcc 116 s, hiring 84 s, jira 77 s, bills 69 s.

**Slowest steps (p50 / p95):** Interview OS 414 s / 492 s; Recruiters read 263 s / 297 s; Amazon retention 240 s / 292 s; Mail Alerts card 175 s / 223 s; Agenda card 158 s / 168 s; Oracle Cloud card 77 s / 120 s; Recruiter Outreach 63 s / 137 s. Startup is 5% of job-seconds. A manual domains run is short (wall p50 35 s, p95 80 s) because it runs only the lanes you pick.

## Top 3 optimisation candidates (none implemented)

| # | Step | Measured saving if halved or removed | Risk class | What must be proven first |
|---|---|---|---|---|
| 1 | **Amazon retention** (mail older than 30 days to the Trash), 240 s p50 / 292 s p95, inside the Amazon job that sits on the serial page-writer chain | Moving it off the chain: about 240 s of the 1,103 s domains wall (22%); halving it: about 120 s | Changes ordering | That the Amazon card does not read anything retention changes (the card renders the saved orders), and that retention can run as its own lane without breaking the "waits for the other page writers" rule, which applies to the card. |
| 2 | **Network Leads blocks on Ledger pages** (step 7c), 267 s p50 / 319 s p95, run after the Jobs tick inside finish | Halving: about 133 s of the 918 s hourly wall (15%); running it beside the Jobs tick: up to 267 s (29%) | Changes ordering | That it needs nothing the Jobs tick produces in the same run, and that two store connections at once stay within the store's connection limits (LIFE-76 jitter, LIFE-533 dropped runners). |
| 3 | **Unit tests in the prep job**, 125 s p50 / 137 s p95, on every hourly run's critical path | Halving: about 62 s (7% of the wall); removing: 125 s (14%) | **Touches a protected gate** | Not for this ticket. Any change to which tests gate an hourly run needs Jim's decision; LIFE-548's affected-test runs already cut pull-request time and are the safer lever. |

The Jobs tick (392 s p50, 43% of an hourly wall) is the biggest single step, but this data cannot say what is slow inside it (next section); measuring it is the first step before any candidate there.

## What the data cannot show

* **Time inside the Jobs tick.** It is one process. `lifeos/jobs/tick.py` prints each stage's outcome (`ok`, `failed`, `timeout`, `skipped`) in its final `tick:` line and enforces a wall-clock budget per stage, but it does **not** print the seconds each stage took, so per-stage time is unknown. Adding the seconds to that line (counts only) is a small production change and is not done here.
* **Waiting for a runner.** Wall time starts at the first job's start; queue time before it, and the time a job waits for its `needs`, are not in a step record.
* **Why a failed run failed.** The profile uses times only, not log text, so it cannot say more than which job and step records exist.
* **Allowance-dependent steps.** Cloud Fit backlog time depends on the daily allowance and the backlog size; one number per run cannot show that.
* **Hand-started runs** are few (5 successful and 1 failed hourly in the window), so their p95 is thin.

## Full per-step tables (every job and step, p50 and p95)

Steps are slowest p95 first within each job. n is the number of runs that executed the step.

### Hourly, success / tick

```text
## hourly / success / tick: 10 runs; wall p50 918 s, p95 1186 s; startup share 3%, unit-test share 13%
critical path (p50 job times): prep > finish > report = 920 s
- job finish: n=10 p50 761 s, p95 1012 s
    - Jobs tick - the whole chain in one process, fast path first, one store: n=10 p50 392 s, p95 616 s
    - Step 7c - Network Leads blocks on Ledger pages (NET-2.3; 41 pages a ru: n=10 p50 267 s, p95 319 s
    - Step 6a2 - Cloud Fit backlog batch (LIFE-41 D219; manual, or after eac: n=10 p50 57 s, p95 74 s
    - Step 8 - Last Seen sync (daily): n=10 p50 9 s, p95 14 s
    - Alerts - detect, dedupe and push to the phone (counts only; a failure : n=10 p50 5 s, p95 12 s
    - Step 8b - retention purge (about weekly and catching up (the stage dec: n=10 p50 4 s, p95 10 s
    - Install semantic layer: n=10 p50 8 s, p95 9 s
    - Run ./.github/actions/python: n=10 p50 3 s, p95 5 s
    - Cloud-Fit shadow only (D214, G6) - join the tailnet as the existing ep: n=10 p50 3 s, p95 3 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Semantic layer (optional, shadow) - local embedding model, cached: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 0 s, p95 1 s
    - Post Cloud-Fit shadow only (D214, G6) - join the tailnet as the existi: n=10 p50 0 s, p95 1 s
    - Post Semantic layer (optional, shadow) - local embedding model, cached: n=10 p50 0 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Lane failures fail the run (other stages still ran): n=10 p50 0 s, p95 0 s
    - Complete job: n=10 p50 0 s, p95 0 s
- job prep: n=10 p50 152 s, p95 165 s
    - Unit tests: n=10 p50 125 s, p95 137 s
    - Stage - extract cards from mail: n=10 p50 8 s, p95 11 s
    - Run ./.github/actions/python: n=10 p50 4 s, p95 6 s
    - LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (the st: n=10 p50 3 s, p95 4 s
    - Stage 1 - sweep: n=10 p50 1 s, p95 3 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Gate - skip a scheduled or tick run when another tick started in the l: n=10 p50 2 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 1 s, p95 1 s
    - Test-only dependency (PyYAML, for the workflow contract tests; skipped: n=10 p50 1 s, p95 1 s
    - Post LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (t: n=10 p50 1 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 0 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 0 s
    - Complete job: n=10 p50 0 s, p95 0 s
- job report: n=10 p50 7 s, p95 10 s
    - Run actions/checkout@v5: n=10 p50 2 s, p95 3 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 1 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Resolver lanes and the finish job report here (the run fails if any la: n=10 p50 0 s, p95 0 s
    - Complete job: n=10 p50 0 s, p95 0 s
```

### Domains, success / after tick

```text
## domains / success / after tick: 10 runs; wall p50 1103 s, p95 1147 s; startup share 5%, unit-test share 0%
critical path (p50 job times): jira > agenda > bills > amazon > mail > hiring > dcc > report = 1065 s
- job recruiters: n=10 p50 764 s, p95 909 s
    - Interview OS (LIFE-88; V7 is the only Interview OS writer - scheduling: n=10 p50 414 s, p95 492 s
    - Recruiters (read-only mail to the Recruiters data source; counts only): n=10 p50 263 s, p95 297 s
    - Recruiter Outreach (LIFE-20; the Daily Report's one Recruiter Outreach: n=10 p50 63 s, p95 137 s
    - Hiring outcome mail (D190; read-only Gmail and Outlook for the compani: n=10 p50 3 s, p95 42 s
    - Job-page outreach (LIFE-10; the outreach table on each Job Ledger job : n=10 p50 2 s, p95 10 s
    - Run ./.github/actions/python: n=10 p50 3 s, p95 7 s
    - LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (the st: n=10 p50 3 s, p95 4 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 0 s, p95 1 s
    - Post LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (t: n=10 p50 1 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Complete job: n=10 p50 0 s, p95 1 s
    - Recruiters warning (does not fail the Jobs run): n=10 p50 0 s, p95 0 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 0 s
- job amazon: n=10 p50 335 s, p95 382 s
    - Amazon retention (D139; mail older than 30 days to the Trash once its : n=10 p50 240 s, p95 292 s
    - Amazon card (reads the canonical orders and, read-only, other stores' : n=10 p50 75 s, p95 78 s
    - Amazon Orders (Gmail to Notion; archive only after read-back): n=10 p50 4 s, p95 45 s
    - Run ./.github/actions/python: n=10 p50 3 s, p95 5 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Set up job: n=10 p50 1 s, p95 1 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 0 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Complete job: n=10 p50 0 s, p95 1 s
- job imessage: n=10 p50 203 s, p95 252 s
    - Oracle Cloud card (daily from 6 AM CT; numbers only in the log; writes: n=10 p50 77 s, p95 120 s
    - AI Capacity card (readings only, no model run; numbers only in the log: n=10 p50 41 s, p95 85 s
    - Communications card, Messages row (7-day unread count and latest sende: n=10 p50 39 s, p95 70 s
    - Run ./.github/actions/python: n=10 p50 4 s, p95 11 s
    - iMessage urgent alerts (metadata only from Oracle; private alert state: n=10 p50 6 s, p95 7 s
    - Join the tailnet as an ephemeral tag:v7-runner node (the tailnet polic: n=10 p50 3 s, p95 4 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Load the iMessage alert credentials from Bitwarden (exactly these 13; : n=10 p50 0 s, p95 2 s
    - Set up job: n=10 p50 1 s, p95 1 s
    - Post Join the tailnet as an ephemeral tag:v7-runner node (the tailnet : n=10 p50 0 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Complete job: n=10 p50 0 s, p95 0 s
    - AI Capacity card warning (does not fail the run): n=1 p50 0 s, p95 0 s
    - Oracle Cloud card warning (does not fail the run): n=1 p50 0 s, p95 0 s
- job mail: n=10 p50 204 s, p95 240 s
    - Mail Alerts card (reads the stored state; writes only the Mail Alerts : n=10 p50 175 s, p95 223 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 8 s
    - Run ./.github/actions/python: n=10 p50 3 s, p95 8 s
    - Physical Mail (Gmail read-only to the private state; saved with read-b: n=10 p50 3 s, p95 6 s
    - LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (the st: n=10 p50 3 s, p95 4 s
    - Physical Mail filing (LIFE-149; the vendor's no-reply notices marked r: n=10 p50 0 s, p95 3 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 1 s, p95 1 s
    - Post LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (t: n=10 p50 1 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Complete job: n=10 p50 0 s, p95 0 s
- job agenda: n=10 p50 180 s, p95 189 s
    - Agenda card (saved snapshot only; writes only when live): n=10 p50 158 s, p95 168 s
    - Run ./.github/actions/python: n=10 p50 4 s, p95 6 s
    - Agenda snapshot (read-only Google Calendar; private save only when liv: n=10 p50 3 s, p95 5 s
    - Run actions/checkout@v5: n=10 p50 2 s, p95 4 s
    - LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (the st: n=10 p50 3 s, p95 4 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 2 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 0 s, p95 1 s
    - Post LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (t: n=10 p50 1 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Complete job: n=10 p50 0 s, p95 0 s
- job dcc: n=10 p50 116 s, p95 119 s
    - Daily Command Center (rules over the cards' first lines; writes only i: n=10 p50 61 s, p95 67 s
    - Attention card (status and suggested decisions above the interactive v: n=10 p50 43 s, p95 44 s
    - Run ./.github/actions/python: n=10 p50 3 s, p95 7 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 1 s, p95 2 s
    - Set up job: n=10 p50 1 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 0 s
    - Complete job: n=10 p50 0 s, p95 0 s
    - Daily Command Center warning (does not fail the Jobs run): n=1 p50 0 s, p95 0 s
- job megibow: n=10 p50 32 s, p95 108 s
    - MegIBOW (Gmail, Outlook and calendars to the Notion table; counts only: n=10 p50 18 s, p95 91 s
    - Run ./.github/actions/python: n=10 p50 3 s, p95 7 s
    - LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (the st: n=10 p50 3 s, p95 4 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 0 s, p95 1 s
    - Post LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (t: n=10 p50 1 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Complete job: n=10 p50 0 s, p95 0 s
- job jira: n=10 p50 77 s, p95 107 s
    - Jim & Matt dashboard from Jira (JFM-221; Wednesday 7 AM CT check-in, q: n=10 p50 13 s, p95 62 s
    - Jira card (Daily Report; counts only; the page is written only when li: n=10 p50 18 s, p95 30 s
    - Jira snapshot (counts only; saved to the private database only when li: n=10 p50 13 s, p95 20 s
    - Run ./.github/actions/python: n=10 p50 3 s, p95 6 s
    - Jira rollover watch (pages the phone when the scheduled rollover has n: n=10 p50 2 s, p95 6 s
    - LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (the st: n=10 p50 3 s, p95 4 s
    - Jira sprint rollover (scheduled; waits for the sprint to end and Monda: n=10 p50 2 s, p95 4 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Set up job: n=10 p50 1 s, p95 1 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 1 s, p95 1 s
    - Post LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (t: n=10 p50 0 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Complete job: n=10 p50 0 s, p95 0 s
- job hiring: n=10 p50 84 s, p95 98 s
    - Hiring Pipeline (Ledger, Notion pages and Calendar to the private stat: n=10 p50 69 s, p95 81 s
    - Run ./.github/actions/python: n=10 p50 3 s, p95 5 s
    - LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (the st: n=10 p50 3 s, p95 4 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 3 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Post LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (t: n=10 p50 1 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 0 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Complete job: n=10 p50 0 s, p95 0 s
- job bills: n=10 p50 69 s, p95 81 s
    - Bills card (saved snapshot only; writes only when live; a stale snapsh: n=10 p50 44 s, p95 57 s
    - Bills snapshot (read-only; private snapshot saved only when live): n=10 p50 3 s, p95 7 s
    - Run ./.github/actions/python: n=10 p50 4 s, p95 6 s
    - Bills Paid processor (before the snapshot; writes Notion only when liv: n=10 p50 4 s, p95 5 s
    - LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (the st: n=10 p50 3 s, p95 4 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 1 s, p95 1 s
    - Post LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (t: n=10 p50 1 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 0 s
    - Complete job: n=10 p50 0 s, p95 0 s
- job outlook: n=10 p50 24 s, p95 40 s
    - Outlook newsletters (job alerts to Jobs OS; counts only; writes only w: n=10 p50 8 s, p95 13 s
    - Calendar sync (Outlook invites to Google; counts only; writes only whe: n=10 p50 6 s, p95 8 s
    - Run ./.github/actions/python: n=10 p50 3 s, p95 6 s
    - Run actions/checkout@v5: n=10 p50 2 s, p95 4 s
    - LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (the st: n=10 p50 3 s, p95 3 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Post LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (t: n=10 p50 1 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 0 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Complete job: n=10 p50 0 s, p95 0 s
- job attention: n=10 p50 25 s, p95 33 s
    - Attention (Gmail label and Outlook category to Notion; counts only; wr: n=10 p50 10 s, p95 14 s
    - Run ./.github/actions/python: n=10 p50 3 s, p95 5 s
    - LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (the st: n=10 p50 3 s, p95 4 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Post LIFE-85 - join the tailnet as the ephemeral tag:v7-runner node (t: n=10 p50 1 s, p95 2 s
    - Load the migrated credentials from Bitwarden (EDGE-1.3, exactly these : n=10 p50 0 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Complete job: n=10 p50 0 s, p95 0 s
- job granola: n=10 p50 21 s, p95 29 s
    - Granola auto (every meeting not yet carried; counts and fixed codes on: n=10 p50 8 s, p95 11 s
    - Run ./.github/actions/python: n=10 p50 3 s, p95 5 s
    - Join the tailnet as an ephemeral tag:v7-runner node (the tailnet polic: n=10 p50 3 s, p95 4 s
    - Set up job: n=10 p50 1 s, p95 2 s
    - Run actions/checkout@v5: n=10 p50 1 s, p95 2 s
    - Bitwarden helper from a saved, fingerprint-checked copy (LIFE-406; dow: n=10 p50 1 s, p95 2 s
    - Load the Granola credentials from Bitwarden (exactly these 12; masked : n=10 p50 1 s, p95 1 s
    - Post Join the tailnet as an ephemeral tag:v7-runner node (the tailnet : n=10 p50 1 s, p95 1 s
    - Post Run ./.github/actions/python: n=10 p50 0 s, p95 1 s
    - Post Run actions/checkout@v5: n=10 p50 0 s, p95 1 s
    - Complete job: n=10 p50 0 s, p95 0 s
```
