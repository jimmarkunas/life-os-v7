"""Pure text for the V3 accountability regions (LIFE-671): no Notion, no Jira, no clock. Every line is plain text; a summary page is at most SUMMARY_MAX characters."""
import re

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


def week_lines(week, today):
    """-> list of lines: one counts line, then one "KEY summary (status)" per open item, overdue first; more than SHOWN is summarised."""
    open_rows = week["open"]
    overdue = [r for r in open_rows if r["due"] and r["due"] < today.isoformat()]
    blocked = [r for r in open_rows if r["status"].lower() == "blocked"]
    head = f"{len(open_rows)} open · {len(overdue)} overdue · {len(blocked)} blocked · {week['done']} done this week"
    if not open_rows:
        return [head, "Nothing open for this week."]
    ordered = overdue + [r for r in open_rows if r not in overdue]
    lines = [head]
    for r in ordered[:SHOWN]:
        flag = ", overdue" if r in overdue else ""
        lines.append(f"{r['key']} {r['summary']} ({r['status']}{flag})")
    if len(ordered) > SHOWN:
        lines.append(f"…and {len(ordered) - SHOWN} more in Jira")
    return lines


def body(lines):
    return cut("\n".join(lines), BODY_MAX)


def summary(title, stamp_text, lines):
    return cut("\n".join([f"{title} · {stamp_text}"] + list(lines)), SUMMARY_MAX)


def progress(lanes):
    """[(lane, done, total)] -> "Career 12 / 20 · Creative 3 / 4"."""
    return " · ".join(f"{name} {goal_cell(done, total)}" for name, done, total in lanes) or NO_EPICS


def closing(year, n, lanes):
    return f"Q{n} {year} — closed: " + progress(lanes)


NO_ARTIFACTS = "Nothing yet. An issue labelled artifact in Jira shows up here with its link."
MAX_ARTIFACTS = 25


def artifact_lines(items):
    """-> lines: "title — link" for each artifact with a link, then "Missing link: KEY title" for those without. None at all gives the plain empty line."""
    if not items:
        return [NO_ARTIFACTS]
    linked = [f"{i['title']} — {i['url']}" for i in items if i["url"]]
    missing = [f"Missing link: {i['key']} {i['title']}" for i in items if not i["url"]]
    lines = (linked + missing)[:MAX_ARTIFACTS]
    return lines + ([f"…and {len(linked) + len(missing) - MAX_ARTIFACTS} more in Jira"] if len(linked) + len(missing) > MAX_ARTIFACTS else [])


def changelog_line(today, year, n):
    return f"{today.strftime('%b')} {today.day} {today.year} — Q{n} {year} began; its quarter epics carry the label goals-{year}-q{n}"
