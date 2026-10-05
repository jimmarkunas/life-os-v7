"""D140: record ALL historical Amazon order mail in the canonical Amazon Orders data source, one calendar month at a time.

Each month is the normal ingest (stage.run) bounded by `after:` and `before:`, so every safety rule holds: only the three allowlisted senders, exactly one order id per
message, monotone lifecycle, one row per order id, mail filed only after the row reads back equal, replay changes nothing. A month that fails is reported and the
backfill moves on; running it again resumes harmlessly. AMAZON_SINCE=YYYY-MM-DD is the first day (default 2015-01-01). Dry run unless live; counts only."""
import os
from datetime import datetime, timezone

from . import stage

MONTH_LIMIT = 2000
SUM_KEYS = ("unreadable", "listed", "accepted", "review", "orders_new", "orders_updated", "orders_same", "filed", "already_filed", "failed")


def months(start, end):
    cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    while cursor < end:
        nxt = datetime(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1, tzinfo=timezone.utc)
        yield max(cursor, start), min(nxt, end)
        cursor = nxt


def run(limit, live, environ=os.environ, gmail=None, notion=None, now=None, ingest=None):
    now = now or datetime.now(timezone.utc)
    raw = (environ.get("AMAZON_SINCE") or "2015-01-01").strip()
    start = datetime.fromisoformat(raw + "T00:00:00+00:00") if len(raw) == 10 else datetime.fromisoformat(raw)
    ingest = ingest or stage.run
    total = {key: 0 for key in SUM_KEYS}
    total.update(months=0, months_failed=0, first_failed="")
    for lo, hi in months(start, now):
        total["months"] += 1
        try:
            counts = ingest(limit if limit and limit > 0 else MONTH_LIMIT, live, environ=environ, gmail=gmail, notion=notion, now=now, since=lo, until=hi, skip_unreadable=True)
        except stage.AmazonError as error:
            total["months_failed"] += 1
            total["first_failed"] = total["first_failed"] or f"{lo:%Y-%m}:{str(error)[:40]}"
            continue
        for key in SUM_KEYS:
            total[key] += counts.get(key, 0)
    return total
