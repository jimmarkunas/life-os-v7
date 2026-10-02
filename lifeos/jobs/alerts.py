"""Jobs OS alert detectors and the alerts stage (D53). Counts only. Detectors are pure functions of counts so they test without a database;
state (which alerts are open, when last notified) lives in v7_alerts so a reminder is not a repeat."""
import datetime as dt
import json
import os
from pathlib import Path

from lifeos.jobs import store
from lifeos.platform import alerts

EXPIRIES = Path(__file__).with_name("expiries.json")
SOURCE_FAILURES = 3          # a sponsor that failed this many runs in a row
STUCK_JOBS = 25              # jobs sitting in READY/RESOLVED for over a day
QUIET_HOURS = 24             # no job published for this long


def detect(counts, now, expiries=None):
    """counts: {"published_24h", "sources_failing", "stuck_jobs"}. -> list[Alert]."""
    out = []
    if counts.get("published_24h") == 0:
        out.append(alerts.Alert("quiet_24h", alerts.INFO, "Nothing published in 24 hours", "No job reached Notion in a day.",
                                "check the last runs; if they succeeded the lanes are simply quiet"))
    if counts.get("sources_failing", 0) > 0:
        out.append(alerts.Alert("sources_failing", alerts.INFO, "Sponsor sources failing", f"{counts['sources_failing']} source(s) failed {SOURCE_FAILURES}+ runs in a row.",
                                "run the probe (scale_up_listing) to see which and why"))
    if counts.get("stuck_jobs", 0) >= STUCK_JOBS:
        out.append(alerts.Alert("stuck_jobs", alerts.INFO, "Jobs stuck before publish", f"{counts['stuck_jobs']} job(s) waiting over 24 hours.",
                                "check enrich and fit counts in the latest run"))
    for key, item in (expiries or {}).items():
        days = (dt.date.fromisoformat(item["expires"]) - now.date()).days
        if days <= 7:
            state = "expired" if days < 0 else f"expires in {days} day(s)"
            out.append(alerts.Alert(f"expiry_{key}", alerts.PAGE, f"{item['label']} {state}", "", item["renew"]))
    return out


def gather(connection, now):
    cutoff = now - dt.timedelta(hours=QUIET_HOURS)
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE status='PUBLISHED' AND updated_at > %s", (cutoff,))
        published = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM v7_sources WHERE failures >= %s", (SOURCE_FAILURES,))
        failing = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE status IN ('READY','RESOLVED') AND updated_at < %s", (cutoff,))
        stuck = cursor.fetchone()[0]
    return {"published_24h": published, "sources_failing": failing, "stuck_jobs": stuck}


def load_state(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT alert_key, severity, last_notified FROM v7_alerts WHERE status='OPEN'")
        return {k: {"severity": s, "last_notified": n} for k, s, n in cursor.fetchall()}


def save(connection, plan, notified, now):
    """notified: the keys whose notification was delivered (a failed push is retried next run, not silently marked sent)."""
    with connection.cursor() as cursor:
        for alert in plan.opened:
            cursor.execute("INSERT INTO v7_alerts (alert_key, severity, status, first_seen, last_seen, last_notified) VALUES (%s,%s,'OPEN',%s,%s,%s) "
                           "ON DUPLICATE KEY UPDATE severity=VALUES(severity), status='OPEN', first_seen=VALUES(first_seen), last_seen=VALUES(last_seen), last_notified=VALUES(last_notified)",
                           (alert.key, alert.severity, now, now, now if alert.key in notified else None))
        for alert in plan.kept:
            cursor.execute("UPDATE v7_alerts SET last_seen=%s, last_notified=IF(%s, %s, last_notified) WHERE alert_key=%s",
                           (now, alert.key in notified, now, alert.key))
        for key in plan.resolved:
            cursor.execute("UPDATE v7_alerts SET status='RESOLVED', last_seen=%s WHERE alert_key=%s", (now, key))
    connection.commit()


def run(limit, live, environ=os.environ, now=None, send=alerts.ntfy):
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    expiries = json.loads(EXPIRIES.read_text())
    with store.connect() as connection:
        store.ensure_schema(connection)
        active = detect(gather(connection, now), now, expiries)
        plan = alerts.reconcile(active, load_state(connection), now)
        if not live:
            return {"active": len(active), "would_notify": len(plan.notify), "opened": len(plan.opened), "resolved": len(plan.resolved)}
        sent = alerts.deliver(plan, environ.get("NTFY_TOPIC", ""), send)
        delivered = {item.key for kind, item in plan.notify if kind != "resolved"} if sent["failed"] == 0 else set()
        save(connection, plan, delivered, now)
    return {"active": len(active), "opened": len(plan.opened), "resolved": len(plan.resolved), **sent}
