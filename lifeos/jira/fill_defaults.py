"""LIFE-741: fill the parent epic, Component and Fix Version of LIFE tickets by one set of rules; a value that is already set is never changed."""
# Rules, in order (Jim, Nov 16 2026): (1) Parent: keep it; else a Bug goes under LIFE-615; else `action-inbox` goes to LIFE-696 and a title "Stability n/7" to LIFE-686; else the newest open epic
# carrying the same `los-N` label; else the container epic "LIFE one-off tasks" (found by its title alone, created once when absent). (2) Component: keep it; else the parent epic's; else Platform / Core OS.
# (3) Fix Version: keep it; else "<Component> 1.0.0". Epics get rules 2 and 3 only. Nothing here creates a Component or a version, or changes a status.
#   python -m lifeos.jira.fill_defaults [--backfill] [--live]     counts only; a dry run unless --live (the hourly stage is lifeos.run jira-fill)
#   python -m lifeos.jira.fill_defaults --plan FILE              FILE = {"issues": [...], "epics": [...]} saved from Jira; prints the planned changes, writes nothing
import json
import os
import re
import sys
import time
from pathlib import Path

from lifeos.jira.search import search
from lifeos.platform.jira import Jira, JiraError

BUGS_EPIC, INBOX_EPIC, STABILITY_EPIC = "LIFE-615", "LIFE-696", "LIFE-686"
CONTAINER_TITLE = "LIFE one-off tasks"
DEFAULT_COMPONENT = "Platform / Core OS"
FIELDS = "summary,issuetype,labels,parent,components,fixVersions,status"
GAPS = "project = LIFE AND (component is EMPTY OR fixVersion is EMPTY OR (issuetype != Epic AND parent is EMPTY))"
LOS = re.compile(r"los-\d+")
STABILITY = re.compile(r"\s*Stability \d+/7")
BATCH, PAUSE = 50, 1.0
LABEL_OF = {"components": "Component", "fixVersions": "Fix Version", "parent": "parent"}


class FillFailed(JiraError):
    """A Jira write failed (the message is the fixed code JIRA_FILL_FAILED)."""


class ParentRequired(JiraError):
    """A new non-epic ticket had no parent epic (JIRA_PARENT_REQUIRED) or the parent was not an epic (JIRA_PARENT_NOT_AN_EPIC)."""


def names(items):
    return [x["name"] for x in items or [] if isinstance(x, dict) and x.get("name")]


def number(key):
    return int(str(key).rsplit("-", 1)[-1])


def is_epic(fields):
    return (fields.get("issuetype") or {}).get("name") == "Epic"


def is_done(fields):
    return ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done"


def find_container(epics):
    """The open epic titled CONTAINER_TITLE (LIFE-780: the title alone picks it, no label); the lowest key when several; None when absent."""
    found = [k for k, e in epics.items() if _is_container(e.get("fields") or {})]
    return min(found, key=number) if found else None


def _is_container(fields):
    return (fields.get("summary") or "").strip().lower() == CONTAINER_TITLE.lower() and not is_done(fields)


def pick_parent(fields, epics, container):
    labels = fields.get("labels") or []
    if (fields.get("issuetype") or {}).get("name") == "Bug":
        return BUGS_EPIC
    if "action-inbox" in labels:
        return INBOX_EPIC
    if STABILITY.match(fields.get("summary") or ""):
        return STABILITY_EPIC
    wanted = {x for x in labels if LOS.fullmatch(x)}
    same = [k for k, e in epics.items() if not is_done(e.get("fields") or {}) and wanted & set((e.get("fields") or {}).get("labels") or [])]
    return max(same, key=number) if same else container


def plan(fields, epics, container):
    """-> {"parent": key, "components": name, "fixVersions": name}: only what is missing."""
    changes = {}
    parent = (fields.get("parent") or {}).get("key")
    if not is_epic(fields) and not parent:
        parent = changes["parent"] = pick_parent(fields, epics, container)
    have = names(fields.get("components"))
    component = have[0] if have else None
    if not have:
        mine = names(((epics.get(parent) or {}).get("fields") or {}).get("components")) if not is_epic(fields) else []
        component = changes["components"] = mine[0] if mine else DEFAULT_COMPONENT
    if not names(fields.get("fixVersions")):
        changes["fixVersions"] = f"{component} 1.0.0"
    return changes


def load_epics(jira):
    return {e["key"]: e for e in search(jira, "project = LIFE AND issuetype = Epic", FIELDS)}


def ensure_container(jira, epics, live):
    """The container epic's key (created once when absent and live; a dry run names it `(new container)`)."""
    key = find_container(epics)
    if key or not live:
        return key or "(new container)"
    try:
        made = jira.post("/rest/api/3/issue", {"fields": {"project": {"key": "LIFE"}, "issuetype": {"name": "Epic"}, "summary": CONTAINER_TITLE,
                                                       "components": [{"name": DEFAULT_COMPONENT}], "fixVersions": [{"name": f"{DEFAULT_COMPONENT} 1.0.0"}]}})
    except JiraError:
        raise FillFailed("JIRA_FILL_FAILED") from None
    epics[made["key"]] = {"key": made["key"], "fields": {"issuetype": {"name": "Epic"}, "summary": CONTAINER_TITLE, "labels": [], "components": [{"name": DEFAULT_COMPONENT}]}}
    return made["key"]


def write(jira, key, changes, comment):
    body = {"fields": {}}
    if "parent" in changes:
        body["fields"]["parent"] = {"key": changes["parent"]}
    if "components" in changes:
        body["fields"]["components"] = [{"name": changes["components"]}]
    if "fixVersions" in changes:
        body["fields"]["fixVersions"] = [{"name": changes["fixVersions"]}]
    try:
        jira.request("PUT", f"/rest/api/3/issue/{key}", payload=body, retry=True)       # idempotent
        if comment:
            text = "[sweep] set " + ", ".join(LABEL_OF[f] for f in ("parent", "components", "fixVersions") if f in changes) + " by rule"
            jira.post(f"/rest/api/3/issue/{key}/comment", {"body": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}})
    except JiraError:
        raise FillFailed("JIRA_FILL_FAILED") from None


def fill_key(jira, key, live=True, comment=False):
    """Fill one ticket by key (readiness); a read error is a plain JiraError, a failed write is FillFailed. -> the changes."""
    issue = jira.get(f"/rest/api/3/issue/{key}", {"fields": FIELDS})
    epics = load_epics(jira)
    changes = plan(issue.get("fields") or {}, epics, find_container(epics) or "(new container)")
    if changes.get("parent") == "(new container)":
        changes["parent"] = ensure_container(jira, epics, live)
    if changes and live:
        write(jira, key, changes, comment)
    return changes


def fill_many(jira, jql, live, comment, sleep=time.sleep):
    """Fill every ticket the JQL finds, BATCH at a time. A failed ticket is counted and the rest go on; only when nothing could be written is the stage failed."""
    found = search(jira, jql, FIELDS)
    epics = load_epics(jira)
    container = find_container(epics)
    out = {"checked": len(found), "changed": 0, "would_change": 0, "failed": 0}
    for index, issue in enumerate(found):
        if index and index % BATCH == 0:
            sleep(PAUSE)
        changes = plan(issue.get("fields") or {}, epics, container or "(new container)")
        if not changes:
            continue
        if not live:
            out["would_change"] += 1
            continue
        try:
            if changes.get("parent") == "(new container)":
                container = changes["parent"] = ensure_container(jira, epics, live)
            write(jira, issue["key"], changes, comment)
        except FillFailed:
            out["failed"] += 1
            continue
        out["changed"] += 1
    if out["failed"] and not out["changed"]:
        raise FillFailed("JIRA_FILL_FAILED")
    return out


def run(limit, live, environ=None, jira=None, days=2):
    """The hourly stage: tickets created or updated in the last `days` days that miss a field; one `[sweep]` comment per changed ticket."""
    jira = jira or Jira.from_env(os.environ if environ is None else environ)
    return fill_many(jira, f"{GAPS} AND updated >= -{int(days)}d", live, True)


def backfill(jira, live, sleep=time.sleep):
    """The one-time pass over every LIFE ticket in every status. -> counts with the gap totals before and after (no per-ticket comments)."""
    before = len(search(jira, GAPS, "summary"))
    out = fill_many(jira, GAPS, live, False, sleep)
    out["gaps_before"], out["gaps_after"] = before, len(search(jira, GAPS, "summary"))
    return out


def create_issue(jira, summary, issuetype, parent, description="", labels=()):
    """LIFE-773: create a LIFE ticket whose Component and Fix Version come from its parent epic by the same rules as the fill; no parent epic is a one-line refusal. -> the new key."""
    fields = {"project": {"key": "LIFE"}, "issuetype": {"name": issuetype}, "summary": summary, "labels": list(labels)}
    if issuetype == "Epic":
        component = DEFAULT_COMPONENT
    else:
        key = str(parent or "").strip()
        if not key:
            raise ParentRequired("JIRA_PARENT_REQUIRED")
        found = (jira.get(f"/rest/api/3/issue/{key}", {"fields": FIELDS}).get("fields") or {})
        if not is_epic(found):
            raise ParentRequired("JIRA_PARENT_NOT_AN_EPIC")
        component = (names(found.get("components")) or [DEFAULT_COMPONENT])[0]
        fields["parent"] = {"key": key}
    fields["components"], fields["fixVersions"] = [{"name": component}], [{"name": f"{component} 1.0.0"}]
    if description:
        fields["description"] = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}]}
    return jira.post("/rest/api/3/issue", {"fields": fields})["key"]


def plan_file(path):
    """Plan from a saved read of Jira (no network): -> {key: changes} for every issue that misses a field."""
    data = json.loads(Path(path).read_text())
    epics = {e["key"]: e for e in data.get("epics") or []}
    container = find_container(epics) or "(new container)"
    return {i["key"]: c for i in data.get("issues") or [] for c in [plan(i.get("fields") or {}, epics, container)] if c}


def main(argv):
    if argv[:1] == ["--plan"] and len(argv) == 2:
        planned = plan_file(argv[1])
        for key in sorted(planned, key=number):
            print(key, json.dumps(planned[key], sort_keys=True))
        print(f"planned {len(planned)}")
        return 0
    live = "--live" in argv
    jira = Jira.from_env()
    print(json.dumps(backfill(jira, live) if "--backfill" in argv else run(0, live, jira=jira), sort_keys=True))
    return 0


__all__ = ["FillFailed", "ParentRequired", "create_issue", "plan", "fill_key", "fill_many", "run", "backfill", "plan_file"]

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
