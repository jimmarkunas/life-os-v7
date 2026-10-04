# Automated MegIBOW: scope and roadmap (scoping only, no code)

Status: defined for Jim's review. Nothing here is built, and this document changes no schema, secret, workflow or `docs/DECISIONS.md`. The product contract is the live Notion page "Automated Megibow Dashboard" (read in full for this document); this file says how V7 computes it, what is missing, and in what order to build it.

**Settled by Jim in the latest round**
- **Automate it, and define it.** Jim's words: "we need to automate this... Let's define." This document is the definition. The Notion page still says "not yet promoted"; promotion in Notion's own process is a separate step I have not taken (section 12, decision 7).
- **Company Call = hiring manager**, "like an interview with a hiring manager" (section 2 says how I read that).
- **The Wednesday 3 PM CT check is real**: a good refresh must exist before the Jim and Matt sync (section 7).
- **Fresh start at a cut-over Monday**, with the cumulative column seeded from the sheet's totals (section 8). Jim: "Fine."
- Earlier: the table moves from Google Drive into Notion; Matt is review-only; Jim's work is tracked, never Matt's.

**Defaults I chose, each with a one-line veto** (section 12): an in-house recruiter is a Company Call; Outlook sent mail is not read; a small database holds frozen weeks and Jim's corrections; the review list uses tick-boxes.

## 1. What it does, in plain words

Every week Jim and Matt look at one small table: five rows of Jim's job-search activity (Outreach, Scheduled, Networking Calls, Recruiter Calls, Company Calls), eight weekly columns, and a running total. Today Jim counts by hand in a Google Sheet. After this feature a V7 job reads Jim's sent mail and calendar, decides what counts, and writes that table into his section of the shared Notion dashboard, so nobody counts by hand. When it cannot tell (was that a call or a calendar hold? is this person a recruiter or an employee?), it does not guess: it lists the item under the table for Jim to settle with one tick. Under the table it can show up to three plain warnings ("no meetings booked yet this week, send five messages today"). The table, not a chart, is the product.

## 2. Definitions as V7 computes them

Week = Monday to Sunday in `America/Chicago`; the table shows the current week plus the prior seven, then a Cumulative column.

| Measure | Counts | Counted in week of | Evidence V7 can use today |
|---|---|---|---|
| **Outreach** | A message Jim sent himself to a job-search contact that advances a relationship or process (introduction, follow-up, referral ask, thank-you, meeting request). Never inbound, automated, application confirmations, ordinary work or client mail, self-sends, a message he is only copied on, or a duplicate of the same message. | When it was sent | Gmail sent mail |
| **Scheduled** | One new job-search meeting formally put on the calendar with a network contact, recruiter or company person. Counted once, when it is first booked; a reschedule never counts again. | The day the event was created (or first confirmed). If that cannot be proven the item goes to review, never guessed from the meeting date. | Google Calendar event creation time |
| **Company Call** | A completed call, video call or in-person meeting with someone acting for a company Jim is pursuing: the **hiring manager** (the model case, "an interview with a hiring manager") and the others the live page lists (a team member interviewing him, an executive, a referral contact at the company). | When the meeting happened | Calendar plus occurrence evidence (below) |
| **Recruiter Call** | A completed meeting with an independent, agency or search-firm recruiter. An agency recruiter discussing a client's role is a Recruiter Call, never a Company Call. | When it happened | same |
| **Networking Call** | A completed meeting with a network, industry, alumni, peer or mentor contact who is neither of the above, and not ordinary work. | When it happened | same |

**How I read "Company Call = hiring manager".** The live page defines a Company Call more widely (it also lists internal recruiters, employees, executives and referral contacts at the target company), and Jim's answer names the typical case. I kept the page's wider list and made the hiring-manager conversation the model case. **One consequence to confirm:** a recruiter employed *by the target company* (an in-house recruiter) is a Company Call, as the page says. If Jim wants the strict reading (Company Call only for the hiring manager or someone interviewing him, with in-house recruiters counted as Recruiter Calls), it is one rule and one sentence on the page.

**Call classification precedence** (resolves the wording problem in section 13): an independent or agency recruiter is a Recruiter Call even when the conversation is about a target company; a person employed by the target company is a Company Call; everyone else qualifying is a Networking Call; anything unresolved is review. The hiring manager's conversation can therefore never be mistaken for a recruiter call, and an agency recruiter can never be mistaken for the company.

**Defaults for cases the page does not cover** (veto any):
- A meeting with only tentative responses from the other side counts as Scheduled but is not enough to count as a completed call; it goes to review for occurrence.
- A calendar hold with no other person, an all-day placeholder, and an event Jim declined never count.
- A recurring meeting counts Scheduled once, when the series is created, and each occurrence counts as a call only when that occurrence qualifies (the page's rule).
- A meeting booked in one week and cancelled later: counted in a closed week if it was counted when the week closed (closed weeks never change); in the open week it drops out when the cancellation appears (the page says the open week is recomputed from evidence and counts may fall).
- A no-show: V7 cannot see one, so it is a review item or Jim's correction.

## 3. What exists in V7 today, and what is missing (verified in code)

| Need | Today | Gap |
|---|---|---|
| Google Calendar read | `GoogleCalendar.list_events` pages through single events and returns the full event, so native events already carry attendees and a creation time | None for native events. |
| Outlook calendar read | `Outlook.events` selects a fixed field list (`EVENT_FIELDS`: no attendees, no creation time) | An optional field-list argument, so the bridge's shared constant is not edited. |
| Outlook events on Google | The bridge copies title, time, place, a short description and busy/free only. **No attendees. The copy's creation time is when the bridge copied it, not when Jim booked it.** A reschedule gives the copy a new id because the id hashes the start time. | MegIBOW must not trust a bridged copy for Scheduled or for who attended. It reads the Outlook original (with attendees and `createdDateTime`) and skips events carrying the bridge's private marker. Its stable meeting identity is the iCalendar UID, which survives a reschedule. |
| Gmail sent mail | `list_ids_complete` (fails closed) and `message_record` exist; a sent-mail query is possible | A metadata-only reader that returns recipients, thread id and time without keeping the body (the body is needed only for a keyword check, in memory). |
| Outlook sent mail | Sent Items can be listed through the folder argument, but the field set has no recipients and no sent time | Not needed by default (section 12, decision 3). |
| Hiring and interview context | Interview and Job Ledger readers exist inside their own packages | MegIBOW may not import them (layering). An adapter in `lifeos/sources/` hands plain data across. |
| A table in Notion | None: `report_region.replace_text` replaces text blocks and leaves tables alone | A bounded table writer, and a page-level proof that everything around the block is unchanged (shared primitive P5). |
| Week windows and history | None | New, in the package. |
| Closed-week storage | `snapshot_store.Store` can save and load one key, but cannot list a range | A small table (section 5). |
| Alerts | Layer exists; Jobs and others share state | P2 first (roadmap). |

## 4. Architecture and placement

```text
Gmail sent mail (metadata)      Google Calendar (native events)     Outlook calendar (originals, with attendees)
          \                              |                                    /
           +------------- evidence adapters (platform clients, read only) ---+
                                         |
   Interview rounds, Job Ledger companies (via lifeos/sources/megibow.py) ---+
                                         v
   classify (pure): relevance -> activity -> contact type -> confidence  =>  COUNTED | EXCLUDED | REVIEW | DEGRADED
                                         v
   overrides (Jim's resolutions, hashed keys)  ->  project (pure): current week recomputed; closed weeks read from the weeks table
                                         v
   warnings (pure)  ->  render (pure)  ->  bounded Notion block inside "Jim Organizer": table, up to 3 warnings, review ticks, last refreshed
```

- Package `lifeos/megibow/` (windows, classify, project, warnings, render, overrides) imports `platform` only. The adapter that reads Interview and Job Ledger context sits in `lifeos/sources/megibow.py`.
- One stage in `lifeos/run.py`, run as a `megibow` job in `domains.yml` after each hourly tick (a new `DOMAIN_SECRETS` entry). No new scheduler, no new `hourly.yml` input.
- No model and no ChatGPT at runtime (D54). Everything is rules over structured fields and fixed keyword lists.
- Output is counts and fixed codes only. The only place a person's or company's name appears is the private Notion page, as with the other cards.

## 5. State: two tiny tables

The live page forbids a "standalone activity database or ledger" and allows "minimal correction state". These two hold counts and decisions, never messages or events:

- `v7_megibow_weeks`: `week_start` (key), the five counts, a `trustworthy` flag, `frozen_at`, a short `note` for a corrected week, and one `LEGACY` row holding the five cumulative totals carried over from the old sheet. It is what the Cumulative column and the four-week trend rule read, and it is why a hand edit to the Notion table can never change history.
- `v7_megibow_overrides`: `kind` (`ACTIVITY` or `CONTACT`), `key_hash` (a hash of the message, meeting or contact identity, never an address), `decision`, `decided_at`. Written when V7 reads Jim's tick-box. A `CONTACT` row is the "reuse a resolved classification" the page allows.

Both are a Hostinger schema change, so Jim approves them at MEG-3. Option without any table: keep closed weeks only in the Notion table block and read them back; I do not recommend it, because one stray edit silently rewrites history. Say so and I scope it that way.

The open week is never stored: it is recomputed from the sources on every run, as the page requires.

## 6. The review list, and how Jim answers it

**The problem, in plain words.** Some items V7 cannot settle alone ("was this 30-minute call a recruiter or an employee?"). The live page shows them as buttons, `[Count as Company Call] [Exclude]`. A GitHub job cannot receive a click. All V7 can do is **look at the Notion page again on its next run and see what Jim changed.** So the question is only: what does Jim change?

**Option A, tick-boxes (recommended).** Under the table each unsettled item is one line followed by a short row of tick-boxes, for example:

> Sep 30 · Acme · 30-minute call · is this a call that happened, and what kind?
> ☐ Company Call ☐ Recruiter Call ☐ Networking Call ☐ Did not happen / exclude ☐ Not sure yet

Jim ticks one. Within the hour (next run) V7 sees the tick, records the decision (so it is remembered, and reused for the same person next time), recounts the week, and rewrites the line away. Nothing to install, works from his phone, no new Notion database. Cost: up to an hour before the table changes, and one tick per item.

**Option B, tell it in words.** Jim (or an assistant on his behalf) writes one line on a private "MegIBOW corrections" page, such as `Sep 30 Acme call = Company`. V7 reads that page each run. Good if he would rather dictate to chat than tap; easy to mistype, so a line V7 cannot read becomes a visible error, never a guess.

**Option C, a small Notion database** with a real dropdown per item. The nicest to use (filters, history), but it is a new Notion database, close to what the live page asks V7 not to build, so it needs Jim's explicit say-so.

Recommended: A, with B available as a fallback later. The tick-box lines only appear for items that could change this week's table or a warning (the page's own limit), and an item left alone stays unsettled and never counts.

## 7. Weekly rhythm and the Wednesday 3 PM check

- **Monday:** the first successful run after Sunday ends in Chicago freezes the finished week into `v7_megibow_weeks` (verified by read-back), then opens the new current week. If the freeze fails the week is not closed and the block says DEGRADED until it is.
- **Tuesday to Sunday:** every hourly tick recomputes the open week and writes only if the block's content changed (so most ticks write nothing).
- **Wednesday:** the page requires a good refresh before 3 PM CT. The block always shows `Last refreshed <time>`. In addition, the `megibow` job raises one alert if, on Wednesday at or after 12 PM Chicago time, the last good refresh is older than six hours or the block is DEGRADED, so there is time to fix it. The alert needs the shared alert fix first (roadmap P2); until then the visible timestamp and DEGRADED label carry it.
- **Degraded evidence:** a failed Gmail or Calendar read, or a block V7 cannot find, never produces zeros or "pipeline healthy". It leaves the last good block in place and says DEGRADED (the page's rule).

## 8. Migration: fresh start with a legacy total

Jim agreed to start the Notion table fresh at a cut-over Monday and seed the Cumulative column from the sheet's totals.
- The first V7 week is the Monday of go-live. Earlier week columns show a dash, not a zero.
- Jim gives the five cumulative totals from the sheet once; a local, dry-run-first command writes them into the `LEGACY` row. They are private counts, so they never appear in the repository.
- Cumulative = legacy row + every frozen week + the open week.
- The sheet's weekly columns are labelled by sync dates (Wednesday to Thursday), not Monday-to-Sunday weeks, and no event-level data exists, so old weeks cannot be re-bucketed. The sheet stays as a read-only archive.
- Trend warnings stay off until four trustworthy completed weeks exist (the page's rule), which is about a month after cut-over. Evidence and forward-pipeline warnings work from week one.

## 9. How each measure is decided without a model

The page's four steps (relevance, activity type, contact type, confidence) are kept. With no language model, "relevance" rests on structured evidence in tiers; the census slices (MEG-1a, 1b) measure how often each tier fires on Jim's real data before any rule is final.

- **Strong, counts automatically:** the contact or thread is already tied to a job-search record (a company in the Job Ledger or the Hiring Pipeline, an Interview round, a person Jim previously settled); or the calendar event's title or linked context names an interview with an external attendee at such a company.
- **Plausible, goes to the tick-box list:** an external human, role or interview wording in the subject or body, but no tie to a known record.
- **Out, never listed:** automated senders, job-board and application acknowledgments, newsletters and bulk mail, self-sends, Jim only copied, and mail to or from the consulting client (kept out by an excluded-domain list in private configuration, never in the repository).
- **Expect more tick-boxes at first.** Outreach classification is the weakest part to automate without a model. Every settled contact is reused, so the list should shrink week by week. If the census shows the list would be unmanageable, the fallback is a narrower definition of "known contact", not guessing.
- **Occurrence of a completed call (the page's strong-evidence list):** counted automatically only when the event stayed accepted and not cancelled through its end **and** there is corroboration V7 can see: Jim sent that attendee a message within three days after the event, or an Interview round for it is recorded as done, or a meeting note or transcript exists (later, from the meeting pipeline). Without corroboration it is `Review needed - occurrence`.
- **Scheduled** is stateless: a qualifying event whose creation time falls in the week, keyed by its iCalendar UID (series master for recurring). No stored list of seen meetings is needed.
- **De-duplication:** Outreach by message id; Scheduled by meeting UID; calls by event occurrence. Gmail scheduling mail and the matching calendar event are one Scheduled.

## 10. Roadmap

| Slice | Outcome | Likely files | Schema | Tests and UAT | Depends on | Size |
|---|---|---|---|---|---|---|
| **MEG-1a Calendar census (dry run)** | Week windows (Monday to Sunday, Chicago, eight weeks) plus counts only from the real calendars: events with an external attendee, with a usable creation time, native versus bridged versus Outlook original, cancelled, declined, all-day, recurring, passed versus future, and the next 14 days. By count it shows whether Scheduled and the call rules are computable from today's data. | `lifeos/megibow/{windows,census}.py`, an optional fields argument on `Outlook.events`, one `run.py` line, a manual `domains.yml` input-free job or local run | none | window edge cases (week boundary, daylight saving, rollover Monday), census counts on fakes, no names in output | Jim's go (this document) | S to M |
| **MEG-1b Sent-mail census (dry run)** | Counts of Jim's sent mail in the window by bucket: human or automated, external recipient, in a known-record tier, role wording, excluded domain. Calibrates section 9 before any rule is locked. | `lifeos/megibow/mailcensus.py`, a Gmail metadata reader (shared mail record P1 only if Recruiters has landed) | none | counts only, no body kept | MEG-1a | M |
| **MEG-2 Classify and project (dry run)** | The full pure engine: relevance, activity, contact type, occurrence, de-duplication, weekly projection, with COUNTED, EXCLUDED, REVIEW and DEGRADED outcomes; a dry run prints the table and the review count, no Notion write. | `lifeos/megibow/{classify,project}.py`, `lifeos/sources/megibow.py` | none | table-driven rules for every row of sections 2 and 9 and for the page's scenarios 1 to 9 | MEG-1a, MEG-1b | L |
| **MEG-3 Block, overrides and weeks** | The table appears in Jim's section; review ticks work; settled items are remembered; closed weeks are stored. Jim creates the block once. | `lifeos/megibow/{render,overrides,store,card}.py`, a bounded table writer and page digest in `platform` (P5), router entry, `domains.yml` job, `DOMAIN_SECRETS` entry | `v7_megibow_weeks`, `v7_megibow_overrides` | owned block replaced and nothing around it touched, read-back, no-op when unchanged, block-not-found is DEGRADED and writes nothing, a tick is read back and applied once | MEG-2, Jim's table approval, the block and a Notion integration shared with the dashboard | L |
| **MEG-4 Rollover, freeze, legacy** | Monday freeze with verification, the legacy cumulative row, "Historical review suggested" for late evidence (Jim approves any change to a frozen week). | `lifeos/megibow/rollover.py`, the legacy-seed command | none new | the page's scenarios 13 to 15; failed freeze is DEGRADED | MEG-3 | M |
| **MEG-5 Warnings** | The page's v1 rules exactly: baseline, precedence, at most three, one suggestion each, quiet healthy line, trend rules off until four trustworthy weeks. | `lifeos/megibow/warnings.py` | none | the page's scenario 10 plus each threshold edge | MEG-3 | M |
| **MEG-6 Freshness alert and acceptance** | The Wednesday alert, the sixteen-scenario audit on the real dashboard, and a later occurrence source (meeting notes or transcripts) once the meeting pipeline exists. | `lifeos/megibow/alerts.py` | none | scenarios 11, 12 and 16 live | MEG-3 and the alert fix (P2) | S to M |

One PR per slice; the first four steps of the roadmap run these census slices early because they cost nothing and decide whether the rules are computable.

## 11. Approvals (asked when the slice starts)

- **Hostinger:** the two tables in section 5 (MEG-3).
- **Notion:** the bounded MegIBOW block that Jim creates inside Jim Organizer (V7 never creates a missing block), and the dashboard page shared with the Notion integration V7 uses. The Jira accountability view may also write to this page (to confirm), so the page-level protection check must cover it.
- **Secrets:** the dashboard block id, a token if the existing one is not shared with that page, a private list of excluded domains.
- **Workflow and tests:** a `domains.yml` job, a `DOMAIN_SECRETS` entry, a router entry, an optional Outlook fields argument.
- **Other repository:** the scheduler wording (section 13) in `life-os-automation`, only on Jim's explicit go.
- **Hiring Pipeline and Network Intelligence** are inputs, not blockers: MEG-2 works with the Job Ledger and Interview data alone and gets better as they land.

## 12. Decisions and defaults for Jim

1. **Company Call.** Default: the hiring manager is the model case; the page's wider list (employees, executives, referral contacts, an in-house recruiter) stays. Strict version on request.
2. **In-house recruiter.** Default: Company Call (the page says so). Veto: Recruiter Call.
3. **Mailboxes.** Default: Gmail sent mail only, as the page lists; Outlook sent mail is not read. The consulting calendar and mailbox are not read (they are kept out). Say if the work Outlook sent items should count.
4. **Storage.** Default: two small Hostinger tables (section 5). Alternative: Notion block only.
5. **Review input.** Default: Option A, tick-boxes (section 6).
6. **Tentative, declined, all-day, recurring, cancelled.** The defaults in section 2.
7. **Promotion.** The Notion page still says "not yet promoted". Say "promote it" and I update its status line and add a short note recording the definitions here (Company Call reading, precedence, the scheduler sentence); I will not touch the page's contract text otherwise.

## 13. Corrections the Notion page needs (not edited; offered)

1. **Precedence wording.** The top of the page lists independent recruiter first, then target-company representative. Step 4 lists Company Call first, and its first clause ("acting on behalf of a target company") can be read to capture an agency recruiter retained by the company, while scenario 5 says that agency recruiter is a Recruiter Call. Recommended wording: Recruiter Call first (independent, agency or search-firm recruiters), Company Call second (people employed by the target company), Networking Call third.
2. **Company Call = hiring manager.** The page lists a wider set; say whether that stands (decision 1).
3. **The scheduler sentence.** The page says to use `LIFE OS Daily Runs` and the production contract for run times. V7 runs this from its own hourly tick; see `docs/SCHEDULER_AUTHORITY_DELTA.md` for the proposed wording that makes both true.
4. **Status.** Promotion (decision 7).

## 14. First build slice

```text
FIRST BUILD SLICE:
MEG-1a calendar census (dry run, counts only)

WHY FIRST:
No schema, no secret beyond the existing Google and Outlook access, and it answers the question every later rule depends on: how many of Jim's real meetings carry an external attendee and a creation time, and how many are bridged copies that carry neither.

EXPECTED FILES:
lifeos/megibow/__init__.py, windows.py, census.py; an optional fields argument on Outlook.events; one line in lifeos/run.py; tests/megibow/*; a docs/SETUP.md section

SCHEMA:
NONE

ACCEPTANCE:
Week-window tests pass (Monday boundary, Chicago, daylight-saving shift, eight-week span); the census prints counts only (no title, attendee or address anywhere); a bridged copy is counted as bridged and never as a Scheduled candidate; the full suite passes.

DO NOT BUILD YET:
Classification, the Notion block, the tables, warnings, the sent-mail reader, rollover.
```
