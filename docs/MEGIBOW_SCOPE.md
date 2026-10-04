# Automated MegIBOW: scope and roadmap (scoping only, no code)

Status: defined for Jim's review. Nothing here is built, and this document changes no schema, secret, workflow or `docs/DECISIONS.md`. The product contract is the live Notion page "Automated Megibow Dashboard" (read in full for this document); this file says how V7 computes it, what is missing, and in what order to build it.

**Settled by Jim**
- **Automate it, and define it.** This document is the definition. **Promoted in Notion on October 4, 2026** (status line, a promotion record, the Product Backlog row, and the contract changes below), because Jim asked for it.
- **Company Call is the hiring-manager conversation** ("like an interview with a hiring manager"); **any recruiter, including one employed in-house by the target company, is a Recruiter Call.**
- **Every connected mailbox and calendar counts** (Gmail, Outlook personal and work, Google Calendar), subject to the page's job-search relevance rules. Ordinary client or consulting mail and meetings still do not count.
- **Wednesday: a good refresh must exist before the 3 PM CT meeting with Matt** (section 7 gives the guarantee).
- **Two small Hostinger tables** hold closed weeks and Jim's decisions.
- **Review input: a small Notion database with a dropdown** (Option C), with tick-boxes (Option A) as the fallback if the database cannot sit where it needs to (section 6).
- **Fresh start at a cut-over Monday**, with the cumulative column seeded from the old sheet's totals (section 8).
- **The scheduler fix is approved** and made in the production contract (`docs/SCHEDULER_AUTHORITY_DELTA.md`): MegIBOW runs on V7's tick.
- Earlier: the table moves from Google Drive into Notion; Matt is review-only; Jim's work is tracked, never Matt's.

**Notion page changes made at promotion** (read back and checked): the status line says promoted; Recruiter Call now includes in-house recruiters and Company Call is the hiring manager and other non-recruiter people at the target company (the call definitions, the precedence list, Step 4, the review trigger, and scenarios 4 and 5); Gmail, Outlook and all connected calendars are named as evidence; the review input surface, the Wednesday target, and the V7 tick replace the "Daily Runs" runtime sentence; a promotion record points here. Nothing else on the page changed.

## 1. What it does, in plain words

Every week Jim and Matt look at one small table: five rows of Jim's job-search activity (Outreach, Scheduled, Networking Calls, Recruiter Calls, Company Calls), eight weekly columns, and a running total. Today Jim counts by hand in a Google Sheet. After this feature a V7 job reads Jim's sent mail and calendars (Gmail, Outlook and Google), decides what counts, and writes that table into his section of the shared Notion dashboard, so nobody counts by hand. When it cannot tell (was that a call or a calendar hold? is this person a recruiter or a hiring manager?), it does not guess: it adds the item to a small review list that sits beside the table, where Jim settles it with a dropdown. Under the table it can show up to three plain warnings ("no meetings booked yet this week, send five messages today"). The table, not a chart, is the product.

## 2. Definitions as V7 computes them

Week = Monday to Sunday in `America/Chicago`; the table shows the current week plus the prior seven, then a Cumulative column.

| Measure | Counts | Counted in week of | Evidence V7 can use today |
|---|---|---|---|
| **Outreach** | A message Jim sent himself to a job-search contact that advances a relationship or process (introduction, follow-up, referral ask, thank-you, meeting request). Never inbound, automated, application confirmations, ordinary work or client mail, self-sends, a message he is only copied on, or a duplicate of the same message. | When it was sent | Gmail and Outlook sent mail |
| **Scheduled** | One new job-search meeting formally put on the calendar with a network contact, recruiter or company person. Counted once, when it is first booked; a reschedule never counts again. | The day the event was created (or first confirmed). If that cannot be proven the item goes to review, never guessed from the meeting date. | Calendar event creation time (Google, and Outlook originals) |
| **Company Call** | A completed call, video call or in-person meeting with the **hiring manager** at a company Jim is pursuing (the model case, "an interview with a hiring manager"), or another non-recruiter person acting for that company (a team member interviewing him, an executive, a referral contact). | When the meeting happened | Calendar plus occurrence evidence (below) |
| **Recruiter Call** | A completed meeting with **any recruiter**: independent, agency, search-firm, or employed in-house by a target company. A recruiter is a Recruiter Call even when the conversation is about a role at their own company or a client's, never a Company Call. | When it happened | same |
| **Networking Call** | A completed meeting with a network, industry, alumni, peer or mentor contact who is neither of the above, and not ordinary work. | When it happened | same |

**Settled (Jim): recruiters are recruiters.** Anyone functioning as a recruiter (talent acquisition, agency, search firm, independent, or in-house) is a Recruiter Call. The hiring-manager conversation is the model Company Call, along with the other non-recruiter people at the target company the live page lists. This reverses one sentence of the page, which had put in-house recruiters under Company Call; the page has been updated.

**Call classification precedence** (also written into the page): any recruiter is a Recruiter Call; otherwise a person employed by the target company is a Company Call; everyone else qualifying is a Networking Call; anything unresolved is review. The "functioning as a recruiter" test is a job-function test (title or signature wording such as recruiter, talent acquisition, sourcer, staffing), not an employer test, so it can be computed and reused per contact.

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
| Outlook calendar read | `Outlook.events` selects a fixed field list (`EVENT_FIELDS`: no attendees, no creation time) | An optional field-list argument, so the bridge's shared constant is not edited; read for every signed-in Outlook account, not only the bridged one. |
| Outlook events on Google | The bridge copies title, time, place, a short description and busy/free only. **No attendees. The copy's creation time is when the bridge copied it, not when Jim booked it.** A reschedule gives the copy a new id because the id hashes the start time. | MegIBOW must not trust a bridged copy for Scheduled or for who attended. It reads the Outlook original (with attendees and `createdDateTime`) and skips events carrying the bridge's private marker. Its stable meeting identity is the iCalendar UID, which survives a reschedule. |
| Gmail sent mail | `list_ids_complete` (fails closed) and `message_record` exist; a sent-mail query is possible | A metadata-only reader that returns recipients, thread id and time without keeping the body (the body is needed only for a keyword check, in memory). |
| Outlook sent mail | Sent Items can be listed through the folder argument, but the field set has no recipients and no sent time | A sent-items reader with an optional field list (recipients, sent time, conversation id), for every signed-in Outlook account, in memory only. |
| Hiring and interview context | Interview and Job Ledger readers exist inside their own packages | MegIBOW may not import them (layering). An adapter in `lifeos/sources/` hands plain data across. |
| A table in Notion | None: `report_region.replace_text` replaces text blocks and leaves tables alone | A bounded table writer, and a page-level proof that everything around the block is unchanged (shared primitive P5). |
| Week windows and history | None | New, in the package. |
| Closed-week storage | `snapshot_store.Store` can save and load one key, but cannot list a range | A small table (section 5). |
| A Notion database for review items | V7 already upserts rows into a Notion database and checks its property types (Amazon Orders), so rows with a dropdown are a proven pattern | A schema check for the review database; Jim creates the database and puts a view of it beside the block. |
| Alerts | Layer exists; Jobs and others share state | P2 first (roadmap). |

## 4. Architecture and placement

```text
Gmail + Outlook sent mail (metadata)   Google Calendar (native events)   Outlook calendars (originals, with attendees)
          \                              |                                    /
           +------------- evidence adapters (platform clients, read only) ---+
                                         |
   Interview rounds, Job Ledger companies (via lifeos/sources/megibow.py) ---+
                                         v
   classify (pure): relevance -> activity -> contact type -> confidence  =>  COUNTED | EXCLUDED | REVIEW | DEGRADED
                                         v
   overrides (Jim's dropdown choices, hashed keys)  ->  project (pure): current week recomputed; closed weeks read from the weeks table
                                         v
   warnings (pure)  ->  render (pure)  ->  bounded Notion block inside "Jim Organizer": table, up to 3 warnings, review count, last refreshed;
                                          review rows go to the small Notion review database beside it
```

- Package `lifeos/megibow/` (windows, classify, project, warnings, render, overrides) imports `platform` only. The adapter that reads Interview and Job Ledger context sits in `lifeos/sources/megibow.py`.
- One stage in `lifeos/run.py`, run as a `megibow` job in `domains.yml` after each hourly tick (a new `DOMAIN_SECRETS` entry). No new scheduler, no new `hourly.yml` input.
- No model and no ChatGPT at runtime (D54). Everything is rules over structured fields and fixed keyword lists.
- Output is counts and fixed codes only. The only place a person's or company's name appears is the private Notion page, as with the other cards.

## 5. State: two tiny tables, and one small Notion database

The live page forbids a "standalone activity database or ledger" and allows "minimal correction state". These two Hostinger tables hold counts and decisions, never messages or events:

- `v7_megibow_weeks`: `week_start` (key), the five counts, a `trustworthy` flag, `frozen_at`, a short `note` for a corrected week, and one `LEGACY` row holding the five cumulative totals carried over from the old sheet. It is what the Cumulative column and the four-week trend rule read, and it is why a hand edit to the Notion table can never change history.
- `v7_megibow_overrides`: `kind` (`ACTIVITY` or `CONTACT`), `key_hash` (a hash of the message, meeting or contact identity, never an address), `decision`, `decided_at`. Written when V7 reads Jim's dropdown choice. A `CONTACT` row is the "reuse a resolved classification" the page allows.

Both are a Hostinger schema change, so Jim approves them at MEG-3. Option without any table: keep closed weeks only in the Notion table block and read them back; I do not recommend it, because one stray edit silently rewrites history.

The review database (section 6) is the input form only: it holds open items and recent decisions, and V7 archives each row 14 days after it is settled. The durable memory of a decision is the overrides table, not the database.

The open week is never stored: it is recomputed from the sources on every run, as the page requires.

## 6. The review list, and how Jim answers it

**The problem, in plain words.** Some items V7 cannot settle alone ("was this 30-minute call with a recruiter or a hiring manager?"). The live page shows them as buttons. A GitHub job cannot receive a click. All V7 can do is **look at a Notion page again on its next run and see what Jim changed.** So the question is only where Jim makes the change. Jim chose a small database with a dropdown.

**How it works (Option C).**
1. A small Notion database, "MegIBOW Review", sits inside the dashboard page next to the MegIBOW block. Jim creates it once (V7 never creates structure) with the properties below and adds a view of it under the table in Jim Organizer, so the items appear in the same place he and Matt look.
2. When V7 cannot settle an item it adds one row: the date, the person or company, what it might be (Outreach, Scheduled, or a call), and why it is unsure. Its **Resolution** dropdown starts empty.
3. Jim picks one: Count as Outreach, Count as Scheduled, Count as Networking Call, Count as Recruiter Call, Count as Company Call, Exclude, or Defer. That is the page's own list.
4. On the next run (within the hour) V7 reads the dropdown, writes the decision to its overrides table (so the same person is remembered next time), recounts the week, marks the row Applied, and archives it 14 days later. An item left alone stays open and never counts.

Properties: **Item** (title, written by V7), **Week of** (date), **Candidate** (select: Outreach, Scheduled, Call), **Why uncertain** (select of the page's triggers), **Person or company** (text), **Resolution** (select, Jim's), **Status** (select: Open, Applied, Superseded; V7's), **Source key** (text, a hash, V7's). Optional later: a relation to the Job Ledger or Hiring Pipeline row, so an item can sit next to its opportunity; it is not needed for the first version.

**Is the database "associated how it needs to be"?** Yes, in the ways that matter, with one limit: V7 can create and read rows and read the dropdown (the Amazon Orders code does the same kind of thing), and the database inherits access from the dashboard page the V7 integration is already shared with. What V7 cannot do is create the view that places it under the table, so Jim adds that once in Notion. If he cannot place it there, **the fallback is Option A**: each item becomes a line with tick-boxes inside the MegIBOW block (Company, Recruiter, Networking, Did not happen, Not sure), which V7 reads back the same way. The decision is made at MEG-3 with the block in front of him.

The page says V7 should not build "a second canonical activity database". This one holds only unsettled items and recent decisions, which is the "minimal correction state" the page allows, and it is the input form, not a ledger.

## 7. Weekly rhythm and the Wednesday guarantee

- **Monday:** the first successful run after Sunday ends in Chicago freezes the finished week into `v7_megibow_weeks` (verified by read-back), then opens the new current week. If the freeze fails the week is not closed and the block says DEGRADED until it is.
- **Tuesday to Sunday:** every tick recomputes the open week and writes only if the block's content changed (so most ticks write nothing).
- **Wednesday, before the 3 PM CT meeting with Matt.** Hourly ticks are not exact, so the guarantee is built from margins:
  - the `megibow` job forces a full refresh (no "nothing changed, skip") on every tick between 11 AM and 2 PM Chicago time, and the target is a good refresh completed by **2:00 PM**, an hour of margin;
  - if no good refresh has completed since 12:00 PM by the **1:30 PM** tick, V7 pushes an alert to Jim's phone (the existing alert channel), and pushes a second one at **2:30 PM** if it is still not fresh, so there is time to react before 3;
  - the block always shows `Last refreshed <time>`, and a failed or stale refresh shows DEGRADED rather than old numbers presented as current.
  The alerts need the shared alert fix first (roadmap P2). Until it lands, the visible timestamp and the DEGRADED label carry the guarantee, and a manual refresh can be run before the meeting.
- **Degraded evidence:** a failed Gmail, Outlook or Calendar read, or a block V7 cannot find, never produces zeros or "pipeline healthy". It leaves the last good block in place and says DEGRADED (the page's rule).

## 8. Migration: fresh start with a legacy total

Jim agreed to start the Notion table fresh at a cut-over Monday and seed the Cumulative column from the sheet's totals.
- The first V7 week is the Monday of go-live. Earlier week columns show a dash, not a zero.
- **The legacy totals are entered once, in the cloud, without V7 touching Drive.** ChatGPT, which has Drive access, reads the old sheet's five cumulative figures and writes them into a "Legacy totals" line in the MegIBOW block (or Jim types them there); at cut-over V7 reads that line once into the `LEGACY` row and then ignores it. V7 has no Drive client. The numbers are private, so they never appear in the repository.
- Cumulative = legacy row + every frozen week + the open week.
- The sheet's weekly columns are labelled by sync dates (Wednesday to Thursday), not Monday-to-Sunday weeks, and no event-level data exists, so old weeks cannot be re-bucketed. The sheet stays as a read-only archive.
- Trend warnings stay off until four trustworthy completed weeks exist (the page's rule), which is about a month after cut-over. Evidence and forward-pipeline warnings work from week one.

## 9. How each measure is decided without a model

The page's four steps (relevance, activity type, contact type, confidence) are kept. With no language model, "relevance" rests on structured evidence in tiers; the census slices (MEG-1a, 1b) measure how often each tier fires on Jim's real data before any rule is final.

- **Strong, counts automatically:** the contact or thread is already tied to a job-search record (a company in the Job Ledger or the Hiring Pipeline, an Interview round, a person Jim previously settled); or the calendar event's title or linked context names an interview with an external attendee at such a company.
- **Plausible, goes to the review list:** an external human, role or interview wording in the subject or body, but no tie to a known record.
- **Out, never listed:** automated senders, job-board and application acknowledgments, newsletters and bulk mail, self-sends, Jim only copied, and ordinary work: mail and meetings with the consulting client, from the work Outlook account or any other (kept out by the page's "ordinary work/client/consulting" rule, applied through an excluded-domain list in private configuration, never in the repository). Every connected mailbox and calendar is read, but a work item counts only when it is tied to a job-search record.
- **Expect more review items at first.** Outreach classification is the weakest part to automate without a model. Every settled contact is reused, so the list should shrink week by week. If the census shows the list would be unmanageable, the fallback is a narrower definition of "known contact", not guessing.
- **Occurrence of a completed call (the page's strong-evidence list):** counted automatically only when the event stayed accepted and not cancelled through its end **and** there is corroboration V7 can see: Jim sent that attendee a message within three days after the event, or an Interview round for it is recorded as done, or a meeting note or transcript exists (later, from the meeting pipeline). Without corroboration it is `Review needed - occurrence`.
- **Scheduled** is stateless: a qualifying event whose creation time falls in the week, keyed by its iCalendar UID (series master for recurring). No stored list of seen meetings is needed.
- **De-duplication:** Outreach by message id; Scheduled by meeting UID; calls by event occurrence. Gmail scheduling mail and the matching calendar event are one Scheduled.

## 10. Roadmap

| Slice | Outcome | Likely files | Schema | Tests and UAT | Depends on | Size |
|---|---|---|---|---|---|---|
| **MEG-1a Calendar census (dry run)** | Week windows (Monday to Sunday, Chicago, eight weeks) plus counts only from the real calendars (Google, and the Outlook originals of every signed-in account): events with an external attendee, with a usable creation time, native versus bridged versus Outlook original, cancelled, declined, all-day, recurring, passed versus future, and the next 14 days. By count it shows whether Scheduled and the call rules are computable from today's data. | `lifeos/megibow/{windows,census}.py`, an optional fields argument on `Outlook.events`, one `run.py` line, a `megibow` job in `domains.yml` (dry run) | none | window edge cases (week boundary, daylight saving, rollover Monday), census counts on fakes, no names in output | Jim's go (this document) | S to M |
| **MEG-1b Sent-mail census (dry run)** | Counts of Jim's sent mail (Gmail and every Outlook account) in the window by bucket: human or automated, external recipient, in a known-record tier, role wording, excluded domain. Calibrates section 9 before any rule is locked. | `lifeos/megibow/mailcensus.py`, a Gmail metadata reader and an Outlook sent-items reader (shared mail record P1 only if Recruiters has landed) | none | counts only, no body kept | MEG-1a | M |
| **MEG-2 Classify and project (dry run)** | The full pure engine: relevance, activity, contact type, occurrence, de-duplication, weekly projection, with COUNTED, EXCLUDED, REVIEW and DEGRADED outcomes; a dry run prints the table and the review count, no Notion write. | `lifeos/megibow/{classify,project}.py`, `lifeos/sources/megibow.py` | none | table-driven rules for every row of sections 2 and 9 and for the page's scenarios 1 to 9 | MEG-1a, MEG-1b | L |
| **MEG-3 Block, review database, overrides and weeks** | The table appears in Jim's section; review rows appear in the review database and Jim's dropdown choices are applied; settled items are remembered; closed weeks are stored. Jim creates the block and the database once. | `lifeos/megibow/{render,overrides,store,card,review}.py`, a bounded table writer and page digest in `platform` (P5), router entry, `domains.yml` job, `DOMAIN_SECRETS` entry | `v7_megibow_weeks`, `v7_megibow_overrides` | owned block replaced and nothing around it touched, read-back, no-op when unchanged, block-not-found is DEGRADED and writes nothing, a dropdown choice is read back and applied once, review rows archived 14 days after settling, schema check on the review database | MEG-2, Jim's table approval, the block, the review database and a Notion integration shared with the dashboard | L |
| **MEG-4 Rollover, freeze, legacy** | Monday freeze with verification, the legacy cumulative row, "Historical review suggested" for late evidence (Jim approves any change to a frozen week). | `lifeos/megibow/{rollover,legacy}.py` (the one-time read of the legacy line in the block) | none new | the page's scenarios 13 to 15; failed freeze is DEGRADED | MEG-3 | M |
| **MEG-5 Warnings** | The page's v1 rules exactly: baseline, precedence, at most three, one suggestion each, quiet healthy line, trend rules off until four trustworthy weeks. | `lifeos/megibow/warnings.py` | none | the page's scenario 10 plus each threshold edge | MEG-3 | M |
| **MEG-6 Wednesday guarantee and acceptance** | The Wednesday forced refresh and the 1:30 PM and 2:30 PM alerts, the sixteen-scenario audit on the real dashboard, and a later occurrence source (meeting notes or transcripts) once the meeting pipeline exists. | `lifeos/megibow/alerts.py` | none | scenarios 11, 12 and 16 live | MEG-3 and the alert fix (P2) | S to M |

One PR per slice; the first four steps of the roadmap run these census slices early because they cost nothing and decide whether the rules are computable.

## 11. Approvals (asked when the slice starts)

- **Hostinger:** the two tables in section 5 (MEG-3).
- **Notion:** the bounded MegIBOW block and the MegIBOW Review database, both created by Jim inside the dashboard page (V7 never creates a missing block), and the dashboard page shared with the Notion integration V7 uses. The Jira accountability view may also write to this page (to confirm), so the page-level protection check must cover it.
- **Secrets:** the dashboard block id, the review database id, a token if the existing one is not shared with that page, a private list of excluded domains.
- **Workflow and tests:** a `domains.yml` job, a `DOMAIN_SECRETS` entry, a router entry, an optional field list on the Outlook events and messages calls.
- **Other repository:** the scheduler wording, **made** in `life-os-automation` on the task branch (production contract 2.15.12); it takes effect when Jim merges it.
- **Hiring Pipeline and Network Intelligence** are inputs, not blockers: MEG-2 works with the Job Ledger and Interview data alone and gets better as they land.

## 12. Decisions: all settled, one thing for Jim later

1. Company Call, recruiter, mailboxes, storage, review input, migration, Wednesday timing and promotion: settled (the list at the top).
2. **Tentative, declined, all-day, recurring, cancelled:** the defaults in section 2 stand unless Jim changes one.
3. **Later, at MEG-3:** create the block and the review database, and place a view of the database under the table. If the view cannot go there, say so and the tick-box fallback is used.
4. **Later, at MEG-4:** the five legacy totals go into the MegIBOW block once (ChatGPT reads them from the old sheet, or you type them).

## 13. Corrections to the Notion page: made

All made on October 4, 2026 and read back: the status line; the Recruiter and Company Call definitions, precedence, Step 4, the review trigger and scenarios 4 and 5 (which also resolves the earlier ambiguity between Step 4 and scenario 5); Gmail, Outlook and all connected calendars as evidence; the review database as the input surface; the Wednesday target; V7's tick in place of the "Daily Runs" runtime sentence; a promotion record; and the Product Backlog row (`CAREER-OPS1`) now reads active for Megibow. Nothing else on either page changed.

## 14. First build slice

```text
FIRST BUILD SLICE:
MEG-1a calendar census (dry run, counts only)

WHY FIRST:
No schema, no secret beyond the existing Google and Outlook access, and it answers the question every later rule depends on: how many of Jim's real meetings (Google and every Outlook account) carry an external attendee and a creation time, and how many are bridged copies that carry neither.

EXPECTED FILES:
lifeos/megibow/__init__.py, windows.py, census.py; an optional fields argument on Outlook.events; one line in lifeos/run.py; tests/megibow/*; a docs/SETUP.md section

SCHEMA:
NONE

ACCEPTANCE:
Week-window tests pass (Monday boundary, Chicago, daylight-saving shift, eight-week span); the census prints counts only (no title, attendee or address anywhere); a bridged copy is counted as bridged and never as a Scheduled candidate; the full suite passes.

DO NOT BUILD YET:
Classification, the Notion block, the tables, warnings, the sent-mail reader, rollover.
```
