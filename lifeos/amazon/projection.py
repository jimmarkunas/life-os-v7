"""Region-local Amazon status for the Daily Command Center (consumer is ChatGPT-owned): a pure function of one run's counts, fail closed."""
from lifeos.platform.router import DEGRADED, NO_ACTION, PASS


def outcome(counts):
    """PASS when orders were read and nothing needs review; NO_ACTION when nothing arrived; DEGRADED for any failure, review item, or unreadable
    result. Only this region's status changes: Jira, Calendar, Jobs and every other region are never read or touched here."""
    if not isinstance(counts, dict):
        return DEGRADED
    try:
        if counts.get("failed", 0) or counts.get("review", 0):
            return DEGRADED
        return PASS if counts.get("listed", 0) else NO_ACTION
    except TypeError:
        return DEGRADED
