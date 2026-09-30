# LIFE OS v7 - Decisions (canon)

Numbered, dated, and owned by Jim. If this file and any other document disagree, **this file wins**.
Status: HARD = non-negotiable constraint. DECIDED = approved. PROPOSED = recommended default, awaiting Jim.

## D1 - No unresolved jobs (HARD, 2026-09-30)
Every job that reaches Notion has BOTH a resolved final apply link AND its full job description. A job is never
published, or left in limbo, because the ATS/employer link "could not be resolved". There is no retargeting /
retry-later layer. If resolution fails for an open job, that is a defect in the resolver (fix the resolver), not a
state to ship. A posting that is genuinely closed or expired is excluded as `CLOSED` - never published half-resolved.

## D2 - No tiered publishing (HARD, 2026-09-30)
Nothing is published "now, upgraded later". A job is published once, fully resolved.

## D3 - Final apply link ranking (DECIDED)
1. Employer website (preferred). 2. The employer's official third-party board (Greenhouse, Ashby, Lever, Workday, ...).
3. Easy Apply, only when it is the sole path (LinkedIn or Jobright). 4. Aggregator-only, flagged `aggregator`
(allowed only when the aggregator truly is the only apply path). All sources are recorded; one final link is published.

## D4 - Posting date / job age ranking (DECIDED, 2026-09-30)
Sources in order of trust; we record which source supplied the date (`posted_source`):
1. Employer/ATS structured data (JobPosting `datePosted`; ATS API published/created timestamp).
2. The aggregator's own job page (LinkedIn posted time, Jobright page data).
3. Newsletter text ("Posted N days ago") + the email's age.
4. Email age alone (job is at least as old as the email that mentioned it).
The earliest credible date is the original posting date; all signals are kept, none overwritten.

## D5 - Freshness window (DECIDED)
Jobs known to be older than 14 days are excluded before Notion (`EXCLUDED_STALE`); unknown age is allowed.

## D6 - Reposted jobs (PROPOSED)
A repost is NOT automatically excluded or labelled a ghost job. Reposts reset dates on many sites, so:
keep it, flag `reposted`, and age it by the ORIGINAL date when known (so D5 still excludes a repost of an old job).
"Ghost job" likelihood (long-open, repeatedly reposted, no hiring activity) is a separate Phase 2 scoring signal, not a filter.

## D7 - Privacy (HARD)
Public repository: no PII, secrets, mailbox content, or job data in code, logs, or PRs. See `docs/PRIVACY.md`.

## D8 - Notion (DECIDED)
Free-tier API only (<= 3 req/s). Hostinger is the dedupe brain; no Notion reads to dedupe. Job description goes in the
page body only, in the machine-readable `v7.jd.1` format (see PLAN.md); Hostinger keeps the authoritative copy.

## D9 - Provider match scores (DECIDED)
Aggregator match % is stored as evidence only and counts zero today. Phase 2 decides: 0% or 25% weight.

## D10 - Browser tooling (DECIDED, 2026-09-30)
Automated HTTP fetching is blocked or login-gated for Lensa, LinkedIn (employer link) and Jobright (employer link).
Resolution uses a real browser path: TinyFish (Fetch first because it is free; Agent only where a login or clicks are
unavoidable, because it is metered per step) and the existing Jobright login. Browser path is proven on a sample
BEFORE anything is published.
