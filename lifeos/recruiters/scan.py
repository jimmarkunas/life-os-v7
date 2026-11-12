"""LIFE-610: which mail read the Recruiters mail step does this run. Pure logic; the clock, the ledger times and the table row are passed in.

Every 3 hours on a scheduled tick (the step is skipped in between), and the first read after 09:00 CT each day is the full 14-day scan, exactly as before. Manual runs are never skipped.
No ledger record (empty, unreadable, or no 14-day scan ever recorded) also means the full scan, and the reason says so."""
import datetime as dt
import json
from pathlib import Path

from lifeos.platform import due
from lifeos.platform.mail_seen import FULL_SCAN, NEW_MAIL

STEP = "recruiters_mail"


def step_row(path=due.TABLE):
    return json.loads(Path(path).read_text())["steps"][STEP]


def plan(now, row, ledger_state, scheduled):
    """-> (action, kind, why). action: 'read' or 'skip'. ledger_state: None (no ledger available) or (last_read, last_kind, last_full) from seen.last_read."""
    if ledger_state is None:
        return "read", FULL_SCAN, "no ledger: full scan"
    last_read, _kind, last_full = ledger_state
    if last_full is None:
        return "read", FULL_SCAN, "no 14-day scan recorded: full scan"
    if due.slot_passed(row["full_scan_ct"], last_full, now):
        return "read", FULL_SCAN, "daily " + ", ".join(row["full_scan_ct"]) + " CT scan"
    if scheduled and last_read and now - last_read < dt.timedelta(hours=row["every_hours"]) - due.SLACK:
        return "skip", NEW_MAIL, f"not due: every {row['every_hours']} h"
    return "read", NEW_MAIL, "new mail only"


def clock(moment):
    return moment.astimezone(due.CENTRAL).strftime("%-I:%M %p") + " CT"
