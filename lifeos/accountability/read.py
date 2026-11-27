"""Read-only Jira reads (GET only) for the V3 accountability regions.

A quarter counts only tasks open now or completed inside it. Ticket text goes to Notion only, never to a log."""

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlparse

from lifeos.accountability import closure
from lifeos.platform.jira import JiraError

PEOPLE = (("Jim", "JFM"), ("Matt", "MEJ"))
WEEK_FIELDS = "summary,status,duedate,issuetype"
EPIC_FIELDS = "summary"
CHILD_FIELDS = "status,parent,resolutiondate,issuetype," + closure.FIELDS
MEJ_MEMBERSHIP = Path(__file__).with_name("mej_quarter_membership.json")
PAGE, MAX_PAGES = 100, 30
URL = re.compile(r"https?://[^\s\"\\<>)]+")
TAIL = re.compile(r"\s+Q[1-4]\s+\d{4}$")


def site_url(environ=None):
    """Base for Jira issue links: JIRA_SITE_URL, else JIRA_BASE_URL unless it is a gateway host ("api."); None gives
    plain text."""
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
    out, token = [], None
    for _ in range(MAX_PAGES):
        data = jira.get(
            "/rest/api/3/search/jql",
            {"jql": jql, "fields": fields, "maxResults": PAGE, **({"nextPageToken": token} if token else {})},
        )
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
    return {n: first_wednesday(year, 3 * n - 2) for n in (1, 2, 3, 4)}


def quarter(today):
    """-> (year, n, first day, last day) of the quarter `today` is in. A quarter starts on the first Wednesday of
    Jan/Apr/Jul/Oct, so earlier days belong to last year's Q4."""
    for year in (today.year, today.year - 1):
        for n in (4, 3, 2, 1):
            if starts(year)[n] <= today:
                return year, n, starts(year)[n], _next_start(year, n) - timedelta(days=1)
    raise ValueError("date out of range")


def _next_start(year, n):
    return starts(year)[n + 1] if n < 4 else starts(year + 1)[1]


def quarter_bounds(year, n):
    return starts(year)[n], _next_start(year, n) - timedelta(days=1)


def previous(year, n):
    return (year - 1, 4) if n == 1 else (year, n - 1)


def label(year, n):
    return f"goals-{year}-q{n}"


def lane(summary):
    return TAIL.sub("", summary or "").strip()


def person_of(key):
    project = key.split("-")[0]
    return next((name for name, prefix in PEOPLE if prefix == project), None)


def epics(jira, year, n):
    found = search(jira, f'issuetype = Epic AND labels = "{label(year, n)}" ORDER BY key ASC', EPIC_FIELDS)
    return [
        {"key": i["key"], "lane": lane((i.get("fields") or {}).get("summary")), "person": person_of(i["key"])}
        for i in found
    ]


def _counts(children, first, last):
    out = {}
    for issue in children:
        fields = issue.get("fields") or {}
        if (fields.get("issuetype") or {}).get("subtask") or (fields.get("issuetype") or {}).get("name") == "Epic":
            continue
        parent = (fields.get("parent") or {}).get("key")
        if not parent:
            continue
        done = ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done"
        if done and closure.classify(fields) == "cancelled":
            continue
        if done:
            resolved = (fields.get("resolutiondate") or "")[:10]
            if not resolved or not first.isoformat() <= resolved <= last.isoformat():
                continue
        row = out.setdefault(parent, [0, 0])
        row[0] += int(done)
        row[1] += 1
    return out


def _mej_project_goals(jira, quarter_keys):
    if not quarter_keys:
        return {}
    projects = search(
        jira,
        'project = MEJ AND issuetype = Epic AND labels = "mej-project" ORDER BY key ASC',
        "issuetype,issuelinks,labels",
    )
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
    """First historical transition INTO Done, across Done/reopen cycles; missing history is degraded, never estimated."""
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
                if to_name != "done" and (
                    not current_done_status_id or str(item.get("to")) != str(current_done_status_id)
                ):
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


def _frozen_row(first, manifest=None, project="MEJ"):
    """The approved quarter row for one board: tasks (key -> parent, goal), plus optional snapshot and gaps.

    Schema 2 also records why, who approved and from when each key is a member (reason, approval, effective); a
    missing approval fails closed."""
    if manifest is None:
        try:
            manifest = json.loads(MEJ_MEMBERSHIP.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") not in (1, 2):
        raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED")
    quarter_id = f"{first.year}-q{(first.month - 1) // 3 + 1}"
    row = (manifest.get("quarters") or {}).get(quarter_id)
    if isinstance(row, dict) and "boards" in row:
        row = (row["boards"] or {}).get(project) if isinstance(row["boards"], dict) else None
    elif project != "MEJ":
        row = None
    if (
        not isinstance(row, dict)
        or row.get("approved") is not True
        or not row.get("baseline_id")
        or not row.get("approval_ref")
        or not isinstance(row.get("tasks"), dict)
    ):
        raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED")
    for key, assignment in row["tasks"].items():
        pattern = project + r"-\d+"
        if (
            not re.fullmatch(pattern, key)
            or not isinstance(assignment, dict)
            or not re.fullmatch(pattern, str(assignment.get("parent") or ""))
            or not re.fullmatch(pattern, str(assignment.get("goal") or ""))
        ):
            raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED")
        if manifest["schema_version"] == 2 and not (
            str(assignment.get("reason") or "").strip()
            and str(assignment.get("approval") or "").strip()
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(assignment.get("effective") or ""))
        ):
            raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_INCOMPLETE")
    return row


def _frozen_mej_membership(first, manifest=None, project="MEJ"):
    """The approved Task->parent->goal baseline for one board; missing approval fails closed."""
    return _frozen_row(first, manifest, project)["tasks"]


def _mej_v2_tally(jira, quarter_keys, first, last, manifest=None, project="MEJ", today=None):
    """v2 counts for one board from the approved key list alone. A member's current Jira parent never changes its
    goal, so reparenting or a new project Epic cannot move a count; a key that is not on the list is unassigned.
    A closed quarter with a published snapshot returns that snapshot, never a recount. A goal listed under the row's
    `gaps` has incomplete baseline evidence: its figure is None (reported, never reconstructed)."""
    if not quarter_keys:
        return {}
    row = _frozen_row(first, manifest, project)
    frozen = row["tasks"]
    gaps = row.get("gaps") if isinstance(row.get("gaps"), dict) else {}
    gap_goals = {goal for goal in quarter_keys if goal in gaps}
    snapshot = row.get("snapshot")
    if isinstance(snapshot, dict) and last < (today or datetime.now(ZoneInfo("America/Chicago")).date()):
        figures = snapshot.get("figures")
        if not snapshot.get("version") or not isinstance(figures, dict) or any(g not in figures for g in quarter_keys):
            raise JiraError("ACCOUNTABILITY_MEJ_SNAPSHOT_INCOMPLETE")
        return {goal: [int(figures[goal][0]), int(figures[goal][1])] for goal in quarter_keys}
    tally = {goal: [0, 0] for goal in quarter_keys - gap_goals}
    seen = set()
    keys = sorted(frozen)
    issues = []
    for at in range(0, len(keys), 50):
        issues += search(jira, f"key in ({','.join(keys[at : at + 50])})", CHILD_FIELDS)
    for issue in issues:
        key = issue["key"]
        fields = issue.get("fields") or {}
        kind = fields.get("issuetype") or {}
        goal = (frozen.get(key) or {}).get("goal")
        if (
            key in seen
            or key not in frozen
            or goal not in quarter_keys
            or kind.get("subtask")
            or kind.get("name") not in ("Task", "Story")
        ):
            raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_DRIFT")
        seen.add(key)
        done = ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done"
        if done and closure.classify(fields) == "cancelled":
            continue
        if done:
            completed = _first_verified_done(jira, key, (fields.get("status") or {}).get("id"))
            tally[goal][0] += int(first <= completed <= last)
        tally[goal][1] += 1
    if seen != set(frozen):
        raise JiraError("ACCOUNTABILITY_MEJ_MEMBERSHIP_DRIFT")
    return {**tally, **{goal: None for goal in gap_goals}}


def tally(jira, found, first, last, *, mej_metric_version=None, frozen_membership=None):
    """v1 direct-child metrics, or v2 frozen metrics (needs `mej_metric_version="v2"` and an approved frozen quarter
    list)."""
    if not found:
        return {}
    keys = ",".join(e["key"] for e in found)
    rows = _counts(search(jira, f"parent in ({keys})", CHILD_FIELDS), first, last)
    out = {e["key"]: rows.get(e["key"], [0, 0]) for e in found}
    mode = mej_metric_version if mej_metric_version is not None else "v1"
    if mode not in ("v1", "v2"):
        raise JiraError("ACCOUNTABILITY_MEJ_METRIC_VERSION_INVALID")
    if mode == "v2":
        for person, project in PEOPLE:
            board_keys = {e["key"] for e in found if e["person"] == person}
            if board_keys:
                out.update(_mej_v2_tally(jira, board_keys, first, last, frozen_membership, project))
    return out


def unparented(jira, project, epic_keys, *, mej_metric_version=None):
    allowed = set(epic_keys)
    if project == "MEJ" and mej_metric_version == "v2":
        allowed.update(_mej_project_goals(jira, allowed))
    count = 0
    for issue in search(
        jira, f"project = {project} AND issuetype != Epic AND statusCategory != Done", "parent,issuetype"
    ):
        fields = issue.get("fields") or {}
        if (fields.get("issuetype") or {}).get("subtask"):
            continue
        if (fields.get("parent") or {}).get("key") not in allowed:
            count += 1
    return count


def _row(issue, **extra):
    fields = issue.get("fields") or {}
    status = fields.get("status") or {}
    return {
        "key": issue["key"],
        "summary": fields.get("summary") or "",
        "status": status.get("name") or "Unknown",
        "due": fields.get("duedate"),
        "done": (status.get("statusCategory") or {}).get("key") == "done",
        **extra,
    }


V3_WEEK_FIELDS = WEEK_FIELDS + ",parent,labels"


def _is_subtask(issue):
    return bool(((issue.get("fields") or {}).get("issuetype") or {}).get("subtask"))


def _v3_week(jira, project, today, monday):
    """Group the V3 week view: each key lands in one section: `open`, `subtasks` ({parent key: open Sub-tasks}),
    `blocked` and `overdue` (outside the sprint), `done`. A Jira failure propagates so the V3 writer keeps its last
    accepted content."""
    tail = " ORDER BY duedate ASC, key ASC"
    standard = f"project = {project} AND issuetype != Epic AND issuetype != Sub-task AND statusCategory != Done AND "
    sprint = "(sprint in openSprints())"
    everything = search(
        jira,
        f"project = {project} AND issuetype != Epic AND statusCategory != Done AND {sprint}" + tail,
        V3_WEEK_FIELDS,
    )
    commitments = [i for i in everything if not _is_subtask(i)]
    keys = {i["key"] for i in commitments}
    subtasks, elsewhere = {}, 0
    for issue in everything:
        if not _is_subtask(issue):
            continue
        parent = ((issue.get("fields") or {}).get("parent") or {}).get("key")
        if parent in keys:
            subtasks.setdefault(parent, []).append(_row(issue))
        else:
            elsewhere += 1
    outside_clause = f'(sprint not in openSprints() OR sprint is EMPTY) AND (duedate < "{today}" OR status = blocked)'
    outside = [i for i in search(jira, standard + outside_clause + tail, V3_WEEK_FIELDS) if i["key"] not in keys]
    rows = [_row(i) for i in outside]
    blocked = [r for r in rows if r["status"].lower() == "blocked"]
    overdue = [r for r in rows if r["status"].lower() != "blocked" and r["due"] and r["due"] < today.isoformat()]
    done = search(
        jira,
        f'project = {project} AND issuetype != Epic AND issuetype != Sub-task AND statusCategory = Done AND statusCategoryChangedDate >= "{monday}" ORDER BY key ASC',
        V3_WEEK_FIELDS,
    )
    return {
        "v3": True,
        "open": [_row(i) for i in commitments],
        "subtasks": subtasks,
        "subtasks_elsewhere": elsewhere,
        "blocked": blocked,
        "overdue": overdue,
        "done": [_row(i, labels=list((i.get("fields") or {}).get("labels") or [])) for i in done],
        "jql": standard + sprint,
        "outside_jql": standard + outside_clause,
    }


def week(jira, project, today, mej_task_focus=False, v3_week_view=False):
    """This week's commitments and completions: the plain query by default; `mej_task_focus=True` separates MEJ sprint
    commitments from work due outside it; `v3_week_view=True` gives both boards the V3 view."""
    sunday = today + timedelta(days=6 - today.weekday())
    monday = today - timedelta(days=today.weekday())
    if v3_week_view:
        return _v3_week(jira, project, today, monday)
    tail = " ORDER BY duedate ASC, key ASC"
    if project == "MEJ" and mej_task_focus:
        base = f"project = {project} AND issuetype != Epic AND issuetype != Sub-task AND statusCategory != Done AND "
        clause = "(sprint in openSprints())"
        # An invalid sprint query is not evidence of membership: propagate it.
        active = search(jira, base + clause + tail, WEEK_FIELDS)
        due_clause = f'(duedate <= "{sunday}")'
        due = search(jira, base + due_clause + tail, WEEK_FIELDS)
        active_keys = {i["key"] for i in active}
        outside = [i for i in due if i["key"] not in active_keys]
        done = search(
            jira,
            f'project = {project} AND issuetype != Epic AND issuetype != Sub-task AND statusCategory = Done AND statusCategoryChangedDate >= "{monday}" ORDER BY key ASC',
            WEEK_FIELDS,
        )
        return {
            "open": [_row(i) for i in active],
            "outside": [_row(i) for i in outside],
            "done": [_row(i) for i in done],
            "jql": base + clause,
            "outside_jql": base + due_clause + ' AND (sprint not in openSprints() OR sprint is EMPTY)',
        }

    base = f"project = {project} AND issuetype != Epic AND statusCategory != Done AND "
    clause = f'(sprint in openSprints() OR duedate <= "{sunday}")'
    try:
        found = search(jira, base + clause + tail, WEEK_FIELDS)
    except JiraError as error:
        if not str(error).endswith("_400"):
            raise
        clause = f'(statusCategory = "In Progress" OR duedate <= "{sunday}")'
        found = search(jira, base + clause + tail, WEEK_FIELDS)
    done = search(
        jira,
        f'project = {project} AND issuetype != Epic AND statusCategory = Done AND statusCategoryChangedDate >= "{monday}" ORDER BY key ASC',
        WEEK_FIELDS,
    )
    return {"open": [_row(i) for i in found], "done": [_row(i) for i in done], "jql": base + clause}


GOOGLE_FILE = re.compile(r"https://docs\.google\.com/(document|spreadsheets)/d/([A-Za-z0-9_-]{20,})")


def google_files(jira, project):
    """-> {Google file id: {kind, keys}} for every Google Doc or Sheet a project issue links in its description or
    comments."""
    out = {}
    for issue in search(jira, f"project = {project} ORDER BY key ASC", "description,comment"):
        fields = issue.get("fields") or {}
        text = json.dumps([fields.get("description"), (fields.get("comment") or {}).get("comments")])
        for kind, file_id in GOOGLE_FILE.findall(text):
            row = out.setdefault(file_id, {"kind": "Doc" if kind == "document" else "Sheet", "keys": []})
            if issue["key"] not in row["keys"]:
                row["keys"].append(issue["key"])
    return out
