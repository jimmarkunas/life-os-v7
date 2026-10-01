# Phase 2 - fit scoring (deterministic, no LLM)

Code: `lifeos/jobs/fit/` (generic, public). Personal evidence, committed corrections, role families and private
exclusions are injected at run time from the `FIT_PROFILE_JSON` secret (shape: `lifeos/jobs/fit/profile.py`) and are never
committed (D16, D19). The score is a pure function of the job description and the profile: the same input always gives the same line.

## Flow
0. **Hard exclusions** -> `[0%] No-Go | Excluded: <reason>`. Generic defaults (clinical, healthcare, security clearance,
   federal/DoD) plus the profile's own rules. Hard when in the title or company, in the first 600 characters, strict
   (clearance), or repeated; one deep mention is noted as soft only.
1. **Extract** requirements from the description into four weighted buckets: Required Skills 40, Platform/Stack 25,
   Role/Title 20, Seniority/Scope 15. The description and requirements carry the weight; the title is one item at half weight.
2. **Classify** each requirement against the profile: Direct 100, Adjacent 75, Method-equivalent 50, Unsupported 0.
   Committed corrections are always Direct. Baseline soft skills count Direct.
3. **Arithmetic:** `bucket = 1 - sum(w * penalty * (1 - value)) / sum(w)`; total = weighted mean over the buckets the posting exercises.
4. **Inflated or optional** requirements (years beyond a technology's age, 15+ years, a PhD for a PM role, preferred or
   "nice to have", the tail of a 12+ platform list) carry half the penalty.
5. **Output** `[XX%] Go/No-Go | Strengths: ... | Gaps: ...`; Go at 76% or higher. Stored in `v7_job_fit` with the bucket
   scores, a per-requirement trace, confidence (high/low), model version, profile hash.
6. On Go: resume tailoring (separate, later).

## Pipeline
`fit` runs after `enrich` and before `publish`. A job is re-scored when the model, the profile or the description changes.
`publish` writes **LIFE OS Fit** and **Why It Fits** to the Ledger. Shadow mode by default; set repo variable
`V7_FIT_GATE=true` and No-Go jobs become `EXCLUDED_FIT` and only Go jobs publish. A missing profile makes the stage a no-op.
The provider's own match score is evidence only and never enters the arithmetic (D9).

## Known limits (calibrate before turning the gate on)
Recall depends on the profile's term lists: a requirement the profile does not name is Unsupported. Low-confidence
scores (thin description, few recognised requirements) are flagged. Calibrate against the user's own past Go/No-Go calls.
