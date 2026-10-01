# Lanes: US Remote, UK Scale-Up, UK Skilled Worker

One Jobs OS, three opportunity policies. Identity, professional Fit (floor **72**, `lifeos/jobs/fit`), dedupe, the canonical Job
and its lifecycle are shared. A lane adds only opportunity policy, as data (`lifeos/jobs/lanes.py`: `LanePolicy`,
`qualify()`), never as a separate code path. Unresolved evidence is REVIEW, definitive negative evidence is EXCLUDE.

| Policy | US Remote | UK Scale-Up | UK Skilled Worker |
|---|---|---|---|
| Fit floor | 72 | 72 | 72 |
| Market | US | UK | UK |
| Work mode | remote only (unknown = Review) | any | any |
| Explicit pay floor | $80,000 (missing pay allowed) | none | £65,000 (missing pay allowed) |
| New-admission age | 14 days (unknown date = Review) | 30 days (unknown date does not suppress) | 14 days (unknown date = Review) |
| Route evidence | none | Scale-up, positive required | Skilled Worker, positive required |
| Geography evidence | none | positive required | none (London policy at acquisition) |
| Status | active | built, not scheduled until promoted | disabled (Phase 2) |

Decisions (Jim, 2026-10-01): US Remote keeps 14 days (the Notion canon says 7). Scale-Up gets a 30-day age gate (the earlier canon had
none; age is still not closure evidence, a closed vacancy is excluded in every lane). A job eligible for both UK routes is one
job and Scale-Up is the visible lane. Target/Curated never changes the Fit floor.

`parse_pay` reads the posted pay field only (never the description) and compares only in the lane's own currency;
`detect_work_mode` decides from location and title, with the description only confirming explicit statements.
Tests: `tests/jobs/test_lanes.py` encodes the matrix.

## Retention (decided, not yet implemented)
30-day stale purge of canonical Jobs with a 90-day suppression tombstone (so a repost does not recreate a purged job); a job marked
Applied that has not reached an interview stage within 30 days is purged; a job that reaches an interview stage is kept.
