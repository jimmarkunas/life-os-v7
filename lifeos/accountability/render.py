"""Pure text for the V3 accountability regions (LIFE-671): no Notion, no Jira, no clock. Every line is plain text; a summary page is at most SUMMARY_MAX characters."""
from lifeos.accountability import read
from lifeos.platform import ct_stamp

SUMMARY_MAX = 1000
BODY_MAX = 1800                       # one Notion text item holds 2,000 characters
SHOWN = 12
NO_EPICS = "No quarter epics yet for this quarter."
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


NO_INDEX = "The artifact index is not set up yet."
NO_ARTIFACTS = "No verified artifacts indexed yet."
REVIEWS_NONE = "Review pages are not indexed yet."


def artifact_directory(items):
    """-> (parts, plain lines): one linked title per verified artifact, in index order; `None` (no index page) and an empty list each say so plainly."""
    if items is None:
        return [p(NO_INDEX)], [NO_INDEX]
    if not items:
        return [p(NO_ARTIFACTS)], [NO_ARTIFACTS]
    parts, lines = [], []
    for item in items[:MAX_PARTS // 2]:
        parts += ([NL] if parts else []) + [p(item["title"], item["url"])]
        lines.append(f"{item['title']} — {item['url']}")
    return parts, lines


def combined_heading(person, year, n):
    return f"Quarterly Goals & Review — {person} · Q{n} {year}"


def review_parts(links):
    """-> the Quarterly Reviews toggle's paragraph: each review page linked, the current quarter first; None (no index) and an empty list say so plainly."""
    if not links:
        return [p(REVIEWS_NONE)]
    parts = []
    for label, url in links:
        parts += ([NL] if parts else []) + [p(label, url)]
    return parts


def fit(parts):
    """Notion holds MAX_PARTS pieces in one paragraph: drop whole trailing lines until it fits (a "…and N more" link already points at Jira)."""
    parts = list(parts)
    while len(parts) > MAX_PARTS:
        parts = parts[:max(i for i, part in enumerate(parts[:-1]) if part == NL)]
    return parts


def summary(title, stamp_text, lines):
    return cut("\n".join([f"{title} · {stamp_text}"] + list(lines)), SUMMARY_MAX)


def changelog_line(first, year, n):
    return f"{first.strftime('%b')} {first.day} {first.year} — Q{n} {year} began (first Wednesday of the quarter): Jim and Matt create the quarter's epics, labelled goals-{year}-q{n}"
