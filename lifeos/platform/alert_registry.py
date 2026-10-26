"""The alert registry (LIFE-75): every alert LIFE OS can send, in one list. Each entry says who owns it, how loud it is (severity), what it means, and which channels carry it
by default. Severity and channels are read from here at send time, never typed at the call site: `alerts.push` refuses a key that is not listed, and tests/contracts/test_alert_registry.py
fails when the code builds an alert or a push that is not listed. A key ending in `*` covers a family whose last part is data (a source name, a credential name).
Jim approved the severities on Oct 25 2026 (LIFE-75 comment 11182): PAGE is the loud kind, INFO the quiet kind. Nothing here changes WHEN an alert fires.
The channel names are the three places a push can land: the phone (ntfy) and the two copies the Oracle relay makes (Telegram, WhatsApp)."""
from typing import NamedTuple

PAGE, INFO = "page", "info"
CHANNELS = ("ntfy", "telegram", "whatsapp")
LOUD = CHANNELS                      # a PAGE reaches every channel by default
QUIET = ("ntfy",)                    # an INFO reaches the phone only by default


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
    "calendar_sync_stuck": _page("calendar", "The calendar bridge has not synced for 3 hours."),
    "imessage_time_critical": _page("imessage", "A message was classified time critical."),
    "imessage_action_required": _info("imessage", "A message was classified action required (held through quiet hours 10 PM to 8 AM Central)."),
    # --- the pipeline's own health (lifeos/platform/gate.py, the watchdog)
    "pipeline_stalled": _page("platform", "No hourly run has succeeded for 2.5 hours."),
    "pipeline_stalled_recovered": _info("platform", "The stalled pipeline recovered."),
    "pipeline_failing": _page("platform", "Three or more hourly runs failed in 24 hours."),
    "pipeline_failing_recovered": _info("platform", "No failed hourly run in 24 hours."),
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


def severity(key, fallback=None):
    spec = lookup(key)
    return spec.severity if spec else fallback


def channels(key):
    spec = lookup(key)
    return spec.channels if spec else ()
