"""LIFE-664: how many live runs in a row each sent-mail thread has been unreadable, kept in the private daily-counts table the live stages already write to.

One row per thread and day, named by a short hash of the thread id (no id is stored). The value is the run streak at that day's last live run; a thread read again is set to 0. A dry run reads and never writes. Any store problem leaves the streaks empty, so a thread stays `failed` rather than going quiet."""
import re
from datetime import timedelta
from hashlib import sha1

from lifeos.jobs import daily, store

PREFIX = "mailgone_"
FAILED_RUNS = 2                                  # an unreadable thread is `failed` for its first two runs and `permanent` from the third


def label(thread):
    return PREFIX + sha1(thread.encode()).hexdigest()[:12]


def previous(connect=None, now=None):
    """-> {label: the streak at the last live run}, or None when the store cannot be read."""
    since = daily.today(now) - timedelta(days=daily.KEEP_DAYS)
    try:
        with (connect or store.connect)() as connection, connection.cursor() as cursor:
            rows = daily.read(cursor, since)
    except Exception:                                                                            # noqa: BLE001 - never fails the stage; a thread then stays failed
        return None
    return {name: values[max(values)] for name, values in rows.items() if re.fullmatch(PREFIX + "[0-9a-f]{12}", name) and values}


def save(streaks, before, connect=None, now=None):
    """Write today's streak for each unreadable label and reset the ones that were counting and are not now. -> False when the write failed."""
    reset = {name: 0 for name, n in (before or {}).items() if n and name not in streaks}
    try:
        with (connect or store.connect)() as connection, connection.cursor() as cursor:
            for name, n in {**reset, **streaks}.items():
                daily.put(cursor, daily.today(now), name, n)
            connection.commit()
    except Exception:                                                                            # noqa: BLE001
        return False
    return True
