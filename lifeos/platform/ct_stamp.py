"""LIFE-577: the one helper that prints the current Central time in the format receipts and Jira comments use: `Nov 11 2026, 1:05 PM CT`.

    python -m lifeos.platform.ct_stamp

Central time (America/Chicago, so CST and CDT both read "CT"), 12-hour, no UTC. Receipts take their stamp from this output and never from a time typed by hand.
Stdlib only (`when` and `updated`, LIFE-660, build the card-heading stamp); the format is built by hand so it needs no 24-hour format string."""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def stamp(now=None):
    """`now`: an aware datetime in any zone (tests pass one); default the system clock."""
    moment = (now if now is not None else datetime.now(CENTRAL)).astimezone(CENTRAL)
    hour = moment.hour % 12 or 12
    return f"{MONTHS[moment.month - 1]} {moment.day} {moment.year}, {hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'} CT"


def when(moment):
    """LIFE-660: the one form every Daily Report card heading shows a moment in: `Nov 13 · 3:31 PM CT` (Central, 12-hour, `Mon D` with no year, a middle dot). An aware moment in any zone is converted;
    a naive one is already Central wall time (the cards hold theirs that way)."""
    moment = moment.replace(tzinfo=CENTRAL) if moment.tzinfo is None else moment.astimezone(CENTRAL)
    return f"{MONTHS[moment.month - 1]} {moment.day} · {moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'} CT"


def updated(moment):
    """`Updated Nov 13 · 3:31 PM CT`: the stamp inside a card heading, `<Title> (Updated Nov 13 · 3:31 PM CT)`. DEGRADED and STALE headings use `when` after their own words."""
    return "Updated " + when(moment)


def main(argv=None):
    print(stamp())
    return 0


if __name__ == "__main__":
    sys.exit(main())
