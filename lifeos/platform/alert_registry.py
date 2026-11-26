"""The alert registry: every alert LIFE OS can send, in one list; each entry says who owns it, how loud it is (severity),
what it means and which channels carry it by default.

Severity and channels are read from here at send time: `alerts.push` refuses a key that is not listed, and
tests/contracts/test_alert_registry.py fails when the code builds an alert or push that is not listed. A key ending in
`*` covers a family whose last part is data. PAGE is the loud kind, INFO the quiet kind. Channels: the phone (ntfy)
and the two copies the Oracle relay makes (Telegram, WhatsApp). Nothing here changes WHEN an alert fires.
"""
from typing import NamedTuple

PAGE, INFO = "page", "info"
CHANNELS = ("ntfy", "telegram", "whatsapp")
LOUD = CHANNELS  # a PAGE reaches every channel by default
QUIET = ("ntfy",)  # an INFO reaches the phone only by default


class Spec(NamedTuple):
    severity: str
    owner: str
    meaning: str
    channels: tuple


def _page(owner, meaning):
    return Spec(PAGE, owner, meaning, LOUD)


def _info(owner, meaning):
    return Spec(INFO, owner, meaning, QUIET)


REGISTRY = {
    # --- Jobs: the alerts stage (lifeos/jobs/alerts.py)
    "quiet_24h": _info("jobs", "No job reached Notion in 24 hours."),
    "sources_failing": _info("jobs", "Sponsor sources failed three runs in a row."),
    "stuck_jobs": _info("jobs", "25 or more jobs sat in READY or RESOLVED for over a day."),
    "unresolved_terminal": _info("jobs", "Worthwhile jobs stayed unresolved after their one recovery attempt."),
    "source_stopped_*": _page("jobs", "A job source that normally brings 3+ jobs a day brought none in 24 hours."),
    "source_stuck_*": _info("jobs", "Half or more of a source's last 72 hours of jobs are still NEW or HOLD."),
    "source_yield_*": _info("jobs", "A source published under 5% of its last 7 days of jobs."),
    "board_reads_failing": _info("jobs", "A fifth or more of two days of board reads failed (not counting boards that answer not found)."),
    "search_failing": _info("jobs", "A fifth or more of two days of search calls failed."),
    "hold_rising": _info("jobs", "The HOLD pile grew on each of the last three days."),
    "hold_not_retried": _info("jobs", "A large, growing HOLD pile with nothing touched in 24 hours."),
    "expiry_*": _page("jobs", "A credential or token expires within a week."),
    # --- other watchers with their own state
    "jira_rollover_stuck": _page("jira", "The Monday sprint rollover has not worked for 3 hours."),
    "jira_reconcile_failed": _info("jira", "The daily Jira sprint reconciliation failed for a board (LIFE-918; one phone push per failure code per day; phone only)."),
    "v2_promotion_failed": _info("jira", "The v2 quarterly promotion check failed: legacy stays official (LIFE-918; one push, phone only)."),
    "calendar_sync_stuck": _page("calendar", "The calendar bridge has not synced for 3 hours."),
    "offsite_backup_failed": _info("edge", "The nightly off-site backup (Oracle to Drive) failed, or has not succeeded for 36 hours (LIFE-129, Jim: quiet, phone only)."),
    "openclaw_workers_leaking": _info("edge", "OpenClaw has more Codex app-server workers than agents on Oracle: the duplicate-worker leak is back (LIFE-531, upstream 196499; phone only)."),
    "boot_backups_full": _info("edge", "Oracle boot-volume backups are at their limit of 5: the next weekly backup of the Edge will be refused until one is deleted (LIFE-808; phone only)."),
    "imessage_action": _page("imessage", "A message needs action: time critical goes out at once, action required waits through quiet hours (10 PM to 8 AM Central)."),
    # --- the pipeline's own health (lifeos/platform/gate.py, the watchdog)
    "pipeline_stalled": _page("platform", "No hourly run has succeeded for 2.5 hours."),
    "pipeline_stalled_recovered": _info("platform", "The stalled pipeline recovered."),
    "pipeline_failing": _page("platform", "Three or more hourly runs failed in 24 hours."),
    "pipeline_failing_recovered": _info("platform", "No failed hourly run in 24 hours."),
    "tick_run_stuck": _info("platform", "An hourly tick run has been queued or running for over 40 minutes (LIFE-580); later ticks still run, the run needs a look (phone only)."),
    "tick_run_stuck_recovered": _info("platform", "No hourly tick run is stuck any more."),
    "known_state_expired_*": _page("platform", "A known failure (the same item, the same fixed code, shown as WARN and never paged) is still failing after 7 days: one page, once (LIFE-690)."),
    "scheduled_red_*": _page("platform", "A scheduled workflow's newest scheduled run failed: one page per workflow, none for a second red run (LIFE-692)."),
    "scheduled_recovered_*": _info("platform", "A scheduled workflow that was red passed its scheduled run(s) again (LIFE-692)."),
    "daily_check": _info("platform", "The daily check-in (LIFE-603): OK or DEGRADED, with the last 24 hours of hourly runs (counts only). Its absence is the alarm."),
    # --- the edge host (lifeos/edge/ticker/ticker.py; INFO: phone only)
    "hermes_disk_full": _info("edge", "The Hermes disk image is 80% full or more."),
    "hermes_disk_full_recovered": _info("edge", "The Hermes disk image is back under 80%."),
    "deploy_drift": _info("edge", "A repo-owned file installed on the Oracle VM differs from main, or its unit is not running (LIFE-528)."),
    "deploy_drift_recovered": _info("edge", "Every checked file on the Oracle VM matches main again."),
    "deploy_stalled": _info("edge", "The Close service release timer has found no green main head for over 2 hours (LIFE-562)."),
    "deploy_stalled_recovered": _info("edge", "A green main head deployed; the Close release is moving again."),
    "release_proof_failed": _page("edge", "The dry proof run after a Close release failed: one page with the deployed and previous good commits, the failing stage code and the revert line; Close releases pause (LIFE-693)."),
    "release_proof_recovered": _info("edge", "A proof run passed after a failed one: the pause on Close releases is lifted (LIFE-693)."),
    "release_pause_unreadable": _page("edge", "The Close release timer cannot read the release-paused flag, so it deploys nothing (an unknown state never deploys); lifts by itself when GitHub is readable (LIFE-693)."),
    "release_pause_unreadable_recovered": _info("edge", "The release timer can read the release-paused flag again; Close releases can deploy (LIFE-693)."),
    # --- one-shot events
    "attention_new": _info("attention", "New items need your attention (counts only)."),
    "physmail_new": _info("physmail", "New physical mail arrived (counts only)."),
    "megibow_stale": _page("megibow", "On Wednesday 1:30 to 3 PM Central the MegIBOW refresh is still failing."),
    # --- pushes from workflow steps (python -m lifeos.platform.alerts push --key ...)
    "run_failed": _page("workflow", "An hourly run failed (the failed lanes and stages are named)."),
    "domain_jobs_failed": _page("workflow", "One or more domain jobs failed."),
    "purge_done": _info("workflow", "The weekly retention purge finished."),
    "purge_failed": _page("workflow", "The weekly retention purge failed."),
    "safety_failed": _page("workflow", "The weekly safety check failed."),
    "test_push": _page("workflow", "A manual test push from the watchdog workflow (its --severity may be set)."),
}


def lookup(key):
    """-> the Spec for an exact key, else the longest `prefix*` entry that matches, else None."""
    if key in REGISTRY:
        return REGISTRY[key]
    best = None
    for pattern, spec in REGISTRY.items():
        if pattern.endswith("*") and key.startswith(pattern[:-1]) and (best is None or len(pattern) > len(best[0])):
            best = (pattern, spec)
    return best[1] if best else None


def pattern_of(key):
    """-> the registry key that covers `key`: itself, or the `prefix*` family; None when it is not registered."""
    if key in REGISTRY:
        return key
    best = None
    for pattern in REGISTRY:
        if pattern.endswith("*") and key.startswith(pattern[:-1]) and (best is None or len(pattern) > len(best)):
            best = pattern
    return best


def severity(key, fallback=None):
    spec = lookup(key)
    return spec.severity if spec else fallback


def channels(key):
    spec = lookup(key)
    return spec.channels if spec else ()
