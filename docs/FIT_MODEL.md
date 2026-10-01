# Phase 2 - professional Fit (deterministic, no LLM)

Code: `lifeos/jobs/fit/` (generic, public). Personal evidence is injected at run time from the `FIT_PROFILE_JSON` secret
(shape: `profile.py`) and never committed (D16, D19). The live Candidate Experience & Fit Profile (Notion) is the authority;
the secret is a derived projection stamped with a hash, and a changed profile re-scores every job.

## Two layers, never mixed
1. **Professional Fit (0-100)** = title + substantive JD + candidate evidence. Never sees location, work mode, pay, visa,
   provider score, freshness (tested). Title-only or requirement-free postings are UNSCORABLE, not 0.
2. **Gates** (`exclusions.py`): hard exclusions (clinical, healthcare, security clearance, federal/DoD, plus private rules).
   A gated job keeps its real Fit score; decision is No-Go and the line says `Excluded: <reason>`.
Decision: **Go at 72 or higher** and no gate; otherwise No-Go.

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

## Known limit
Recall depends on the profile's term lists (a requirement the profile does not name is Unsupported). Calibrate against the
accepted outcomes and the Job Ledger before turning the gate on.
