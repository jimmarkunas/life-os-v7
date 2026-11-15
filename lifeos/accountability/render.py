"""Pure text for the V3 accountability regions (LIFE-671): no Notion, no Jira, no clock. Every line is plain text; a summary page is at most SUMMARY_MAX characters."""
import re

from lifeos.accountability import read
from lifeos.platform import ct_stamp

SUMMARY_MAX = 1000
BODY_MAX = 1800                       # one Notion text item holds 2,000 characters
SHOWN = 12
NO_EPICS = "No quarter epics yet for this quarter."
PAST_LINE = re.compile(r"^Q[1-4] \d{4} — closed")
ACCEPTED = "showing last accepted "


def stamp(now):
    return f"Last refreshed {ct_stamp.when(now)}"


def degraded_stamp(old, now, what="Jira"):
    """DEGRADED keeps the last accepted stamp it replaces; a region never accepted says so."""
    text = (old or "").strip()
    if text.startswith("DEGRADED") and ACCEPTED in text:
        accepted = text.split(ACCEPTED, 1)[1]
    elif ct_stamp.read(text) is not None and not text.lower().startswith("not updated"):
        accepted = text.replace("Last refreshed ", "")
    else:
        accepted = "nothing yet"
    return f"DEGRADED · {what} could not be read at {ct_stamp.when(now)} · {ACCEPTED}{accepted}"


def cut(text, limit):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def goal_cell(done, total):
    return f"{done} / {total}"


def p(text, url=None, bold=False):
    """One piece of a Notion paragraph: (text, link or None, bold)."""
    return (text, url, bold)


NL = p("\n")
SECTION_OPEN, SECTION_DONE = "In progress / To do", "Done"
OPEN_SHOWN, DONE_SHOWN = 10, 8
MAX_PARTS = 95                                    # Notion holds 100 text pieces in one paragraph


def _issue(row, site, flag="", indent=""):
    """-> (parts, plain line) for "KEY summary (status)", the key linked to the Jira issue."""
    rest = f" {row['summary']} ({row['status']}{flag})"
    return [NL, p(indent + row["key"], read.issue_url(site, row["key"])), p(rest)], f"{indent}{row['key']}{rest}"


def _group(title, rows, shown, site, more_url, flags=None, indent=None):
    """A bold heading with a count, then the first `shown` rows as linked lines, then "…and N more in Jira" linked to the filtered Jira view."""
    parts, lines = [NL, p(f"{title} ({len(rows)})", bold=True)], [f"{title} ({len(rows)}):"]
    if not rows:
        return parts + [NL, p("Nothing.")], lines + ["Nothing."]
    for row in rows[:shown]:
        got, line = _issue(row, site, (flags or {}).get(row["key"], ""), (indent or {}).get(row["key"], ""))
        parts, lines = parts + got, lines + [line]
    if len(rows) > shown:
        parts += [NL, p(f"…and {len(rows) - shown} more in Jira", more_url)]
        lines.append(f"…and {len(rows) - shown} more in Jira")
    return parts, lines


def week_view(week, today, site):
    """-> (parts, plain lines): the counts line, "In progress / To do" (overdue first), then "Done" this week; every key is a link to its Jira issue."""
    open_rows, done_rows = week["open"], week["done"]
    overdue = {r["key"] for r in open_rows if r["due"] and r["due"] < today.isoformat()}
    blocked = [r for r in open_rows if r["status"].lower() == "blocked"]
    head = f"{len(open_rows)} open · {len(overdue)} overdue · {len(blocked)} blocked · {len(done_rows)} done this week"
    ordered = [r for r in open_rows if r["key"] in overdue] + [r for r in open_rows if r["key"] not in overdue]
    more = read.jql_url(site, week["jql"] + " ORDER BY duedate ASC")
    got_open, lines_open = _group(SECTION_OPEN, ordered, OPEN_SHOWN, site, more, flags={k: ", overdue" for k in overdue})
    got_done, lines_done = _group(SECTION_DONE + " this week", done_rows, DONE_SHOWN, site, read.jql_url(site, "statusCategoryChangedDate >= startOfWeek() AND statusCategory = Done"))
    return [p(head)] + got_open + [NL] + got_done, [head] + lines_open + lines_done


NO_ARTIFACTS = "Nothing yet. An issue labelled artifact in Jira shows up here with its link."
MAX_ARTIFACTS = 6


def artifact_parts(items, site):
    """-> (parts, plain lines): the artifact title linked to its address, or "Missing link: KEY title" with the key linked to Jira."""
    if not items:
        return [p(NO_ARTIFACTS)], [NO_ARTIFACTS]
    ordered = [i for i in items if i["url"]] + [i for i in items if not i["url"]]
    parts, lines = [], []
    for item in ordered[:MAX_ARTIFACTS]:
        lead = [NL] if parts else []
        if item["url"]:
            parts += lead + [p(item["title"], item["url"])]
            lines.append(f"{item['title']} — {item['url']}")
        else:
            parts += lead + [p("Missing link: "), p(item["key"], read.issue_url(site, item["key"])), p(f" {item['title']}")]
            lines.append(f"Missing link: {item['key']} {item['title']}")
    if len(ordered) > MAX_ARTIFACTS:
        parts += [NL, p(f"…and {len(ordered) - MAX_ARTIFACTS} more labelled artifact in Jira", read.jql_url(site, 'labels = "artifact"'))]
        lines.append(f"…and {len(ordered) - MAX_ARTIFACTS} more")
    return parts, lines


def todo_view(items, site, project):
    """The current sprint's tasks, each followed by its sub-tasks (indented), split into "In progress / To do" and "Done"; a sub-task whose task is not in the sprint comes last."""
    more = read.jql_url(site, read.TODO_JQL.format(project=project))
    title = [NL, p("To-do lists, this week's sprint", bold=True)]
    if not items:
        return title + [NL, p("Nothing in a sprint yet.")], ["To-do lists, this week's sprint: nothing in a sprint yet."]
    keys = {i["key"] for i in items if not i["subtask"]}
    ordered = []
    for task in (i for i in items if not i["subtask"]):
        ordered += [task] + [i for i in items if i["subtask"] and i["parent"] == task["key"]]
    ordered += [i for i in items if i["subtask"] and i["parent"] not in keys]
    indent = {i["key"]: "  - " for i in items if i["subtask"]}
    got_open, lines_open = _group(SECTION_OPEN, [i for i in ordered if not i["done"]], OPEN_SHOWN, site, more, indent=indent)
    got_done, lines_done = _group(SECTION_DONE, [i for i in ordered if i["done"]], DONE_SHOWN, site, more, indent=indent)
    return title + got_open + [NL] + got_done, ["To-do lists, this week's sprint:"] + lines_open + lines_done


def fit(parts):
    """Notion holds MAX_PARTS pieces in one paragraph: drop whole trailing lines until it fits (a "…and N more" link already points at Jira)."""
    parts = list(parts)
    while len(parts) > MAX_PARTS:
        parts = parts[:max(i for i, part in enumerate(parts[:-1]) if part == NL)]
    return parts


def summary(title, stamp_text, lines):
    return cut("\n".join([f"{title} · {stamp_text}"] + list(lines)), SUMMARY_MAX)


def progress(lanes):
    """[(lane, done, total)] -> "Career 12 / 20 · Creative 3 / 4"."""
    return " · ".join(f"{name} {goal_cell(done, total)}" for name, done, total in lanes) or NO_EPICS


def closing(year, n, lanes):
    return f"Q{n} {year} — closed: " + progress(lanes)


def changelog_line(first, year, n):
    return f"{first.strftime('%b')} {first.day} {first.year} — Q{n} {year} began (first Wednesday of the quarter): Jim and Matt create the quarter's epics, labelled goals-{year}-q{n}"
