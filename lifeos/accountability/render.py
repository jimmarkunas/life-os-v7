"""Pure text for the V3 accountability regions: no Notion, no Jira, no clock.

A summary page is at most SUMMARY_MAX characters."""
from lifeos.accountability import read
from lifeos.platform import ct_stamp

SUMMARY_MAX = 1000
BODY_MAX = 1800
SHOWN = 12
NO_EPICS = "No quarter epics yet for this quarter."
ACCEPTED = "showing last accepted "


def stamp(now):
    return f"Last refreshed {ct_stamp.when(now)}"


def degraded_stamp(old, now, what="Jira"):
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


BAR = 10
GREEN, ORANGE, RED, GRAY = "green", "orange", "red", "gray"


def bar(done, total):
    """Ten-block Progress cell: filled blocks = Done / all; green at 70% or more, orange 30-69%, red under 30%."""
    share = done / total if total else 0.0
    filled = min(BAR, int(share * BAR + 0.5))
    color = GREEN if share >= 0.7 else ORANGE if share >= 0.3 else RED
    return ([("█" * filled, None, False, color)] if filled else []) + ([("█" * (BAR - filled), None, False, GRAY)] if filled < BAR else [])


def p(text, url=None, bold=False):
    return (text, url, bold)


NL = p("\n")
SECTION_OPEN, SECTION_DONE = "In progress / To do", "Done"
OPEN_SHOWN, DONE_SHOWN = 10, 8
MAX_PARTS = 95


def _issue(row, site, flag="", indent=""):
    rest = f" {row['summary']} ({row['status']}{flag})"
    return [NL, p(indent + row["key"], read.issue_url(site, row["key"])), p(rest)], f"{indent}{row['key']}{rest}"


def _group(title, rows, shown, site, more_url, flags=None, indent=None):
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


def _v3_week_view(week, today, site):
    open_rows, subtasks = week["open"], week["subtasks"]
    blocked, overdue, done_rows = week["blocked"], week["overdue"], week["done"]
    sub_count = sum(len(rows) for rows in subtasks.values())
    head = (f"{len(open_rows)} sprint commitments · {sub_count} sub-tasks under them · {len(blocked)} blocked outside sprint · "
            f"{len(overdue)} overdue backlog · {len(done_rows)} done this week")
    late = {r["key"] for r in open_rows if r["due"] and r["due"] < today.isoformat()}
    ordered = [r for r in open_rows if r["key"] in late] + [r for r in open_rows if r["key"] not in late]
    shown = ordered[:OPEN_SHOWN]
    parts, lines = [NL, p(f"Sprint commitments ({len(open_rows)})", bold=True)], [f"Sprint commitments ({len(open_rows)}):"]
    if not open_rows:
        parts, lines = parts + [NL, p("Nothing.")], lines + ["Nothing."]
    for row in shown:
        got, line = _issue(row, site, ", overdue" if row["key"] in late else "")
        parts, lines = parts + got, lines + [line]
        for sub in subtasks.get(row["key"], []):
            got, line = _issue(sub, site, "", "    · ")
            parts, lines = parts + got, lines + [line]
    if len(open_rows) > len(shown):
        more = f"…and {len(open_rows) - len(shown)} more in Jira"
        parts, lines = parts + [NL, p(more, read.jql_url(site, week["jql"] + " ORDER BY duedate ASC"))], lines + [more]
    blocked_parts, blocked_lines = _group("Blocked outside sprint", blocked, OPEN_SHOWN, site, read.jql_url(site, week["outside_jql"] + " ORDER BY duedate ASC"))
    overdue_parts, overdue_lines = _group("Overdue backlog", overdue, OPEN_SHOWN, site, read.jql_url(site, week["outside_jql"] + " ORDER BY duedate ASC"),
                                          flags={r["key"]: ", overdue" for r in overdue})
    done_more = read.jql_url(site, "issuetype != Sub-task AND statusCategoryChangedDate >= startOfWeek() AND statusCategory = Done")
    done_parts, done_lines = _group("Done this week", done_rows, DONE_SHOWN, site, done_more)
    return ([p(head)] + parts + [NL] + blocked_parts + [NL] + overdue_parts + [NL] + done_parts,
            [head] + lines + [""] + blocked_lines + [""] + overdue_lines + [""] + done_lines)


def week_view(week, today, site):
    """Render the JFM weekly view, MEJ's sprint/due view, or the V3 view both boards share."""
    if week.get("v3"):
        return _v3_week_view(week, today, site)
    open_rows, done_rows = week["open"], week["done"]
    if "outside" in week:
        outside = week["outside"]
        combined = open_rows + outside
        overdue = {r["key"] for r in combined if r["due"] and r["due"] < today.isoformat()}
        blocked = [r for r in combined if r["status"].lower() == "blocked"]
        head = (f"{len(open_rows)} sprint open · {len(outside)} due outside sprint · "
                f"{len(overdue)} overdue · {len(blocked)} blocked · {len(done_rows)} done this week")
        ordered = [r for r in open_rows if r["key"] in overdue] + [r for r in open_rows if r["key"] not in overdue]
        more = read.jql_url(site, week["jql"] + " ORDER BY duedate ASC")
        active_parts, active_lines = _group("Sprint commitments", ordered, OPEN_SHOWN, site, more,
                                            flags={k: ", overdue" for k in overdue})
        due_order = [r for r in outside if r["key"] in overdue] + [r for r in outside if r["key"] not in overdue]
        due_more = read.jql_url(site, week["outside_jql"] + " ORDER BY duedate ASC")
        due_parts, due_lines = _group("Due outside sprint", due_order, OPEN_SHOWN, site, due_more,
                                      flags={k: ", overdue" for k in overdue})
        done_more = read.jql_url(site, f'project = MEJ AND issuetype != Sub-task AND statusCategoryChangedDate >= startOfWeek() AND statusCategory = Done')
        done_parts, done_lines = _group("Done this week", done_rows, DONE_SHOWN, site, done_more)
        return ([p(head)] + active_parts + [NL] + due_parts + [NL] + done_parts,
                [head] + active_lines + [""] + due_lines + [""] + done_lines)

    overdue = {r["key"] for r in open_rows if r["due"] and r["due"] < today.isoformat()}
    blocked = [r for r in open_rows if r["status"].lower() == "blocked"]
    head = f"{len(open_rows)} open · {len(overdue)} overdue · {len(blocked)} blocked · {len(done_rows)} done this week"
    ordered = [r for r in open_rows if r["key"] in overdue] + [r for r in open_rows if r["key"] not in overdue]
    more = read.jql_url(site, week["jql"] + " ORDER BY duedate ASC")
    got_open, lines_open = _group(SECTION_OPEN, ordered, OPEN_SHOWN, site, more, flags={k: ", overdue" for k in overdue})
    got_done, lines_done = _group(SECTION_DONE + " this week", done_rows, DONE_SHOWN, site, read.jql_url(site, "statusCategoryChangedDate >= startOfWeek() AND statusCategory = Done"))
    return [p(head)] + got_open + [NL] + got_done, [head] + lines_open + lines_done

MAX_ARTIFACT_LINES = 40
NO_INDEX = "The artifact index is not set up yet."
NO_ARTIFACTS = "No verified artifacts indexed yet."
REVIEWS_NONE = "Review pages are not indexed yet."


def artifact_lines(items):
    """-> (lines, plain lines, bulleted): one linked line per verified artifact; None (no index) or empty gives one
    plain message line."""
    if items is None:
        return [[p(NO_INDEX)]], [NO_INDEX], False
    if not items:
        return [[p(NO_ARTIFACTS)]], [NO_ARTIFACTS], False
    shown = items[:MAX_ARTIFACT_LINES]
    return [[p(item["title"], item["url"])] for item in shown], [f"{item['title']} — {item['url']}" for item in shown], True


def combined_heading(person, year, n):
    return f"Quarterly Goals & Review — {person} · Q{n} {year}"


def review_parts(links):
    if not links:
        return [p(REVIEWS_NONE)]
    parts = []
    for label, url in links:
        parts += ([NL] if parts else []) + [p(label, url)]
    return parts


def fit(parts):
    parts = list(parts)
    while len(parts) > MAX_PARTS:
        parts = parts[:max(i for i, part in enumerate(parts[:-1]) if part == NL)]
    return parts


def summary(title, stamp_text, lines):
    return cut("\n".join([f"{title} · {stamp_text}"] + list(lines)), SUMMARY_MAX)


def changelog_line(first, year, n):
    return f"{first.strftime('%b')} {first.day} {first.year} — Q{n} {year} began (first Wednesday of the quarter): Jim and Matt create the quarter's epics, labelled goals-{year}-q{n}"
