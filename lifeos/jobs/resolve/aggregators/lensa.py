"""Lensa rows -> final apply link. Lensa pages are blocked from the runner, so nothing here touches Lensa: the card's
company / title / location are matched against the employer's public ATS boards, then the free Search API."""
from datetime import datetime

from lifeos.platform import limits, tinyfish_search
from lifeos.jobs.resolve import ats_match
from lifeos.jobs.resolve.aggregators import linkedin

TITLE_FIX_AT = datetime(2026, 10, 3, 22, 0)      # UTC: D80 shipped before this; a job that fails again after it is never requeued again


def make_resolver():
    """One search budget for the whole run, shared by every batch the stage hands in."""
    budget = {"left": limits.TINYFISH_SEARCH_PER_RUN_LENSA}
    tinyfish_search.solo_after(limits.TINYFISH_SEARCH_SOLO_AFTER_SECONDS)       # LinkedIn's lane is finished by then: use the full 27/min

    def resolve_rows(rows):
        return linkedin.match_rows(rows, [{"outcome": "external_hidden"} for _ in rows], budget=budget)
    return resolve_rows


def requeue_held(cursor, cutoff=TITLE_FIX_AT):
    """One-time catch-up for D80 (decorated titles no longer defeat the board and search match): Lensa jobs parked on HOLD by a definite `no_match_*`
    BEFORE the fix, whose title carried noise the matcher now removes, go back to NEW for one more try. A job that fails again is stamped after the
    cutoff, so the timestamp (not a reason that the failure would overwrite) is what makes this once."""
    cursor.execute("SELECT id, title FROM v7_jobs WHERE source='lensa' AND status='HOLD' AND unresolved_reason LIKE 'no_match%%' AND updated_at < %s", (cutoff,))
    ids = [row[0] for row in cursor.fetchall() if len(ats_match.title_variants(row[1])) > 1]
    for job_id in ids:
        cursor.execute("UPDATE v7_jobs SET status='NEW', resolve_attempts=0, unresolved_reason='requeued_titles', updated_at=UTC_TIMESTAMP() WHERE id=%s", (job_id,))
    return len(ids)


resolve_rows = make_resolver()
