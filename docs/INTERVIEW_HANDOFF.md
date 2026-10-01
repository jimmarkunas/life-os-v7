# Interview OS in V7 — handoff for the Interview track

Read this first. It says how to build Interview OS in this repo without repeating what cost Jobs time. Product decisions (canon, page shapes, what a Brief says) belong to the Interview track;
this file covers repo mechanics and safety. Decisions: docs/DECISIONS.md (D1–D37). Jobs parity examples: `lifeos/jobs/guard.py`, `ledger.py`, `readback.py`, `audit.py`, `hiring_pipeline.py`.

## Ownership
- Interview track owns `lifeos/interview/` (new). Jobs owns `lifeos/jobs/`, `lifeos/sources/`. Shared platform lives in `lifeos/platform/` and may import nothing from either (the boundary test enforces it).
- Shared code only when a second executable consumer exists. `platform/runtime.py` (RunContext, ExecutionResult) and `platform/redact.py` already exist; the resolve stage is runtime's second consumer, Interview is the third.
- **Normalization is shared.** `lifeos/platform/names.py` is the one normalizer (British to American spellings, legal-suffix and generic-word rules, `same_company`, `same_role`, `split_title`). Interview imports it from `platform`; it must NOT write its own and must not import from `lifeos.jobs` (boundary test).
- **Notion client:** reuse `lifeos.platform.notion_client.Client(environ)` by passing an environment mapping whose `NOTION_API_TOKEN` is filled from `NOTION_INTERVIEW_TOKEN` and never falling back to the Jobs token. For the read-only probe/B1, create the Notion integration with read-only capability on the Hiring Pipeline page only; widen it for B2.
- **First machine-owned page.** Existing pages are HUMAN (no write). A page becomes MACHINE only when Interview OS creates it (B2: parent creation) or Jim adds the marker himself (explicit adoption). The marker lives in the FIRST BODY BLOCK (like Jobs' `v7-jd:1`), not a property, so existing page schemas are untouched.
- **Workflow wiring is Claude's.** `hourly.yml` already has the `interview` job (dispatch inputs `interview_probe` and `interview`, only `NOTION_INTERVIEW_TOKEN` and `HIRING_PIPELINE_PAGE_ID`, every Jobs secret blanked, step-level `continue-on-error` + a warning so an Interview failure never fails the Jobs run). Codex adds only `STAGES` entries `interview` and `interview-probe` in `lifeos/run.py` and the stage code.
- **Decision numbers:** Interview D39 to D49, Jobs D50 and up.
- Jobs reads Interview state only through `jobs/hiring_pipeline.py` (INT-7.1A): company AND role must match one ACTIVE opportunity, it can only PROTECT a Job Ledger page, and it fails closed. When Interview ships a stricter parent resolver, Jobs will switch to it; keep BLOCKED/ambiguous distinguishable from "no match" so Jobs can stay fail-closed.

## Hard rules
1. **One scheduler, one workflow.** `hourly.yml` is triggered by GitHub cron (three triggers an hour) and an outside HTTP timer (`tick=true`) through one gate (D25/D30/D31). No second cron, no Interview workflow. Add Interview as its OWN JOB in `hourly.yml` (needs: prep, `continue-on-error`, one line in the `report` job) so it never lengthens the Jobs path.
2. **Counts-only logs.** The repo and its Actions logs are public. A stage prints one line of counts and fixed reason codes. No names, subjects, URLs, meeting ids, passcodes, page ids, transcript text. Exception text goes through `redact.safe_error`. Private meeting credentials (passcode, meeting id, join token) are sensitive evidence: they may appear only on the bounded interview operating surface where a person needs them to join; never in logs, dashboards, summaries or derived context.
3. **Privacy test scans TRACKED files** (`tests/contracts/test_privacy.py`): no email addresses outside the allowlist, even in test fixtures. It passes locally on an untracked file and fails once committed (this broke `main` once). Run the full suite after `git add`, not before. Use `example.test`-style fixtures without `@`, or add to the allowlist deliberately.
4. **Every Notion mutation: target check -> human-state guard -> write -> authoritative read-back.** Copy the shape of `ledger.verify()` (required property names/types on the data source), `guard.protection()` (missing safety property = unknown = protected) and `readback.py`. A legacy or human-created page is NO WRITE until adoption is explicitly authorized (Uber case). Machine-owned regions are bounded; Raw Notes and Live Notes are never written.
5. **Use a separate Notion integration token for Interview** (`NOTION_INTERVIEW_TOKEN`), shared only on the Interview pages. Jobs' token is scoped to the Job Ledger.
6. **Fixed codes, never "zero".** An unreadable source is DEGRADED/FAILED with a code, never an empty success and never a removal (see `sources/web/lister.py`: FAILED never implies zero).
7. **Tests budget:** the whole suite runs in well under 60 seconds in the `prep` job; no network, no sleeps. Behavioural tests over private-function tests (see `tests/contracts/test_guardrails.py::AuditBehaviour`).
8. **Docs:** every decision goes into `docs/DECISIONS.md` as the next D-number in the same PR.

## The build order that worked on Jobs (do the same)
The build sandbox cannot reach Notion, Gmail, Calendar or company sites. Everything external is proven on the runner:
1. A **counts-only shape probe** (`lifeos/probe.py`, dispatch input `probe`) prints status, sizes and key NAMES only. Read the real shape first.
2. **Dry run** (`live` unticked): counts, no writes. Verify the numbers make sense.
3. **Live** with a small limit; read back; then widen.
A manual dispatch counts as a run for the gate (a scheduled/timer run within 50 minutes cancels itself, which is correct). Dispatch input labels show their DESCRIPTION text, not the input name; word descriptions for a human.

## Decide first (before INT-7.1B1 code)
**How is Derived content produced?** The platform is AI-agnostic and V7 has no model call in its runtime. Choose one and write it as a decision: (a) deterministic extraction and templates (recommended to start), (b) a local model, (c) an API model behind an adapter with its own key and cost cap. Derived content stays marked Derived and never mixes with Facts.

## Sequence (from the Interview track's plan)
INT-7.1B1 identity + protection contract -> 7.1B2 parent/child vertical slice (Smartsheet shape, no rewrite of human content) -> 7.1B3 prep and carry-forward -> 7.1C presentation convergence -> 7.1D acceptance (Smartsheet success; missing child; ambiguous parent/child; Uber legacy/orphan = preserve + reference; human-note survival; read-back). MEET-1A then plugs into the Interview identity/evidence contract.

Live fact for 7.1D from the Jobs side: the Hiring Pipeline page has 5 active opportunities, 2 with rounds, and one title that does not follow "Company - Role" (it protects nothing until renamed).

## Mechanics
- Dev branch per track; PRs to `main`, squash-merged by Jim. Do not push to `main`. Jim says "Merge PR N". After a merge, reset your branch: `git fetch origin main && git checkout -B <branch> origin/main && git push --force-with-lease -u origin <branch>`.
- A merge to `main` is live on the next run. Keep PRs small, tested, reversible.
- Add a stage by one line in `lifeos/run.py` `STAGES`; the stage is `(limit, live) -> counts`.
- Times in chat are Central (CDT), never UTC.
