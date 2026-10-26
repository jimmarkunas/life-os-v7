"""LIFE-37 (Jim, Oct 26): one job Jim names by hand. Manual only (manual-job.yml); a dry run unless live.

Jim gives the posting link, the company and the title (MANUAL_JOB_URL / _COMPANY / _TITLE). Two halves, both idempotent:
1. Jobs OS: the job enters like a recruiter's posting link (RESOLVED at that link, lane Recruiter, source `manual`), so Enrich reads the posting, Fit scores it and Publish writes its
   Ledger row. Its Stable Job Key is the one Jobs OS gives that link.
2. Interview OS: the existing writer creates the machine opportunity page and its Interview 1 round, keyed by that same Stable Job Key. The round is either dated
   (MANUAL_JOB_ROUND_DAY, a Central day Jim states) or Scheduling Pending at a request message link (MANUAL_JOB_REQUEST_URL). Either is Jim's own evidence; neither is guessed.
Counts and fixed codes only: no link, name or title is ever logged."""
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


def job_of(url, company, title, now):
    return {"url": url, "status": "RESOLVED", "title": title, "company": company, "location": "", "salary": "", "source": "manual", "provider": "Manual",
            "lane": lanes.RECRUITER, "age_days": None, "provider_score": None, "received": now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)}


def add_job(url, company, title, now, live, connect=store.connect):
    """-> 'new' | 'present' (live: the job was added or already held; dry run: it would be added or is held). Read only unless live."""
    key = identity.url_hash(url)
    stamp = now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    with connect() as connection, connection.cursor() as cursor:
        if not live:
            cursor.execute("SELECT 1 FROM v7_jobs WHERE dedupe_key=%s LIMIT 1", (key,))
            return "present" if cursor.fetchone() else "new"
        _, new = intake.add_job(cursor, job_of(url, company, title, now), stamp)
        if new:
            cursor.execute("UPDATE v7_jobs SET final_apply_url=%s, apply_kind=%s, updated_at=%s WHERE dedupe_key=%s", (url, classify.apply_kind(url), stamp, key))
    return "new" if new else "present"


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
        return stop("MANUAL_JOB_ROUND_EVIDENCE_MISSING")         # a round is dated by Jim's day or pending at a request message: never invented
    try:
        counts["job"] = add_job(url, company, title, now, live, connect)
    except Exception:                                            # noqa: BLE001 - a fixed code, never the exception text (the store's errors can carry hosts)
        return stop("MANUAL_JOB_STORE_FAILED")
    key = identity.url_hash(url)
    round_query = RoundQuery(True, day or None, None, 1, source=request or None, stable_job_key=key)
    live = bool(live) and environ.get("INTERVIEW_OS_WRITER") == "v7"
    counts["interview"] = stage.run(limit, live, environ, client=client or notion.make_client(environ), evidence=[(ParentQuery(company, title), round_query)],
                                    context=context or RunContext.start(STAGE_SECONDS))
    counts["failed"] += counts["interview"].get("blocked", 0)
    return counts
