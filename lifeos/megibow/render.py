"""MegIBOW block text (D134). Pure: projection -> the table rows and the note lines. No names, only counts and fixed words."""
from lifeos.megibow import classify as C
from lifeos.megibow import project as P
from lifeos.megibow import warnings as W
from lifeos.megibow.windows import chicago

DASH = "–"
HEADER = ["Activity", "Cumulative"] + [f"Week -{n}" for n in range(7, 0, -1)] + ["Current Week"]


def _cell(value):
    return DASH if value is None else str(value)


def table(proj):
    """6 rows x 10 columns of strings: header, Jim — Total, then the five activities."""
    rows = [list(HEADER)]
    counts = [proj["counts"][w] for w in proj["weeks"]]
    rows.append(["Jim — Total", str(P.total(proj["cumulative"]))] + [_cell(P.total(c) if c else None) for c in counts])
    for activity in C.ACTIVITIES:
        rows.append([activity, str(proj["cumulative"][activity])] + [_cell(c[activity] if c else None) for c in counts])
    return rows


def status(now, degraded=False, review=0, reason=None):
    stamp = chicago(now).strftime("%-I:%M %p")
    if degraded:
        return f"DEGRADED — counts may be incomplete · last attempt {stamp} CT" + (f" ({reason})" if reason else "")
    line = f"Last refreshed {chicago(now).strftime('%a %b %-d')}, {stamp} CT"
    if review:
        line += f" · Review needed — {review} activity candidate{'s' if review != 1 else ''}"
    return line


def notes(warnings_list, degraded=False):
    if warnings_list:
        return "\n".join(warnings_list)
    return "" if degraded else W.HEALTHY
