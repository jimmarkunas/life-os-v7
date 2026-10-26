"""Jobs OS alert detectors and the alerts stage (D53). Counts only. Detectors are pure functions of counts so they test without a database;
state (which alerts are open, when last notified) lives in v7_alerts so a reminder is not a repeat."""
import datetime as dt
import json
import os
import re
from pathlib import Path

from lifeos.jobs import daily, hold, store
from lifeos.platform import alerts

EXPIRIES = Path(__file__).with_name("expiries.json")
SOURCE_FAILURES = 3          # a sponsor that failed this many runs in a row
STUCK_JOBS = 25              # jobs sitting in READY/RESOLVED for over a day
QUIET_HOURS = 24             # no job published for this long
STOP_WINDOW_HOURS = 24       # SAFE-1.1: a source that normally delivers new jobs ...
STOP_BASELINE_DAYS = 14      # ... (measured over the 14 days before the window) ...
STOP_PER_DAY = 3.0           # ... at least this many a day, and delivered none in the window, has stopped
STUCK_WINDOW_HOURS, STUCK_MIN_JOBS, STUCK_SHARE = 72, 20, 0.5      # LIFE-74 M1: a source with at least 20 jobs first seen in 72 h, half or more still NEW or HOLD, has a stage that is not moving them (Lensa, Oct 6-17: 2 resolved of 109)
YIELD_WINDOW_DAYS, YIELD_MIN_JOBS, YIELD_FLOOR = 7, 30, 0.05       # LIFE-74 M2: at least 30 jobs in 7 days with under 5% published has stopped yielding (Scale-Up discovery, Oct 17: 1 job in 5 runs)
BOARD_FAIL_MIN_READS, BOARD_FAIL_SHARE = 100, 0.20                  # LIFE-74 M3: of the last two days' board reads, a fifth or more failed for a reason other than not_found
SEARCH_MIN_CALLS, SEARCH_FAIL_SHARE = 20, 0.20                    # LIFE-74 M6: of the last two days' search calls (discover, title watch), a fifth or more failed
HOLD_MIN, HOLD_GROWTH, HOLD_DAYS = 25, 1.2, 3                     # LIFE-74 M4: the HOLD pile grew every day for three days, by a fifth or more, and holds at least 25 jobs; M5: it holds 25+ and nothing in it was touched in 24 h while it grew
KEEP_DAILY_DAYS = 8
BOARD_GROUPS = re.compile(r"web:(gh|ab|lv|su)-")                    # employer boards are watched by ATS family, never one by one (2,449 boards); every other web:* source is "web:other"
RETIRED_SOURCES = {"lensa": "Lensa retired (D201): Cloudflare blocks it from the runners", "web:openjobs": "Open Jobs ended: the project shut down on 10 Oct 2026 (D201)"}   # never "stopped"


def source_group(source):
    """The name a source is monitored under: its own for mail and search sources, its ATS family for employer boards; None for a retired source."""
    if source in RETIRED_SOURCES:
        return None
    if str(source).startswith("web:"):
        match = BOARD_GROUPS.match(source)
        return f"web:{match.group(1)}" if match else "web:other"
    return str(source)


def group_status_counts(rows):
    """rows: (source, status, n) -> {group: {status: n}}. Pure."""
    out = {}
    for source, status, n in rows:
        group = source_group(source)
        if group is not None:
            table = out.setdefault(group, {})
            table[status] = table.get(status, 0) + int(n or 0)
    return out


def yield_monitors(by_72h, by_7d):
    """-> {"stuck": {group: share}, "low_yield": {group: share}}: the groups past a threshold, with the share that tripped it. Pure functions of counts."""
    stuck, low = {}, {}
    for group, table in by_72h.items():
        total = sum(table.values())
        share = (table.get("NEW", 0) + table.get("HOLD", 0)) / total if total else 0
        if total >= STUCK_MIN_JOBS and share >= STUCK_SHARE:
            stuck[group] = round(share, 3)
    for group, table in by_7d.items():
        total = sum(table.values())
        share = table.get("PUBLISHED", 0) / total if total else 0
        if total >= YIELD_MIN_JOBS and share < YIELD_FLOOR:
            low[group] = round(share, 3)
    return {"stuck": dict(sorted(stuck.items())), "low_yield": dict(sorted(low.items()))}


def daily_monitors(rows, today):
    """rows: {name: {iso day: value}} from the daily counts. -> {monitor: value} for the monitors that tripped. Pure.
    M3 board_reads_failing, M6 search_failing: share failed over today and yesterday. M4 hold_rising: the HOLD snapshot rose on each of the last three days.
    M5 hold_not_retried: the HOLD pile is large and grew, yet no HOLD job was touched in the last 24 hours."""
    def total(name):
        return sum(rows.get(name, {}).get((today - dt.timedelta(days=back)).isoformat(), 0) for back in (0, 1))

    def snap(name, back):
        return rows.get(name, {}).get((today - dt.timedelta(days=back)).isoformat())
    out = {}
    reads, failed = total("board_reads"), total("board_failed")
    if reads >= BOARD_FAIL_MIN_READS and failed / reads >= BOARD_FAIL_SHARE:
        out["board_reads_failing"] = round(failed / reads, 3)
    calls, bad = total("search_calls"), total("search_failed")
    if calls >= SEARCH_MIN_CALLS and bad / calls >= SEARCH_FAIL_SHARE:
        out["search_failing"] = round(bad / calls, 3)
    pile = [snap("hold_total", back) for back in (HOLD_DAYS - 1, 1, 0)]
    if None not in pile and pile[0] < pile[1] < pile[2] and pile[2] >= HOLD_MIN and pile[2] >= HOLD_GROWTH * pile[0]:
        out["hold_rising"] = pile
    touched, now_pile, then = snap("hold_touched_24h", 0), snap("hold_total", 0), snap("hold_total", HOLD_DAYS - 1)
    if None not in (touched, now_pile, then) and now_pile >= HOLD_MIN and touched == 0 and now_pile > then:
        out["hold_not_retried"] = now_pile
    return out


def snapshot_hold(connection, now):
    """Today's HOLD pile and how much of it was touched in the last 24 hours, kept for the trend monitors (counts only); also drops rows older than the keep window."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*), SUM(updated_at > %s) FROM v7_jobs WHERE status='HOLD'", (now - dt.timedelta(hours=24),))
        total, touched = cursor.fetchone()
        daily.put(cursor, now.date(), "hold_total", int(total or 0))
        daily.put(cursor, now.date(), "hold_touched_24h", int(touched or 0))
        daily.prune(cursor, now.date())
    connection.commit()


def _key(group):
    return re.sub(r"[^a-z0-9]+", "_", group.lower()).strip("_")


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
    if counts.get("unresolved_terminal", 0) > 0:                                  # D185: worthwhile jobs that stayed unresolved after their one recovery attempt
        out.append(alerts.Alert("unresolved_terminal", alerts.INFO, "Jobs ended unresolved after recovery", f"{counts['unresolved_terminal']} worthwhile job(s) stayed unresolved after one recovery attempt in 24 hours.",
                                "run fit-review for the company, or check the resolver counts for that source"))
    for name, per_day in counts.get("stopped_sources", []):
        out.append(alerts.Alert(f"source_stopped_{name}", alerts.PAGE, f"Job source stopped: {name}",
                                f"No new jobs in {STOP_WINDOW_HOURS} hours; it normally brings about {per_day:g} a day.",
                                "open the source's last run (and its Gmail label or site) to see why"))
    found = yield_monitors(counts.get("yield_72h") or {}, counts.get("yield_7d") or {})
    for group, share in found["stuck"].items():
        out.append(alerts.Alert(f"source_stuck_{_key(group)}", alerts.INFO, f"Jobs stuck early: {group}", f"{share:.0%} of its jobs from the last {STUCK_WINDOW_HOURS} hours are still NEW or HOLD.",
                                "open the funnel report (report=funnel) and the source's resolver counts"))
    for group, share in found["low_yield"].items():
        out.append(alerts.Alert(f"source_yield_{_key(group)}", alerts.INFO, f"Low yield: {group}", f"Only {share:.0%} of its jobs from the last {YIELD_WINDOW_DAYS} days reached Notion.",
                                "open the funnel report (report=funnel) to see where its jobs end"))
    flagged = daily_monitors(counts.get("daily") or {}, now.date())
    if "board_reads_failing" in flagged:
        out.append(alerts.Alert("board_reads_failing", alerts.INFO, "Board reads failing", f"{flagged['board_reads_failing']:.0%} of board reads in the last two days failed (not counting boards that answer not found).",
                                "check the boards line of the latest run: failed_why shows the reasons"))
    if "search_failing" in flagged:
        out.append(alerts.Alert("search_failing", alerts.INFO, "Search calls failing", f"{flagged['search_failing']:.0%} of search calls in the last two days failed.",
                                "check the TinyFish key and the 429 count in the discover and title-watch lines"))
    if "hold_rising" in flagged:
        out.append(alerts.Alert("hold_rising", alerts.INFO, "HOLD pile growing", f"HOLD held {flagged['hold_rising'][0]}, then {flagged['hold_rising'][1]}, then {flagged['hold_rising'][2]} job(s) on the last three days.",
                                "open the funnel report to see which sources and reasons feed it"))
    if "hold_not_retried" in flagged:
        out.append(alerts.Alert("hold_not_retried", alerts.INFO, "HOLD jobs not being retried", f"{flagged['hold_not_retried']} job(s) on HOLD and none touched in 24 hours while the pile grew.",
                                "check the daily retry slot in enrich and the HOLD reasons"))
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
        cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE status='EXCLUDED_UNRESOLVED' AND unresolved_reason=%s AND updated_at > %s", (hold.FAILED_RECOVERY, cutoff))
        unresolved = cursor.fetchone()[0]
        window = now - dt.timedelta(hours=STOP_WINDOW_HOURS)
        start = window - dt.timedelta(days=STOP_BASELINE_DAYS)
        cursor.execute("SELECT source, SUM(first_seen > %s), SUM(first_seen <= %s) FROM v7_jobs WHERE source IS NOT NULL AND first_seen > %s GROUP BY source",
                       (window, window, start))
        rows = cursor.fetchall()
        cursor.execute("SELECT source_id FROM v7_sources WHERE last_complete_at > %s", (window,))
        healthy = {f"web:{row[0]}" for row in cursor.fetchall()}     # a board read in full inside the window is idle, not stopped
        cursor.execute("SELECT feed_id FROM v7_feed WHERE status='OK'")
        healthy |= {f"web:{row[0]}" for row in cursor.fetchall()}    # D178: the Open Jobs feed whose last run read it without error is idle, not stopped (a failed read stores DEGRADED, and that still pages)
        stopped = [(source, round(int(base) / STOP_BASELINE_DAYS, 1)) for source, recent, base in rows
                   if int(recent or 0) == 0 and int(base or 0) / STOP_BASELINE_DAYS >= STOP_PER_DAY and source not in healthy and source not in RETIRED_SOURCES]
        cursor.execute("SELECT source, status, COUNT(*) FROM v7_jobs WHERE source IS NOT NULL AND first_seen > %s GROUP BY source, status", (now - dt.timedelta(hours=STUCK_WINDOW_HOURS),))
        by_72h = group_status_counts(cursor.fetchall())
        cursor.execute("SELECT source, status, COUNT(*) FROM v7_jobs WHERE source IS NOT NULL AND first_seen > %s GROUP BY source, status", (now - dt.timedelta(days=YIELD_WINDOW_DAYS),))
        by_7d = group_status_counts(cursor.fetchall())
        daily_rows = daily.read(cursor, (now - dt.timedelta(days=KEEP_DAILY_DAYS)).date())
    return {"published_24h": published, "sources_failing": failing, "stuck_jobs": stuck, "unresolved_terminal": unresolved, "stopped_sources": sorted(stopped),
            "yield_72h": by_72h, "yield_7d": by_7d, "daily": daily_rows}


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
        if live:
            snapshot_hold(connection, now)                                                 # before the read, so today's HOLD pile is part of what the monitors see
        counts = gather(connection, now)
        active = detect(counts, now, expiries)
        monitors = yield_monitors(counts["yield_72h"], counts["yield_7d"])             # LIFE-74: printed in the log line, so the monitors are readable while pushes are suspended
        plan = alerts.reconcile(active, load_state(connection), now)
        if not live:
            return {"active": len(active), "would_notify": len(plan.notify), "opened": len(plan.opened), "resolved": len(plan.resolved), "monitors": {**monitors, **daily_monitors(counts["daily"], now.date())}}
        sent = alerts.deliver(plan, environ.get("NTFY_TOPIC", ""), send)
        delivered = {item.key for kind, item in plan.notify if kind != "resolved"} if sent["failed"] == 0 else set()
        save(connection, plan, delivered, now)
    return {"active": len(active), "opened": len(plan.opened), "resolved": len(plan.resolved), "monitors": {**monitors, **daily_monitors(counts["daily"], now.date())}, **sent}
