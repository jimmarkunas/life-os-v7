# Plan: Job Newsletter pipeline (Phase 1), built to grow

> Binding decisions and hard constraints live in [DECISIONS.md](DECISIONS.md) and win over anything below.

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
| 5 | describe | final apply page -> full job description stored privately: full text + summary / responsibilities / requirements / qualifications (`v7_job_descriptions`) | 1 |
| 5 | publish | fresh (<=14d) `RESOLVED` rows → Notion Job Ledger (core fields only), then mark `Processed` | 1 |
| 6-7 | triage / score | cursory match, then % fit (Claude project + ChatGPT/GitHub method) | 2 |

## Final-apply-link rules (ranked; record ALL sources, publish ONE final link)
1. **Employer website** (preferred): the company's own careers/job page.
2. **Employer's official third-party board**: Greenhouse, Ashby, Lever, Workday, SmartRecruiters, etc. (`apply_kind=ats`).
3. **Easy Apply as the only path** (occasionally LinkedIn, occasionally Jobright): that aggregator URL *is* final (`apply_kind=easy_apply`).
4. **Aggregator only** (Lensa/Jobright/LinkedIn page that never resolves): published but flagged `apply_kind=aggregator`; always replaced if a better source appears later.
- Follow redirects until the destination stops being an aggregator; canonicalize tracking URLs (e.g. LinkedIn `/comm/jobs/view/ID?...` -> `/jobs/view/ID`).
- Jobright: read `originalUrl`/`applyLink` from the page's `__NEXT_DATA__` first; logged-in browser only as fallback.
- **No unresolved jobs, no tiered publishing** (DECISIONS D1, D2): a job is published only when fully resolved; a failing resolver is a bug to fix, not a state to ship.

## Learned from real mail (structure only; no content)
- **Lensa** (two layouts: "jobalert/aggregated" digests with ~20 cards, and "career advocate" notes with ~3): each job is a tracked-link card with company, title, salary *estimate*, location. **No posting date**, so freshness comes from the destination page at resolve time.
- Card links are **per-recipient tracking redirects** (same job = different URL in every email). So: dedupe cannot use the email URL; it happens after the final apply URL is resolved (Step 5). Until then a cheap `fuzzy_key` (company|title|location) prevents resolving the same job repeatedly.
- Following a tracked link counts as a "click" for Lensa; that is expected and harmless.

## Job descriptions (required)
- Every resolved job must store its **full description**: complete text plus best-effort sections (summary, responsibilities, requirements, qualifications). Full text is always kept, so sections can be re-split later or scored directly.
- Source order: ATS API (Greenhouse/Ashby/Lever) -> `JobPosting` JSON-LD on the page -> readable page text. Aggregator-only jobs use the aggregator's text and are flagged.
- Stored **only in Hostinger** (`v7_job_descriptions`), never in the repo or logs. Phase 2 scoring reads it from there.
- Notion: summary + requirements + qualifications go in the page *body* inside the same create call (no extra requests, within free-tier limits). **Decided (Jim): page body only, and machine-readable.**

### Notion page body format (machine-readable contract `v7.jd.1`)
- Block 1: a paragraph exactly `v7-jd:1 | key=<Stable Job Key>`.
- Then, in fixed order, only for non-empty sections: a `heading_2` whose text is exactly `Summary`, `Responsibilities`, `Requirements`, or `Qualifications`, followed by `bulleted_list_item` blocks (one per bullet) or `paragraph` blocks.
- Parse rule: walk the page children; a `heading_2` with one of the four exact names starts a section; everything until the next `heading_2` belongs to it. Text longer than 2000 characters is split across consecutive paragraph blocks.
- The authoritative machine-readable copy is always `v7_job_descriptions` in Hostinger (Phase 2 scoring reads Hostinger, so it needs zero Notion reads). The Notion body is the human-and-machine-readable mirror and is written once in the create call.

## Freshness gate (front end, before Notion)
- Keep jobs up to **14 days old**. A job is excluded only when it is *known* older than 14 days (posting date or "N days/weeks ago" text in the newsletter or job page). Age unknown = allowed.
- Excluded rows stay in Hostinger as `EXCLUDED_STALE` (so they are not re-processed) and **never reach Notion**.
- Applied at extract (age text in the email) and again at resolve (JSON-LD `datePosted` on the final page).

## Notion limits (global rule)
- Use only the free official Notion API with the integration token, at <= 3 requests/second. No Notion AI, MCP, or paid features in the runtime.
- Hostinger is the dedupe brain, so the pipeline does **no Notion reads to dedupe**: it creates a page once per job and stores `notion_page_id`; later changes are PATCHed only when a field actually changed.
- Publishing is capped per run (configurable) so a day-one backlog drains over several hourly runs instead of bursting.

## Mail classification rules
- Only **automated job-alert newsletters** are swept. Recruiter-led mail (Dice Private Email relays, LinkedIn messages/InMail/invitations) is **not** a newsletter and is left alone (a separate recruiter-lead stream may come later).
- Dice is not a newsletter source (decided): it is bundled into the US Remote producer (a second `lifeos/sources/<name>` that emits jobs through `intake.add_job`).

## Build order (each step = one commit, one acceptance check)
| Step | Ship | Acceptance (on real data) |
|---|---|---|
| **1 sweep** | this commit | dry run prints counts; `--live` moves mail and Inbox count drops; re-run moves 0 |
| 2 store schema | `v7_jobs` and the other `v7_` tables + connectivity check via SSH tunnel | CI creates tables; re-run is a no-op |
| 3 extract: Lensa | parse cards (+ age text -> freshness gate) from labeled Lensa mail into `NEW` / `EXCLUDED_STALE` rows | row count matches the cards in 3 hand-checked emails |
| 4 extract: LinkedIn, Jobright (done) | same for the other senders; canonical URLs (no personal tracking tokens stored); Jobright gives salary + age | parsed real samples: 6/6 cards each, no tracking query in stored URLs |
| 5 resolve + describe | final URL + `apply_kind` + full job description (JSON-LD / ATS API / page text) | 20 sampled rows hand-verified; every resolved row has non-empty full text |
| 6 publish | Notion rows, `Processed` label, no duplicate on re-run | rows appear in the Ledger; 2nd run adds 0 |
| 7 Jobright browser fallback | only if `__NEXT_DATA__` path fails | unresolved Jobright rate < agreed threshold |

Ship target: steps 1-6 within the 2-hour window. Step 7 and all of Phase 2 follow.

## Scalability hooks (designed in, not built)
- `source` + `parser` registry: add newsletters/ATS boards without touching other stages.
- `v7_jobs.first_seen/last_seen` and a per-source `fingerprint` column allow open-jobs-style diffing later.
- Stage status machine lets Scale-Up / US Remote become additional *producers* of `NEW` rows, not a second engine.

## Decisions recorded
- Repo: life-os-v7 (fresh). Destination: existing Notion Job Ledger, **core fields only** (Job, Company, Apply URL, Source Provider, First Surfaced, Stable Job Key; Admission Status = "Passed / Review"; Fit blank).
- Senders: Lensa, Jobright, LinkedIn job alerts. **Dice relays excluded** (recruiter-led, confirmed); Dice is handled by the US Remote producer, not a newsletter rule.
- First run backfills the entire Inbox. Hostinger: reuse existing DB, new `v7_` tables only.
- Scheduled runs are dry-run until repo variable `V7_LIVE=true`.
