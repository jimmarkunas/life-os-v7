# Phase 2 - professional Fit (deterministic, no LLM)

Code: `lifeos/jobs/fit/` (generic, public). Personal evidence is injected at run time from the `FIT_PROFILE_JSON` secret
(shape: `profile.py`) and never committed (D16, D19). The live Candidate Experience & Fit Profile (Notion) is the authority;
the secret is a derived projection stamped with a hash, and a changed profile re-scores every job.

## Two layers, never mixed
1. **Professional Fit (0-100)** = title + substantive JD + candidate evidence. Never sees location, work mode, pay, visa,
   provider score, freshness (tested). Title-only or requirement-free postings are UNSCORABLE, not 0.
2. **Gates** (`exclusions.py`): hard exclusions (clinical, healthcare, security clearance, federal/DoD, plus private rules).
   A gated job keeps its real Fit score; decision is No-Go and the line says `Excluded: <reason>`.
Decision: **Go at 72 or higher** (the universal admission floor, D191) and no gate; otherwise No-Go.

## Arithmetic (V3, ported from V2 `fit_arithmetic`)
Dimensions: Role/Seniority 29 (title evidence is a separate channel, 29/4 of it), Functional 29, Technical/Platform 21,
Delivery complexity 14, Competitive advantage 7. Each requirement belongs to exactly one. Evidence: Direct 1,
Adjacent 3/4, Method-equivalent 3/5, Unsupported 0. REQUIRED counts double; an inflated requirement (15+ years, years beyond a
technology's age, a PhD for a PM role, the tail of a 12+ platform list) counts half. A dimension the posting does not activate is
not applicable. A Direct title specialization adds 3 (positive-only). Fit is capped at 40 only when the posting's actual
profession is a hard-family mismatch (title, or at least half of 3+ requirement/duty lines), never for one stray requirement.
Committed corrections are always Direct. Stored in `v7_job_fit` with dimension scores, a per-requirement trace, confidence,
model version and profile hash. `publish` writes **LIFE OS Fit** and **Why It Fits**.

## Pipeline
`fit` runs after `enrich`, before `publish`. Shadow mode by default; `V7_FIT_GATE=true` makes No-Go jobs `EXCLUDED_FIT` and
publishes only Go. Missing profile = the stage is a no-op.

## Semantic layer (shadow, local, free)
`semantic.py` + `requirements-fit.txt` (`fastembed`, a small local sentence-embedding model, cached between runs; no text leaves
the runner). It only ranks how close a requirement line is to the profile's own capability phrases; the evidence CLASS always
comes from that capability, never from the model (canon: no upgrade because a JD sounds similar). Cosine >= 0.82 keeps the
capability's class, 0.72-0.82 is one step weaker, below 0.72 is no match (fail closed, stays Unsupported). **Shadow mode:** the
real score never uses it; `v7_job_fit.shadow_score` / `shadow_changes` record what it would change and the stage log reports
counts (jobs changed, decision flips, similarity bands). If the model cannot load, the layer is off and scoring is unchanged.
Thresholds are starting values, to be tuned from the shadow counts against your Ledger decisions before it may affect a score.

## Known limit
Recall depends on the profile's term lists (a requirement the profile does not name is Unsupported). Calibrate against the
accepted outcomes and the Job Ledger before turning the gate on.

## Profile sync (Notion page -> a small table in the store)
Jim's Notion page is the authority for new evidence. `lifeos.jobs.fit.profile_sync` (stage `fit-profile-sync`, workflow `profile-sync.yml`, its own isolated job) copies the page's **approved** JSON block into the
store table `v7_fit_profile_sync` (one row). `profile.load(environ, synced=...)` then appends that block to the profile exactly as it appends `FIT_PROFILE_EXTRA_JSON` (D105); the Fit stage, the cloud backlog and the
safety audit all load it. Bitwarden is never written: the secrets stay read-only for every job. The block may only carry `capabilities` (ids starting `nsync_`) and `functions` (tagged `owner: nsync`), so it can add
evidence but never replace or remove an entry of the secret profile, and it cannot add exclusions.
The page carries one JSON code block and one paragraph that is exactly `APPROVED HASH: <16 hex>`; a block whose content hash differs is not stored, and prose that merely quotes the line is not an approval. The reader returns
the block only when the stored hash matches the block's own recomputed hash; a store that cannot be read, or a row that does not match, makes the profile unavailable for that run (the Fit stage does nothing), never a profile that
silently lost its block. Nothing synced yet, or no store configured, means no change. The log prints one fixed code (`SYNC_WRITTEN`, `SYNC_UNCHANGED`, `SYNC_NOT_APPROVED`, `SYNC_BLOCK_INVALID`, ...) and a count, never page content.
A stored change changes the profile hash, which already re-scores every job. The cloud (Oracle) brief is a separate copy and is not touched by this stage.
Inert until Jim sets the variable `FIT_PROFILE_SYNC=on`; dispatch `profile-sync` by hand (dry run unless ticked) to try it.
