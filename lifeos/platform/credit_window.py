"""LIFE-950: the monthly window of the promotional API credit: the 5th at 6:00 PM CT to the next 5th at 6:00 PM CT.

CT is America/Chicago, daylight time included.
The Honcho meter (lifeos/edge/honcho/meter_proxy.py) and Oracle's Anthropic report reader (lifeos/edge/anthropic_report/reader.py) are single files that run alone on Oracle, so each carries the same
12-line rule; tests/platform/test_credit_window.py pins that all three give the same answer. The Daily Report cards use this module. Stdlib only."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
PROMO_USD = 100.0                                                    # the credit that resets each window


def window(moment):
    """-> (start, end) as aware datetimes in CT: the window holding `moment`."""
    now = moment.astimezone(CENTRAL)
    year, month = now.year, now.month
    if now < datetime(year, month, 5, 18, tzinfo=CENTRAL):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    nyear, nmonth = (year + 1, 1) if month == 12 else (year, month + 1)
    return datetime(year, month, 5, 18, tzinfo=CENTRAL), datetime(nyear, nmonth, 5, 18, tzinfo=CENTRAL)


def epochs(moment):
    start, end = window(moment)
    return int(start.timestamp()), int(end.timestamp())


def since(moment):
    """-> the window start as the UTC timestamp Anthropic's report API takes (YYYY-MM-DDTHH:MM:SSZ)."""
    return window(moment)[0].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
