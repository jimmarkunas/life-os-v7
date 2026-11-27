"""Offline readiness validator: is a LIFE issue ready to start.

Refuses (exit 2): an invalid key, no Done-when line, an open `Blocks` predecessor, an unchecked Jim must do first
item, a release-gate reason, or unreadable input. Anything else prints `warn v1 <code> <field>` on stderr and exits
0.
Usage: python -m lifeos.sources.devtools.readiness issue FILE | issue-key KEY | issue-json FILE | issue-mcp FILE
KEY | open-gaps | open-gaps-mcp FILE | epic-review FILE | epic-review-key KEY | repo [ROOT]
"""

import json
import re
import sys
import time
from pathlib import Path

VERSION = 1
LANES = ("lane-career-v7", "lane-edge-comm")
DESCRIPTION_LIMIT = 4000
KEY = re.compile(r"LIFE-\d+")
SIZE_LIMIT = 15 * 1024
POLICY_LIMIT = 15 * 1024
GWT = ("given", "when", "then")
ISSUE_FIELDS = {"key", "labels", "origin", "blockers", "authorization", "permissions", "acceptance"}


def refuse(code, field, detail=""):
    return {"code": code, "field": field, "detail": detail}


def text(value):
    return isinstance(value, str) and bool(value.strip())


HARD = {
    "issue_unreadable",
    "key_invalid",
    "blockers_unreadable",
    "blocked_by_open",
    "gwt_missing",
    "jim_first_open",
    "no_readiness_label",
    "two_readiness_labels",
    "not_ready_label",
    "epic_scoping",
    "epic_status_unread",
    "epic_unreviewed",
}
HARD_FIELDS = {"key", "blockers", "acceptance"}


def findings(issue):
    if not isinstance(issue, dict):
        return [refuse("issue_unreadable", "issue")]
    out = [refuse("field_absent", name) for name in sorted(ISSUE_FIELDS - issue.keys())]
    if not KEY.fullmatch(str(issue.get("key", ""))):
        out.append(refuse("key_invalid", "key", str(issue.get("key", ""))))
    origin = issue.get("origin")
    if "origin" in issue:
        parent = origin.get("parent") if isinstance(origin, dict) else None
        if not parent:
            out.append(refuse("origin_missing", "origin"))
        elif not (re.fullmatch(r"LIFE-\d+", str(parent)) and origin.get("approved") is True):
            out.append(refuse("origin_unapproved", "origin", str(parent)))
    if "owner" in issue and not text(issue["owner"]):
        out.append(refuse("owner_missing", "owner"))
    if "labels" in issue:
        lanes = (
            [x for x in issue["labels"] if isinstance(x, str) and x.startswith("lane-")]
            if isinstance(issue["labels"], list)
            else []
        )
        if not lanes:
            out.append(refuse("lane_missing", "labels"))
        elif len(lanes) > 1 or lanes[0] not in LANES:
            out.append(refuse("lane_unknown", "labels", ",".join(lanes)))
    if "components" in issue and not (isinstance(issue["components"], list) and issue["components"]):
        out.append(refuse("component_missing", "components"))
    for name, code in (("host", "host_missing"), ("source", "source_missing")):
        if name in issue and not text(issue[name]):
            out.append(refuse(code, name))
    if "blockers" in issue:
        blockers = issue["blockers"]
        if not isinstance(blockers, list):
            out.append(refuse("blockers_unreadable", "blockers"))
        else:
            out += [
                refuse("blocked_by_open", "blockers", f"{b.get('key')}={b.get('status')}")
                for b in blockers
                if not (isinstance(b, dict) and b.get("status") == "Done")
            ]
    if "authorization" in issue:
        auth = issue["authorization"]
        if isinstance(auth, dict) and auth.get("denied") is True:
            out.append(refuse("authorization_denied", "authorization"))
        elif not (
            isinstance(auth, dict)
            and text(auth.get("approved_by"))
            and text(auth.get("provenance"))
            and auth.get("denied", False) is False
        ):
            out.append(refuse("authorization_missing", "authorization"))
    if "permissions" in issue and not (
        isinstance(issue["permissions"], list) and issue["permissions"] and all(text(p) for p in issue["permissions"])
    ):
        out.append(refuse("permissions_missing", "permissions"))
    if "gate" in issue:
        why = issue["gate"]
        if why in (
            "no_readiness_label",
            "two_readiness_labels",
            "not_ready_label",
            "epic_scoping",
            "epic_status_unread",
        ):
            out.append(refuse(why, "gate"))
    if "jim_first" in issue:
        jf = issue["jim_first"]
        if not isinstance(jf, dict) or not jf.get("present"):
            out.append(refuse("jim_first_missing", "jim_first"))
        elif jf.get("open"):
            out.append(refuse("jim_first_open", "jim_first", f"{jf['open']} unchecked"))
    for name, code in (("needs", "prerequisite_open"), ("decisions", "decision_open")):
        if name in issue:
            rows = issue[name]
            if not isinstance(rows, list):
                out.append(refuse(f"{name}_unreadable", name))
            else:
                out += [
                    refuse(code, name, f"item {i + 1}")
                    for i, r in enumerate(rows)
                    if not (
                        isinstance(r, dict)
                        and text(r.get("what"))
                        and text(r.get("proof"))
                        and r.get("status") == "done"
                    )
                ]
    if "requirement" in issue:
        req = issue["requirement"]
        if not (isinstance(req, dict) and isinstance(req.get("pages"), list)):
            out.append(refuse("requirement_unreadable", "requirement"))
    if "acceptance" in issue:
        items = issue["acceptance"]
        if not (isinstance(items, list) and items):
            out.append(refuse("gwt_missing", "acceptance"))
        else:
            out += [
                refuse("gwt_incomplete", "acceptance", f"item {i + 1}")
                for i, c in enumerate(items)
                if not (isinstance(c, dict) and (text(c.get("done_when")) or all(text(c.get(k)) for k in GWT)))
            ]
    if (
        str(issue.get("key", "")).startswith("LIFE-")
        and isinstance(issue.get("description_chars"), int)
        and issue["description_chars"] > DESCRIPTION_LIMIT
    ):
        out.append(
            refuse(
                "description_long", "description", f"{issue['description_chars']} characters, limit {DESCRIPTION_LIMIT}"
            )
        )
    return out


def check_issue(issue):
    refusals, warnings = [], []
    for f in findings(issue):
        (
            refusals if f["code"] in HARD or (f["code"] == "field_absent" and f["field"] in HARD_FIELDS) else warnings
        ).append(f)
    return refusals, warnings


def check_repo(root):
    root, out = Path(root), []
    try:
        rows, section = [], ""
        for line in (root / "INDEX.md").read_text().splitlines():
            section = line[3:].strip() if line.startswith("## ") else section
            if line.startswith("| [`"):
                rows.append((section == "Global sources", [c.strip() for c in line.strip().strip("|").split("|")]))
        schema = json.loads((root / "docs" / "jira" / "issue-schema.v1.json").read_text())
    except (OSError, ValueError):
        return [refuse("index_unreadable", "INDEX.md")]
    if sum(is_global for is_global, _ in rows) < 5:
        out.append(refuse("index_empty", "INDEX.md"))
    for is_global, row in rows:
        link = re.match(r"\[`([^`]+)`\]\(([^)]+)\)$", row[0])
        if len(row) != (5 if is_global else 1) or not link:
            out.append(refuse("index_row_malformed", "INDEX.md", row[0][:60]))
            continue
        if link.group(2) != link.group(1):
            out.append(refuse("link_destination_mismatch", link.group(1), link.group(2)))
            continue
        path = root / link.group(1)
        if not path.is_file():
            out.append(refuse("link_broken", link.group(1)))
            continue
        if is_global and not (row[1] and row[3]):
            out.append(refuse("owner_or_authority_missing", link.group(1)))
    sized = [root / n for n in ("INDEX.md", "AGENTS.md", "CLAUDE.md", "docs/life-os-development-policy.md")]
    out += [
        refuse("size_over_limit", str(f.relative_to(root)), str(f.stat().st_size))
        for f in sized
        if f.is_file()
        and f.stat().st_size > (POLICY_LIMIT if f.name == "life-os-development-policy.md" else SIZE_LIMIT)
    ]
    if not set(("key", "acceptance_criteria", "owner", "authorization")) <= set(schema.get("required", [])):
        out.append(refuse("schema_fields_lost", "docs/jira/issue-schema.v1.json"))
    return out


def jira_issue(mode, arg):
    from lifeos.jira import readiness_record as rr
    from lifeos.jira import (
        fill_defaults,
    )  # a live read first fills the parent, Component and Fix Version; a failed Jira write is the one `fill_failed` refusal

    try:
        if mode == "issue-key":
            client = rr.Jira.from_env()
            fill_defaults.fill_key(client, arg)
            raw = rr.fetch(arg, client)
        else:
            raw = json.loads(Path(arg).read_text())
        return (*check_issue(rr.normalize(raw)), None)
    except fill_defaults.FillFailed:
        return [refuse("fill_failed", "jira")], [], None
    except Exception as exc:  # noqa: BLE001  fixed code only
        code = str(exc) if str(exc).startswith(("JIRA_", "INCOMPLETE:")) else type(exc).__name__
        return [], [], f"QUEUE_NOT_READY — {code}"


MCP_MAX_AGE = 15 * 60


def mcp_issue(path, key):
    """-> (refusals, warnings, gap, fields used). The file cannot be proven unmodified; the caller states that limit."""
    from lifeos.jira import readiness_record as rr

    p, shown = Path(path), None
    try:
        stale = time.time() - p.stat().st_mtime > MCP_MAX_AGE
        raw = rr.from_mcp(json.loads(p.read_text()), key)
        shown = rr.used(raw)
        refusals, warnings = check_issue(rr.normalize(raw))
        return (
            refusals,
            ([refuse("stale_read", "file", "saved more than 15 minutes ago")] if stale else []) + warnings,
            None,
            shown,
        )
    except Exception as exc:  # noqa: BLE001  fixed code only
        code = str(exc) if str(exc).startswith(("INCOMPLETE:", "KEY_MISMATCH")) else type(exc).__name__
        return [], [], f"QUEUE_NOT_READY — {code}", shown


REVIEW = re.compile(r"^[#*\s]*ChatGPT review:\s*(pass|changes)\b", re.I)


def moment(value):
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def epic_review(bundle):
    """-> None, or why an Epic in Ready has no current pass: "missing", "failed" (newest review says changes) or
    "stale" (a child was edited after it).
    bundle: {"key", "status", "comments": [{"body", "created"}], "children": [{"key", "updated"}]}.
    """
    if (bundle.get("status") or "") != "Ready":
        return None
    from lifeos.jira.readiness_record import lines

    found = []
    for c in bundle.get("comments") or []:
        first = next(iter(lines(c.get("body"))), "")
        m, at = REVIEW.match(first), moment(c.get("created"))
        if m and at:
            found.append((at, m.group(1).lower()))
    if not found:
        return "missing"
    at, kind = max(found)
    if kind != "pass":
        return "failed"
    edits = [e for e in (moment(c.get("updated")) for c in bundle.get("children") or []) if e]
    return "stale" if edits and max(edits) > at else None


def review_report(bundle):
    why = epic_review(bundle)
    return report([refuse("epic_unreviewed", "review", f"{bundle.get('key')} {why}")] if why else [], [])


GAPS_JQL = "project = LIFE AND statusCategory != Done AND issuetype != Epic ORDER BY key ASC"
GAPS_FIELDS = "parent,components,fixVersions,labels"


def open_gaps(issues):
    from lifeos.jira.release import READINESS

    out = []
    for issue in issues:
        f = issue.get("fields") or {}
        missing = [
            name
            for name, absent in (
                ("parent", not f.get("parent")),
                ("component", not f.get("components")),
                ("fix_version", not f.get("fixVersions")),
                ("readiness_label", not any(x in READINESS for x in f.get("labels") or [])),
            )
            if absent
        ]
        if missing:
            out.append((issue.get("key", "?"), missing))
    return out


def gaps_report(issues):
    found = open_gaps(issues)
    print(f"gaps v{VERSION} {len(found)} of {len(issues)} open issues")
    for key, missing in found:
        print(f"gap {key} {','.join(missing)}")
    return 0


def report(refusals, warnings):
    for w in warnings:
        print(f"warn v{VERSION} {w['code']} {w['field']} {w['detail']}".rstrip(), file=sys.stderr)
    for r in refusals:
        print(f"refused v{VERSION} {r['code']} {r['field']} {r['detail']}".rstrip(), file=sys.stderr)
    if not refusals:
        print(f"ready v{VERSION}")
    return 2 if refusals else 0


def run(argv):
    if argv[:1] == ["issue-mcp"] and len(argv) == 3:
        refusals, warnings, gap, shown = mcp_issue(argv[1], argv[2])
        print("SOURCE mcp-read (the session saved this file; it cannot be proven unmodified)")
        if shown:
            print("FIELDS " + " ".join(f"{k}={v}" for k, v in shown.items()))
        if gap:
            print(gap, file=sys.stderr)
            return 2
        return report(refusals, warnings)
    if argv[:1] == ["epic-review"] and len(argv) == 2 or argv[:1] == ["epic-review-key"] and len(argv) == 2:
        try:
            if argv[0] == "epic-review":
                bundle = json.loads(Path(argv[1]).read_text())
            else:
                from lifeos.jira import readiness_record as rr

                bundle = rr.epic_bundle(argv[1])
            if not isinstance(bundle, dict):
                raise ValueError("INCOMPLETE:bundle")
        except Exception as exc:  # noqa: BLE001  fixed code only
            code = str(exc) if str(exc).startswith(("JIRA_", "INCOMPLETE:", "JM_DASH_")) else type(exc).__name__
            print(f"QUEUE_NOT_READY — {code}", file=sys.stderr)
            return 2
        return review_report(bundle)
    if argv[:1] == ["open-gaps"] and len(argv) == 1 or argv[:1] == ["open-gaps-mcp"] and len(argv) == 2:
        try:
            if argv[0] == "open-gaps":
                from lifeos.jira import readiness_record as rr
                from lifeos.jira.search import search

                issues = search(rr.Jira.from_env(), GAPS_JQL, GAPS_FIELDS)
            else:
                nodes = ((json.loads(Path(argv[1]).read_text()) or {}).get("issues") or {}).get("nodes")
                issues = nodes if isinstance(nodes, list) else None
                if issues is None:
                    raise ValueError("INCOMPLETE:issues")
        except Exception as exc:  # noqa: BLE001  fixed code only
            code = str(exc) if str(exc).startswith(("JIRA_", "INCOMPLETE:", "JM_DASH_")) else type(exc).__name__
            print(f"QUEUE_NOT_READY — {code}", file=sys.stderr)
            return 2
        return gaps_report(issues)
    if argv[:1] in (["issue-key"], ["issue-json"]) and len(argv) == 2:
        refusals, warnings, gap = jira_issue(argv[0], argv[1])
        if gap:
            print(gap, file=sys.stderr)
            if "_MISSING:" in gap:
                print(
                    "hint: no Jira settings here? Read the ticket with the Atlassian MCP, save the result and run: python -m lifeos.sources.devtools.readiness issue-mcp FILE KEY",
                    file=sys.stderr,
                )
            return 2
        return report(refusals, warnings)
    try:
        mode, args = argv[0], argv[1:]
        checks = {
            "issue": lambda: check_issue(json.loads(Path(args[0]).read_text())),
            "repo": lambda: (check_repo(args[0] if args else Path(__file__).resolve().parents[3]), []),
        }
        refusals, warnings = checks[mode]()
    except Exception:  # noqa: BLE001  any error is a refusal, never a pass
        refusals, warnings = [refuse("input_unreadable", "argv")], []
    return report(refusals, warnings)


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
