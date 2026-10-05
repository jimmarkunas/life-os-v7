# Handoff: the big-features scoping, for the implementing Claude Code session

Status: **final as of October 5, 2026**, after Jim settled the open decisions. Written at Jim's request to hand the scoping to another Claude Code chat. This is an index and a ledger, not a new design: every design lives in the scope documents listed below. Where a decision is Jim's and still open, this file says so and the implementer must not guess.

## 1. Where everything is

- Repository `jimmarkunas/life-os-v7` (active implementation; `AGENTS.md` says so). `life-os-automation` (V1) and `life-os-v2` are read-only references; the V1 contract was edited once, on explicit instruction (below).
- **All scoping documents are on the branch `claude/multi-carrier-delivery-tracking-lpaytv`, not on `main`.** Check it out to read them: `git fetch origin claude/multi-carrier-delivery-tracking-lpaytv && git switch claude/multi-carrier-delivery-tracking-lpaytv`. No pull request is open for the scoping branch itself (the inspector has its own, above).
- The inspector is **merged into `main`** (pull request 148, commit `9d38274`, October 5, 2026; decision D132 there). Its earlier copy is in commit `baca90f` of the scoping branch; ignore that copy and work from `main`. No scoping pull request is open: the scoping branch itself has none.
- The original briefs Jim attached (Recruiters, MegIBOW, Network Intelligence, Hiring Pipeline, Physical Mail, MegIBOW context, Bytalos cockpit, OpenClaw and Meeting roadmap update) are **not in the repository**; Jim has them. The scope documents already fold them in.

Read in this order:
1. `docs/BIG_FEATURES_ROADMAP.md`: the integrated roadmap, per-feature scope, sequencing, approvals, decisions, and what was not verified.
2. The deep scope for the feature you are building: `docs/MEGIBOW_SCOPE.md`, `docs/NETWORK_INTELLIGENCE_SCOPE.md`, `docs/DELIVERY_TRACKING_SCOPE.md`, `docs/EDGE_GATEWAY_SCOPE.md`, `docs/SCHEDULER_AUTHORITY_DELTA.md`.
3. `AGENTS.md`, `docs/PRIVACY.md`, `docs/DECISIONS_INDEX.md` (grep `docs/DECISIONS.md`, never read it whole), `docs/SETUP.md`.

## 2. Rules the implementer must keep

- Tests: `python -m unittest discover -s tests -t .` must print `OK`. `git add` new files before running (the privacy test checks staged files). Layering is enforced: `platform` imports nothing; OS packages import `platform` only; only `lifeos/sources/` crosses OS packages.
- Public repository: no names, addresses, emails, tracking numbers, identifiers or message content in code, tests, logs, errors or docs. Output is counts and fixed codes.
- **A schema change stops work for Jim's approval** (every new Hostinger table, Notion database or secret). Dry run is the default; writes need `--live`.
- V7 must work with ChatGPT unavailable (D54). Fail closed. One writer per Daily Report region (D58). `hourly.yml` is at its 25-input limit: add none. New scheduled work is a stage in V7's tick or a `domains.yml` job with its own `DOMAIN_SECRETS` entry; **no new scheduler** (D123 on the scoping branch).
- **Never read, download or print Jim's personal data** (the connections file, mail, calendar content) while working. A sandbox safety check blocked a download of the connections file in the scoping session, correctly. Real data is measured only by counts-only runs in Jim's own cloud.
- No pull request unless Jim asks; never merge. Commit trailer: the usual Claude co-author line (no model name in it) plus the session link; copy it from earlier commits on the scoping branch.
- Jim's style: plain language, no jargon, a default he can veto in one line, and decisions printed in chat rather than buried in documents. He gets frustrated by repeated questions and by guesses; when a fact is unknown, name the one fact and how to get it.

## 3. Status per feature

| Feature | Scoped? | Can the first slice start now? | Blocked on |
|---|---|---|---|
| Delivery tracking (multi-carrier) | Yes, with the carrier research folded in | **D1 yes** (extractor and dry-run census, no schema). D0 needs credentials | D0: USPS, FedEx and UPS developer credentials from Jim (section 21 of its scope). D2 waits for the D1 census and table approval |
| MegIBOW | Yes, defined and promoted in Notion | **MEG-1a yes** (calendar census, no schema) | Later: the block and review database Jim creates (MEG-3); the five legacy totals (MEG-4); the alert fix P2 for the Wednesday alerts |
| Network Intelligence | Roster import: yes. Ongoing refresh: partly | **NET-1a inspector is merged into `main`** (workflow `network-inspect`) | Jim runs it once and reports its one counts line; the roster importer follows that result; the refresh door (needs ChatGPT's answers, section 6). **The three tables are approved** |
| Physical Mail | Yes | PR 1 now (table approved) | The Mail Alerts callout and block id and the exact heading (Jim creates the callout); the tracking stage waits for a real example |
| Hiring Pipeline | Yes | PR 1 (no mail) | Memory (one private row) and the table-format box are settled; open: waiting-state limit, one acquire step, mail evidence (PR 2) |
| Recruiters and Human Outreach | Yes, fully decided | PR 1 | Jim adds a "Needs review" checkbox column to the Recruiters database by hand; the interview-proof fallbacks and "What they want" need ChatGPT's second pass (P7), whose contract text is on hold |
| Company operating cockpit | Scoped only, by design | Slice 1 (client-delivery callout from saved snapshots) | Jim's decisions 2 to 5 in roadmap 3.7; this session did not build it |
| Edge gateway (Oracle VM, OpenClaw) | Yes | **EDGE-0 only, and only once the VM exists** | Oracle upgrade (stalled the VM); timing and access settled; three small decisions in section 6 |
| Communications and Meeting program | Notion edits applied | n/a (nothing in V7) | Design for crossing the Mac-to-GitHub boundary |
| Scheduler scopes | Done | n/a | Jim's merge of the contract branch (on hold) |

**Verdict (October 5, after Jim settled the open decisions): scoping is complete enough to start Delivery D1, MegIBOW MEG-1a, the Network import path, Recruiters PR 1, Physical Mail PR 1 and Hiring Pipeline PR 1. It is not complete for the cockpit (Jim's decisions 2 to 5), nor for the Network refresh and the edge gateway (each needs one outside fact).**

## 4. Settled by Jim (the ledger)

- **Carriers:** USPS in; he has active UPS and FedEx accounts; few tracking numbers; build order USPS, FedEx, UPS, DHL only if DHL approves a company-named account; UPS current-state cache purged 30 days after delivery is **accepted**; whether his UPS account is a shipper account is unconfirmed (check at D0).
- **MegIBOW:** automate it; a Company Call is the hiring-manager conversation; **any recruiter, including an in-house one, is a Recruiter Call**; every connected mailbox and calendar counts (Gmail, Outlook, Google), subject to the relevance rules; a good refresh must exist before the Wednesday 3 PM CT meeting with Matt; two small Hostinger tables (frozen weeks, decisions); review input is a Notion database with a dropdown (tick-boxes as fallback); fresh start at a cut-over Monday with legacy totals carried over. Promoted in Notion.
- **Network Intelligence:** the whole roster (about 5,000 connections and where each works) lives in Hostinger; everything in the cloud and automated; no local files, commands or hand-kept lists; ChatGPT keeps data fresh (its connected LinkedIn app and Drive access); **V7 does not touch Drive**; no small caps; company aliases learned from a "same company?" tick; no correction command; the fastest import is the CSV already attached to the Notion page "LI Connection Database & Integration".
- **Hiring Pipeline:** V7 fills the Hiring Pipeline Daily Report box; ChatGPT may add commentary elsewhere. Memory between runs is one private snapshot row in Hostinger (table approved at the start of that slice). The box is a table, so a table writer and page-level digest are built once in `platform`, by whichever slice needs it first.
- **Network tables:** `v7_network_people`, `v7_network_positions`, `v7_network_batches` are approved (other tables come slice by slice).
- **Parallel work:** two tracks, one open pull request each: mail and delivery (Delivery, Physical Mail, Recruiters, Hiring Pipeline) and job-search intelligence (MegIBOW, Network Intelligence).
- **Daily Report boxes:** V7 fills Deliveries and Mail Alerts as well as Hiring Pipeline (router change); Recruiters stays a Notion-native linked view.
- **Tracking numbers:** may be stored in the private database only (V1 contract wording still conflicts; the clarifying edit is on hold).
- **ChatGPT second pass (P7):** V7 writes the deterministic row; ChatGPT fills language-dependent cells on its own run; V7 never overwrites or waits (roadmap section 2, P7).
- **Recruiters (roadmap 3.1), all six decided:** scan Inbox and Junk of every connected account, last 14 days; job-board relays count when a human name shows (one fixed parser per board, found by a counts-only census); "What they want" is blank from V7 and filled by ChatGPT; unclear identity becomes a needs-review row (needs the "Needs review" checkbox Jim adds); hiring-manager interview proof is a calendar event by default, mail wording confirmed by ChatGPT second, Jim's own mark third (his mark always wins); a later message never reopens a Done row, a new week gets a new row.
- **Physical Mail:** starting point is option A, 11 items waiting as of October 4, 2026 (keep the number out of code; it goes in the state row); the one table is approved; items waiting over 30 days show "check the portal" (display only).
- **Scheduler:** each scheduler is sole within its own scope, no third ever, one owner per Daily Report region. The edit was made in `life-os-automation` (production contract 2.15.12) on the branch `claude/multi-carrier-delivery-tracking-lpaytv` there, commit `2fa8eb6`. **Merge is on hold.**
- **Edge:** the Oracle account is being upgraded to Pay As You Go (that stalled the VM); a backlog line was added under `OPENCLAW-0`; start EDGE-0 once the VM exists and hold EDGE-1 until Jim says "prioritize OPENCLAW-0"; close port 22 and use Tailscale, with the SSH key in a GitHub Environment he approves.
- **Cockpit:** scope only; the implementer is another Claude or Codex session.
- **Notion edits already made** (read back at the time): Product Backlog and Communications canon updates for the OpenClaw and Meeting roadmap; sections 22 and 23 of the Communications canon aligned; the MegIBOW page promoted with Jim's definition decisions; Product Backlog row `CAREER-OPS1` marked active for MegIBOW; one line under `OPENCLAW-0`.

## 5. On hold (do not do these)

- Merging the `life-os-automation` contract branch.
- Adding the scope clause to the Notion page "LI Connection Database & Integration".
- Adding ChatGPT's "Network Lookups" module text to the production contract.
- Any other production-contract change (tracking numbers in private storage, Physical Mail's unread-state rule, the cockpit cadence). None was drafted or decided.
- Any pull request or merge on Jim's behalf.

## 6. Open decisions (Jim's; do not guess)

Settled on October 5 and no longer open: the three Network tables; two tracks; V7 fills Deliveries and Mail Alerts; tracking numbers in the private database; all six Recruiters decisions; Hiring memory and table format; Physical Mail's table and aged-item note; the edge timing and access.

- **Network:** whether `REVIEW` jobs count as triggers (default: no); the refresh door (A direct, B Notion file drop, C Drive vetoed), which turns on ChatGPT's answers to the prompt in `NETWORK_INTELLIGENCE_SCOPE.md` section N; erasure and retention.
- **Hiring Pipeline (3.4):** the source of accepted mail evidence (recommended: none in PR 1, ChatGPT second pass in PR 2); a time limit for waiting states and whether a Retired page means closed; one acquire step or one per secret.
- **Physical Mail (3.5):** where Jim sees the tracking number today (one sanitized example; is a portal read approved or denied); the Mail Alerts callout, its block id and exact heading (Jim supplies when he creates the callout).
- **Cockpit (3.7):** cadence (default: acquire on every tick), neutral titles, Notion access, callout structure, Jira lens, decision markers, calendar approach, mailbox label.
- **Edge (`EDGE_GATEWAY_SCOPE.md` section 9):** a read-only Notion integration for EDGE-1 (recommended), no model in EDGE-1 (recommended), the health approach.
- **Cross-cutting (roadmap 6):** correct the stale "V2 is active" statement in the V1 and V2 `CLAUDE.md` files; creating the shared mail record (P1) in the first slice that needs both providers and the alert active-set fix (P2) before any new detector.
- **Delivery:** the aggregator fallback (default: no); the closure thresholds; which Outlook accounts feed it.

## 7. Jim's own to-dos (not the implementer's)

- Network: share the Notion page with the Jobs integration, add the secret `NETWORK_HANDOFF_PAGE_ID` (already added, per Jim), run the `network-inspect` workflow from the Actions tab (Run workflow, no inputs) and report its one line of counts, then run ChatGPT's capability prompt.
- Recruiters: add a checkbox column named "Needs review" to the Recruiters database (V7 never changes a Notion schema).
- Physical Mail: create the Mail Alerts callout on the Daily Report and give the implementer its heading and block id.
- Edge: finish the Oracle upgrade and launch the VM.
- MegIBOW (later): create the block and review database; enter the legacy totals once.
- Delivery (at D0): create the USPS, FedEx and UPS developer credentials.

## 8. Recommended order, and the traps already found

Start with the slices that need nothing more from Jim: **Delivery D1** and **MegIBOW MEG-1a**; **NET-1a** is built and merged (waiting only for Jim's first run). Settled decisions have also unblocked **Recruiters PR 1** (after Jim adds the "Needs review" column), **Physical Mail PR 1** (table approved; the callout is Jim's) and **Hiring Pipeline PR 1** (no mail). Keep to the two tracks: one open pull request for mail and delivery, one for job-search intelligence. The Network importer follows the inspector's first real run (the tables are already approved); then the rest in roadmap section 4.

Traps:
- **`main` has moved, and the inspector's decision number is now settled there.** The scoping branch was cut from an older `main`. Its D123 (scheduler scopes) clashes with `main`'s D123 (already used); renumber it to the next free number after D132 (D133 at the time of writing). Its D124 (the inspector) is the same decision `main` recorded as D132, so **drop D124 from the scoping docs rather than renumbering it**. Never drop any other entry; check `docs/DECISIONS_INDEX.md` on the latest `main` before choosing numbers.
- **The inspector needs no rebuild.** Do not copy files from `baca90f`; `main` already carries `lifeos/network`, `tests/network`, `.github/workflows/network.yml` and the `network-inspect` stage.
- **Manual workflows run only from the default branch.** The `network-inspect` workflow is now on `main`, so Jim can run it; any new manual workflow must reach `main` before it can be run.
- **The real connections file has never been read.** Its shape (blanks, duplicates, odd company values) is unmeasured until the inspector runs in Jim's cloud. Plan for commas and emoji in names, employer values that are the person's own name or "freelance", and rows with no profile address.
- **`life-os-automation`:** its own static check (`scripts/execution_constitution_check.py`) already failed before the scheduler edit (an artifact upload in an unrelated workflow); do not mistake it for a regression.
- **Bridged Outlook events on Google carry no attendees, and their creation time is the bridge's copy time.** MegIBOW reads the Outlook originals and keys meetings by iCalendar UID.
- **Oracle facts are second-hand:** the sandbox could not reach Oracle's or Telegram's pages. Verify the free-tier allowance, idle rule and Chicago capacity on the live pages before EDGE-0.

## 9. Paste-ready prompt for the other session

> You are the implementing Claude Code session for `jimmarkunas/life-os-v7`. Scoping is done and lives on the branch `claude/multi-carrier-delivery-tracking-lpaytv` (not `main`). Read `docs/HANDOFF_BIG_FEATURES.md` on that branch first, then `docs/BIG_FEATURES_ROADMAP.md`, `AGENTS.md` and `docs/PRIVACY.md`. Follow the handoff's rules: tests must print OK, public-repository privacy, stop for Jim on any schema change, no new scheduler, no pull request or merge unless Jim asks, and never read or print Jim's personal data. Do not guess any decision listed as open or on hold. Start with the slices that need nothing more from Jim, in this order: Delivery D1 (extractor and dry-run census), MegIBOW MEG-1a (calendar census); the Network inspector is already merged into `main` (do not rebuild or change it; Jim runs it and reports its counts, and the roster importer waits for that result). Recruiters PR 1, Physical Mail PR 1 and Hiring Pipeline PR 1 are unblocked by Jim's October 5 answers (ledger in section 4), except that Recruiters needs Jim's "Needs review" column first; keep one open pull request per track (mail and delivery; job-search intelligence). Report counts only, say what you could not verify, and ask Jim one plain question at a time.
