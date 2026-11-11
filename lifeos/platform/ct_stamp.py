"""LIFE-577: the one helper that prints the current Central time in the format receipts and Jira comments use: `Nov 11 2026, 1:05 PM CT`.

    python -m lifeos.platform.ct_stamp

Central time (America/Chicago, so CST and CDT both read "CT"), 12-hour, no UTC. Receipts take their stamp from this output and never from a time typed by hand.
Stdlib only; the format is built by hand so it needs no 24-hour format string."""
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


def main(argv=None):
    print(stamp())
    return 0


if __name__ == "__main__":
    sys.exit(main())
