# Lanes: US Remote, UK Scale-Up, UK Skilled Worker

One Jobs OS, three opportunity policies. Identity, professional Fit (floor **68**, `lifeos/jobs/fit`), dedupe, the canonical Job
and its lifecycle are shared. A lane adds only opportunity policy, as data (`lifeos/jobs/lanes.py`: `LanePolicy`,
`qualify()`), never as a separate code path. Unresolved evidence is REVIEW, definitive negative evidence is EXCLUDE.

| Policy | US Remote | UK Scale-Up | UK Skilled Worker |
|---|---|---|---|
| Fit floor | 68 | 68 | 68 |
| Market | US | UK | UK |
| Work mode | remote only (unknown = Review) | any | any |
| Explicit pay floor (hard exclusion, reason `explicit pay below $75,000` / `£40,000` / `£65,000`) | $75,000 (missing pay allowed) | £40,000 (missing pay allowed) | £65,000 (missing pay allowed) |
| New-admission age | 14 days (unknown date = Review) | 30 days (unknown date does not suppress) | 14 days (unknown date = Review) |
| Route evidence | none | Scale-up, positive required | Skilled Worker, positive required |
| Market | US | UK | UK (unknown market = Review) |
| Geography evidence | none | positive required | London positive; named non-target place negative; UK-remote / unplaced = Review |
| Status | active | active, scheduled (Jim 2026-10-01, D32) | active (Phase 2, Jim 2026-10-01); depends on the sponsor register being loaded |

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
`lifeos/sources/web/scale_up.json` holds the 48 curated sponsors (36 listable: 19 public ATS boards, 17 first-party careers pages read by `html_readers`, D33; 12 have no discoverable ATS and stay DEGRADED). Membership of this universe is the route evidence (`Scale-up:POSITIVE` on the job);
geography comes from the location (London positive, named non-target places negative, else Review). Run with `python -m lifeos.run web-scale-up`
(workflow input `web_scale_up`; also part of every scheduled run, 12 due boards per run).

## UK Skilled Worker (Phase 2)
Route evidence is the Home Office register of licensed sponsors, Skilled Worker route, A-rated (`lifeos/sources/sponsor_register.py`, stage `sponsors`,
weekly or forced; read from the gov.uk content API; replaces `v7_sponsors` atomically; a bad download keeps the old register). Lookup
(`lifeos/jobs/sponsors.py`) is distinctive-token equality of the ACTUAL employer name (`lifeos/jobs/names.py`): POSITIVE on the register, NEGATIVE
when the register is loaded and the employer is not on it, UNRESOLVED (Review) when no register is loaded, the company is empty, or the company is a
recruiter/intermediary (its own licence cannot qualify an unresolved client employer). Sources: any job the pipeline already holds, so UK jobs from the
newsletters and Open Jobs are judged too. `decide_all` lets the job's own lane judge it first; when another lane admits it and its own does not, that
lane takes it (a UK newsletter job at a sponsor becomes Skilled Worker); a dual route is one job, Scale-Up visible, both lanes in Eligible Lanes.
Until the register is loaded every UK job simply stays in its own lane's decision.

## Interview progression handoff (INT-7.1A, bounded)
`lifeos/jobs/hiring_pipeline.py` reads the Notion Hiring Pipeline page (secret `HIRING_PIPELINE_PAGE_ID`; the integration must be shared on the page):
Active / Retired Opportunities hold one page per pursuit ("Company - Role"), an opportunity page holds one child page per round. A job is PROTECTED
only on a deterministic company AND role match to an ACTIVE opportunity (`Handoff`: state HIRING_ACTIVE / INTERVIEW_ACTIVE, page-id evidence).
No match proves nothing, so this source can protect a pursuit but never unprotect one; Applied jobs stay UNKNOWN and are never retired. When the page
is configured but unreadable, retention retires nothing. When it is not configured, retention behaves as before and reports `handoff: not_configured`.
