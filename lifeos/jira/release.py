"""Move tickets from To Do to Agent Go by themselves, in rank order, when their blockers are Done and their epic carries the label `auto-release`.

A child also needs: never in Agent Go before, a Done-when line, a complete Jim must do first section, no `needs-jim`
label; at most RELEASE_LIMIT per run. `gate` returns the fixed refusal reasons shared with promote and readiness. Dry
run unless live.
"""

import os
import re

from lifeos.jira import package
from lifeos.jira.search import search
from lifeos.platform.jira import Jira, JiraError

EPIC_LABEL = "auto-release"
NEEDS_JIM = "needs-jim"
READINESS = ("ready", "needs-prep", NEEDS_JIM)
SCOPING_CHILD = {"research", "verdict"}
RELEASE_LIMIT = 3
AGENT_GO = "17"
DONE_WHEN = re.compile(r"done when", re.I)
ROW = re.compile(r"\bgiven\b|\bthen\b|^\s*[-*•]", re.I)
FIELDS = "summary,description,labels,parent,issuelinks"


def text_of(node):
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(text_of(n) for n in node)
    if isinstance(node, dict):
        inner = text_of(node.get("content") or []) if node.get("content") else node.get("text", "")
        return inner + ("\n" if node.get("type") in ("paragraph", "listItem", "heading", "bulletList") else "")
    return ""


def has_done_when(description):
    lines = text_of(description).splitlines()
    for index, line in enumerate(lines):
        if DONE_WHEN.search(line):
            if ROW.search(line.split(":", 1)[-1]) and ":" in line:
                return True
            return any(ROW.search(later) for later in lines[index + 1 : index + 12])
    return False


JIM_FIRST = re.compile(r"^[#*\s]*jim must do first\b", re.I)
OPEN_ITEM = re.compile(r"^\s*(?:[-*•]\s*)?\[ \]")
DONE_ITEM = re.compile(r"^\s*(?:[-*•]\s*)?\[[xX]\]")
NONE_ITEM = re.compile(r"^\s*(?:[-*•]\s*)?\(?none\)?\.?\s*$", re.I)


def checkbox_lines(node, out=None):
    out = [] if out is None else out
    if isinstance(node, str):
        out.extend(node.splitlines())
    elif isinstance(node, list):
        for child in node:
            checkbox_lines(child, out)
    elif isinstance(node, dict):
        kind, inner = node.get("type"), node.get("content") or []
        flat = lambda: text_of(inner).replace("\n", " ").strip()
        if kind == "taskItem":
            out.append(("- [x] " if (node.get("attrs") or {}).get("state") == "DONE" else "- [ ] ") + flat())
        elif kind == "heading":
            out.append("# " + flat())
        elif kind == "paragraph":
            out.append(flat())
        elif kind == "listItem" and not any(
            isinstance(c, dict) and c.get("type") in ("bulletList", "orderedList", "taskList") for c in inner
        ):
            out.append("- " + flat())
        else:
            checkbox_lines(inner, out)
    return out


BULLET = re.compile(r"^\s*[-*•]\s+\S")


def jim_first(description):
    """-> (present, open items) for the Jim must do first section: an unchecked box or plain bullet is open, a checked
    box or `None` is done; a missing section is (False, [])."""
    rows = checkbox_lines(description)
    for index, line in enumerate(rows):
        if not JIM_FIRST.match(line):
            continue
        inline = line.split(":", 1)[1] if ":" in line else ""
        out = []
        for item in [inline] + rows[index + 1 :]:
            item = item.replace("\\", "").strip()
            bare = item.strip("*").strip()
            if not bare or DONE_ITEM.match(bare) or NONE_ITEM.match(bare):
                continue
            if OPEN_ITEM.match(bare) or BULLET.match(item):
                out.append(bare)
                continue
            break
        return True, out
    return False, []


def blockers_done(links):
    for link in links or []:
        if (link.get("type") or {}).get("name") == "Blocks" and link.get("inwardIssue"):
            status = ((link["inwardIssue"].get("fields") or {}).get("status") or {}).get("statusCategory") or {}
            if status.get("key") != "done":
                return False
    return True


def parent_status(fields):
    return (((fields.get("parent") or {}).get("fields") or {}).get("status") or {}).get("name") or ""


def label_reason(labels):
    held = [x for x in labels or [] if x in READINESS]
    if not held:
        return "no_readiness_label"
    if len(held) > 1:
        return "two_readiness_labels"
    return None if held[0] == "ready" else "not_ready_label"


def epic_reason(fields):
    """Epics in Scoping refuse build work except research or verdict children and a complete experimental package."""
    if not fields.get("parent"):
        return None
    status = parent_status(fields)
    if not status:
        return "epic_status_unread"
    labels = set(fields.get("labels") or [])
    if status == "Scoping" and not (SCOPING_CHILD & labels):
        if package.PACKAGE_LABEL in labels:
            return "package_incomplete" if package.missing(fields.get("description")) else None
        return "epic_scoping"
    return None


# A ticket whose only work is waiting for a date or event is a UAT check under the Bug Epic, never a build ticket.
WAIT_ONLY = re.compile(r"^\W*(watch|soak|wait for|waiting for)\b|\(not before\b", re.I)


def wait_only_reason(fields):
    if "uat" in (fields.get("labels") or []) or not WAIT_ONLY.search(fields.get("summary") or ""):
        return None
    return "wait_only_not_uat"


def gate(fields):
    return (
        label_reason(fields.get("labels"))
        or wait_only_reason(fields)
        or epic_reason(fields)
        or (None if blockers_done(fields.get("issuelinks")) else "blocked_by_open")
    )


def candidates(jira):
    epics = [i["key"] for i in search(jira, f'issuetype = Epic AND labels = "{EPIC_LABEL}"', "summary")]
    if not epics:
        return [], []
    keys = ",".join(epics)
    return epics, search(
        jira,
        f'parent in ({keys}) AND status = "To Do" AND status was not "Agent Go" AND issuetype != Epic ORDER BY Rank ASC',
        FIELDS,
    )


def eligible(issue):
    fields = issue.get("fields") or {}
    why = label_reason(fields.get("labels"))
    if why:
        return why
    if not has_done_when(fields.get("description")):
        return "no_done_when"
    why = gate(fields)
    if why:
        return why
    present, open_items = jim_first(fields.get("description"))
    if not present:
        return "no_jim_first"
    if open_items:
        return "jim_first_open"
    return None


def release(jira, key, epic):
    jira.post(f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": AGENT_GO}})
    body = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": f"[agent-v7] Released to Agent Go by the auto-release rule: epic {epic} is approved (label {EPIC_LABEL}), the Done-when line is present and every blocker is Done.",
                    }
                ],
            }
        ],
    }
    jira.post(f"/rest/api/3/issue/{key}/comment", {"body": body})


def hold(jira, key, comment):
    """Swap `ready` for `needs-jim` and say what Jim must approve (the same words `promote.act` gives)."""
    jira.put(f"/rest/api/3/issue/{key}", {"update": {"labels": [{"remove": "ready"}, {"add": NEEDS_JIM}]}})
    body = {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"[agent-v7] {comment}"}]}],
    }
    jira.post(f"/rest/api/3/issue/{key}/comment", {"body": body})


def run(limit, live, environ=None, jira=None):
    from lifeos.jira import promote  # noqa: PLC0415  (promote builds on this module)

    jira = jira or Jira.from_env(os.environ if environ is None else environ)
    epics, children = candidates(jira)
    out = {"epics": len(epics), "candidates": len(children), "released": 0, "would_release": 0, "skipped": {}}
    for issue in children:
        why = eligible(issue)
        if why:
            out["skipped"][why] = out["skipped"].get(why, 0) + 1
            continue
        decision = promote.act(issue)
        if decision["decision"] == "wait":
            # A ticket that names a credential, live access, a host step, spend and so on waits for Jim's yes (policy
            # section 4); the `needs-jim` label then keeps it out of every later pass.
            out["skipped"]["protected"] = out["skipped"].get("protected", 0) + 1
            if live:
                try:
                    hold(jira, issue["key"], decision["comment"])
                except JiraError:
                    out["failed"] = out.get("failed", 0) + 1
            continue
        if out["released"] + out["would_release"] >= RELEASE_LIMIT:
            out["skipped"]["over_limit"] = out["skipped"].get("over_limit", 0) + 1
            continue
        if not live:
            out["would_release"] += 1
            continue
        try:
            release(jira, issue["key"], ((issue.get("fields") or {}).get("parent") or {}).get("key", "?"))
        except JiraError:
            out["failed"] = out.get("failed", 0) + 1
            continue
        out["released"] += 1
    if out.get("failed") and not out["released"]:
        raise JiraError("JIRA_RELEASE_FAILED")
    return out
