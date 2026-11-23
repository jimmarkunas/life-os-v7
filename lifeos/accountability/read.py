"""Read-only Jira reads for the V3 accountability regions (LIFE-671). GET only; nothing is written to Jira.

A quarter is a calendar quarter; its epics carry the label goals-<year>-q<n> (LIFE-670). Because an epic keeps its older Done tasks, a quarter counts only tasks that are open now or were
completed inside that quarter. Output is plain dicts for the renderers; ticket text goes to Notion only, never to a log."""
import json
import os
import re
from datetime import date, timedelta
from urllib.parse import quote, urlparse

from lifeos.platform.jira import JiraError

PEOPLE = (("Jim", "JFM"), ("Matt", "MEJ"))
WEEK_FIELDS = "summary,status,duedate,issuetype"
EPIC_FIELDS = "summary"
CHILD_FIELDS = "status,parent,resolutiondate,statuscategorychangedate,issuetype"
PAGE, MAX_PAGES = 100, 30
URL = re.compile(r"https?://[^\s\"\\<>)]+")
TAIL = re.compile(r"\s+Q[1-4]\s+\d{4}$")


def site_url(environ=None):
    """The address Jira issue links are built from: JIRA_SITE_URL, else JIRA_BASE_URL unless it is a gateway address (host starting "api."); None gives plain text without links."""
    environ = os.environ if environ is None else environ
    for raw in (environ.get("JIRA_SITE_URL"), environ.get("JIRA_BASE_URL")):
        value = (raw or "").strip().rstrip("/")
        if value.startswith("https://") and not (urlparse(value).hostname or "").startswith("api."):
            return value
    return None


def issue_url(site, key):
    return f"{site}/browse/{key}" if site else None


def jql_url(site, jql):
    return f"{site}/issues/?jql={quote(jql, safe='')}" if site else None


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


def first_wednesday(year, month):
    day = date(year, month, 1)
    return day + timedelta(days=(2 - day.weekday()) % 7)


def starts(year):
    """{n: first day of quarter n}: a quarter begins on the first Wednesday of January, April, July and October (Jim, Nov 15 2026), when Jim and Matt create its epics."""
    return {n: first_wednesday(year, 3 * n - 2) for n in (1, 2, 3, 4)}


def quarter(today):
    """-> (year, n, first day, last day) of the quarter `today` falls in; before January's first Wednesday it is still last year's quarter 4."""
    for year in (today.year, today.year - 1):
        for n in (4, 3, 2, 1):
            if starts(year)[n] <= today:
                return year, n, starts(year)[n], _next_start(year, n) - timedelta(days=1)
    raise ValueError("date out of range")


def _next_start(year, n):
    return starts(year)[n + 1] if n < 4 else starts(year + 1)[1]


def quarter_bounds(year, n):
    """-> (first day, last day) of quarter n of `year`."""
    return starts(year)[n], _next_start(year, n) - timedelta(days=1)


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


def _mej_project_goals(jira, quarter_keys):
    """Project Epic key -> quarter Epic key, by one explicit Jira Relates link.

    Only opt-in project Epics labelled 'mej-project' participate. A Relates
    link to a non-quarter issue cannot affect attribution. Multiple quarter
    targets are ambiguous and fail closed rather than falsify V3 metrics.
    The label and links are applied in MEJ only after Matt approves the exact
    migration; this reader never writes Jira.
    """
    if not quarter_keys:
        return {}
    projects = search(jira, 'project = MEJ AND issuetype = Epic AND labels = "mej-project" ORDER BY key ASC',
                      "issuetype,issuelinks,labels")
    out = {}
    for project in projects:
        found = set()
        for link in (project.get("fields") or {}).get("issuelinks") or []:
            if ((link.get("type") or {}).get("name") or "").lower() != "relates":
                continue
            for side in ("inwardIssue", "outwardIssue"):
                key = (link.get(side) or {}).get("key")
                if key in quarter_keys:
                    found.add(key)
        if len(found) > 1:
            raise JiraError("ACCOUNTABILITY_MEJ_GOAL_AMBIGUOUS")
        if found:
            out[project["key"]] = next(iter(found))
    return out


def _mej_project_tally(jira, project_goals, first, last):
    """Count each deliverable Task once, never project Epics or Sub-tasks.

    This is a new, explicitly versioned metric for project-linked quarter
    goals. A Done child counts only when the status-category transition
    occurred during the quarter, as the approved V3 source describes.
    Existing direct-parent metrics are left untouched until cutover.
    """
    if not project_goals:
        return {}
    keys = ",".join(sorted(project_goals))
    out = {}
    for issue in search(jira, f"parent in ({keys})", CHILD_FIELDS):
        fields = issue.get("fields") or {}
        kind = fields.get("issuetype") or {}
        if kind.get("subtask") or kind.get("name") == "Epic":
            continue
        parent = (fields.get("parent") or {}).get("key")
        goal = project_goals.get(parent)
        if not goal:
            continue
        done = ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done"
        stamp = (fields.get("statuscategorychangedate") or "")[:10] if done else ""
        if done and not stamp:
            # A missing transition cannot silently erase a completed Task from
            # the denominator or manufacture a quarter-specific completion.
            raise JiraError("ACCOUNTABILITY_MEJ_COMPLETION_DATE_MISSING")
        row = out.setdefault(goal, [0, 0])
        row[0] += int(done and first.isoformat() <= stamp <= last.isoformat())
        row[1] += 1
    return out


def tally(jira, found, first, last):
    """Quarterly direct-child counts plus opt-in MEJ linked-project Tasks."""
    if not found:
        return {}
    keys = ",".join(e["key"] for e in found)
    rows = _counts(search(jira, f"parent in ({keys})", CHILD_FIELDS), first, last)
    out = {e["key"]: rows.get(e["key"], [0, 0]) for e in found}
    matt_keys = {e["key"] for e in found if e["person"] == "Matt"}
    if matt_keys:
        linked = _mej_project_tally(jira, _mej_project_goals(jira, matt_keys), first, last)
        for key, (done, total) in linked.items():
            out[key][0] += done
            out[key][1] += total
    return out


def unparented(jira, project, epic_keys):
    """Count open standard work without an approved current-quarter mapping."""
    allowed = set(epic_keys)
    if project == "MEJ":
        allowed.update(_mej_project_goals(jira, allowed))
    count = 0
    for issue in search(jira, f"project = {project} AND issuetype != Epic AND statusCategory != Done", "parent,issuetype"):
        fields = issue.get("fields") or {}
        if (fields.get("issuetype") or {}).get("subtask"):
            continue
        if (fields.get("parent") or {}).get("key") not in allowed:
            count += 1
    return count


def _row(issue, **extra):
    fields = issue.get("fields") or {}
    status = fields.get("status") or {}
    return {"key": issue["key"], "summary": fields.get("summary") or "", "status": status.get("name") or "Unknown", "due": fields.get("duedate"),
            "done": (status.get("statusCategory") or {}).get("key") == "done", **extra}


def week(jira, project, today):
    """-> {open, done, jql} for one project: open work in an open sprint or due by Sunday (overdue included), and what was finished since Monday; each a list of {key, summary, status, due}."""
    sunday = today + timedelta(days=6 - today.weekday())
    monday = today - timedelta(days=today.weekday())
    base = f"project = {project} AND issuetype != Epic AND statusCategory != Done AND "
    tail = " ORDER BY duedate ASC, key ASC"
    clause = f'(sprint in openSprints() OR duedate <= "{sunday}")'
    try:
        found = search(jira, base + clause + tail, WEEK_FIELDS)
    except JiraError as error:
        if not str(error).endswith("_400"):                # a project without sprints rejects the sprint clause (HTTP 400); any other failure is a real one
            raise
        clause = f'(statusCategory = "In Progress" OR duedate <= "{sunday}")'
        found = search(jira, base + clause + tail, WEEK_FIELDS)
    done = search(jira, f'project = {project} AND issuetype != Epic AND statusCategory = Done AND statusCategoryChangedDate >= "{monday}" ORDER BY key ASC', WEEK_FIELDS)
    return {"open": [_row(i) for i in found], "done": [_row(i) for i in done], "jql": base + clause}


GOOGLE_FILE = re.compile(r"https://docs\.google\.com/(document|spreadsheets)/d/([A-Za-z0-9_-]{20,})")


def google_files(jira, project):
    """-> {Google file id: {kind, keys}} for every Google Doc or Sheet a project issue links directly in its description or comments (all issue types and statuses, whatever the label or sprint),
    deduplicated by file id. Bounded paging; only the link text is read, nothing of a document, and nothing is written."""
    out = {}
    for issue in search(jira, f"project = {project} ORDER BY key ASC", "description,comment"):
        fields = issue.get("fields") or {}
        text = json.dumps([fields.get("description"), (fields.get("comment") or {}).get("comments")])
        for kind, file_id in GOOGLE_FILE.findall(text):
            row = out.setdefault(file_id, {"kind": "Doc" if kind == "document" else "Sheet", "keys": []})
            if issue["key"] not in row["keys"]:
                row["keys"].append(issue["key"])
    return out
