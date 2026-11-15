# Stability clock (LIFE-694)

**A clean day means no failed run and no platform incident issue.**

The Platform Stability card, the fourth card of the Daily Command Center, shows `N of 7 clean days`: how many completed America/Chicago calendar days in a row (cap 7) had no failed hourly tick, no failed domains run and no platform incident issue open. Code: `lifeos/platform/stability.py` (the count), `lifeos/dcc/card.py` (the card, drawn by the existing Command Center renderer; no new job, store, secret or phone push). Counting starts on **Nov 15 2026**, the day this merged; no earlier day is ever credited and today's partial day is not.

## What counts (read from GitHub's own history with the dcc job's read-only token)

| Evidence | Counts | Why |
|---|---|---|
| A failed hourly tick (`hourly.yml`, run title `tick`) | yes | it pushes `run_failed` |
| A failed domains run (`domains.yml`, run title `domains (after tick)`) | yes | it pushes `domain_jobs_failed` |
| Incident issue `pipeline-stale` (alert `pipeline_stalled`, PAGE) | yes, every day it is open | the platform stopped running |
| Incident issue `pipeline-failing` (alert `pipeline_failing`, PAGE) | yes, every day it is open | hourly runs keep failing |
| Incident issue `pipeline-run-stuck` (alert `tick_run_stuck`) | yes, every day it is open | a tick run is stuck |
| Incident issue `deploy-stalled` (alert `deploy_stalled`) | yes, every day it is open | no green main head deployed |
| Incident issue `scheduled-red-<workflow>` (alert family `scheduled_red_*`, PAGE) | yes, every day it is open | a scheduled workflow is red (LIFE-692) |
| Manual runs, cancelled runs, runs with other titles | no | the same rule as the watchdog's tick counting |
| Incident issue `deploy-drift` (`deploy_drift`, INFO) | no | quiet INFO about a file differing on the Oracle VM, not a failure |
| Incident issue `hermes-disk-full` (`hermes_disk_full`, INFO) | no | quiet INFO |
| `imessage_action`, `physmail_new`, `attention_new`, `expiry_*`, `source_*` | no | outside events or reminders, not platform failures |

**Not visible to the clock (no durable history to read):** alerts whose state lives only as a current row in a private table or that are one-shot pushes: the jobs-store detectors (`quiet_24h`, `stuck_jobs`, `board_reads_failing`, `search_failing`, `hold_*`), `jira_rollover_stuck`, `calendar_sync_stuck`, `offsite_backup_failed`, `openclaw_workers_leaking`, `megibow_stale` and `known_state_expired_*`. A clean day can still contain one of these; the card does not claim otherwise. Giving them a durable record is a separate decision (a push log is a new store).

The label list is in code (`stability.counted_labels()`); a test keeps this table and the code identical.

## States

| Card text | When |
|---|---|
| `N of 7 clean days` | counting, N completed clean days since the last failure (or since the start) |
| `0 of 7 clean days · RESET` | a failure or an open platform incident issue today or yesterday, after counting began |
| `7 of 7 clean days` | seven completed clean days: the card stays visible and means "ready for epic closure review" |
| `Unverified` | GitHub cannot be read (no token or repository in the environment, an error, a list longer than the card will page): seven empty segments, never a zero shown as healthy |

Each state has exactly seven segments (`■` filled, `□` empty), no timestamp, no explanation and no button.

## At 7 of 7

No phone push and no Jira call: no credential for Jira is allowed in CI. The run's counts carry `stability: {state: complete, days: 7}` (job log) and the card reads `7 of 7 clean days`. **The epic lead (a Claude Code session) posts the one comment on LIFE-686** after reading the real card; nothing in Jira changes automatically.

## Setup the card needs (a workflow edit, protected: one approval, in the combined workflow PR of LIFE-686, Nov 15 2026)

The card shows `Unverified` whenever GitHub is unreadable: the safe state. In `.github/workflows/domains.yml`, job `dcc` has a job `permissions:` block (all read-only) and one env line on the `dcccard` step, so the card can read the run history and the incident issues:

```yaml
  dcc:
    ...
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read      # the job's own checkout (what the workflow already grants)
      actions: read       # the run history
      issues: read        # the incident issues
    steps:
      ...
      - id: dcccard
        env:
          GITHUB_TOKEN: ${{ github.token }}
          DCC_CARD_BLOCK_ID: ...
```

`GITHUB_REPOSITORY` is set by GitHub Actions itself. No write permission anywhere.

## Read-back (not a merge gate)

After the workflow edit has merged and a normal hourly refresh has run: open the Daily LIFE OS Report, find ChatGPT · Daily Command Center, and check four equal cards, Platform Stability fourth, `N of 7 clean days` with seven segments and no timestamp; the other three cards unchanged. The first credited day is Nov 16 2026 (`1 of 7`).
