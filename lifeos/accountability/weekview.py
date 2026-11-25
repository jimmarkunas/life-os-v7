"""LIFE-918 AC3: the V3 weekly sprint view as native Notion blocks, to the approved wireframe.

Jim approved it Nov 25 2026, 3:19 AM CT; layout corrected 3:22 AM CT: Jim left, Matt right.

Pure: no Notion, no Jira, no clock. `spec` gives a list of block specs; a spec is {"type", "rich": [(text, link, bold, color)], "children": [spec], "icon": emoji, "color": block colour}.
`region.write_tree` turns a spec into blocks inside one person's week callout and reads it back. Blocks: a title line, a 2 x 2 grid of tile callouts (Sprint commitments and Overdue backlog left,
Blocked outside sprint and Done this week right; one big number, one paragraph holding the bold label and a gray line), the grouped lists with Sub-tasks under their parent, a Done this week toggle and the
gray "Next sprints" line of four short names (PROJECT W{week} {year}). Which side a board sits on is the V3 page's own layout (the Jim and Matt callouts are already paired Jim left, Matt right).
`python -m lifeos.accountability.weekview --synthetic` prints it from fixtures with no network."""
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.accountability import read
from lifeos.platform import ct_stamp

OPEN_SHOWN, DONE_SHOWN = 10, 8
AUTO = "auto-closed"
GRAY, RED = "gray", "red"
CHI = ZoneInfo("America/Chicago")


def r(text, link=None, bold=False, color=None):
    return (text, link, bold, color)


def node(kind, rich=(), children=(), **extra):
    return {"type": kind, "rich": list(rich), "children": list(children), **extra}


def sprint_name(project, monday):
    """The short sprint name of decision C: PROJECT W{iso week} {iso week-year}."""
    year, week = monday.isocalendar()[:2]
    return f"{project} W{week} {year}"


def _day(d):
    return f"{d.strftime('%a')} {d.strftime('%b')} {d.day}"


def _due(value):
    d = date.fromisoformat(value)
    return f"due {d.strftime('%b')} {d.day}"


def _line(row, site, bold=False, tail=(), color=None):
    return [r(row["key"], read.issue_url(site, row["key"]), bold, color), r(f" — {row['summary']}", None, bold, color), *tail]


def _bullet(rich, children=()):
    return node("bulleted_list_item", rich, children)


def _title(text):
    return node("paragraph", [r(text, None, True)])


def _more(count, shown, url):
    return _bullet([r(f"…and {count - shown} more in Jira", url)]) if count > shown else None


def _section(title, count, items):
    body = [i for i in items if i is not None]
    return [_title(f"{title} — {count}")] + (body or [node("paragraph", [r("Nothing.", None, False, GRAY)])])


def _tile(icon, number, label, line, red=False):
    return node("callout", [], [node("heading_3", [r(str(number), None, False, RED if red and number else None)]),
                                node("paragraph", [r(label, None, True), r("\n" + line, None, False, GRAY)])], icon=icon, color="default")


def spec(week, today, site, project, now):
    """-> the weekly view of one board as a list of block specs, from `read.week(..., v3_week_view=True)`."""
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    open_rows, subtasks, blocked, overdue, done = week["open"], week["subtasks"], week["blocked"], week["overdue"], week["done"]
    sub_count = sum(len(rows) for rows in subtasks.values())
    clock = ct_stamp.when(now)
    refreshed = clock.split(" · ", 1)[1]
    auto = sum(1 for row in done if AUTO in (row.get("labels") or []))
    title = f"{sprint_name(project, monday)} · {_day(monday)} – {_day(sunday)} ({clock})"
    tiles = node("column_list", [], [
        node("column", [], [_tile("📋", len(open_rows), "Sprint commitments", f"+ {sub_count} sub-tasks"),
                            _tile("📅", len(overdue), "Overdue backlog", f"{project} · {refreshed}", True)]),
        node("column", [], [_tile("⛔", len(blocked), "Blocked outside sprint", f"{project} · {refreshed}", True),
                            _tile("✅", len(done), "Done this week", f"{auto} auto-closed · {len(done) - auto} by hand")])])
    late = {row["key"] for row in open_rows if row["due"] and row["due"] < today.isoformat()}
    commitments = []
    for row in open_rows[:OPEN_SHOWN]:
        status = r(" · "), (r(row["status"], None, False, RED) if row["status"].lower() == "blocked" else r(row["status"]))
        tail = [*status, *([r(" · "), r(_due(row["due"]), None, False, RED)] if row["key"] in late else [])]
        kids = [_bullet(_line(sub, site, tail=[r(" · "), r(sub["status"])])) for sub in subtasks.get(row["key"], [])]
        commitments.append(_bullet(_line(row, site, True, tail), kids))
    commitments.append(_more(len(open_rows), OPEN_SHOWN, read.jql_url(site, week["jql"] + " ORDER BY duedate ASC")))
    outside_url = read.jql_url(site, week["outside_jql"] + " ORDER BY duedate ASC")
    blocked_items = [_bullet(_line(row, site)) for row in blocked[:OPEN_SHOWN]] + [_more(len(blocked), OPEN_SHOWN, outside_url)]
    overdue_items = [_bullet(_line(row, site, tail=[r(" · "), r(_due(row["due"]), None, False, RED)])) for row in overdue[:OPEN_SHOWN]] + [_more(len(overdue), OPEN_SHOWN, outside_url)]
    done_url = read.jql_url(site, "issuetype != Sub-task AND statusCategoryChangedDate >= startOfWeek() AND statusCategory = Done")
    done_items = [_bullet(_line(row, site, tail=[r(" · " + AUTO)] if AUTO in (row.get("labels") or []) else [])) for row in done[:DONE_SHOWN]] + [_more(len(done), DONE_SHOWN, done_url)]
    done_body = [i for i in done_items if i is not None] or [node("paragraph", [r("Nothing.", None, False, GRAY)])]
    nxt = " · ".join(sprint_name(project, monday + timedelta(days=7 * n)) for n in range(1, 5))
    return ([_title(title), tiles] + _section("Sprint commitments", len(open_rows), commitments) + _section("Blocked outside sprint", len(blocked), blocked_items)
            + _section("Overdue backlog", len(overdue), overdue_items) + [node("toggle", [r(f"Done this week — {len(done)}")], done_body),
                                                                         node("paragraph", [r(f"Next sprints: {nxt}", None, False, GRAY)])])


def plain(specs, depth=0):
    """The text of a spec tree, one line per block, children indented: for the tests and `--synthetic`."""
    out = []
    for item in specs:
        text = "".join(piece[0] for piece in item["rich"])
        if text or item["type"] not in ("column_list", "column", "callout"):
            out.append("    " * depth + text.replace("\n", " | "))
        out += plain(item["children"], depth + 1)
    return out


def _fixture(project):
    p = project

    def row(key, status="To Do", due=None, labels=()):
        return {"key": key, "summary": f"Synthetic {key}", "status": status, "due": due, "done": status == "Done", "labels": list(labels)}
    week = {"v3": True, "open": [row(f"{p}-1", "In Progress"), row(f"{p}-2"), row(f"{p}-3", "Blocked")],
            "subtasks": {f"{p}-1": [row(f"{p}-11"), row(f"{p}-12", "In Progress")]}, "subtasks_elsewhere": 0,
            "blocked": [row(f"{p}-21", "Blocked"), row(f"{p}-22", "Blocked")], "overdue": [row(f"{p}-20", "To Do", "2026-11-20")],
            "done": [row(f"{p}-30", "Done", None, [AUTO])], "jql": f"project = {p}", "outside_jql": f"project = {p} AND sprint is EMPTY"}
    return week


def main(argv):
    if list(argv) != ["--synthetic"]:
        print("usage: python -m lifeos.accountability.weekview --synthetic")
        return 2
    now, today = datetime(2026, 11, 25, 8, 0, tzinfo=CHI), date(2026, 11, 25)
    for project in ("JFM", "MEJ"):
        print("\n".join(plain(spec(_fixture(project), today, "https://example.invalid", project, now))))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
