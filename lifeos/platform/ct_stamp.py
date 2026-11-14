"""LIFE-577: the one helper that prints the current Central time in the format receipts and Jira comments use: `Nov 11 2026, 1:05 PM CT`.

    python -m lifeos.platform.ct_stamp

Central time (America/Chicago, so CST and CDT both read "CT"), 12-hour, no UTC. Receipts take their stamp from this output and never from a time typed by hand.
Stdlib only (`when`, LIFE-660, builds the Daily Report stamp and `read`, LIFE-679, reads it back); the format is built by hand so it needs no 24-hour format string."""
import re
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
    """LIFE-679: the one form every Daily Report card heading shows a moment in: `11/13 · 3:31 PM CT` (Central, 12-hour, month/day numbers with no leading zero and no year, a middle dot). An aware moment in any zone is converted;
    a naive one is already Central wall time (the cards hold theirs that way). A heading is `<Title> (11/13 · 3:31 PM CT)`; DEGRADED and STALE headings put their own words in front of the same text."""
    moment = moment.replace(tzinfo=CENTRAL) if moment.tzinfo is None else moment.astimezone(CENTRAL)
    return f"{moment.month}/{moment.day} · {moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'} CT"


# LIFE-679: what a reader accepts. The new `11/13 · 3:31 PM CT`, the LIFE-660 `Updated Nov 13 · 3:31 PM CT` and the time-only `Updated 3:31 PM CT` an older heading still carries until its card rewrites itself on a run.
STAMP = re.compile(r"(?:Updated (?:(?P<name>[A-Z][a-z]{2}) (?P<day>\d{1,2}) · )?|(?P<month>\d{1,2})/(?P<date>\d{1,2}) · )(?P<hour>\d{1,2}):(?P<minute>\d{2}) (?P<half>[AP]M) CT")


def read(text):
    """-> (month or None, day or None, hour 1-12, minute, "AM"|"PM") of the first stamp in `text` in any accepted form (None for the date of a time-only stamp), or None when there is none."""
    found = STAMP.search(text or "")
    if not found:
        return None
    month = day = None
    if found["month"] or found["name"]:
        month = int(found["month"]) if found["month"] else MONTHS.index(found["name"]) + 1 if found["name"] in MONTHS else 0
        day = int(found["date"] or found["day"])
        if not 1 <= month <= 12:
            return None
    return month, day, int(found["hour"]), int(found["minute"]), found["half"]


def main(argv=None):
    print(stamp())
    return 0


if __name__ == "__main__":
    sys.exit(main())
