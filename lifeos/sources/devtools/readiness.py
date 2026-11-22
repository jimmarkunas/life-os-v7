"""Offline readiness validator for coding agents and CI: is a LIFE issue ready to start (LIFE-225 ARCH-04, delivery flow v3).

Versioned, deterministic, stdlib only, no network, no state of its own. Four things refuse (exit 2): an invalid key, no Done-when line, a `Blocks` predecessor not Done, and an unchecked Jim must do first item (Jim, Nov 21 2026). Anything else prints `warn v1 <code> <field>` on stderr and exits 0 (`ready v1`). Unreadable input or an error refuses
(exit 2) before the caller mutates anything.
Usage: python -m lifeos.sources.devtools.readiness issue FILE | issue-key KEY | issue-json FILE | issue-mcp FILE KEY | open-gaps | open-gaps-mcp FILE | repo [ROOT]   (FILE is JSON)
  issue      a normalized LIFE issue record; issue-key a LIVE issue read from Jira by key (read-only GET with JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN); issue-json a saved REST v3 issue
  issue-mcp  the unmodified Atlassian MCP getJiraIssue result saved to FILE (no Jira credential; prints SOURCE mcp-read; a file older than 15 minutes is a warn)
  open-gaps  LIFE-850, reporting only (exit 0): every open non-Epic LIFE issue missing its parent, Component, Fix Version or readiness label, read live (the same Jira settings as issue-key); open-gaps-mcp reads a saved Atlassian MCP search result instead. Nothing is filled or moved (LIFE-740 fills Component and Fix Version)
  repo       the global source index still points at real files with an owner and an authority, inside the size limit"""
# LIFE-225 (ARCH-04), simplified by delivery flow v3 (Jim, Nov 9 2026). A Done-when line is a Given/When/Then row with any id or none, or a bullet under "Done when".
import json
import re
import sys
import time
from pathlib import Path

VERSION = 1
LANES = ("lane-career-v7", "lane-edge-comm")
DESCRIPTION_LIMIT = 4000                      # characters: a longer LIFE description is a warn
KEY = re.compile(r"LIFE-\d+")
SIZE_LIMIT = 15 * 1024                        # bytes: the one-file policy and the boot files stay one read each
GWT = ("given", "when", "then")
ISSUE_FIELDS = {"key", "labels", "origin", "blockers", "authorization", "permissions", "acceptance"}   # the lane label is the owner; owner, host and source are optional


def refuse(code, field, detail=""):
    return {"code": code, "field": field, "detail": detail}


def text(value):
    return isinstance(value, str) and bool(value.strip())


HARD = {"issue_unreadable", "key_invalid", "blockers_unreadable", "blocked_by_open", "gwt_missing", "jim_first_open",
        "no_readiness_label", "two_readiness_labels", "not_ready_label", "epic_scoping", "epic_status_unread"}      # LIFE-850: the release gate's reasons are hard here too          # the only refusals; field_absent refuses only for the three inputs below
HARD_FIELDS = {"key", "blockers", "acceptance"}


def findings(issue):
    """Every gap in a normalized issue record, each on its own line; check_issue sorts them into refusals and warnings."""
    if not isinstance(issue, dict):
        return [refuse("issue_unreadable", "issue")]
    out = [refuse("field_absent", name) for name in sorted(ISSUE_FIELDS - issue.keys())]
    if not KEY.fullmatch(str(issue.get("key", ""))):
        out.append(refuse("key_invalid", "key", str(issue.get("key", ""))))
    origin = issue.get("origin")
    if "origin" in issue:
        parent = origin.get("parent") if isinstance(origin, dict) else None
        if not parent:
            out.append(refuse("origin_missing", "origin"))                 # LIFE-849: LOS is retired; the origin is a LIFE parent epic, nothing else
        elif not (re.fullmatch(r"LIFE-\d+", str(parent)) and origin.get("approved") is True):
            out.append(refuse("origin_unapproved", "origin", str(parent)))
    if "owner" in issue and not text(issue["owner"]):
        out.append(refuse("owner_missing", "owner"))
    if "labels" in issue:
        lanes = [x for x in issue["labels"] if isinstance(x, str) and x.startswith("lane-")] if isinstance(issue["labels"], list) else []
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
            out += [refuse("blocked_by_open", "blockers", f"{b.get('key')}={b.get('status')}") for b in blockers if not (isinstance(b, dict) and b.get("status") == "Done")]
    if "authorization" in issue:
        auth = issue["authorization"]
        if isinstance(auth, dict) and auth.get("denied") is True:
            out.append(refuse("authorization_denied", "authorization"))
        elif not (isinstance(auth, dict) and text(auth.get("approved_by")) and text(auth.get("provenance")) and auth.get("denied", False) is False):
            out.append(refuse("authorization_missing", "authorization"))
    if "permissions" in issue and not (isinstance(issue["permissions"], list) and issue["permissions"] and all(text(p) for p in issue["permissions"])):
        out.append(refuse("permissions_missing", "permissions"))
    if "gate" in issue:                                                        # LIFE-850: the release gate's label and Epic reasons (lifeos.jira.release.gate), read from the fields the record was built from
        why = issue["gate"]
        if why in ("no_readiness_label", "two_readiness_labels", "not_ready_label", "epic_scoping", "epic_status_unread"):
            out.append(refuse(why, "gate"))
    if "jim_first" in issue:                                                   # Jim, Nov 21 2026: an unchecked Jim must do first item refuses; a missing section warns at start (release refuses it)
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
                out += [refuse(code, name, f"item {i + 1}") for i, r in enumerate(rows) if not (isinstance(r, dict) and text(r.get("what")) and text(r.get("proof")) and r.get("status") == "done")]
    if "requirement" in issue:
        req = issue["requirement"]
        if not (isinstance(req, dict) and isinstance(req.get("pages"), list)):
            out.append(refuse("requirement_unreadable", "requirement"))
    if "acceptance" in issue:
        items = issue["acceptance"]
        if not (isinstance(items, list) and items):
            out.append(refuse("gwt_missing", "acceptance"))                  # no Given/When/Then row and no Done-when bullet
        else:
            out += [refuse("gwt_incomplete", "acceptance", f"item {i + 1}") for i, c in enumerate(items) if not (isinstance(c, dict) and (text(c.get("done_when")) or all(text(c.get(k)) for k in GWT)))]
    if str(issue.get("key", "")).startswith("LIFE-") and isinstance(issue.get("description_chars"), int) and issue["description_chars"] > DESCRIPTION_LIMIT:
        out.append(refuse("description_long", "description", f"{issue['description_chars']} characters, limit {DESCRIPTION_LIMIT}"))
    return out


def check_issue(issue):
    """-> (refusals, warnings). Only HARD codes (and a missing key, blockers or acceptance input) refuse; every other gap is a warning."""
    refusals, warnings = [], []
    for f in findings(issue):
        (refusals if f["code"] in HARD or (f["code"] == "field_absent" and f["field"] in HARD_FIELDS) else warnings).append(f)
    return refusals, warnings


def check_repo(root):
    """Refusals for the global source index: every indexed path exists, has an owner and an authority, and is inside the size limit."""
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
        if len(row) != (5 if is_global else 1) or not link:              # global-source rows carry all five columns; only the Not global list is link-only
            out.append(refuse("index_row_malformed", "INDEX.md", row[0][:60]))
            continue
        if link.group(2) != link.group(1):
            out.append(refuse("link_destination_mismatch", link.group(1), link.group(2)))   # the clickable destination must be the file the label names
            continue
        path = root / link.group(1)
        if not path.is_file():
            out.append(refuse("link_broken", link.group(1)))
            continue
        if is_global and not (row[1] and row[3]):
            out.append(refuse("owner_or_authority_missing", link.group(1)))
    sized = [root / n for n in ("INDEX.md", "AGENTS.md", "CLAUDE.md", "docs/life-os-development-policy.md")]
    out += [refuse("size_over_limit", str(f.relative_to(root)), str(f.stat().st_size)) for f in sized if f.is_file() and f.stat().st_size > SIZE_LIMIT]
    if not set(("key", "acceptance_criteria", "owner", "authorization")) <= set(schema.get("required", [])):
        out.append(refuse("schema_fields_lost", "docs/jira/issue-schema.v1.json"))
    return out


def jira_issue(mode, arg):
    """LIFE-313: normalize a Jira issue (live by key, or a saved REST v3 JSON file) and run the same issue check. Gaps in Jira data are QUEUE_NOT_READY."""
    from lifeos.jira import readiness_record as rr          # lazy: the offline modes never import or touch the network
    from lifeos.jira import fill_defaults                    # LIFE-741: a live read first fills the parent, Component and Fix Version; a failed Jira write is the one `fill_failed` refusal
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
    except Exception as exc:                                  # noqa: BLE001  every failure is a fixed code, never a body, URL or value
        code = str(exc) if str(exc).startswith(("JIRA_", "INCOMPLETE:")) else type(exc).__name__
        return [], [], f"QUEUE_NOT_READY — {code}"


MCP_MAX_AGE = 15 * 60                         # seconds: an older saved MCP read is a warning (v3), no longer a refusal


def mcp_issue(path, key):
    """LIFE-363: -> (refusals, warnings, gap, fields used). The session wrote the file, so the limit (it cannot be proven unmodified) is stated in the output by the caller."""
    from lifeos.jira import readiness_record as rr
    p, shown = Path(path), None
    try:
        stale = time.time() - p.stat().st_mtime > MCP_MAX_AGE
        raw = rr.from_mcp(json.loads(p.read_text()), key)
        shown = rr.used(raw)
        refusals, warnings = check_issue(rr.normalize(raw))
        return refusals, ([refuse("stale_read", "file", "saved more than 15 minutes ago")] if stale else []) + warnings, None, shown
    except Exception as exc:                                  # noqa: BLE001  every failure is a fixed code, never a body or value
        code = str(exc) if str(exc).startswith(("INCOMPLETE:", "KEY_MISMATCH")) else type(exc).__name__
        return [], [], f"QUEUE_NOT_READY — {code}", shown


GAPS_JQL = "project = LIFE AND statusCategory != Done AND issuetype != Epic ORDER BY key ASC"
GAPS_FIELDS = "parent,components,fixVersions,labels"


def open_gaps(issues):
    """LIFE-850: -> [(key, [missing...])] for the issues that lack a parent, a Component, a Fix Version or a readiness label (`ready`, `needs-prep`, `needs-jim`). Pure; reporting only."""
    from lifeos.jira.release import READINESS
    out = []
    for issue in issues:
        f = issue.get("fields") or {}
        missing = [name for name, absent in (("parent", not f.get("parent")), ("component", not f.get("components")), ("fix_version", not f.get("fixVersions")),
                                             ("readiness_label", not any(x in READINESS for x in f.get("labels") or []))) if absent]
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
    """Warn lines first, then refusals; exit 0 with `ready` only when nothing refused."""
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
        except Exception as exc:                                  # noqa: BLE001  a fixed code only, never a body, URL or value
            code = str(exc) if str(exc).startswith(("JIRA_", "INCOMPLETE:", "JM_DASH_")) else type(exc).__name__
            print(f"QUEUE_NOT_READY — {code}", file=sys.stderr)
            return 2
        return gaps_report(issues)
    if argv[:1] in (["issue-key"], ["issue-json"]) and len(argv) == 2:
        refusals, warnings, gap = jira_issue(argv[0], argv[1])
        if gap:
            print(gap, file=sys.stderr)
            if "_MISSING:" in gap:                                    # the settings-missing gap (LIFE-313); names only
                print("hint: no Jira settings here? Read the ticket with the Atlassian MCP, save the result and run: python -m lifeos.sources.devtools.readiness issue-mcp FILE KEY", file=sys.stderr)
            return 2
        return report(refusals, warnings)
    try:
        mode, args = argv[0], argv[1:]
        checks = {"issue": lambda: check_issue(json.loads(Path(args[0]).read_text())),
                  "repo": lambda: (check_repo(args[0] if args else Path(__file__).resolve().parents[3]), [])}
        refusals, warnings = checks[mode]()
    except Exception:                                                        # noqa: BLE001  unreadable input, bad mode or any error is a refusal, never a pass
        refusals, warnings = [refuse("input_unreadable", "argv")], []
    return report(refusals, warnings)


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
