# Production Contract part 6: Centralized Job Engine

<!-- Part of docs/life-os-production-contract.md (the index). Section text moved unchanged. -->

## Centralized Job Engine

### Architectural boundary

Acquisition lanes determine how vacancies are discovered and may supply route-specific evidence. Shared `jobs/*` owns identity/dedupe, common qualification, persistence, lifecycle, Saturn, Applied handling, closure, and rendering.

Shared production components:
- `jobs/identity.py`
- `jobs/qualification.py`
- `jobs/canonical_engine.py`
- `jobs/notion_repository.py`
- `jobs/panel_config.py`
- `jobs/lane_adapter.py`
- `jobs/view_spec.py`
- `jobs/fit_scoring.py`
- `docs/job-fit-scoring-model.md`
- `config/job-engine.json`

Source absence never deletes a known-good job. Definitive vacancy closure/removal evidence is required.

### Notion query conservation and correlated job snapshot

Use the lowest-cost reliable Notion path:
1. exact fetch by known page/data-source/view ID;
2. saved view;
3. current-run GitHub Python / official Notion Public API snapshot;
4. metered MCP rows/SQL only as an exception.

An entitlement/quota failure is DEGRADED, never zero, and must not replace accepted lower-cost evidence.

For any run that may read/write Job Ledger state, write a unique trigger to `data/notion-job-snapshot-trigger.txt` and consume the Runtime-produced job snapshot. It is valid only when:
- schema >=1;
- status PASS;
- exact current trigger;
- generated_at <=30 minutes old;
- `row_count == unique_stable_job_keys` and duplicates empty;
- config proves exactly three canonical lanes: US Remote, Scale-Up, Skilled Worker, with Scale-Up enabled and Skilled Worker explicitly quarantined/disabled rather than treated as active acquisition; Newsletter and US Web are front-end acquisition paths for US Remote and must not appear as independent lanes in the Job Config;
- source IDs match canonical Job Ledger and Job Lane Configuration.

Snapshot is read-only. Invalid evidence degrades Jobs and preserves known-good state.

### Job configuration authority

`config/job-engine.json` owns immutable shared safety invariants/defaults. The Notion Job Lane Configuration panel owns approved lane controls only. Live panel values may override configured controls only through `jobs/panel_config.py` validation. Invalid, missing, or duplicate configuration is DEGRADED and authorizes no policy-changing write.

The current validated baseline keeps US Remote enabled/acquisition-enabled and keeps Scale-Up `enabled=true` and `acquisition_enabled=true` (Jim, V7 Phase 1), and keeps Skilled Worker explicitly `enabled=false`, `acquisition_enabled=false`, and `show_in_daily_report=false` while quarantined. Newsletter (Gmail/email acquisition) and US Web (public-web acquisition) are both front-end acquisition paths that write to the US Remote lane via `LANE_ALIASES` canonicalization. The canonical downstream lane is US Remote in both `config/job-engine.json` and the Job Lane Configuration Notion panel. Do not infer a lane disabled/enabled from stale history; validate current configuration for each run that depends on it.

### Stable Job Key and append-only ledger

Every distinct cold lead has one Stable Job Key. New-key preference:
1. existing Stable Job Key;
2. company + stable provider job ID;
3. canonicalized application URL stripped of common tracking parameters;
4. normalized company|role|location/work-mode fallback.

Never rewrite an existing production key merely to adopt a newer algorithm.

Current snapshot is the bulk exact-key index. Existing key → update known page. Accepted snapshot proves absence → create one row. Duplicate/ambiguous key or uncertain absence → DEGRADED; never auto-delete duplicates.

The Job Ledger is the sole persistence source for cold leads. Four job pages are filtered presentation views only.

### Qualification

Shared admission floor = 72%.

Target Company review floor = 68%; target candidates at 68–77 are Passed / Review, not admitted.

Newsletter/Curated exception: Target review band does not apply. Newsletter candidate below validated production fit floor is Excluded, never Passed / Review. Provider Score contributes zero. Re-run shared qualification whenever LIFE OS Fit changes.

Compensation:
- US: hard floor $80,000 annual equivalent only when pay is explicitly posted;
- Scale-up: no compensation floor;
- Skilled Worker Phase 2 policy: hard floor £65,000 only when explicitly posted;
- missing/undisclosed pay allowed.

Geography:
- Newsletter and US Web: US Remote only;
- Scale-up and future Skilled Worker Phase 2: London/Greater London, any work mode.

Freshness:
- Newsletter: vacancy-level when possible; no hard max-age; email receipt date never substitutes for Posting Date;
- US Web: new admission <=7 calendar days inclusive; unresolved age → Review;
- Scale-up: posting age informational for existing rows (V7 applies a 30-day new-admission age gate by Jim's decision of 2026-10-01; its LANES.md is the detailed record);
- Skilled Worker Phase 2 policy: new admission <=14 days; unresolved age → Review;
- age after admission does not remove a still-live row.

Visa routing:
- Scale-up page requires positive vacancy-specific Scale-up evidence.
- Future Skilled Worker Phase 2: verified current membership of the **actual employing legal entity** on the UK approved sponsor list is sufficient positive route evidence and sponsorship is assumed. Vacancy silence/negative wording does not override verified employer-list membership.
- An intermediary cannot use its own licence when actual client employer is unresolved.
- If both routes validate after Skilled Worker re-entry, Scale-up wins visible routing; ledger may preserve both.
- Otherwise-plausible London roles with unresolved employer/sponsor identity remain Review.

### Acquisition lanes

Newsletter uses the Newsletter section above and `docs/newsletter-job-discovery-contract.md`.

US Web uses `docs/us-remote-job-discovery-contract.md` + `config/us-remote-source-registry.json`, with the established guaranteed population of 30 Tier-1 employers + 10 staffing agencies + 3 discovery helpers, plus defensible broader gap discovery under the configured 200-new-general-web-candidate/day ceiling.

Scale-Up is ACTIVE in V7 Phase 1 (Jim; see the `NEWSLETTER + US REMOTE OPERATING OVERRIDE`). It acquires through V7's `web-scale-up` stage and other V7 sources and writes to the shared Job Ledger under the Scale-Up rules in this section. **Verification observed October 20–21, 2026 (not a standing acceptance gate):** a successful V7 hourly run executed `web-scale-up` for lane Scale-Up (2 due, 2 complete, 2 admitted, 0 failed), and the Job Ledger holds live Visible Lane Scale-up rows surfaced October 4–20 (including Admitted rows from Greenhouse sources) with Fit Authority "Authoritative" and current Updated At. Known quality gaps seen in the same sample: Public Web rows that are not vacancies (a remote-working marketing page; a visa-statistics article) reached Passed / Review, and some rows have an empty Role. **Coverage observed October 21, 2026:** the Ledger's Scale-up rows come from 10 companies, and only 8 of the registry's 36 listable boards have ever produced one (the other 28, and the 12 boards with no discoverable ATS, have none); "no row" can mean no role that passes Fit or a reader that is not working, and V7's per-board diagnostic (`diag` stage `scale-up-boards`) is the instrument that tells them apart. Jim's keep list names specific Revolut roles that must reach the board in Review whatever the model scores them (V7 D115, extended in D247). This contract records Scale-Up as running, with those gaps open; it does not certify the lane as defect-free.

Skilled Worker is quarantined — Phase 2. There is no current production acquisition, Runtime trigger/module, QA5/QA6 release prerequisite, source-driven lifecycle refresh, or current acceptance artifact requirement. Preserve existing known-good Job Ledger rows and the persistent Skilled Worker reference page. The approved Phase 2 direction is `LIFE OS Daily Runs → Google Cloud Run Jobs acquisition → non-canonical Google employer/acquisition index → existing LIFE OS Job Engine → existing Notion LIFE OS Job Ledger → existing Skilled Worker presentation surface`. Cloud Run is compute, not a scheduler; the Google Sheet is not a canonical job datastore or business-rule authority.

Target Companies retain metadata regardless of visible lane. US targets require US Remote; London targets any mode; active visa pages require positive route evidence.

### Visible lane precedence

Exactly one visible lane per newly admitted vacancy among active lanes. Current active-lane precedence is:
1. Newsletter
2. US Web

Scale-Up participates in visible routing as its own lane: a vacancy that validates for both UK routes is one job and Scale-Up is the visible lane, with both routes preserved in Eligible Lanes. Skilled Worker does not participate while quarantined; if it is re-enabled in a future authorized re-entry, the applicable precedence must be updated by that authority before activation.

While Skilled Worker is quarantined, it does not participate in new visible routing. Existing known-good Skilled Worker rows are not reclassified, closed, deleted, or expired merely because the lane is quarantined. A source/route improvement may move the same Stable Job Key upward only through an active accepted lane; never duplicate one key across visible pages.

### Saturn lifecycle and Applied

Cold one-sleep applies only to automated/newsletter, proactive web, Target Company, and active visa discovery. Direct human outreach is exempt.

- First Surfaced is immutable once set.
- Saturn Ready defaults to First Surfaced + 1 America/Chicago calendar day.
- Same-day before ready = Today.
- On ready date = Yesterday.
- After ready: blank decision remains Yesterday; unresolved Review/Apply becomes Sticky.
- Skip remains visible on decision day and later becomes Removed.
- Applied checkbox is sole canonical indication of real submission.
- When Applied is first checked and Applied On is empty, stamp once; preserve later manual edits.
- Applied rows become Lifecycle Application, leave cold-lead views, and feed Hiring Pipeline.
- Legacy `Saturn Decision=Submitted` is compatibility-only: bridge once to Applied and clear the legacy decision; never create a new Submitted decision.
- Definitive closure/removal becomes Expired/Removed; source absence alone never closes a row.

Age display:
- First Surfaced today → `Today`;
- one America/Chicago calendar day ago → `Yesterday`;
- two or more days ago → `>2 Days`.

### One-table job pages

Each production job page contains exactly one linked filtered Job Ledger table.

Default action-oriented filter for active US Web and Scale-Up, and a future re-enabled Skilled Worker:
- Visible Lane = page lane;
- Admission Status = Admitted;
- Applied = false;
- Liveness = Live OR Unknown;
- Lifecycle = Today OR Yesterday OR Sticky.

Newsletter persistent historical projection:
- Visible Lane = Newsletter;
- Admission Status = Admitted OR Passed / Review;
- LIFE OS Fit >= validated production floor;
- do not remove solely because lifecycle ages, Applied changes, or liveness changes;
- sort First Surfaced DESC, LIFE OS Fit DESC, Company ASC.

Standard visible job fields center on Applied, Today/>2 Days, Saturn Decision, LIFE OS Fit, Apply URL/Source, Role, Company, Location / Work Mode, Compensation, Posting Date, Freshness Status, Tailored Sentence, Why It Fits, and the required Notion title/Job field.

Linked views are the renderer. Executors update Job Ledger records for enabled lanes; do not rebuild static job tables. The quarantined Skilled Worker page remains a reference view over preserved canonical state and is not refreshed by source acquisition.

### Job run procedure

For each job-enabled run of an authorized executor (the retired `LIFE OS Daily Runs` is not one):
1. accept the current exact-trigger Notion Job snapshot;
2. validate Job Engine config/panel;
3. read manual Applied/Saturn/lifecycle state;
4. acquire enabled-lane observations only;
5. normalize identity by Stable Job Key;
6. compute/recompute LIFE OS Fit where owned and apply common qualification + lane route evidence;
7. merge append-only through the shared lifecycle engine;
8. create/update Notion only for changed/new exact keys;
9. verify affected writes by exact fetch or accepted saved view when practical;
10. verify no duplicate Stable Job Keys or multi-lane visible duplicates;
11. verify Newsletter exposes zero rows below the validated fit floor;
12. never delete rows merely to manufacture parity.

Quarantined Skilled Worker is skipped before acquisition and cannot be turned into an empty/closed lane from source absence.

### Passed / Review Notes

Surface plausible review/excluded candidates with exact reasons when useful, including the Target Company 68–77 band, US-not-remote, explicit US pay below floor, US Web stale/unresolved freshness, future Skilled Worker stale/unresolved freshness after re-entry, unresolved/negative visa route, definitive closure, or another exact gate. Newsletter candidates below the shared fit floor are Excluded rather than routed to Passed / Review. Missing compensation alone is never rejection; Scale-up compensation is never rejection.
