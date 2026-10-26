"""Daily counts (LIFE-74): a few integers per day and name that the stages' own counts lines cannot give the monitors, because a log line is gone after a run and the store keeps only a
job's CURRENT status. Board reads and failures, search calls and failures, and the HOLD pile are kept here for about two weeks. Counts only; no title, company or link.
`record` is called by lifeos.run after a live stage and never raises: a missed row costs a monitor one day of evidence, never a run. Days are UTC dates, like the TinyFish spend."""
import datetime as dt

from lifeos.jobs import store

KEEP_DAYS = 14


def deltas(stage, counts):
    """-> {name: n} to ADD for one stage's counts line. Pure; a stage with nothing to say returns {}."""
    if not isinstance(counts, dict):
        return {}
    num = lambda key: counts.get(key) if type(counts.get(key)) is int else 0                  # noqa: E731
    if stage in ("boards", "boards-backfill", "web", "web-scale-up"):
        failed = num("failed")
        dead = counts.get("failed_why", {}).get("not_found", 0) if isinstance(counts.get("failed_why"), dict) else 0       # a dead board answers not_found: tracked in dead_ids, not a failure of the reader
        return {"board_reads": num("complete") + failed, "board_failed": max(0, failed - (dead if type(dead) is int else 0))}
    if stage == "discover-jobs":
        return {"search_calls": num("searched") + num("search_errors"), "search_failed": num("search_errors")}
    if stage == "web-titlewatch":
        return {"search_calls": num("queries"), "search_failed": num("errors")}
    return {}


def today(now=None):
    return (now or dt.datetime.now(dt.timezone.utc)).date()


def add(cursor, day, name, n):
    cursor.execute("INSERT INTO v7_daily_counts (day, name, value) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE value=value+VALUES(value)", (day, name, int(n)))


def put(cursor, day, name, n):
    """Set a snapshot (the HOLD pile): the last run of the day wins."""
    cursor.execute("INSERT INTO v7_daily_counts (day, name, value) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE value=VALUES(value)", (day, name, int(n)))


def read(cursor, since):
    """-> {name: {iso day: value}} for the days from `since` on."""
    cursor.execute("SELECT day, name, value FROM v7_daily_counts WHERE day >= %s", (since,))
    out = {}
    for day, name, value in cursor.fetchall():
        out.setdefault(name, {})[day.isoformat() if hasattr(day, "isoformat") else str(day)] = int(value or 0)
    return out


def prune(cursor, day):
    cursor.execute("DELETE FROM v7_daily_counts WHERE day < %s", (day - dt.timedelta(days=KEEP_DAYS),))


def record(stage, counts, connect=None, now=None):
    """Add one live stage's deltas to today's row. Best effort; returns the deltas written (or {})."""
    found = {name: n for name, n in deltas(stage, counts).items() if n}
    if not found:
        return {}
    try:
        with (connect or store.connect)() as connection:
            with connection.cursor() as cursor:                                                  # the stages that ran before this call have already made the table (store.SCHEMA)
                day = today(now)
                for name, n in found.items():
                    add(cursor, day, name, n)
            connection.commit()
        return found
    except Exception:                                                                           # noqa: BLE001 - evidence for a monitor must never fail a stage
        return {}
