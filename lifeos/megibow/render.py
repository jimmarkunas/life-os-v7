"""MegIBOW block text (D134). Pure: projection -> the table rows and the note lines. No names, only counts and fixed words."""
from lifeos.megibow import classify as C
from lifeos.megibow import project as P
from lifeos.megibow import warnings as W
from lifeos.megibow.windows import chicago
from lifeos.platform import ct_stamp

DASH = "–"


def header(weeks):
    """Each weekly column is headed by the Monday it begins on; the open week says so."""
    labels = [f"{w.strftime('%b')} {w.day}" for w in weeks]
    labels[-1] += " (current)"
    return ["Activity", "Cumulative"] + labels


def _cell(value):
    return DASH if value is None else str(value)


def table(proj):
    """7 rows x 10 columns of strings: header, the five activities, then Jim — Total last (the card writes that last row in bold)."""
    rows = [header(proj["weeks"])]
    counts = [proj["counts"][w] for w in proj["weeks"]]
    for activity in C.ACTIVITIES:
        rows.append([activity, str(proj["cumulative"][activity])] + [_cell(c[activity] if c else None) for c in counts])
    rows.append(["Jim — Total", str(P.total(proj["cumulative"]))] + [_cell(P.total(c) if c else None) for c in counts])
    return rows


def status(now, degraded=False, review=0, reason=None):
    """LIFE-609: the callout's first line is `MegIBOW (Updated Nov 13 · 3:31 PM CT)`, the form every card heading takes (this callout has no heading block of its own)."""
    if degraded:
        return f"MegIBOW (DEGRADED at {ct_stamp.when(chicago(now))}) — counts may be incomplete" + (f" ({reason})" if reason else "")
    line = f"MegIBOW ({ct_stamp.updated(chicago(now))})"
    if review:
        line += f" · Review needed — {review} activity candidate{'s' if review != 1 else ''}"
    return line


def notes(warnings_list, degraded=False):
    if warnings_list:
        return "\n".join(warnings_list)
    return "" if degraded else W.HEALTHY
