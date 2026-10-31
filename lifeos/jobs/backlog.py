"""LIFE-212 (a) (Jim, Oct 31 2026: "I can't see the backlog ... Add a report"): every job that is not on the Job Ledger yet and not finally bounced, with why it waits.

Reads the Jobs store only and writes ONE private Notion page (lifeos.jobs.private_report), dated so each run is its own page; a dry run unless live. The log carries counts
and fixed codes only. Statuses read: NEW (link not resolved yet), RESOLVED (description not read yet), READY (scored or waiting for a score, not published) and HOLD (parked).
A READY job's reason comes from its Fit row: awaiting a score, waiting to publish (Admit / Review and its Fit), or excluded by the lane rules."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lifeos.jobs import store

OPEN = ("NEW", "RESOLVED", "READY", "HOLD")
ORDER = {"READY": 0, "HOLD": 1, "RESOLVED": 2, "NEW": 3}
MAX_ROWS = 8000                          # the report page holds at most 98 tables of 90 rows; beyond this the oldest are kept and the summary says how many were left out
CT = ZoneInfo("America/Chicago")
SQL = ("SELECT j.status, j.unresolved_reason, f.admission, f.admission_reason, f.score, f.cloud_status, j.company, j.title, j.lane, j.source, j.first_seen, j.posted_date,"
       " COALESCE(j.final_apply_url, j.source_url) FROM v7_jobs j LEFT JOIN v7_job_fit f ON f.job_id = j.id"
       " WHERE j.notion_page_id IS NULL AND j.status IN ('NEW', 'RESOLVED', 'READY', 'HOLD')")
# LIFE-212 (Jim, Oct 31 2026: "show me the published vs bounced split"): where the jobs given their one retry in the first run after hold.MISMATCH_AT are now.
# That run's expire-holds step stamps every job it requeues with the same recovered_at; it can also hold that day's D185 day-6 retries (hold.recover).
RELEASED = ("SELECT j.status, j.source LIKE 'linkedin%%', COUNT(*) FROM v7_jobs j WHERE j.recovered_at = (SELECT MIN(recovered_at) FROM v7_jobs WHERE recovered_at >= %s)"
            " GROUP BY 1, 2")
OUTCOME = {"PUBLISHED": "on the board", "READY": "still on its way", "RESOLVED": "still on its way", "NEW": "still on its way", "HOLD": "still on its way",
           "DUPLICATE": "bounced: same job already on the board"}


def reason(status, why, admission, admission_why, score, cloud):
    """The one-line reason a job is not on the board yet."""
    if status == "READY":
        if admission is None or score is None:
            return "ready · waiting for a Fit score"
        if admission == "EXCLUDE":
            return f"ready · excluded by the lane rules ({admission_why or 'no reason stored'})"
        return f"ready · waiting to publish ({'Admit' if admission == 'ADMIT' else 'Review'}, Fit {score}{', cloud Fit pending' if cloud is None else ''})"
    if status == "HOLD":
        return f"held · {why or 'no reason stored'}"
    if status == "RESOLVED":
        return "link found · waiting for its description" + (f" ({why})" if why else "")
    return "new · waiting for its link to resolve" + (f" ({why})" if why else "")


def _days(now, then):
    return None if then is None else max(0, (now.date() - (then.date() if hasattr(then, "date") else then)).days)


def rows_of(found, now):
    """-> (report rows, summary counts by reason group). Oldest waiting first within each status; READY first (closest to the board)."""
    out, groups = [], {}
    for status, why, admission, admission_why, score, cloud, company, title, lane, source, first_seen, posted, link in found:
        line = reason(status, why, admission, admission_why, score, cloud)
        group = line.split(" (")[0] if status != "HOLD" else line
        groups[group] = groups.get(group, 0) + 1
        clean = lambda v: " ".join(str(v if v is not None else "").split())          # noqa: E731
        waited, age = _days(now, first_seen), _days(now, posted)
        out.append((ORDER.get(status, 9), -(waited or 0), [clean(line), clean(company), clean(title), clean(lane), "" if waited is None else str(waited),
                                                            "unknown" if age is None else str(age), clean(source), clean(link)]))
    out.sort(key=lambda r: (r[0], r[1], r[2][1], r[2][2]))
    return [r[2] for r in out], dict(sorted(groups.items(), key=lambda kv: -kv[1]))


def outcome(status):
    """A released job's current state in plain words: on the board, still moving, or bounced (and why)."""
    if status in OUTCOME:
        return OUTCOME[status]
    if status == "EXCLUDED_UNRESOLVED":
        return "bounced: its retry failed"
    return "bounced: excluded by the lane rules" if str(status).startswith("EXCLUDED") else f"other ({status})"


def released_lines(rows):
    """-> intro lines for the LIFE-212 release split, from RELEASED rows (status, is_linkedin, n)."""
    by = {}
    for status, linkedin, n in rows:
        key = outcome(status)
        total, li = by.get(key, (0, 0))
        by[key] = (total + int(n), li + (int(n) if linkedin else 0))
    if not by:
        return []
    lines = [f"LIFE-212 release: {sum(t for t, _ in by.values())} jobs got their one retry in the first run after 5:00 AM CT on Oct 31. Where they are now:"]
    return lines + [f"{t} · {key} ({li} from LinkedIn)" for key, (t, li) in sorted(by.items(), key=lambda kv: -kv[1][0])]


def run(limit, live, connect=store.connect, client=None, now=None):
    """-> counts: total, by reason group, and the report page result (private_report.publish)."""
    from lifeos.jobs import private_report
    from lifeos.platform.notion_client import Client
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(SQL)
        found = cursor.fetchall()
        from lifeos.jobs import hold                                    # noqa: PLC0415
        cursor.execute(RELEASED, (hold.MISMATCH_AT,))
        release = released_lines(cursor.fetchall())
    rows, groups = rows_of(found, now)
    left_out = max(0, len(rows) - MAX_ROWS)
    rows = rows[:MAX_ROWS]
    stamp = now.replace(tzinfo=timezone.utc).astimezone(CT)
    title = f"LIFE-212 Jobs backlog — {stamp:%b} {stamp.day}, {stamp:%Y} {stamp:%I:%M %p} CT — {len(found)} not on the board"
    intro = [f"{len(found)} jobs are not on the Job Ledger yet and not finally bounced. Newest data from the Jobs store; nothing was changed."]
    intro += [f"{n} · {group}" for group, n in groups.items()]
    if left_out:
        intro.append(f"{left_out} more are left out of the table below (it lists the first {MAX_ROWS}).")
    intro += release
    header = ["why it is not on the board", "company", "title", "lane", "days waiting", "posting age (days)", "source", "link"]
    result = private_report.publish(client or Client(), title, header, rows, live, links=(7,), intro=intro)
    return {"total": len(found), "groups": groups, "left_out": left_out, "release": release[1:], **result}
