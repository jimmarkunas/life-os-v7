# Health model (LIFE-690)

One place that says what pages the phone, what only shows on the Daily Report, and when an incident closes. The code is `lifeos/platform/health.py` (the rules), `lifeos/platform/gate.py` (the pipeline incidents), `lifeos/platform/alerts.py` (the one gateway) and `lifeos/platform/alert_registry.py` (every alert, its severity and channels). A contract test (`tests/contracts/test_health_model.py`) fails if an alert in the registry is missing from the table below.

## Severities

| Severity | What happens | Example |
|---|---|---|
| PAGE | A phone push (loud: ntfy, Telegram and WhatsApp copies) | the hourly runs are failing repeatedly |
| WARN | Shown on the Daily Report card; never a push, never turns a job red | the same item has failed with the same code on every run |
| INFO | A quiet push at the lowest priority (phone only) or shown only | a deploy is waiting |

The registry holds PAGE and INFO (the two kinds that push). WARN lives on cards only: it is the known-state rule below, so it has no registry key.

## Rules

1. **State key.** Every alert is raised under a state key: its registry key, or a registry family plus the item (`source_stopped_dice`). The only sender is `alerts.push`, `alerts.deliver` or the `alerts push --key` command (the alert gateway, LIFE-528). A push without a key, with an unlisted key, or built any other way is refused (`alerts.push` returns False and sends nothing; the command exits 2). `health.state_key` builds keys and refuses malformed ones.
2. **Push on a change of state.** An incident is open or recovered. A push goes out when it opens and when it recovers. While a PAGE incident is open and the newest run is still failing it may remind, never more often than every 3 hours (the pipeline incidents: after 3 quiet hours; the reconcile-based watchers: every 6 hours). An INFO incident raised by the pipeline gate or the ticker (drift, a stuck run) is pushed once and never repeated (LIFE-675). The reconcile-based INFO watchers (the job detectors and the off-site backup) still remind once every 24 hours, which Jim approved for the backup alert (LIFE-129): the ticket's strict reading would remove that reminder, so it is kept until Jim decides.
3. **Close on 3 passes.** A run-based incident closes once the last 3 finished runs passed (`health.recovered`, applied by `gate.failing` and `gate.stalled`). A single success does not close it, so a lucky run cannot flip an incident open and shut. A state of the machine (disk, drift, a marker) closes when that state is gone. One cadence exception (LIFE-692): a WEEKLY scheduled workflow (`safety.yml`) closes on ONE passing scheduled run, because three passes would take three weeks and hide a recovery; the twice-an-hour watchdog keeps the 3-pass rule (`gate.SCHEDULED_WORKFLOWS`, docs/platform/scheduled-checks.md).
4. **Permanent known state.** The same item failing with the same fixed code on 2 or more consecutive runs is a known state: WARN on the card, no push, and it never turns a job red. If it is still failing after 7 days it becomes exactly one PAGE (`known_state_expired_<family>`), so it cannot be forgotten. A new code, or the item passing, resets it. Items are opaque ids and codes are fixed codes: nothing here carries content.

## Inventory (every alert the platform can raise)

| State key | Severity | Raised by | Close rule | Meaning |
|---|---|---|---|---|
| `quiet_24h` | INFO | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | No job reached Notion in 24 hours. |
| `sources_failing` | INFO | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | Sponsor sources failed three runs in a row. |
| `stuck_jobs` | INFO | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | 25 or more jobs sat in READY or RESOLVED for over a day. |
| `unresolved_terminal` | INFO | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | Worthwhile jobs stayed unresolved after their one recovery attempt. |
| `source_stopped_*` | PAGE | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | A job source that normally brings 3+ jobs a day brought none in 24 hours. |
| `source_stuck_*` | INFO | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | Half or more of a source's last 72 hours of jobs are still NEW or HOLD. |
| `source_yield_*` | INFO | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | A source published under 5% of its last 7 days of jobs. |
| `board_reads_failing` | INFO | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | A fifth or more of two days of board reads failed (not counting boards that answer not found). |
| `search_failing` | INFO | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | A fifth or more of two days of search calls failed. |
| `hold_rising` | INFO | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | The HOLD pile grew on each of the last three days. |
| `hold_not_retried` | INFO | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | A large, growing HOLD pile with nothing touched in 24 hours. |
| `expiry_*` | PAGE | lifeos/jobs/alerts.py (alerts stage, every tick) | Detector no longer reports it; INFO is resolved silently, PAGE sends one recovered push | A credential or token expires within a week. |
| `jira_rollover_stuck` | PAGE | lifeos/jira/watch.py | Detector no longer reports it (a successful rollover); one recovered push | The Monday sprint rollover has not worked for 3 hours. |
| `jira_reconcile_failed` | INFO | lifeos/jira/reconcile_stage.py | The next daily reconcile that completes | The daily Jira sprint reconciliation failed for a board (LIFE-918; one phone push per failure code per day; phone only). |
| `v2_promotion_failed` | INFO | lifeos/accountability/promotion_call.py | Never; legacy stays official until Jim acts | The v2 quarterly promotion check failed: legacy stays official (LIFE-918; one push, phone only). |
| `calendar_sync_stuck` | PAGE | lifeos/calendar_bridge/watch.py | Detector no longer reports it (a sync happened); one recovered push | The calendar bridge has not synced for 3 hours. |
| `offsite_backup_failed` | INFO | lifeos/oraclecloud/backup_watch.py | A backup succeeded; resolved silently | The nightly off-site backup (Oracle to Drive) failed, or has not succeeded for 36 hours (LIFE-129, Jim: quiet, phone only). |
| `openclaw_workers_leaking` | INFO | lifeos/oraclecloud/worker_watch.py | Worker count is back to the agent count; resolved silently | OpenClaw has more Codex app-server workers than agents on Oracle: the duplicate-worker leak is back (LIFE-531, upstream 196499; phone only). |
| `boot_backups_full` | INFO | lifeos/oraclecloud/boot_backup_watch.py | A backup slot is free again; resolved silently | Oracle boot-volume backups are at their limit of 5: the next weekly backup of the Edge will be refused until one is deleted (LIFE-808; phone only). |
| `imessage_action` | PAGE | lifeos/imessage/stage.py | One event per message (no incident); never repeats for the same message | A message needs action: time critical goes out at once, action required waits through quiet hours (10 PM to 8 AM Central). |
| `pipeline_stalled` | PAGE | lifeos/platform/gate.py watch (Oracle ticker and GitHub watchdog); one GitHub issue | Last 3 finished hourly ticks passed (LIFE-690); closes with one pipeline_stalled_recovered | No hourly run has succeeded for 2.5 hours. |
| `pipeline_stalled_recovered` | INFO | lifeos/platform/gate.py | The recovered push of pipeline_stalled (INFO, once) | The stalled pipeline recovered. |
| `pipeline_failing` | PAGE | lifeos/platform/gate.py watch; one GitHub issue | Last 3 finished hourly ticks passed (LIFE-681); reminders only while the newest tick still failed, at most every 3 h | Three or more hourly runs failed in 24 hours. |
| `pipeline_failing_recovered` | INFO | lifeos/platform/gate.py | The recovered push of pipeline_failing (INFO, once) | No failed hourly run in 24 hours. |
| `tick_run_stuck` | INFO | lifeos/platform/gate.py watch; one GitHub issue | No hourly tick run is stuck (a run in progress, not a pass or fail: exempt from the 3-run rule); INFO is never re-paged. LIFE-691: the watchdog first asks GitHub to cancel the run; this opens only when GitHub refuses or the run is still active 20 minutes after the 40-minute limit | An hourly tick run has been queued or running for over 40 minutes (LIFE-580); later ticks still run, the run needs a look (phone only). |
| `tick_run_stuck_recovered` | INFO | lifeos/platform/gate.py | The recovered push of tick_run_stuck (INFO, once) | No hourly tick run is stuck any more. |
| `known_state_expired_*` | PAGE | lifeos/platform/health.py escalate(), called by a stage that adopted KnownStates | Once per known state at 7 days; the state clears when the item stops failing | A known failure (the same item, the same fixed code, shown as WARN and never paged) is still failing after 7 days: one page, once (LIFE-690). |
| `scheduled_red_*` | PAGE | lifeos/platform/gate.py `scheduled_checks` (GitHub watchdog and Oracle ticker); one GitHub issue per workflow, label `scheduled-red-<name>` | The workflow's last 3 finished scheduled runs passed (1 for the weekly safety check); no reminders, a second red run adds no push; one scheduled_recovered_<name> | A scheduled workflow's newest scheduled run failed: one page per workflow, none for a second red run (LIFE-692). A failed run whose own alert step already pushed (safety: `Phone alert`) is not paged twice. |
| `scheduled_recovered_*` | INFO | lifeos/platform/gate.py `scheduled_checks` | The recovered push of scheduled_red_<name> (INFO, once) | A scheduled workflow that was red passed its scheduled run(s) again (LIFE-692). |
| `daily_check` | INFO | lifeos/platform/gate.py watch (GitHub watchdog) | Once a day (a report, no incident): from the first watchdog run at or after 7 AM CT (13:00 UTC) that day, never twice; if GitHub skipped the normal window it goes at the first later run and says "Sent late" in its text (LIFE-724); its absence is the alarm | The daily check-in (LIFE-603): OK or DEGRADED, with the last 24 hours of hourly runs (counts only). Its absence is the alarm. Since LIFE-688 it carries one more line: `Notion write conflicts in the last 24 h: N (hiring a, recruiters b); rate limits: M.` N is the number of write conflicts (a domains-run job log in the last 24 hours that shows NOTION_HTTP_409, a page-walk 404 or PROTECTED_REGION_CHANGED, counted once per job per code per run); M is the rate limits (NOTION_HTTP_429), counted apart. When N is above 0 the line ends with `rule: any write conflict moves the Daily Report writers to the full chain (option A, LIFE-688)`. When the logs cannot be read the line says so under a fixed code (WRITE_CONFLICT_LOGS_UNREADABLE, WRITE_CONFLICT_CAP_REACHED or WRITE_CONFLICT_COUNT_UNAVAILABLE) and the count never turns the check-in to DEGRADED or pages by itself. Counting started at Jim's writer-chain decision (Nov 15 2026, 12:05 AM CT); until 24 hours have passed the line says `Counted since the writer-chain decision (Nov 15 2026, 12:05 AM CT).` |
| `hermes_disk_full` | INFO | lifeos/edge/ticker/ticker.py; one GitHub issue | Disk image under 80%; one hermes_disk_full_recovered | The Hermes disk image is 80% full or more. |
| `hermes_disk_full_recovered` | INFO | lifeos/edge/ticker/ticker.py | The recovered push (INFO, once) | The Hermes disk image is back under 80%. |
| `deploy_drift` | INFO | lifeos/edge/ticker/ticker.py via lifeos/platform/drift.py; one GitHub issue | Every checked file matches main and its unit runs; one deploy_drift_recovered; INFO is never re-paged (LIFE-675) | A repo-owned file installed on the Oracle VM differs from main, or its unit is not running (LIFE-528). |
| `deploy_drift_recovered` | INFO | lifeos/edge/ticker/ticker.py | The recovered push (INFO, once) | Every checked file on the Oracle VM matches main again. |
| `deploy_stalled` | INFO | lifeos/edge/ticker/ticker.py; marker from the Close release timer; one GitHub issue | The marker is gone (a green main head deployed); one deploy_stalled_recovered | The Close service release timer has found no green main head for over 2 hours (LIFE-562). |
| `deploy_stalled_recovered` | INFO | lifeos/edge/ticker/ticker.py | The recovered push (INFO, once) | A green main head deployed; the Close release is moving again. |
| `todoist_token` | INFO | lifeos/edge/ticker/ticker.py via lifeos/platform/todoist_refresh.py; one GitHub issue | Both Todoist apps are ok; one todoist_token_recovered | The Todoist token refresher reports a problem: a revoked grant, an unwritable vault or a wrong scope (REQ-88). |
| `todoist_token_recovered` | INFO | lifeos/edge/ticker/ticker.py | The recovered push (INFO, once) | Both Todoist apps refresh normally again. |
| `release_proof_failed` | PAGE | lifeos/edge/ticker/ticker.py via lifeos/platform/release_proof.py; one GitHub issue labelled `release-paused` (the pause flag) | The first passing proof run closes it with one release_proof_recovered; while it is open and the newest proof is still failing it may remind every 3 hours; closing the issue by hand lifts the pause | The dry proof run started after a Close release failed (LIFE-693). |
| `release_proof_recovered` | INFO | lifeos/edge/ticker/ticker.py | The recovered push (INFO, once) | A proof run passed after a failed one: Close releases resume (LIFE-693). |
| `release_pause_unreadable` | PAGE | lifeos/edge/ticker/ticker.py; marker `/etc/lifeos-close/auto.proof-unreadable` from the Close release timer; one GitHub issue labelled `release-pause-unreadable` | The marker is gone (the timer could read the release-paused flag again); one release_pause_unreadable_recovered; no reminders: it lifts by itself | The Close release timer could not read the release-paused flag on GitHub, so it deploys nothing (an unknown state never deploys; LIFE-693). |
| `release_pause_unreadable_recovered` | INFO | lifeos/edge/ticker/ticker.py | The recovered push (INFO, once) | The release timer can read the release-paused flag again; Close releases can deploy. |
| `attention_new` | INFO | Reserved: registered, no raiser in the repository today | n/a (one-shot event) | New items need your attention (counts only). |
| `physmail_new` | INFO | lifeos/physmail/stage.py | One-shot event per arrival batch (counts only); no incident | New physical mail arrived (counts only). |
| `megibow_stale` | PAGE | lifeos/sources/cards/megibow.py | One page inside its Wednesday window while still failing; no incident | On Wednesday 1:30 to 3 PM Central the MegIBOW refresh is still failing. |
| `run_failed` | PAGE | .github/workflows/hourly.yml report step (workflow file: follow-up) | One push per failed hourly run, an event; repeats are folded by pipeline_failing | An hourly run failed (the failed lanes and stages are named). |
| `domain_jobs_failed` | PAGE | .github/workflows/domains.yml report job (workflow file: follow-up) | One push per failed domains run, an event | One or more domain jobs failed. |
| `purge_done` | INFO | .github/workflows/hourly.yml purge step | Weekly event | The weekly retention purge finished. |
| `purge_failed` | PAGE | .github/workflows/hourly.yml purge step | Weekly event | The weekly retention purge failed. |
| `safety_failed` | PAGE | .github/workflows/safety.yml | Weekly event | The weekly safety check failed. |
| `test_push` | PAGE | .github/workflows/watchdog.yml manual | Manual test only | A manual test push from the watchdog workflow (its --severity may be set). |

## Adopting the model (a new source-health alert, for example the Action Inbox)

Keep a dict-like store of `{item: record}` where the stage already keeps state, build `KnownStates(store)`, and once per run call `observe({item: fixed_code}, now)`. `new` items follow the normal failure path, `known` items go to `card_lines()` as WARN and do not fail the job, `escalate` items go to `escalate(observation, family, alerts.push)`. Register the family key in `alert_registry.py` (INFO for anything that is only a notice; the escalation uses `known_state_expired_<family>`, already registered as PAGE).

## Not yet on the known-state rule (follow-ups, listed honestly)

- `interview` and `granola-auto` still exit 1 on any single failed item (marked `partial-red` in `docs/platform/stage-isolation.md`, LIFE-689). Their failure counts mix states that this model turns into alerts: moving them is a separate, reviewed change.
- `run_failed` and `domain_jobs_failed` are pushed by workflow steps (`hourly.yml`, `domains.yml`): they are events per failed run and already carry a registry key. Changing them is a workflow edit (protected, one workflow PR at a time), so it waits for the workflow PR.
- The known-state store has no production adopter yet: `KnownStates` is the mechanism and its tests; the first adopter picks the store.

## Measured before (Nov 7 to Nov 14 2026; counts and fixed codes only)

- GitHub incident issues opened in 7 days: `pipeline-stale` 6, `pipeline-failing` 4 (31 reminder comments), `deploy-drift` 1 (16 reminder comments, before LIFE-675 stopped INFO reminders); `pipeline-run-stuck`, `deploy-stalled` and `hermes-disk-full` 0.
- Hourly workflow runs in 7 days: 87 success, 19 failure, 94 cancelled (the gate cancels runs it does not need). Domains runs: 80 success, 9 failure, 110 skipped.
- Oracle ticker (7 days): 671 watchdog checks, 138 ticks dispatched, 30 skipped, 1 `TICKER_VARIABLES_UNREADABLE`; watchdog change counts: 20 reminders, 5 opens, 4 closes.

## Deploy note

The Oracle ticker runs its own installed copy of `gate.py`, `alerts.py`, `alert_registry.py` and the new `health.py`. The rules above take effect on the Oracle host only after the ticker install (docs/SETUP.md step 37, Jim's typed yes). The GitHub watchdog picks the code up at merge.
