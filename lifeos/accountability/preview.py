"""LIFE-918 AC4 and AC5: the v2 quarterly preview, shown inside the existing Quarterly Goals & Review blocks. OFF BY DEFAULT.

Through Q4 2026 the legacy (v1) figures stay official; this module only works out what v2 would say beside them. With the V2_QUARTER_SHADOW row exactly "on" the goals stage upgrades each person's own
Quarterly Goals & Review table in place (Lane, Progress, Done / all, New count (preview), Status); there is no separate preview block and no address row for one (Jim, comment 14216). Matt shows the v2
count per lane when an approved frozen quarter list exists, else a dash and a red reason: NEEDS Q1 MEMBER LIST while the list is not approved, DATA MISSING when the frozen list and Jira disagree. JFM and MEJ alike
("918 uniform new", Jim, comment 14228). A v2 problem never fails the goals stage and nothing is invented. Pure except `reading`, which only reads Jira; `python -m lifeos.accountability.preview --synthetic` needs no network."""
import re
import sys

from lifeos.accountability import read, render
from lifeos.platform.jira import JiraError

CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")
NOT_APPROVED = "ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED"
NEEDS, MISSING = "NEEDS Q1 MEMBER LIST", "DATA MISSING"
DASH = "—"


def _code(error):
    """The fixed code of an error, or a generic one: an error text is never shown."""
    text = str(error)
    return text if CODE.match(text) else "ACCOUNTABILITY_DEGRADED"


def reading(jira, person, mine, first, last):
    """-> {"applies": bool, "counts": {epic key: (done, all)} or None, "code": fixed code or None}. Both boards have a v2; a read that fails closed gives counts None and the fixed code (legacy is not this module's to hide)."""
    if not mine:
        return {"applies": False, "counts": None, "code": None}
    try:
        counts = read.tally(jira, mine, first, last, mej_metric_version="v2")
    except JiraError as error:
        return {"applies": True, "counts": None, "code": _code(error)}
    return {"applies": True, "counts": {e["key"]: tuple(counts[e["key"]]) for e in mine}, "code": None}


def cells(found, view):
    """-> {lane: [progress, done / all, new count, status]} for the table: each cell [(text, link, bold, color)]. `found` is [(lane, done, all, epic key)]; legacy figures are the official ones."""
    out = {}
    for lane, done, total, key in found:
        if not view["applies"]:
            new, status = DASH, ("OK", render.GREEN)
        elif view["counts"] is None:
            new, status = DASH, ((NEEDS if view["code"] == NOT_APPROVED else MISSING), render.RED)
        else:
            new, status = render.goal_cell(*view["counts"][key]), ("OK", render.GREEN)
        out[lane] = [render.bar(done, total), [(render.goal_cell(done, total), None, False, None)], [(new, None, False, None)], [(status[0], None, False, status[1])]]
    return out


def plain(table):
    return [" · ".join("".join(piece[0] for piece in cell) for cell in row) for row in table.values()]


SYNTHETIC = {"Jim": ([("Career", 5, 26, "JFM-9"), ("LIFE OS", 6, 9, "JFM-102")], {"applies": True, "counts": None, "code": NOT_APPROVED}),
             "Matt": ([("Career", 12, 29, "MEJ-1"), ("Creative", 4, 23, "MEJ-2")], {"applies": True, "counts": None, "code": NOT_APPROVED})}


def main(argv):
    """`--synthetic` prints the cells from fixed fixtures: no Jira, no Notion, no network, no write."""
    if list(argv) != ["--synthetic"]:
        print("usage: python -m lifeos.accountability.preview --synthetic")
        return 2
    for person, (found, view) in SYNTHETIC.items():
        print(f"Quarterly Goals & Review — {person}\n  " + "\n  ".join(plain(cells(found, view))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
