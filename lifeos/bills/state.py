"""Pure due-state buckets over a saved Bill Tracker snapshot."""
from datetime import date, timedelta

from .snapshot import BillsError, load

# The production contract's supported recurring cycles, exactly. Anything else (Lifetime, One Time, "4 Years", a cycle added later, none) is not
# processed automatically and is never "stale due".
SUPPORTED_CYCLES = {"Weekly", "Bi-Weekly", "Monthly", "45 Days", "60 Days", "90 Days", "Quarterly", "180 Days", "Yearly"}


def _day(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _recurring(row):
    return row.get("Cycle") in SUPPORTED_CYCLES


def classify(rows, today):
    active_unpaid = [row for row in rows if row.get("Status") == "Active" and row.get("Paid") is False and _recurring(row)]
    stale_due = [row for row in active_unpaid if (due := _day(row.get("Due Date"))) is not None and due < today]
    due_today = [row for row in active_unpaid if _day(row.get("Next Due")) == today]
    upper = today + timedelta(days=7)
    due_next_7_days = [row for row in active_unpaid
                       if (due := _day(row.get("Next Due"))) is not None and today < due <= upper]
    paid = [row for row in rows if row.get("Paid") is True]
    return {"stale_due": stale_due, "due_today": due_today, "due_next_7_days": due_next_7_days, "paid": paid}


def current(connection, today):
    try:
        snapshot = load(connection)
    except (TypeError, ValueError):
        raise BillsError("BILLS_SNAPSHOT_INVALID") from None
    if snapshot is None:
        raise BillsError("BILLS_SNAPSHOT_MISSING")
    if snapshot.get("schema") != 1 or not isinstance(snapshot.get("rows"), list):
        raise BillsError("BILLS_SNAPSHOT_INVALID")
    if isinstance(today, str):
        today = _day(today)
    if not isinstance(today, date):
        raise BillsError("BILLS_DATE_INVALID")
    return classify(snapshot["rows"], today)
