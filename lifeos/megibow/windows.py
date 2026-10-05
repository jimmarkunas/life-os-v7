"""MegIBOW weeks: Monday to Sunday in America/Chicago, the current week plus the prior seven (D134). Pure."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CHI = ZoneInfo("America/Chicago")
VISIBLE = 8


def chicago(moment):
    """A datetime (aware, or naive UTC) in Chicago time."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(CHI)


def monday(day):
    return day - timedelta(days=day.weekday())


def week_of(moment):
    """The Monday (a date) of the Chicago week that contains this moment."""
    return monday(chicago(moment).date())


def visible_weeks(today, count=VISIBLE):
    """Mondays, oldest first: the prior count-1 weeks and the current one."""
    start = monday(today)
    return [start - timedelta(days=7 * i) for i in range(count - 1, -1, -1)]


def window_utc(today, count=VISIBLE):
    """(start, end) as UTC datetimes: the Monday of the oldest visible week, 00:00 Chicago, to the start of next Monday."""
    first = visible_weeks(today, count)[0]
    start = datetime(first.year, first.month, first.day, tzinfo=CHI)
    end_day = monday(today) + timedelta(days=7)
    end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=CHI)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def forward_window_utc(now, days=14):
    return now.astimezone(timezone.utc), (now + timedelta(days=days)).astimezone(timezone.utc)


def elapsed_business_days(now):
    """Business days elapsed this Chicago week, counting today once it has started: Monday 1 ... Friday 5, then 5 on the weekend."""
    return min(chicago(now).weekday() + 1, 5)


def iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
