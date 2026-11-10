"""LIFE-313: turn one Jira issue (REST v3 JSON) into the record `python -m lifeos.readiness issue` reads. Pure and offline except `fetch`,
which does one read-only GET with the existing Jira credentials (names only, never values). A field that cannot be read from the issue is left
out of the record, so the validator reports it; nothing here ever invents a pass."""
import re

from lifeos.platform.jira import Jira, JiraError

FIELDS = "summary,status,labels,parent,components,issuelinks,description"
ROW = re.compile(r"^\[( |x|X)\]\s*(.+)$")
PROOF = re.compile(r"\(proof:\s*(.*?)(?:;\s*status:[^)]*)?\)\s*\.?\s*(?:Done\.?)?\s*$")
GWT = re.compile(r"(?:\*{0,2}GWT-[A-Z0-9-]+\*{0,2}\s*)?Given (.+?),\s*when (.+?),\s*then (.+)$", re.I)       # delivery flow v3: any id, or none
DONE_WHEN = re.compile(r"^[#*\s]*done when\b[:*\s]*(.*)$", re.I)
HEADING = re.compile(r"^(?:#|\*{2}[A-Z][^*]*:\*{2})|^(?:Outcome|Needs|Decisions|Not in scope|Protected|Build|Never|Steps|Context)\b")
LOS_KEY = re.compile(r"\bLOS-\d+\b")
REQ_PAGE = re.compile(r"\bREQ-\d+\b.*?(https://(?:www\.)?(?:app\.)?notion\.(?:com|so)/\S+)|(https://(?:www\.)?(?:app\.)?notion\.(?:com|so)/\S+).*?\bREQ-\d+\b")
APPROVAL = re.compile(r"^\*{0,2}Origin\s*/\s*approval:\*{0,2}\s*(.+)$", re.I)


def lines(node, out=None):
    """Flatten an ADF document (or a plain string) to one text line per paragraph, heading or list item."""
    out = [] if out is None else out
    if isinstance(node, str):                                  # markdown/wiki text: drop list markers and escapes so checklists parse the same as ADF
        out.extend(re.sub(r"^\s*[*-]\s+", "", x.replace("\\", "")).strip() for x in node.splitlines())
    elif isinstance(node, dict):
        kind = node.get("type")
        if kind in ("paragraph", "heading", "taskItem") or (kind == "listItem" and not any(isinstance(c, dict) and c.get("type") in ("paragraph",) for c in node.get("content", []))):
            out.append("".join(text(node)).strip())
        else:
            for child in node.get("content", []) or []:
                lines(child, out)
    return [x for x in out if x]


def text(node):
    if isinstance(node, dict):
        if node.get("type") == "text":
            yield node.get("text", "")
        elif node.get("type") == "hardBreak":
            yield " "
        for child in node.get("content", []) or []:
            yield from text(child)


def section(rows, start):
    """Lines after the first line that starts with `start`, up to the next bold heading line."""
    for i, line in enumerate(rows):
        if line.replace("*", "").startswith(start):
            rest = []
            for nxt in rows[i + 1:]:
                if re.match(r"^\*{2}[A-Z]", nxt) or re.match(r"^(Outcome|Done when|Needs|Decisions|Not in scope|Protected|Build)\b", nxt.replace("*", "")):
                    break
                rest.append(nxt)
            return rows[i:i + 1] + rest
    return []


def checklist(block):
    out = []
    for line in block:
        m = ROW.match(line.strip())
        if m:
            body = m.group(2).strip()
            p = PROOF.search(body)
            out.append({"what": (body[:p.start()] if p else body).strip(" .:") or body, "proof": (p.group(1).strip() if p else ""), "status": "done" if m.group(1).lower() == "x" and p else "open"})
    return out


def done_when(rows):
    """Delivery flow v3: the Done-when lines (the text after the heading, then each line up to the next heading), checkbox marks removed."""
    for i, line in enumerate(rows):
        m = DONE_WHEN.match(line.replace("\\", ""))
        if not m:
            continue
        out = [m.group(1).strip(" *")]
        for nxt in rows[i + 1:]:
            if HEADING.match(nxt.replace("\\", "")):
                break
            out.append(re.sub(r"^\[[ xX]\]\s*", "", nxt.replace("\\", "")).strip())
        return [x for x in out if x]
    return []


def normalize(raw):
    """The validator record for one REST v3 issue. Raises ValueError("INCOMPLETE:<field>") when the issue lacks what the record is built from."""
    if not isinstance(raw, dict) or not isinstance(raw.get("fields"), dict) or not raw.get("key"):
        raise ValueError("INCOMPLETE:issue")
    f, key = raw["fields"], raw["key"]
    rows = lines(f.get("description"))
    if not rows:
        raise ValueError("INCOMPLETE:description")
    record = {"key": key, "labels": list(f.get("labels") or []), "acceptance": [], "blockers": [], "permissions": []}
    for line in rows:
        m = GWT.search(line.replace("\\", ""))
        if m:
            record["acceptance"].append({"given": m.group(1), "when": m.group(2), "then": m.group(3)})
    record["acceptance"] += [{"done_when": x} for x in done_when(rows) if not GWT.search(x)]          # a bullet under "Done when" counts as a Done-when line
    record["description_chars"] = len(f["description"]) if isinstance(f.get("description"), str) else len("\n".join(rows))
    for line in rows:                                                          # origin and authorization come from the written approval line, never from a comment
        m = APPROVAL.match(line)
        if m:
            parent = (f.get("parent") or {}).get("key")
            lead = m.group(1).split(",")[0].strip(" *")                                 # "<Name>, <date>, ..." or "... named by <Name> ..."
            found = re.fullmatch(r"[A-Z][\w.'-]*(?: [A-Z][\w.'-]*)*", lead) or re.search(r"\b(?:by|from)\s+([A-Z][\w'-]*)", m.group(1))
            who = (found.group(1) if found and found.re.groups else found.group(0)) if found else ""
            named = bool(who)
            record["origin"] = {"parent": parent, "approved": named} if parent else {"approved": False}
            record["authorization"] = {"approved_by": who if named else "", "provenance": "issue description, Origin / approval line" if named else ""}
            break
    record.setdefault("origin", {"approved": False})
    record.setdefault("authorization", {"approved_by": "", "provenance": ""})
    origin_line = next((m.group(1) for m in map(APPROVAL.match, rows) if m), "")          # LIFE-319: a ticket that came from a LOS key must link its Notion requirement page (a line holding REQ-n and the page URL)
    pages = sorted({(m.group(1) or m.group(2)).rstrip(".,;)*") for m in map(REQ_PAGE.search, (r.replace("\\", "") for r in rows)) if m})
    record["requirement"] = {"los": (LOS_KEY.findall(origin_line) or [""])[0], "pages": pages}
    build = " ".join(section(rows, "Build"))
    for needle, perm in (("PR", "repo:write"), ("Notion", "notion:write"), ("Jira", "jira:write")):
        if needle in build:
            record["permissions"].append(perm)
    for link in f.get("issuelinks") or []:
        if (link.get("type") or {}).get("name") == "Blocks" and link.get("inwardIssue"):
            inward = link["inwardIssue"]
            record["blockers"].append({"key": inward.get("key"), "status": ((inward.get("fields") or {}).get("status") or {}).get("name")})
    record["needs"] = checklist(section(rows, "Needs before development"))
    record["decisions"] = checklist(section(rows, "Decisions"))
    if f.get("components") is not None:
        record["components"] = [c.get("name") for c in f["components"] if isinstance(c, dict) and c.get("name")]
    return record


def fetch(key, client=None):
    """One read-only GET. Any failure is a fixed code; the caller reports QUEUE_NOT_READY."""
    client = client or Jira.from_env()
    return client.get(f"/rest/api/3/issue/{key}", {"fields": FIELDS})


def from_mcp(envelope, key):
    """LIFE-363 (REQ-6): unwrap the unmodified result of the Atlassian MCP getJiraIssue (issues.nodes, exactly one node) into the `{key, fields}` shape `normalize` reads.
    ValueError codes: INCOMPLETE:<field> (shape or a FIELDS entry missing), KEY_MISMATCH (the node is not `key`). `normalize` is unchanged."""
    nodes = ((envelope or {}).get("issues") or {}).get("nodes") if isinstance(envelope, dict) else None
    if not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict):
        raise ValueError("INCOMPLETE:issues")
    node = nodes[0]
    if node.get("key") != key:
        raise ValueError("KEY_MISMATCH")
    fields = node.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("INCOMPLETE:fields")
    for name in FIELDS.split(","):
        if name not in fields:
            raise ValueError("INCOMPLETE:" + name)
    return {"key": node["key"], "fields": fields}


def used(raw):
    """The fields a human compares with Jira: key, status, updated time, blocker keys with statuses."""
    f = raw["fields"]
    blockers = [f"{(l.get('inwardIssue') or {}).get('key')}={(((l.get('inwardIssue') or {}).get('fields') or {}).get('status') or {}).get('name')}"
                for l in f.get("issuelinks") or [] if (l.get("type") or {}).get("name") == "Blocks" and l.get("inwardIssue")]
    return {"key": raw["key"], "status": (f.get("status") or {}).get("name") or "unknown", "updated": f.get("updated") or "unknown", "blockers": ",".join(blockers) or "none"}


__all__ = ["normalize", "fetch", "from_mcp", "used", "JiraError"]
