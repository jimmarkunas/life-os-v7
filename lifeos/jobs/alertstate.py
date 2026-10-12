"""Read-only diagnostic: which alerts are open and why. Prints open alert keys (source names only), per source the new jobs in the last 24 hours against the 14-day daily baseline,
and the jobs the stuck_jobs alert counts, grouped by status, whether a link and a Notion page exist, the Fit admission and the stop reason code. Counts only. Writes nothing."""
import datetime as dt

from lifeos.jobs import alerts, funnel, store


def stuck(cursor, now):
    """The rows stuck_jobs counts (READY or RESOLVED, untouched for over a day) as {status|link|page|admission|reason: [count, oldest_hours]}; no job, company or URL leaves the store."""
    cutoff = now - dt.timedelta(hours=alerts.QUIET_HOURS)
    cursor.execute("SELECT j.status, j.final_apply_url IS NOT NULL, j.notion_page_id IS NOT NULL, COALESCE(f.admission, 'none'), j.unresolved_reason, COUNT(*), MIN(j.updated_at)"
                   " FROM v7_jobs j LEFT JOIN v7_job_fit f ON f.job_id=j.id WHERE j.status IN ('READY','RESOLVED') AND j.updated_at < %s GROUP BY 1, 2, 3, 4, 5", (cutoff,))
    out = {}
    for status, link, page, admission, reason, count, oldest in cursor.fetchall():
        key = f"{status}|link={int(bool(link))}|page={int(bool(page))}|fit={admission}|{funnel.reason_code(reason)}"
        have = out.get(key, [0, 0])
        out[key] = [have[0] + int(count), max(have[1], int((now - oldest).total_seconds() // 3600))]
    return dict(sorted(out.items()))


def run(limit, live):
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    with store.connect() as connection:
        open_alerts = alerts.load_state(connection)
        counts = alerts.gather(connection, now)
        window = now - dt.timedelta(hours=alerts.STOP_WINDOW_HOURS)
        start = window - dt.timedelta(days=alerts.STOP_BASELINE_DAYS)
        with connection.cursor() as cursor:
            cursor.execute("SELECT source, SUM(first_seen > %s), SUM(first_seen <= %s), MAX(first_seen) FROM v7_jobs WHERE source IS NOT NULL AND first_seen > %s GROUP BY source",
                           (window, window, start))
            sources = {name: {"last24h": int(recent or 0), "per_day": round(int(base or 0) / alerts.STOP_BASELINE_DAYS, 1), "latest": str(latest)}
                       for name, recent, base, latest in cursor.fetchall()}
            stuck_rows = stuck(cursor, now)
    return {"open_alerts": {key: {"severity": value["severity"], "last_notified": str(value["last_notified"])} for key, value in sorted(open_alerts.items())},
            "published_24h": counts["published_24h"], "stuck_jobs": counts["stuck_jobs"], "sources_failing": counts["sources_failing"],
            "stuck": stuck_rows, "sources": dict(sorted(sources.items()))}
