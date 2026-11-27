"""The v2 quarterly preview inside the Quarterly Goals & Review blocks.

OFF BY DEFAULT; legacy (v1) figures stay official. With V2_QUARTER_SHADOW exactly "on" the goals table gains a Status
column: OK when the v2 read worked, else a red reason; the v2 count is computed but not shown until it is promoted. A v2 problem never fails the goals stage."""

import re
import sys

from lifeos.accountability import read, render
from lifeos.platform.jira import JiraError

CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")
NOT_APPROVED = "ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED"
NEEDS, MISSING, GAP = "NEEDS Q1 MEMBER LIST", "DATA MISSING", "BASELINE GAP"
DASH = "—"


def _code(error):
    text = str(error)
    return text if CODE.match(text) else "ACCOUNTABILITY_DEGRADED"


def reading(jira, person, mine, first, last):
    """-> {"applies": bool, "counts": {epic key: (done, all)} or None, "code": fixed code or None}."""
    if not mine:
        return {"applies": False, "counts": None, "code": None}
    try:
        counts = read.tally(jira, mine, first, last, mej_metric_version="v2")
    except JiraError as error:
        return {"applies": True, "counts": None, "code": _code(error)}
    return {
        "applies": True,
        "counts": {e["key"]: None if counts[e["key"]] is None else tuple(counts[e["key"]]) for e in mine},
        "code": None,
    }


def cells(found, view):
    """-> {lane: [progress, done / all, status]}, cells [(text, link, bold, color)]. `found` is [(lane,
    done, all, epic key)]."""
    out = {}
    for lane, done, total, key in found:
        if not view["applies"]:
            status = ("OK", render.GREEN)
        elif view["counts"] is None:
            status = ((NEEDS if view["code"] == NOT_APPROVED else MISSING), render.RED)
        elif view["counts"][key] is None:
            status = (GAP, render.RED)
        else:
            status = ("OK", render.GREEN)
        out[lane] = [
            render.bar(done, total),
            [(render.goal_cell(done, total), None, False, None)],
            [(status[0], None, False, status[1])],
        ]
    return out


def plain(table):
    return [" · ".join("".join(piece[0] for piece in cell) for cell in row) for row in table.values()]


SYNTHETIC = {
    "Jim": (
        [("Career", 5, 26, "JFM-9"), ("LIFE OS", 6, 9, "JFM-102")],
        {"applies": True, "counts": None, "code": NOT_APPROVED},
    ),
    "Matt": (
        [("Career", 12, 29, "MEJ-1"), ("Creative", 4, 23, "MEJ-2")],
        {"applies": True, "counts": None, "code": NOT_APPROVED},
    ),
}


def main(argv):
    if list(argv) != ["--synthetic"]:
        print("usage: python -m lifeos.accountability.preview --synthetic")
        return 2
    for person, (found, view) in SYNTHETIC.items():
        print(f"Quarterly Goals & Review — {person}\n  " + "\n  ".join(plain(cells(found, view))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
