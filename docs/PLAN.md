# Plan: Job Newsletter pipeline (Phase 1), built to grow

## Rules that prevent the V1/V2 spin
1. **One step at a time.** A step ships only when its acceptance check passes on real data. No step starts before the previous one is green.
2. **One table is the queue.** Every stage reads rows in status X and writes status Y. No orchestrator, no framework, no shared "engine".
3. **Stages are independent and idempotent.** Re-running any stage is safe. Any stage can be disabled without breaking the others.
4. **New source = one parser file. New capability = one new stage.** Phase 2 scoring is just another stage that reads `RESOLVED` rows.
5. **Small.** Stdlib + PyMySQL. No Notion-as-database: Hostinger is the working store, Notion is the published view.
6. **Everything runs as GitHub Actions hourly cron.** No second scheduler.

## Pipeline
```
Gmail Inbox ──1 sweep──> label "J Newsletters" ──2 extract──> jobs(status=NEW)
   ──3 resolve──> jobs(status=RESOLVED, final_apply_url, apply_kind)
   ──4 store (same table, dedupe on final URL)──> ──5 publish──> Notion Job Ledger (status=PUBLISHED)
Phase 2 (later): RESOLVED ──6 triage──> ──7 score──> Notion (Fit %)
```
| # | Stage | Input → Output | Phase |
|---|---|---|---|
| 1 | sweep | Inbox allowlisted senders → `J Newsletters` label (leaves Inbox) | 1 |
| 2 | extract | labeled mail without `Processed` → one row per job card | 1 |
| 3 | resolve | source link → final apply URL (employer / aggregator / LinkedIn Easy Apply) | 1 |
| 4 | store | upsert into Hostinger `v7_jobs`, dedupe key = canonical final URL | 1 |
| 5 | publish | `RESOLVED` rows → Notion Job Ledger (core fields only), then mark `Processed` | 1 |
| 6-7 | triage / score | cursory match, then % fit (Claude project + ChatGPT/GitHub method) | 2 |

## Final-apply-link rules
- LinkedIn posting with **Easy Apply** → the LinkedIn URL *is* final (`apply_kind=linkedin_easy_apply`).
- Employer/ATS URL (Greenhouse, Ashby, Lever, Workday, company site) → final (`employer`).
- Lensa/Jobright/other redirect → follow until the destination stops being an aggregator; if it ends on an aggregator page, keep it (`aggregator`).
- Jobright: read `originalUrl`/`applyLink` from the page's `__NEXT_DATA__` first; logged-in browser only as fallback.
- Unresolvable → `status=UNRESOLVED` with a reason. Never silently dropped, never published as final.

## Build order (each step = one commit, one acceptance check)
| Step | Ship | Acceptance (on real data) |
|---|---|---|
| **1 sweep** | this commit | dry run prints counts; `--live` moves mail and Inbox count drops; re-run moves 0 |
| 2 store schema | `v7_jobs`, `v7_runs` tables + connectivity check via SSH tunnel | CI creates tables; re-run is a no-op |
| 3 extract: Lensa | parse cards from labeled Lensa mail into `NEW` rows | row count matches the cards in 3 hand-checked emails |
| 4 extract: LinkedIn, Jobright | same for the other senders | same hand-check |
| 5 resolve | final URL + `apply_kind` | 20 sampled rows hand-verified |
| 6 publish | Notion rows, `Processed` label, no duplicate on re-run | rows appear in the Ledger; 2nd run adds 0 |
| 7 Jobright browser fallback | only if `__NEXT_DATA__` path fails | unresolved Jobright rate < agreed threshold |

Ship target: steps 1-6 within the 2-hour window. Step 7 and all of Phase 2 follow.

## Scalability hooks (designed in, not built)
- `source` + `parser` registry: add newsletters/ATS boards without touching other stages.
- `v7_jobs.first_seen/last_seen` and a per-source `fingerprint` column allow open-jobs-style diffing later.
- Stage status machine lets Scale-Up / US Remote become additional *producers* of `NEW` rows, not a second engine.

## Decisions recorded
- Repo: life-os-v7 (fresh). Destination: existing Notion Job Ledger, **core fields only** (Job, Company, Apply URL, Source Provider, First Surfaced, Stable Job Key; Admission Status = "Passed / Review"; Fit blank).
- Senders: Lensa, Jobright, LinkedIn job alerts. **Dice excluded**: `*.user.dice.com` is Dice Private Email relaying individual staffing recruiters, not newsletters (awaiting confirmation).
- First run backfills the entire Inbox. Hostinger: reuse existing DB, new `v7_` tables only.
- Scheduled runs are dry-run until repo variable `V7_LIVE=true`.
