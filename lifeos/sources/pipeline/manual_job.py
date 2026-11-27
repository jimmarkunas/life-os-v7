"""One job named by hand. Manual only (manual-job.yml); a dry run unless live.

MANUAL_JOB_URL / _COMPANY / _TITLE name the job. Two idempotent halves: Jobs OS takes the posting link like a
recruiter's
(RESOLVED, lane Recruiter, source `manual`), and Interview OS creates the machine opportunity page and its Interview 1
round keyed by the same Stable Job Key, dated (MANUAL_JOB_ROUND_DAY, a Central day) or Scheduling Pending at a request
message link (MANUAL_JOB_REQUEST_URL). Counts and fixed codes only: no link, name or title is logged.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.interview import notion, stage
from lifeos.interview.identity import valid_date
from lifeos.interview.models import ParentQuery, RoundQuery
from lifeos.jobs import classify, identity, intake, lanes, quality, store
from lifeos.platform.runtime import RunContext

TZ = ZoneInfo("America/Chicago")
STAGE_SECONDS = 300


def _text(environ, name):
    return (environ.get(name) or "").strip()


def enter(cursor, url, company, title, source, provider, received, stamp, kind):
    """A named job enters Jobs OS like a recruiter's posting link: RESOLVED at that link, lane Recruiter.

    -> (Stable Job Key, new); a new job keeps the link as its final apply URL (`kind`: ats, aggregator or employer).
    """
    key, new = intake.add_job(
        cursor,
        {
            "url": url,
            "status": "RESOLVED",
            "title": title,
            "company": company,
            "location": "",
            "salary": "",
            "source": source,
            "provider": provider,
            "lane": lanes.RECRUITER,
            "age_days": None,
            "provider_score": None,
            "received": received,
        },
        stamp,
    )
    if new:
        cursor.execute(
            "UPDATE v7_jobs SET final_apply_url=%s, apply_kind=%s, updated_at=%s WHERE dedupe_key=%s",
            (url, kind, stamp, key),
        )
    return key, new


def add_job(url, company, title, now, live, connect=store.connect):
    """-> 'new' | 'present' (live: added or already held; dry run: would be added or is held). Read only unless
    live."""
    stamp = now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    with connect() as connection, connection.cursor() as cursor:
        if not live:
            cursor.execute("SELECT 1 FROM v7_jobs WHERE dedupe_key=%s LIMIT 1", (identity.url_hash(url),))
            return "present" if cursor.fetchone() else "new"
        return (
            "new"
            if enter(cursor, url, company, title, "manual", "Manual", stamp, stamp, classify.apply_kind(url))[1]
            else "present"
        )


def run(limit, live, environ=os.environ, client=None, now=None, connect=store.connect, context=None):
    url, company, title = (_text(environ, f"MANUAL_JOB_{name}") for name in ("URL", "COMPANY", "TITLE"))
    day, request = _text(environ, "MANUAL_JOB_ROUND_DAY"), _text(environ, "MANUAL_JOB_REQUEST_URL")
    now = (now or datetime.now(TZ)).astimezone(TZ)
    counts = {"job": "none", "failed": 0, "why": {}}

    def stop(code):
        counts["failed"] += 1
        counts["why"][code] = counts["why"].get(code, 0) + 1
        return counts

    if not (company and title):
        return stop("MANUAL_JOB_IDENTITY_MISSING")
    if not url.startswith("https://") or quality.link_problem(url):
        return stop("MANUAL_JOB_LINK_INVALID")
    if day and not valid_date(day):
        return stop("MANUAL_JOB_DAY_INVALID")
    if request and not request.startswith("https://"):
        return stop("MANUAL_JOB_REQUEST_INVALID")
    if not (day or request):
        return stop(
            "MANUAL_JOB_ROUND_EVIDENCE_MISSING"
        )  # a round is dated by the given day or pending at a request message: never invented
    try:
        counts["job"] = add_job(url, company, title, now, live, connect)
    except Exception:  # noqa: BLE001
        return stop("MANUAL_JOB_STORE_FAILED")
    key = identity.url_hash(url)
    round_query = RoundQuery(True, day or None, None, 1, source=request or None, stable_job_key=key)
    live = bool(live) and environ.get("INTERVIEW_OS_WRITER") == "v7"
    counts["interview"] = stage.run(
        limit,
        live,
        environ,
        client=client or notion.make_client(environ),
        evidence=[(ParentQuery(company, title), round_query)],
        context=context or RunContext.start(STAGE_SECONDS),
    )
    counts["failed"] += counts["interview"].get("blocked", 0)
    return counts
