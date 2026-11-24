"""Read-only Jira reads for the V3 accountability regions (LIFE-671). GET only; nothing is written to Jira.

A quarter is a calendar quarter; its epics carry the label goals-<year>-q<n> (LIFE-670). Because an epic keeps its older Done tasks, a quarter counts only tasks that are open now or were
completed inside that quarter. Output is plain dicts for the renderers; ticket text goes to Notion only, never to a log."""
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlparse

from lifeos.platform.jira import JiraError

PEOPLE = (("Jim", "JFM"), ("Matt", "MEJ"))
WEEK_FIELDS = "summary,status,duedate,issuetype"
EPIC_FIELDS = "summary"
CHILD_FIELDS = "status,parent,resolutiondate,issuetype"
MEJ_MEMBERSHIP = Path(__file__).with_name("mej_quarter_membership.json")
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


def _first_verified_done(jira, key, current_done_status_id=None):
    """First historical transition INTO Done, including earlier Done/reopen cycles.

    The live status-category-change field and resolutiondate are never proof.
    Jira changelogs are read oldest-to-newest with complete, bounded paging;
    absent/invalid/incomplete history is a degraded metric, not an estimate.
    """
    start, first = 0, None
    path = f"/rest/api/3/issue/{quote(key, safe='')}/changelog"
    for _ in range(MAX_PAGES):
        page = jira.get(path, {"startAt": start, "maxResults": PAGE})
        values = page.get("values") if isinstance(page, dict) else None
        if not isinstance(values, list):
            raise JiraError("ACCOUNTABILITY_MEJ_COMPLETION_HISTORY_UNVERIFIED")
        for change in values:
            if not isinstance(change, dict) or not isinstance(change.get("items"), list):
                raise JiraError("ACCOUNTABILITY_MEJ_COMPLETION_HISTORY_UNVERIFIED")
            for item in change["items"]:
                if not isinstance(item, dict) or (item.get("fieldId") != "status" and item.get("field") != "status"):
                    continue
                to_name = (item.get("toString") or "").strip().casefold()
                if to_name != "done" and (not current_done_status_id or str(item.get("to")) != str(current_done_status_id)):
                    continue
                try:
                    stamp = datetime.fromisoformat(change["created"])
                    if stamp.tzinfo is None:
                        raise ValueError("missing time zone")
                    day = stamp.astimezone(ZoneInfo("America/Chicago")).date()
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    raise JiraError("ACCOUNTABILITY_MEJ_COMPLETION_HISTORY_UNVERIFIED") from exc
                if first is None or day < first:
                    first = day
        start += len(values)
        if page.get("isLast") is True or (isinstance(page.get("total"), int) and start >= page["total"]):
            if first is None:
                raise JiraError("ACCOUNTABILITY_MEJ_COMPLETION_HISTORY_UNVERIFIED")
            return first
        if not values:
            raise JiraError("ACCOUNTABILITY_MEJ_COMPLETION_HISTORY_UNVERIFIED")
    raise JiraError("ACCOUNTABILITY_MEJ_COMPLETION_HISTORY_UNVERIFIED")


def _frozen_mej_membership(first, manifest=None):
    """Load an explicitly approved, version-controlled Task->parent->goal baseline.

    A project Epic's live Relates link NEVER assigns its entire child backlog
    to a quarter. Missing approval or a missing quarter fails closed. Historical
    quarters can coexist in the same frozen manifest without being rewritten.
    """
    if manifest is None:
        try:
            manifest = json.loads(MEJ_MEMBERSHIP.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED")
    quarter_id = f"{first.year}-q{(first.month - 1) // 3 + 1}"
    row = (manifest.get("quarters") or {}).get(quarter_id)
    if not isinstance(row, dict) or row.get("approved") is not True or not row.get("baseline_id") or not row.get("approval_ref") or not isinstance(row.get("tasks"), dict):
        raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED")
    for key, assignment in row["tasks"].items():
        if not re.fullmatch(r"MEJ-\d+", key) or not isinstance(assignment, dict) or not re.fullmatch(r"MEJ-\d+", str(assignment.get("parent") or "")) or not re.fullmatch(r"MEJ-\d+", str(assignment.get("goal") or "")):
            raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED")
    return row["tasks"]


def _mej_v2_tally(jira, quarter_keys, first, last, manifest=None):
    """K2/K3: quarter counts only frozen Task/Story membership, with verified Done history.

    Keep the v1 reader separate and off-by-default. Missing or drifted entries
    refuse the v2 projection rather than silently changing the denominator.
    """
    if not quarter_keys:
        return {}
    frozen = _frozen_mej_membership(first, manifest)
    project_goals = _mej_project_goals(jira, quarter_keys)
    parent_keys = sorted(set(quarter_keys) | set(project_goals))
    tally = {goal: [0, 0] for goal in quarter_keys}
    seen = set()
    for issue in search(jira, f"parent in ({','.join(parent_keys)})", CHILD_FIELDS):
        key = issue["key"]
        if key not in frozen:
            continue
        fields = issue.get("fields") or {}
        kind = fields.get("issuetype") or {}
        if kind.get("subtask") or kind.get("name") not in ("Task", "Story"):
            raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_DRIFT")
        parent = (fields.get("parent") or {}).get("key")
        goal = parent if parent in quarter_keys else project_goals.get(parent)
        expected = frozen[key]
        if key in seen or goal not in quarter_keys or expected["goal"] != goal or expected["parent"] != parent:
            raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_DRIFT")
        seen.add(key)
        done = ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done"
        if done:
            completed = _first_verified_done(jira, key, (fields.get("status") or {}).get("id"))
            tally[goal][0] += int(first <= completed <= last)
        tally[goal][1] += 1
    if seen != set(frozen):
        raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_DRIFT")
    return tally


def tally(jira, found, first, last, *, mej_metric_version=None, frozen_membership=None):
    """v1 historical direct-child metrics, or opt-in future MEJ v2 frozen metrics.

    JFM always retains historical v1 behavior; v2 is reachable only through the
    explicit `mej_metric_version="v2"` code argument AND an approved frozen
    quarter manifest (no environment variable, no default).
    """
    if not found:
        return {}
    keys = ",".join(e["key"] for e in found)
    rows = _counts(search(jira, f"parent in ({keys})", CHILD_FIELDS), first, last)
    out = {e["key"]: rows.get(e["key"], [0, 0]) for e in found}
    mode = mej_metric_version if mej_metric_version is not None else "v1"
    if mode not in ("v1", "v2"):
        raise JiraError("ACCOUNTABILITY_MEJ_METRIC_VERSION_INVALID")
    if mode == "v2":
        matt_keys = {e["key"] for e in found if e["person"] == "Matt"}
        if matt_keys:
            out.update(_mej_v2_tally(jira, matt_keys, first, last, frozen_membership))
    return out


def unparented(jira, project, epic_keys, *, mej_metric_version=None):
    """How many open non-epic, non-sub-task issues of the project sit under none of the quarter's epics.

    v1 (the default, and always for JFM) is the historical count. Only an explicit MEJ v2 call also accepts a labelled project Epic linked to a quarter Epic as a parent."""
    allowed = set(epic_keys)
    if project == "MEJ" and mej_metric_version == "v2":
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


def week(jira, project, today, mej_task_focus=False):
    """Read this week's commitments and completions.

    OFF BY DEFAULT (LIFE-914): the default path is exactly the existing query and summary for every project, MEJ included. Only an explicit
    `mej_task_focus=True` code argument (a future approved release, LIFE-918; no setting, env var or credential switches it) gives MEJ the view below.
    When on, MEJ explicitly separates open sprint commitments from work due outside that
    sprint. Sub-tasks remain visible through their parent Task in Jira rather
    than inflating Matt's weekly commitment count. JFM retains its previously
    approved query/summary contract (LIFE-914).
    """
    sunday = today + timedelta(days=6 - today.weekday())
    monday = today - timedelta(days=today.weekday())
    tail = " ORDER BY duedate ASC, key ASC"
    if project == "MEJ" and mej_task_focus:
        base = f"project = {project} AND issuetype != Epic AND issuetype != Sub-task AND statusCategory != Done AND "
        clause = "(sprint in openSprints())"
        # An invalid active-sprint query is not evidence of sprint membership.
        # Propagate any Jira failure so the V3 writer keeps the last accepted
        # content and reports DEGRADED instead of inventing commitments.
        active = search(jira, base + clause + tail, WEEK_FIELDS)
        due_clause = f'(duedate <= "{sunday}")'
        due = search(jira, base + due_clause + tail, WEEK_FIELDS)
        active_keys = {i["key"] for i in active}
        outside = [i for i in due if i["key"] not in active_keys]
        done = search(jira, f'project = {project} AND issuetype != Epic AND issuetype != Sub-task AND statusCategory = Done AND statusCategoryChangedDate >= "{monday}" ORDER BY key ASC', WEEK_FIELDS)
        return {"open": [_row(i) for i in active], "outside": [_row(i) for i in outside],
                "done": [_row(i) for i in done], "jql": base + clause,
                "outside_jql": base + due_clause + ' AND (sprint not in openSprints() OR sprint is EMPTY)'}

    # The existing behavior for every project (JFM, and MEJ until the explicit argument above is passed).
    base = f"project = {project} AND issuetype != Epic AND statusCategory != Done AND "
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
