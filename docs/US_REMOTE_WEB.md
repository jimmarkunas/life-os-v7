# US Remote Web: incremental acquisition plan

Status: SCOPED, not built. Authority: the US Remote web contract (v3, 2026-09-28) and `docs/LANES.md`. The source universe is in
`lifeos/sources/web/us_remote.json` (43 sources harvested from V2: 30 employers, 10 staffing agencies, 3 discovery helpers).

## Principle
Never crawl the world in one run. Boards are polled when due, diffed against Hostinger state, and only NEW or MATERIAL_CHANGE vacancies
cost JD fetches, Fit work or a Notion write. UNCHANGED costs nothing. A failed or partial source is DEGRADED, never zero, and never implies a removal.
One scheduler (the hourly workflow); no second cron.

## Coverage today (from the registry)
| | ready (V7 lists it today) | port (JSON API, small adapter) | bespoke (site parser) | discovery |
|---|---|---|---|---|
| Employers (30) | 25: 20 Greenhouse, 4 Ashby, ServiceNow (SmartRecruiters) | 3: Adobe, Postman (Workday CXS), GitHub (Jibe) | 2: Shopify, Atlassian | |
| Staffing (10) | 1: Apex (SmartRecruiters) | | 9: Insight Global, TEKsystems, Motion, Randstad, Akkodis, Kforce, Robert Half, Experis, Dexian | |
| Helpers (3) | | | | LinkedIn Jobs, Built In, Dice |
So 26 of 43 sources can be read with code V7 already has, 3 need a small port, 11 need site-specific parsers, 3 are discovery-only.
The browser is not an option for any of this (D12): free HTTP and the free fetch only.

## Hostinger state (new tables, all `v7_`)
- `v7_sources`: source id, kind, slug, `due_at`, `last_status` (COMPLETE | DEGRADED | FAILED), `last_complete_at`, `frontier_hash`.
- `v7_source_items`: (source id, provider job id) key, `material_hash` (title, location, url, content, posted), `first_seen` (written once),
  `last_seen`, `state` (CURRENT | REMOVED), `job_id` link to `v7_jobs`.
- `v7_source_runs`: run history per source with added / changed / unchanged / removed counts and status. Counts only in logs.

## One run (bounded)
1. Pick due sources: a deterministic per-board slot spreads load; at most N boards and a wall-clock budget per run (limits live in `platform/limits.py`).
2. List the board with a status-aware lister (today's `ats_match.board` returns `[]` for both "empty" and "failed"; the new lister must tell them apart).
   Status is COMPLETE only when the provider's own completeness contract is met (all pages read, no 429/403/timeout).
3. Diff against `v7_source_items`: NEW, MATERIAL_CHANGE, UNCHANGED, and (only after a COMPLETE run) REMOVED.
4. Cheap suppression before any JD or Fit work: unequivocal hard-family title, clearly non-US or non-remote location
   (`lanes.detect_work_mode`), a suppression tombstone. An ambiguous target-family title (for example "Technical Program Manager, DevOps Platform") continues.
5. NEW / MATERIAL_CHANGE survivors enter Jobs OS through `intake.add_job` (lane `US Remote`, provider = the ATS, final URL known, so status RESOLVED).
   Where the list carries the full description (Ashby, Lever, Greenhouse with content, SmartRecruiters detail, Workable) it is stored at once;
   otherwise `enrich` reads the page, one fetch, once.
6. Everything after that is the existing path: enrich guards, `fit`, lane policy, publish. REMOVED marks Hostinger state only; it never deletes a published Notion row.

## Posting date
The employer's date where the list gives it (Ashby `publishedAt`, Lever `createdAt`, SmartRecruiters `releasedDate`, Workable `published_on`).
Greenhouse list `updated_at` is not a posting date: verify `first_published` in phase 1, else the page's JSON-LD `datePosted` through enrich.
A crawl time is never used as a posting date; First Surfaced is the age fallback (docs/LANES.md).

## Channel A: Open Jobs change feed (broad coverage)
Open Jobs (CC0) publishes daily diffs and a verified change feed (`/data/diffs/`, `/data/changes/`): `upsert` and `remove` events keyed `ats/slug#id`,
each upsert with title, location, url, full content, published and first-seen dates (no company name; derive it from the board slug). Consume it as a
cursor in Hostinger: pin one generation, verify, apply oldest-first, commit the cursor with the events, replay is a no-op, a gap is DEGRADED (never skipped).
Filter to the target role family and US locations; an upsert that stops matching removes the local candidate state. Send a contact in the User-Agent
(held in an environment variable, not in this public repo). The sandbox here cannot reach the host, so it must be proven from Actions.
This channel makes most of the 11 bespoke boards unnecessary: it covers the ATS families they sit on.

## Phases
1. Registry (done) and the three tables; a status-aware lister over the 5 ready ATS kinds; the diff and classification; 26 sources live in shadow mode
   (dry run counts only). Tests with recorded responses.
2. Workday listing (Adobe, Postman) and Jibe (GitHub).
3. Open Jobs feed intake with the cursor and generation rules.
4. Bespoke staffing parsers only where the feed does not cover the board and you still want that agency.
5. Discovery helpers: LinkedIn (no login), Built In, Dice stay discovery-only: they can propose a candidate, never an Apply URL or a JD.

## Acceptance (the contract's 15 points, mapped)
Frontier advances only on COMPLETE; a failed board keeps its state; NEW / MATERIAL_CHANGE / UNCHANGED / REMOVED classify correctly; junk is suppressed
with no JD or Notion work; a plausible job gets JD, Apply URL, non-null Fit and a lane decision; an unchanged replay does zero fetches, zero Fit work and zero
Notion writes; one qualifying NEW job creates exactly one Notion row and is read back; a below-floor job creates no row; Newsletter and Web evidence for one
vacancy converge to one job (same `dedupe_key` and fuzzy key); human lifecycle state is never overwritten; one run touches only bounded due work.

## Decisions for Jim
1. Take the Open Jobs feed as channel A (broad coverage), or direct boards only?
2. Staffing agencies: keep all 10 (your canon prefers C2C/1099 over W-2 over permanent, so agencies matter) or start with the one ready agency (Apex)?
3. Discovery helpers (LinkedIn, Built In, Dice): keep as discovery-only, or drop them once the feed is in?
