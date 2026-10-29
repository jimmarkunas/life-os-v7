"""JFM-221 (Jim, Oct 29 2026): the Jira reads behind the Jim & Matt dashboard. Read only: this module never writes to Jira. Jira is the source of truth; Notion is its output.

People are projects: JFM is Jim, MEJ is Matt. "Done" is the status-category change date (statuscategorychangedate), never the resolution date, which is not reliably set.
An issue's epic is its parent when that parent is an epic, else its parent's parent (a sub-task under a task). Errors are fixed codes."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform.jira import JiraError

CENTRAL = ZoneInfo("America/Chicago")
PEOPLE = {"JFM": "Jim", "MEJ": "Matt"}
PROJECTS = tuple(PEOPLE)
EPICS = {"MEJ-1": "Career", "MEJ-2": "Creative (incl. Time & Money Freedom)", "MEJ-3": "Health / Finances / Travel", "MEJ-4": "Relationships",
         "JFM-9": "Career", "JFM-102": "LIFE OS", "JFM-104": "Health", "JFM-105": "Creative", "JFM-106": "Relationships"}
MUST_WIN = "must-win"
FIELDS = "summary,status,statuscategorychangedate,duedate,labels,parent,issuetype,project"
PAGE = 100
MAX_PAGES = 30
BLOCKED_MAX = 40                                       # blocked issues checked for an explanatory comment per run (each is one read)


def quarter_of(day):
    return (day.month - 1) // 3 + 1


def quarter_label(day):
    """The QBR label of a quarter, e.g. q4-qbr-2026 (Jim, Oct 29: one planning session and one synopsis page per quarter)."""
    return f"q{quarter_of(day)}-qbr-{day.year}"


def quarter_name(day):
    return f"Q{quarter_of(day)} {day.year}"


def search(jira, jql, fields=FIELDS):
    """Every issue the JQL matches (the enhanced search API, token paging, bounded)."""
    out, token = [], None
    for _ in range(MAX_PAGES):
        params = {"jql": jql, "fields": fields, "maxResults": PAGE, **({"nextPageToken": token} if token else {})}
        data = jira.get("/rest/api/3/search/jql", params)
        issues = data.get("issues") if isinstance(data, dict) else None
        if not isinstance(issues, list):
            raise JiraError("JM_DASH_JIRA_BAD_PAGE")
        out.extend(issues)
        token = data.get("nextPageToken")
        if data.get("isLast", not token) or not token:
            return out
    raise JiraError("JM_DASH_JIRA_TOO_MANY_PAGES")


def _when(value):
    try:
        moment = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
    except (TypeError, ValueError):
        return None
    return moment.astimezone(CENTRAL)


def _day(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def normalize(raw, site):
    f = raw.get("fields") or {}
    key = str(raw.get("key") or "")
    status = f.get("status") or {}
    itype = f.get("issuetype") or {}
    parent = f.get("parent") or {}
    parent_level = ((parent.get("fields") or {}).get("issuetype") or {}).get("hierarchyLevel")
    return {"key": key, "person": PEOPLE.get(key.split("-")[0], "?"), "summary": str(f.get("summary") or ""),
            "status": str(status.get("name") or ""), "category": str((status.get("statusCategory") or {}).get("key") or ""),
            "changed": _when(f.get("statuscategorychangedate")), "due": _day(f.get("duedate")), "labels": tuple(f.get("labels") or ()),
            "type": str(itype.get("name") or ""), "level": itype.get("hierarchyLevel", 0), "parent": parent.get("key"),
            "epic": parent.get("key") if parent_level == 1 else None, "url": f"{site}/browse/{key}"}


def resolve_epics(jira, issues):
    """Sub-tasks under a task take the task's epic (one batched read for every such parent)."""
    need = sorted({i["parent"] for i in issues if not i["epic"] and i["parent"]})
    found = {}
    for start in range(0, len(need), 50):
        for raw in search(jira, f"key in ({','.join(need[start:start + 50])})", "parent"):
            p = (raw.get("fields") or {}).get("parent") or {}
            if (((p.get("fields") or {}).get("issuetype") or {}).get("hierarchyLevel")) == 1:
                found[raw.get("key")] = p.get("key")
    for i in issues:
        if not i["epic"] and i["parent"] in found:
            i["epic"] = found[i["parent"]]
    return issues


def epic_name(key, names=None):
    return EPICS.get(key) or (names or {}).get(key) or ("No epic" if not key else key)


def blocked(issue):
    return "block" in issue["status"].lower()


def unexplained(jira, issue):
    """True when no comment was added after the issue last moved into its current (blocked) status. One read: the issue with its comments and recent changelog."""
    data = jira.get(f"/rest/api/3/issue/{issue['key']}", {"fields": "comment", "expand": "changelog"})
    histories = ((data.get("changelog") or {}).get("histories")) or []
    moved = [h.get("created") for h in histories for item in h.get("items") or [] if item.get("field") == "status"
             and str(item.get("toString") or "").lower() == issue["status"].lower()]
    since = max((m for m in (_when(x) for x in moved) if m), default=None)
    comments = (((data.get("fields") or {}).get("comment") or {}).get("comments")) or []
    later = [c for c in comments if (w := _when(c.get("created"))) and (since is None or w >= since)]
    return not later


def adf_text(node):
    """Plain text of an Atlassian Document Format node."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    parts = [adf_text(c) for c in node.get("content") or []]
    return ("\n" if node.get("type") in ("doc", "bulletList", "orderedList") else "").join(parts)


def vision(description):
    """The Mary Morrissey vision statement of a Win ticket: the text under its "Mary Morrissey vision" heading, up to the next heading. None when there is none."""
    blocks = (description or {}).get("content") or [] if isinstance(description, dict) else []
    out, inside = [], False
    for block in blocks:
        if block.get("type") == "heading":
            if inside:
                break
            inside = "mary morrissey" in adf_text(block).lower()
            continue
        if inside:
            text = adf_text(block).strip()
            if text:
                out.append(text)
    return "\n".join(out) or None


def window(issues, now):
    """The check-in's sections for one person -> {done, due, overdue}: done in the last 7 days by status-category change date; open with a due date in the next 7 days or past."""
    today, start = now.date(), now - timedelta(days=7)
    open_ = [i for i in issues if i["category"] != "done" and i["level"] != 1]
    return {"done": sorted((i for i in issues if i["category"] == "done" and i["level"] != 1 and i["changed"] and start <= i["changed"] <= now),
                           key=lambda i: i["changed"]),
            "due": sorted((i for i in open_ if i["due"] and today <= i["due"] <= today + timedelta(days=7)), key=lambda i: (i["due"], i["key"])),
            "overdue": sorted((i for i in open_ if i["due"] and i["due"] < today), key=lambda i: (i["due"], i["key"]))}


def read_all(jira, site, now):
    """Every issue the dashboard needs, in one pass -> list of normalized issues (open, done in the last 120 days, and every labelled must-win / QBR issue)."""
    projects = ",".join(PROJECTS)
    seen = {}
    for jql in (f"project in ({projects}) AND statusCategory != Done",
                f"project in ({projects}) AND statusCategory = Done AND statusCategoryChangedDate >= -120d",
                f"project in ({projects}) AND labels in ({MUST_WIN}, {quarter_label(now.date())})"):
        for raw in search(jira, jql):
            seen[raw.get("key")] = raw
    return resolve_epics(jira, [normalize(raw, site) for raw in seen.values()])
