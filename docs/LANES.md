# Lanes: US Remote, UK Scale-Up, UK Skilled Worker

One Jobs OS, three opportunity policies. Identity, professional Fit (floor **72**, `lifeos/jobs/fit`), dedupe, the canonical Job
and its lifecycle are shared. A lane adds only opportunity policy, as data (`lifeos/jobs/lanes.py`: `LanePolicy`,
`qualify()`), never as a separate code path. Unresolved evidence is REVIEW, definitive negative evidence is EXCLUDE.

| Policy | US Remote | UK Scale-Up | UK Skilled Worker |
|---|---|---|---|
| Fit floor | 72 | 72 | 72 |
| Market | US | UK | UK |
| Work mode | remote only (unknown = Review) | any | any |
| Explicit pay floor | $80,000 (missing pay allowed) | none | £65,000 (missing pay allowed) |
| New-admission age | 14 days (unknown date = Review) | 30 days (unknown date does not suppress) | 14 days (unknown date = Review) |
| Route evidence | none | Scale-up, positive required | Skilled Worker, positive required |
| Geography evidence | none | positive required | none (London policy at acquisition) |
| Status | active | built, not scheduled until promoted | disabled (Phase 2) |

Decisions (Jim, 2026-10-01): US Remote keeps 14 days (the Notion canon says 7). Scale-Up gets a 30-day age gate (the earlier canon had
none; age is still not closure evidence, a closed vacancy is excluded in every lane). A job eligible for both UK routes is one
job and Scale-Up is the visible lane. Target/Curated never changes the Fit floor.

`parse_pay` reads the posted pay field only (never the description) and compares only in the lane's own currency;
`detect_work_mode` decides from location and title, with the description only confirming explicit statements.
Tests: `tests/jobs/test_lanes.py` encodes the matrix.

## In the pipeline
`fit` judges every scored job by its lane policy (a Newsletter job is a US Remote job) and stores `admission`, a reason, the detected
work mode and the lane in `v7_job_fit`. Shadow mode (default) records only; publishing and the Ledger's Admission Status are unchanged.
With `V7_FIT_GATE=true`: EXCLUDE becomes `EXCLUDED_FIT` and is never published; ADMIT publishes as **Admitted**; REVIEW publishes as
**Passed / Review** with a **Review Reason**. Published rows also get Work Mode, Compensation (posted pay), Fit Authority, Eligible Lanes
and Visible Lane = the lane name.

## Retention (built)
Unapplied jobs are retired 30 days after the employer Posting Date (else First Surfaced; never Notion's Created At) when `progression.resolve` says
NOT_PROTECTED: the page is trashed (recoverable for 30 days in Notion), marked PURGED in Hostinger, and a **90-day tombstone** (dedupe key and fuzzy key only)
stops the same vacancy being recreated. A vacancy with a later posting date than the retirement may re-enter. The legacy Lifecycle field is never read
for a decision or written. **Applied jobs are never destructively retired by Jobs**: `progression.resolve` returns UNKNOWN for them (and PROTECTED when a
Saturn Decision exists) until the Interview progression handoff (INT-7.1A) supplies evidence; a job that reaches an interview stage is therefore always kept.
Only our own pages are touched (matched by stored page id). Hostinger: descriptions 90 days, never-published job rows 365 days, tombstones 90 days.

## Scale-Up on the shared web substrate
`lifeos/sources/web/scale_up.json` holds the 48 curated sponsors (13 listable today: 5 Ashby, 3 Greenhouse, 2 Workable, Lever, Pinpoint, Workday; 23 need a
site parser, 12 have no discoverable ATS and stay DEGRADED). Membership of this universe is the route evidence (`Scale-up:POSITIVE` on the job);
geography comes from the location (London positive, named non-target places negative, else Review). Run with `python -m lifeos.run web-scale-up`
(workflow input `web_scale_up`): dispatch only, never scheduled until promoted.
