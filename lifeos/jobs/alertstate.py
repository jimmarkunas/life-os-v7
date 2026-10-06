"""Read-only diagnostic: which alerts are open and why. Prints open alert keys (source names only), and per source the new jobs in the last 24 hours against the 14-day daily baseline. Writes nothing."""
import datetime as dt

from lifeos.jobs import alerts, store


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
    return {"open_alerts": {key: {"severity": value["severity"], "last_notified": str(value["last_notified"])} for key, value in sorted(open_alerts.items())},
            "published_24h": counts["published_24h"], "stuck_jobs": counts["stuck_jobs"], "sources_failing": counts["sources_failing"],
            "sources": dict(sorted(sources.items()))}
