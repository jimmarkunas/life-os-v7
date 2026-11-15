"""Read-only Jira reads for the V3 accountability regions (LIFE-671). GET only; nothing is written to Jira.

A quarter is a calendar quarter; its epics carry the label goals-<year>-q<n> (LIFE-670). Because an epic keeps its older Done tasks, a quarter counts only tasks that are open now or were
completed inside that quarter. Output is plain dicts for the renderers; ticket text goes to Notion only, never to a log."""
import re
from datetime import date, timedelta

from lifeos.platform.jira import JiraError

PEOPLE = (("Jim", "JFM"), ("Matt", "MEJ"))
WEEK_FIELDS = "summary,status,duedate,issuetype"
EPIC_FIELDS = "summary"
CHILD_FIELDS = "status,parent,resolutiondate,issuetype"
PAGE, MAX_PAGES = 100, 30
TAIL = re.compile(r"\s+Q[1-4]\s+\d{4}$")


def search(jira, jql, fields):
    """Every issue a JQL matches through Jira's enhanced search (token paging, bounded). Read only; errors are fixed codes."""
    out, token = [], None
    for _ in range(MAX_PAGES):
        data = jira.get("/rest/api/3/search/jql", {"jql": jql, "fields": fields, "maxResults": PAGE, **({"nextPageToken": token} if token else {})})
        issues = data.get("issues") if isinstance(data, dict) else None
        if not isinstance(issues, list):
            raise JiraError("ACCOUNTABILITY_JIRA_BAD_PAGE")
        out.extend(issues)
        token = data.get("nextPageToken")
        if data.get("isLast", not token) or not token:
            return out
    raise JiraError("ACCOUNTABILITY_JIRA_TOO_MANY_PAGES")


def quarter(today):
    """-> (year, n, first day, last day) of the calendar quarter holding `today`."""
    n = (today.month - 1) // 3 + 1
    first = date(today.year, 3 * n - 2, 1)
    nxt = date(today.year + (n == 4), 1 if n == 4 else 3 * n + 1, 1)
    return today.year, n, first, nxt - timedelta(days=1)


def quarter_bounds(year, n):
    """-> (first day, last day) of quarter n of `year`."""
    return quarter(date(year, 3 * n - 2, 1))[2:]


def previous(year, n):
    return (year - 1, 4) if n == 1 else (year, n - 1)


def label(year, n):
    return f"goals-{year}-q{n}"


def lane(summary):
    """"Career Q4 2026" -> "Career"."""
    return TAIL.sub("", summary or "").strip()


def person_of(key):
    project = key.split("-")[0]
    return next((name for name, prefix in PEOPLE if prefix == project), None)


def epics(jira, year, n):
    """-> [{key, lane, person}] for the quarter's epics, in key order. None of them is an error only for the caller to say so plainly."""
    found = search(jira, f'issuetype = Epic AND labels = "{label(year, n)}" ORDER BY key ASC', EPIC_FIELDS)
    return [{"key": i["key"], "lane": lane((i.get("fields") or {}).get("summary")), "person": person_of(i["key"])} for i in found]


def _counts(children, first, last):
    """{epic: [done, all]}: open tasks count in all; a completed task counts only when it was resolved inside [first, last]. Sub-tasks and Epics are not counted."""
    out = {}
    for issue in children:
        fields = issue.get("fields") or {}
        if (fields.get("issuetype") or {}).get("subtask") or (fields.get("issuetype") or {}).get("name") == "Epic":
            continue
        parent = (fields.get("parent") or {}).get("key")
        if not parent:
            continue
        done = ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done"
        if done:
            resolved = (fields.get("resolutiondate") or "")[:10]
            if not resolved or not first.isoformat() <= resolved <= last.isoformat():
                continue
        row = out.setdefault(parent, [0, 0])
        row[0] += int(done)
        row[1] += 1
    return out


def tally(jira, found, first, last):
    """-> {epic key: [done, all]} for every epic in `found` (an epic with no tasks is [0, 0])."""
    if not found:
        return {}
    keys = ",".join(e["key"] for e in found)
    rows = _counts(search(jira, f"parent in ({keys})", CHILD_FIELDS), first, last)
    return {e["key"]: rows.get(e["key"], [0, 0]) for e in found}


def unparented(jira, project, epic_keys):
    """How many open non-epic, non-sub-task issues of the project sit under none of the quarter's epics."""
    count = 0
    for issue in search(jira, f"project = {project} AND issuetype != Epic AND statusCategory != Done", "parent,issuetype"):
        fields = issue.get("fields") or {}
        if (fields.get("issuetype") or {}).get("subtask"):
            continue
        if (fields.get("parent") or {}).get("key") not in epic_keys:
            count += 1
    return count


def week(jira, project, today):
    """-> {open: [{key, summary, status, category, due}], done: int, week_end} for one project: open work in an open sprint or due by Sunday (overdue included)."""
    sunday = today + timedelta(days=6 - today.weekday())
    monday = today - timedelta(days=today.weekday())
    base = f"project = {project} AND issuetype != Epic AND statusCategory != Done AND "
    tail = " ORDER BY duedate ASC, key ASC"
    try:
        found = search(jira, base + f'(sprint in openSprints() OR duedate <= "{sunday}")' + tail, WEEK_FIELDS)
    except JiraError as error:
        if not str(error).endswith("_400"):                # a project without sprints rejects the sprint clause (HTTP 400); any other failure is a real one
            raise
        found = search(jira, base + f'(statusCategory = "In Progress" OR duedate <= "{sunday}")' + tail, WEEK_FIELDS)
    done = search(jira, f'project = {project} AND issuetype != Epic AND statusCategory = Done AND statusCategoryChangedDate >= "{monday}"', "summary")
    rows = []
    for issue in found:
        fields = issue.get("fields") or {}
        rows.append({"key": issue["key"], "summary": fields.get("summary") or "", "status": (fields.get("status") or {}).get("name") or "Unknown", "due": fields.get("duedate")})
    return {"open": rows, "done": len(done), "week_end": sunday}
