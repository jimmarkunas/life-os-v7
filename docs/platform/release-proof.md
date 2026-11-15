# Release proof: a proof run after every deploy (LIFE-693)

Stability 6 of 7 (epic LIFE-686). A Close release that is green on CI can still break the real tick. This makes every deploy answer for itself within the hour, pages once if it fails, and holds further releases until a proof passes. There is **no automatic rollback**: the alert is the action.

## Map (what exists, read before building)

| Piece | Where | What it means here |
| --- | --- | --- |
| A deploy | `lifeos-close-release.timer` (every 15 min) runs the root-owned `install-close.sh auto`, which stages main's green head and switches `/opt/lifeos-close/current` | A deploy is a **new commit in `/opt/lifeos-close/current/MANIFEST`** (`commit <40 hex>`, world-readable, the deployed-version proof from LIFE-528). The previous tree's commit is in `/opt/lifeos-close/previous/MANIFEST`. |
| Benchmark mode | Removed by LIFE-645 | The ticket's "benchmark mode / frozen replay" no longer exists. The proof is the dry **quick** run of `hourly.yml`: `workflow_dispatch` inputs `quick=true`, `live=false` (the tick path with small caps, writes nothing). Both inputs already exist, so **no workflow file changes**. |
| The ticker watch | `lifeos-ticker-watch` on lifeos-edge-01 (:05/:20/:35/:50) | Already holds GitHub Actions read/write and Issues read/write. It is the one place that can see the deployed commit and start a run, so the proof lives there. The release timer holds no GitHub token and stays that way. |
| State | GitHub issues | The platform keeps no store. The record is one issue labelled `release-proof`; the pause flag is an open issue labelled `release-paused`. |
| Alerts | `lifeos/platform/alerts.py`, registry `alert_registry.py` | One PAGE (`release_proof_failed`) and one INFO (`release_proof_recovered`), both with a state key, both in `docs/platform/health-model.md`. |

## Flow

1. Each watch reads the deployed commit. If no proof exists for it, the watch starts **one** proof run and writes a `pending` line in the record, with the previous good commit.
2. A later watch finds that run (the oldest unclaimed manual run created at or after the dispatch) and records `pass` or `fail`, with the finish time and the run id.
3. **Pass:** the line says `pass` with the commit. Nothing else happens, no alert. The Daily Report can read the newest `pass` line: "deployed X, proof passed at h:mm".
4. **Fail** (the run failed, never appeared, or was unfinished 90 minutes after dispatch): ONE PAGE through the gateway, and the pause flag issue opens. The page names the deployed commit, the previous good commit, the failing stage's fixed code (`STAGE/CODE` read from the run's `<STAGE> FAILED: <code>` line; `RELEASE_PROOF_FAILED` if the log is unreadable; `RELEASE_PROOF_TIMED_OUT` / `RELEASE_PROOF_RUN_NOT_FOUND` otherwise) and the revert line.
5. **While paused:** no second page. A new proof run starts at most every 60 minutes against what is deployed, so the pause cannot deadlock. The first pass closes the flag and sends one `release_proof_recovered` push.
6. **Two deploys within 10 minutes:** each deployed commit gets its own line. If the first proof had not finished, its line becomes `superseded` with `note=superseded_by_<12 hex>`, and the second commit's proof decides.

## Reading and clearing the alert (no commands)

The page title reads `Release proof failed: deployed <12 hex>, previous good <12 hex>`. Its body carries the code and the revert line, for example `git revert --no-edit <previous>..<deployed>`: ask the coding session to open that revert as a PR to main, or fix forward. Either way the next release is blocked until a proof run passes. To clear it:

* Do nothing: the watch re-proofs hourly; the first passing proof closes the `release-paused` issue itself and sends one INFO push.
* Or close the `release-paused` issue by hand (lifts the pause without a pass; the next watch still records the real proof result next to it).

Nothing here rolls back, redeploys, or edits the host.

## What was built (all in the repo, none protected)

* `lifeos/platform/release_proof.py`: the pure decision rule, the record format (one line per deploy: `v1 <commit> <status> dispatched=… finished=… run=… previous=… code=… note=…`), the alert text, the revert line, the log-code reader.
* `lifeos/edge/ticker/ticker.py` `release_proof()`: the glue, called from `watch()` (never raises, never fails the watchdog).
* Registry, `codes.json` (four fixed codes), health-model rows, module lists (`runtime.json`, `drift.py`, `install-ticker.sh`, SETUP step 37), module inventory.
* Tests: `tests/platform/test_release_proof.py` (the Done-when cases) and `tests/edge/ticker/test_ticker.py` (`ReleaseProof`).

## Protected steps (not done by this PR)

| # | Step | Files | Who |
| --- | --- | --- | --- |
| 1 | Install the new ticker modules on the Oracle host between ticks (rollback named first: re-run `install-ticker.sh` from the previous main). Ticker modules changed: `ticker.py` plus `alert_registry.py`, `drift.py` and the new `release_proof.py`. Unchanged: `gate.py`, `alerts.py`, `alert_settings.py`, `redact.py`, `health.py`. | `lifeos/edge/ticker/oracle/install-ticker.sh` | Jim (host) |
| 2 | Make the release timer obey the pause flag (without it the proof records and pages, but releases do not wait). In `lifeos/edge/oracle/close_release.py` add a `paused(fetch)` that reads `https://api.github.com/repos/<repo>/issues?state=open&labels=release-paused&per_page=1` (public, unauthenticated) and make `latest()` raise `Refused("PROOF_PAUSED")` when it is non-empty; an unreadable answer must not pause (fail open, as the stall marker does). The `auto` branch of `install-close.sh` already treats a `latest` refusal as "wait". | `lifeos/edge/oracle/close_release.py` (and tests) | Protected-file PR approved by Jim |
| 3 | Copy the updated root-owned updater to the host: `install-close.sh auto-units` (one-time, Jim only: "main can change the app, never the updater"). Drift check flags the old copy until then. | `docs/SETUP.md`, `lifeos/edge/oracle/install-close.sh` | Jim (host) |
| 4 | Confirm the dry `quick` proof replaces the removed benchmark mode, so the ticket's wording changes. | Jira LIFE-693 | Jim |

No workflow file, token, permission or credential change is needed. The watchdog token stays `actions: read`. The combined workflow PR with LIFE-692's second-date step and a possible LIFE-694 edit is separate.

## Unverified until installed

* The proof dispatch and the issue labels run only on the host with its real token; the tests use fakes. The first real result appears after step 1 and the next deploy.
* A manual run waits in the tick gate for an older run (up to about 25 minutes, D210); the 90-minute allowance covers it.
