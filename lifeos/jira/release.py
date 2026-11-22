"""LIFE-727: a ticket whose blockers are Done and whose epic Jim approved moves To Do to Agent Go by itself, in rank order.

Jim approves an epic's list once with the label `auto-release` on the epic. A child is released only when ALL hold: its status is To Do; it was never in Agent Go before (so a ticket Jim moved back is never moved again);
it has a Done-when line; every Blocks predecessor is Done; its Jim must do first section exists and every item in it is checked (Jim, Nov 21 2026); it does not carry `needs-jim`; at most RELEASE_LIMIT per run, in Jira rank order. Each release is one short comment naming the epic. Dry run unless live.
Counts only in the output (ticket keys stay in Jira).

LIFE-850: the same fixed reasons stop a release and a promotion (`gate`): `no_readiness_label`, `two_readiness_labels` (exactly one of ready, needs-prep, needs-jim), `not_ready_label` (needs-prep or needs-jim), `epic_scoping` (the parent Epic is in Scoping and the child is neither `research` nor `verdict`), `epic_status_unread` (a parent whose status the read did not
return is never assumed Ready) and `blocked_by_open` (an `is blocked by` issue that is not Done, so build work waits for its Epic's verdict ticket). Only a child labelled `ready` moves."""
import os
import re

from lifeos.jira.search import search
from lifeos.platform.jira import Jira, JiraError

EPIC_LABEL = "auto-release"
NEEDS_JIM = "needs-jim"                                          # LIFE-780 (Jim, Nov 16 2026: "needs Jim + blocking only"): the one workflow-control label; blocking is the Blocks links below
READINESS = ("ready", "needs-prep", NEEDS_JIM)                   # LIFE-850 (policy section 7): every child carries exactly one; only `ready` moves to Agent Go
SCOPING_CHILD = {"research", "verdict"}                          # the only children allowed to be released while their Epic is in Scoping
RELEASE_LIMIT = 3
AGENT_GO = "17"                                                  # the To Do to Agent Go transition
DONE_WHEN = re.compile(r"done when", re.I)
ROW = re.compile(r"\bgiven\b|\bthen\b|^\s*[-*•]", re.I)
FIELDS = "summary,description,labels,parent,issuelinks"


def text_of(node):
    """The plain text of a Jira rich-text body, one line per block."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(text_of(n) for n in node)
    if isinstance(node, dict):
        inner = text_of(node.get("content") or []) if node.get("content") else node.get("text", "")
        return inner + ("\n" if node.get("type") in ("paragraph", "listItem", "heading", "bulletList") else "")
    return ""


def has_done_when(description):
    """A "Done when" heading or label followed by at least one Given/Then row or bullet."""
    lines = text_of(description).splitlines()
    for index, line in enumerate(lines):
        if DONE_WHEN.search(line):
            if ROW.search(line.split(":", 1)[-1]) and ":" in line:
                return True
            return any(ROW.search(later) for later in lines[index + 1:index + 12])
    return False


JIM_FIRST = re.compile(r"^[#*\s]*jim must do first\b", re.I)                   # Jim, Nov 21 2026 (the older "Needs before development" list stays a warning)
OPEN_ITEM = re.compile(r"^\s*(?:[-*•]\s*)?\[ \]")
DONE_ITEM = re.compile(r"^\s*(?:[-*•]\s*)?\[[xX]\]")
NONE_ITEM = re.compile(r"^\s*(?:[-*•]\s*)?\(?none\)?\.?\s*$", re.I)


def checkbox_lines(node, out=None):
    """The plain text lines of a Jira body: a heading as `# text`, a list item as `- text`, a Jira task item as `- [x] text` (done) or `- [ ] text` (open), so ADF and markdown read alike."""
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
        elif kind == "listItem" and not any(isinstance(c, dict) and c.get("type") in ("bulletList", "orderedList", "taskList") for c in inner):
            out.append("- " + flat())
        else:
            checkbox_lines(inner, out)
    return out


BULLET = re.compile(r"^\s*[-*•]\s+\S")


def jim_first(description):
    """-> (present, open items) for the Jim must do first section (Jim, Nov 21 2026). Inside it an unchecked box or a plain bullet is open, a checked box or `None` is done,
    and the first other non-empty line (a heading, a paragraph, the next bold label) ends the section. A missing section is (False, [])."""
    rows = checkbox_lines(description)
    for index, line in enumerate(rows):
        if not JIM_FIRST.match(line):
            continue
        inline = line.split(":", 1)[1] if ":" in line else ""
        out = []
        for item in [inline] + rows[index + 1:]:
            item = item.replace("\\", "").strip()
            bare = item.strip("*").strip()                                    # markdown bold around an inline `None`
            if not bare or DONE_ITEM.match(bare) or NONE_ITEM.match(bare):
                continue
            if OPEN_ITEM.match(bare) or BULLET.match(item):
                out.append(bare)
                continue
            break
        return True, out
    return False, []


def blockers_done(links):
    """Every issue that blocks this one (the inward side of a Blocks link) is Done."""
    for link in links or []:
        if (link.get("type") or {}).get("name") == "Blocks" and link.get("inwardIssue"):
            status = ((link["inwardIssue"].get("fields") or {}).get("status") or {}).get("statusCategory") or {}
            if status.get("key") != "done":
                return False
    return True


def parent_status(fields):
    """The parent Epic's status name from the fields already fetched ("" when no parent is present or the read did not return it)."""
    return (((fields.get("parent") or {}).get("fields") or {}).get("status") or {}).get("name") or ""


def label_reason(labels):
    """None when exactly one readiness label is `ready`, else the fixed reason."""
    held = [x for x in labels or [] if x in READINESS]
    if not held:
        return "no_readiness_label"
    if len(held) > 1:
        return "two_readiness_labels"
    return None if held[0] == "ready" else "not_ready_label"


def epic_reason(fields):
    """The parent Epic's state refuses build work during Scoping; a research or verdict child is allowed (when otherwise eligible). No parent is not judged here (readiness reports it)."""
    if not fields.get("parent"):
        return None
    status = parent_status(fields)
    if not status:
        return "epic_status_unread"
    if status == "Scoping" and not (SCOPING_CHILD & set(fields.get("labels") or [])):
        return "epic_scoping"
    return None


def gate(fields):
    """LIFE-850: -> the first fixed reason this fetched issue may not move to Agent Go, or None. Pure: reads only the fields already fetched (labels, parent with its status, issuelinks)."""
    return label_reason(fields.get("labels")) or epic_reason(fields) or (None if blockers_done(fields.get("issuelinks")) else "blocked_by_open")


def candidates(jira):
    """-> (epic keys, children in rank order)."""
    epics = [i["key"] for i in search(jira, f'issuetype = Epic AND labels = "{EPIC_LABEL}"', "summary")]
    if not epics:
        return [], []
    keys = ",".join(epics)
    return epics, search(jira, f'parent in ({keys}) AND status = "To Do" AND status was not "Agent Go" AND issuetype != Epic ORDER BY Rank ASC', FIELDS)


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
    body = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"[agent-v7] Released to Agent Go by the auto-release rule: epic {epic} is approved (label {EPIC_LABEL}), the Done-when line is present and every blocker is Done."}]}]}
    jira.post(f"/rest/api/3/issue/{key}/comment", {"body": body})


def run(limit, live, environ=None, jira=None):
    jira = jira or Jira.from_env(os.environ if environ is None else environ)
    epics, children = candidates(jira)
    out = {"epics": len(epics), "candidates": len(children), "released": 0, "would_release": 0, "skipped": {}}
    for issue in children:
        why = eligible(issue)
        if why:
            out["skipped"][why] = out["skipped"].get(why, 0) + 1
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
