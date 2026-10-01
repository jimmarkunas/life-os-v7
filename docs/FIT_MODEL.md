# Phase 2 - fit scoring model (generic spec)

Personal evidence (career facts, engagements, committed scoring corrections, hard-exclusion specifics) is NOT stored in
this public repo. It lives in private storage and is loaded at run time. This file holds only the model's structure.

## Flow
0. Hard exclusions (configured privately) -> instant No-Go, score 0, reason, stop.
1. Extract JD requirements into four weighted buckets: Required Skills 40%, Platform/Stack 25%, Role/Title 20%,
   Seniority/Scope 15%.
2. Classify each requirement against the private evidence: Direct 100%, Adjacent 75%, Method-equivalent 50%,
   Unsupported 0%. Committed corrections (private) override a default classification.
3. bucket_score = sum(values) / (count x 100); total = 0.40 R + 0.25 P + 0.20 T + 0.15 S.
4. Inflated/aspirational requirements (unrealistic years, stacked nice-to-haves) carry ~50% less penalty.
5. Output first line: `[XX%] Go/No-Go | Strengths: ... | Gaps: ...`. Go at 76% or higher; below is No-Go with one sentence.
6. On Go: resume tailoring (separate, later).

## How it fits the pipeline
- Input is the job description in `v7_job_descriptions` (Hostinger). LinkedIn and Jobright pages expose a full JD
  without the final link, so a provisional score can be computed early and the scarce resolve effort (Search is 30
  requests/min) spent on jobs that clear the threshold. The final link is still required before publishing (D1).
- The provider's own match score is stored as evidence only (D9), never as the score.
- The scorer needs an LLM to classify requirements; it will run with an Anthropic API key held as a secret and read the
  private evidence from private storage. Decision pending: where the private evidence lives.
