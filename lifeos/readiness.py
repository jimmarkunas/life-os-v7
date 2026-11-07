"""LIFE-225 (ARCH-04; LOS-74 Q24, Q30): one versioned, deterministic, offline readiness validator for coding agents and CI.

Three checks, one refusal shape, stdlib only, no network, no state of its own:
  issue      a normalized LIFE issue record is eligible for autonomous work (origin, lane, native blockers, authorization, permissions, Given/When/Then; owner/host/source optional, checked if present; requirement: a ticket naming a LOS origin must link its Notion REQ page (LIFE-319); needs/decisions: every row {what, proof, status: done} or the issue is not released)
  preflight  a writer claim is safe to act on (live-fetched, unchanged GitHub baseline; no other writer on the issue or surface; with --pre only availability, otherwise this writer's own recorded claim too)
  issue-key  the same check on a LIVE LIFE issue read from Jira by key (LIFE-313; read-only GET with the existing JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN), and issue-json FILE on a saved REST v3 issue; unreadable or incomplete Jira data prints QUEUE_NOT_READY, never ready
  issue-mcp  (LIFE-363, REQ-6) the same check on the unmodified Atlassian MCP getJiraIssue result saved to FILE by the session: key must match, the file must be under 15 minutes old, every field present; no Jira credential; prints SOURCE mcp-read and the fields used
  repo       the global source index still points at real files with an owner and an authority, inside the size limit

Fail closed: any refusal, unreadable input or error exits 2 before the caller mutates anything. Exit 0 prints `ready`.
Usage: python -m lifeos.readiness issue FILE | issue-key KEY | issue-json FILE | issue-mcp FILE KEY | preflight [--pre] CLAIM STATE [REPO] | repo [ROOT]   (FILE, CLAIM, STATE are JSON; main is fetched live from REPO's origin, never asserted)
The ChatGPT connector preflight is procedural (Q30); this module does not claim to enforce it."""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

VERSION = 1
LANES = ("lane-career-v7", "lane-edge-comm")
SHA = re.compile(r"[0-9a-f]{40}")
KEY = re.compile(r"(LIFE|LOS)-\d+")
SIZE_LIMIT = 15 * 1024                        # bytes: the policy-part limit (Q12), applied to every indexed global source
GWT = ("given", "when", "then")
ISSUE_FIELDS = {"key", "labels", "origin", "blockers", "authorization", "permissions", "acceptance"}   # the lane label is the owner; owner, host and source are optional


def refuse(code, field, detail=""):
    return {"code": code, "field": field, "detail": detail}


def text(value):
    return isinstance(value, str) and bool(value.strip())


def check_issue(issue):
    """Refusals for a normalized issue record; an empty list means eligible. Each gap is reported on its own."""
    if not isinstance(issue, dict):
        return [refuse("issue_unreadable", "issue")]
    out = [refuse("field_absent", name) for name in sorted(ISSUE_FIELDS - issue.keys())]
    if not KEY.fullmatch(str(issue.get("key", ""))):
        out.append(refuse("key_invalid", "key", str(issue.get("key", ""))))
    origin = issue.get("origin")
    if "origin" in issue:
        los = origin.get("los") if isinstance(origin, dict) else None
        parent = origin.get("parent") if isinstance(origin, dict) else None
        if not (los or parent):
            out.append(refuse("origin_missing", "origin"))                 # a new outcome cites an approved LOS Idea; maintenance cites its approved parent Epic
        elif los and not (re.fullmatch(r"LOS-\d+", str(los)) and origin.get("approved") is True):
            out.append(refuse("origin_unapproved", "origin", str(los)))
        elif parent and not los and not (re.fullmatch(r"LIFE-\d+", str(parent)) and origin.get("approved") is True):
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
            out += [refuse("blocker_not_done", "blockers", f"{b.get('key')}={b.get('status')}") for b in blockers if not (isinstance(b, dict) and b.get("status") == "Done")]
    if "authorization" in issue:
        auth = issue["authorization"]
        if isinstance(auth, dict) and auth.get("denied") is True:
            out.append(refuse("authorization_denied", "authorization"))    # explicit denial wins even when approved_by and provenance are present
        elif not (isinstance(auth, dict) and text(auth.get("approved_by")) and text(auth.get("provenance")) and auth.get("denied", False) is False):
            out.append(refuse("authorization_missing", "authorization"))   # a Jira comment or agent message alone grants nothing; a non-boolean denied is malformed
    if "permissions" in issue and not (isinstance(issue["permissions"], list) and issue["permissions"] and all(text(p) for p in issue["permissions"])):
        out.append(refuse("permissions_missing", "permissions"))
    for name, code in (("needs", "prerequisite_open"), ("decisions", "decision_open")):   # delivery-flow v2 (PENDING Jim): checked when present; a ticket that cannot run is not released
        if name in issue:
            rows = issue[name]
            if not isinstance(rows, list):
                out.append(refuse(f"{name}_unreadable", name))
            else:
                out += [refuse(code, name, f"item {i + 1}") for i, r in enumerate(rows) if not (isinstance(r, dict) and text(r.get("what")) and text(r.get("proof")) and r.get("status") == "done")]
    if "requirement" in issue:                                              # LIFE-319: only a ticket that names a LOS origin needs a Notion requirement page; no Notion read, no credential
        req = issue["requirement"]
        if not (isinstance(req, dict) and isinstance(req.get("pages"), list)):
            out.append(refuse("requirement_unreadable", "requirement"))
        elif req.get("los") and not req["pages"]:
            out.append(refuse("requirement_page_missing", "requirement", str(req["los"])))
    if "acceptance" in issue:
        items = issue["acceptance"]
        if not (isinstance(items, list) and items):
            out.append(refuse("gwt_missing", "acceptance"))
        else:
            out += [refuse("gwt_incomplete", "acceptance", f"item {i + 1}") for i, c in enumerate(items) if not (isinstance(c, dict) and all(text(c.get(k)) for k in GWT))]
    return out


def fetch_main(repo):
    """The live origin/main SHA, fetched here by git itself; the caller cannot assert it. None when the fetch or the lookup fails."""
    try:
        subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin", "main"], check=True, capture_output=True, timeout=60)
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "origin/main"], check=True, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return sha if SHA.fullmatch(sha) else None


def check_preflight(claim, state, main, pre=False):
    """Refusals before any mutation. claim = what this agent intends; state = the writer-claim record just read; main = the SHA fetch_main returned.
    pre=True is the pre-claim availability check (may I record this claim?). The default is the mutation gate: it also requires this writer's own recorded claim."""
    if not (isinstance(claim, dict) and isinstance(state, dict)):
        return [refuse("preflight_unreadable", "input")]
    out = []
    if not (text(claim.get("writer")) and KEY.fullmatch(str(claim.get("issue", ""))) and isinstance(claim.get("surfaces"), list)):
        out.append(refuse("claim_missing", "claim"))
    base = claim.get("base_sha")
    if not (isinstance(base, str) and SHA.fullmatch(base) and isinstance(main, str) and SHA.fullmatch(main)):
        out.append(refuse("baseline_unverified", "main_sha"))            # main was not fetched live, so there is nothing to compare with
    elif base != main:
        out.append(refuse("baseline_changed", "base_sha", f"claimed {base[:7]}, main {main[:7]}"))
    claims = state.get("claims")
    if not isinstance(claims, list):
        out.append(refuse("claims_unverified", "claims"))                # the claim record was not read: never assume it is empty
    elif not out:
        mine = set(claim["surfaces"])
        for other in claims:
            if not isinstance(other, dict) or not text(other.get("writer")):
                out.append(refuse("claims_unverified", "claims"))
            elif other["writer"] != claim["writer"] and (other.get("issue") == claim["issue"] or mine & set(other.get("surfaces", [])) or other.get("exclusive") is True):
                out.append(refuse("writer_conflict", "claims", f"{other['writer']} holds {other.get('issue')}"))
        recorded = [c for c in claims if isinstance(c, dict) and c.get("writer") == claim["writer"] and c.get("issue") == claim["issue"] and mine <= set(c.get("surfaces", []))]
        if not (pre or recorded or out):
            out.append(refuse("claim_not_recorded", "claims", f"{claim['writer']} has no recorded claim on {claim['issue']} covering the surfaces"))
    return out


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
    sized = [root / n for n in ("INDEX.md", "AGENTS.md", "CLAUDE.md")] + sorted((root / "docs" / "development-policy").glob("*.md"))
    out += [refuse("size_over_limit", str(f.relative_to(root)), str(f.stat().st_size)) for f in sized if f.is_file() and f.stat().st_size > SIZE_LIMIT]
    if not set(("key", "acceptance_criteria", "owner", "authorization")) <= set(schema.get("required", [])):
        out.append(refuse("schema_fields_lost", "docs/jira/issue-schema.v1.json"))
    return out


def jira_issue(mode, arg):
    """LIFE-313: normalize a Jira issue (live by key, or a saved REST v3 JSON file) and run the same issue check. Gaps in Jira data are QUEUE_NOT_READY."""
    from lifeos.jira import readiness_record as rr          # lazy: the offline modes never import or touch the network
    try:
        raw = rr.fetch(arg) if mode == "issue-key" else json.loads(Path(arg).read_text())
        return check_issue(rr.normalize(raw)), None
    except Exception as exc:                                  # noqa: BLE001  every failure is a fixed code, never a body, URL or value
        code = str(exc) if str(exc).startswith(("JIRA_", "INCOMPLETE:")) else type(exc).__name__
        return [], f"QUEUE_NOT_READY — {code}"


MCP_MAX_AGE = 15 * 60                         # seconds: a saved MCP read older than this is stale (REQ-6)


def mcp_issue(path, key):
    """LIFE-363: -> (refusals, gap, fields used). The session wrote the file, so the limit (it cannot be proven unmodified) is stated in the output by the caller."""
    from lifeos.jira import readiness_record as rr
    p, shown = Path(path), None
    try:
        if time.time() - p.stat().st_mtime > MCP_MAX_AGE:
            return [], "QUEUE_NOT_READY — STALE_MCP_READ", None
        raw = rr.from_mcp(json.loads(p.read_text()), key)
        shown = rr.used(raw)
        return check_issue(rr.normalize(raw)), None, shown
    except Exception as exc:                                  # noqa: BLE001  every failure is a fixed code, never a body or value
        code = str(exc) if str(exc).startswith(("INCOMPLETE:", "KEY_MISMATCH")) else type(exc).__name__
        return [], f"QUEUE_NOT_READY — {code}", shown


def run(argv):
    if argv[:1] == ["issue-mcp"] and len(argv) == 3:
        refusals, gap, shown = mcp_issue(argv[1], argv[2])
        print("SOURCE mcp-read (the session saved this file; it cannot be proven unmodified)")
        if shown:
            print("FIELDS " + " ".join(f"{k}={v}" for k, v in shown.items()))
        if gap:
            print(gap, file=sys.stderr)
            return 2
        for r in refusals:
            print(f"refused v{VERSION} {r['code']} {r['field']} {r['detail']}".rstrip(), file=sys.stderr)
        if not refusals:
            print(f"ready v{VERSION}")
        return 2 if refusals else 0
    if argv[:1] in (["issue-key"], ["issue-json"]) and len(argv) == 2:
        refusals, gap = jira_issue(argv[0], argv[1])
        if gap:
            print(gap, file=sys.stderr)
            if "_MISSING:" in gap:                                    # the settings-missing gap (LIFE-313); names only
                print("hint: no Jira settings here? Read the ticket with the Atlassian MCP, save the result and run: python -m lifeos.readiness issue-mcp FILE KEY", file=sys.stderr)
            return 2
        for r in refusals:
            print(f"refused v{VERSION} {r['code']} {r['field']} {r['detail']}".rstrip(), file=sys.stderr)
        if not refusals:
            print(f"ready v{VERSION}")
        return 2 if refusals else 0
    try:
        mode, args = argv[0], argv[1:]
        pre = mode == "preflight" and args[:1] == ["--pre"]
        args = args[1:] if pre else args
        load = lambda p: json.loads(Path(p).read_text())                    # noqa: E731
        refusals = {"issue": lambda: check_issue(load(args[0])), "preflight": lambda: check_preflight(load(args[0]), load(args[1]), fetch_main(args[2] if len(args) > 2 else Path(__file__).resolve().parents[1]), pre),
                    "repo": lambda: check_repo(args[0] if args else Path(__file__).resolve().parents[1])}[mode]()
    except Exception:                                                        # noqa: BLE001  unreadable input, bad mode or any error is a refusal, never a pass
        refusals = [refuse("input_unreadable", "argv")]
    for r in refusals:
        print(f"refused v{VERSION} {r['code']} {r['field']} {r['detail']}".rstrip(), file=sys.stderr)
    if not refusals:
        print(f"ready v{VERSION}")
    return 2 if refusals else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
