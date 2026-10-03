"""Lensa rows -> final apply link. Lensa pages are blocked from the runner, so nothing here touches Lensa: the card's
company / title / location are matched against the employer's public ATS boards, then the free Search API."""
from datetime import datetime, timedelta, timezone

from lifeos.platform import limits, tinyfish_search
from lifeos.jobs import lanes
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


SEEN_ELSEWHERE = ("RESOLVED", "READY", "PUBLISHED", "EXCLUDED_FIT", "EXCLUDED_STALE", "CLOSED")    # another producer already carried this opening to a result
SCREEN_WINDOW_DAYS = 60


def _opening(company, title):
    forms = ats_match.title_variants(title)
    return (ats_match.norm(company), forms[-1]) if forms and ats_match.norm(company) else None


def screen(cursor, live, now=None):
    """D88: spend no search on a Lensa job we can already settle. (1) The posted pay is explicit, in dollars and under the US floor: EXCLUDED_FIT
    (`screen_pay`). (2) The same company and title (aggregator noise removed, three words or more) is already a result from another producer in the last
    60 days (a board, Jobright, LinkedIn): DUPLICATE of it (`dup_other_source`), because the other copy is resolved, published, or excluded on the same posting.
    A job whose other copy is still NEW or on HOLD is not screened. Counts only."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    counts = {"screened_pay": 0, "screened_dup": 0}
    floor = lanes.POLICIES["US Remote"]
    cursor.execute("SELECT id, company, title, salary_text FROM v7_jobs WHERE source='lensa' AND status='NEW'")
    new = cursor.fetchall()
    cursor.execute("SELECT id, company, title FROM v7_jobs WHERE source<>'lensa' AND status IN %s AND first_seen >= %s",
                   (SEEN_ELSEWHERE, now - timedelta(days=SCREEN_WINDOW_DAYS)))
    elsewhere = {}
    for job_id, company, title in cursor.fetchall():
        key = _opening(company, title)
        if key and len(key[1].split()) >= 3:
            elsewhere.setdefault(key, job_id)
    for job_id, company, title, salary in new:
        pay, currency = lanes.parse_pay(salary)
        if pay is not None and currency == floor.currency and pay < floor.pay_floor:
            counts["screened_pay"] += 1
            if live:
                cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT', unresolved_reason='screen_pay', updated_at=%s WHERE id=%s", (now, job_id))
            continue
        key = _opening(company, title)
        other = elsewhere.get(key) if key and len(key[1].split()) >= 3 else None
        if other:
            counts["screened_dup"] += 1
            if live:
                cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', repost_of=%s, unresolved_reason='dup_other_source', updated_at=%s WHERE id=%s",
                               (other, now, job_id))
    return counts


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
