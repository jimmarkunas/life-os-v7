"""Lane guard (LIFE-78, Jim-approved Oct 21 2026, concurrent lane ownership): a pull request may change only the paths its declared lane owns, and the check is a function of the repository, not of who remembers a rule.
Reads docs/OWNERSHIP.yml (a strict YAML subset: version, lanes, rules[path, lane|kind, sections[lane, match, scope, item_indent]]). A rule's `kind` is one of: both (every other lane must be named in a Handoff),
generated (any lane), append (any lane, additions only), unassigned (any lane; reported, never silently owned). A changed path no rule covers FAILS (unknown ownership). In a shared file a changed line belongs to the first
section whose regex matches that line (scope line) or the YAML block around it (scope block: the nearest line at item_indent), else to the rule's lane.
PR provenance is the PR body: `Lane: <lane>`, `Handoff: path=<glob> owner=<lane> approval=<https url>`, `Coordinated-with: #<n>`. A Handoff is checked for shape (known owner other than the PR lane, a glob that covers the
path, an https approval link on an approved host); this check cannot verify that the approval itself is genuine. Another open PR of a different lane that changes the same file and section is reported and fails unless
coordinated. Stdlib only, read only, no secret; any unreadable input is a failure, never a pass. python -m lifeos.lane_guard"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

OWNERSHIP = "docs/OWNERSHIP.yml"
KINDS = ("both", "generated", "append", "unassigned")
HOSTS = ("jimmarkunas.atlassian.net", "github.com", "app.notion.com", "www.notion.so")
HANDOFF = re.compile(r"^Handoff:\s*path=(\S+)\s+owner=(\S+)\s+approval=(https://\S+)\s*$", re.M)


class GuardError(Exception):
    """Fixed reason only; never a body, token or private value."""


def _scalar(text):
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        return [_scalar(p) for p in text[1:-1].split(",") if p.strip()]
    if len(text) > 1 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return int(text) if text.isdigit() else text


def _block(rows, i, indent):
    if rows[i][1].startswith("- "):
        out = []
        while i < len(rows) and rows[i][0] == indent and rows[i][1].startswith("- "):
            rows[i] = (indent + 2, rows[i][1][2:])                                   # the item's first key is a mapping line one level deeper
            item, i = _block(rows, i, indent + 2)
            out.append(item)
        return out, i
    out = {}
    while i < len(rows) and rows[i][0] == indent and not rows[i][1].startswith("- "):
        key, sep, value = rows[i][1].partition(":")
        i += 1
        if not sep or not key.strip():
            raise GuardError("OWNERSHIP_SHAPE")
        if value.strip():
            out[key.strip()] = _scalar(value)
        elif i < len(rows) and rows[i][0] > indent:
            out[key.strip()], i = _block(rows, i, rows[i][0])
        else:
            raise GuardError("OWNERSHIP_EMPTY_VALUE")
    return out, i


def parse_ownership(text):
    """The OWNERSHIP.yml subset -> dict. Anything outside it raises GuardError (fail closed)."""
    rows = [(len(r) - len(r.lstrip()), r.strip()) for r in text.splitlines() if r.strip() and not r.lstrip().startswith("#")]
    if not rows or rows[0][0] != 0:
        raise GuardError("OWNERSHIP_INDENT")
    doc, end = _block(rows, 0, 0)
    if end != len(rows):
        raise GuardError("OWNERSHIP_INDENT")
    return doc


def validate(doc):
    lanes = doc.get("lanes")
    if doc.get("version") != 1 or not isinstance(lanes, list) or not lanes or not isinstance(doc.get("rules"), list):
        raise GuardError("OWNERSHIP_INVALID")
    for rule in doc["rules"]:
        if not isinstance(rule.get("path"), str) or (rule.get("lane") not in lanes) == (rule.get("kind") not in KINDS):
            raise GuardError("OWNERSHIP_RULE_INVALID")
        for s in rule.get("sections", []):
            if s.get("lane") not in lanes or not s.get("match") or s.get("scope", "line") not in ("line", "block"):
                raise GuardError("OWNERSHIP_SECTION_INVALID")
            re.compile(s["match"])
    return doc


def rule_for(doc, path):
    """The most specific rule: the longest literal prefix of a matching glob; None = unknown ownership."""
    hits = [r for r in doc["rules"] if fnmatch.fnmatchcase(path, r["path"])]
    return max(hits, key=lambda r: len(r["path"].split("*")[0]), default=None)


def block_text(lines, i, indent):
    """The block around line i whose first line is the nearest one at exactly `indent` spaces; None when line i is not inside such a block."""
    depth = lambda s: len(s) - len(s.lstrip())                                       # noqa: E731
    if not 0 <= i < len(lines):                                                      # no file text to read the block from (another PR's patch): the caller falls back to the line
        return None
    j = i
    while j >= 0 and not (lines[j].strip() and depth(lines[j]) == indent):
        if lines[j].strip() and depth(lines[j]) < indent:
            return None
        j -= 1
    if j < 0:
        return None
    k = j + 1
    while k < len(lines) and (not lines[k].strip() or depth(lines[k]) > indent):
        k += 1
    return "\n".join(lines[j:k])


def line_lane(rule, text, lines, i):
    for s in rule.get("sections", []):
        subject = text
        if s.get("scope", "line") == "block":
            subject = block_text(lines, i, s.get("item_indent", 0)) or text
        if re.search(s["match"], subject):
            return s["lane"]
    return rule.get("lane")


def changed_lines(diff):
    """A `git diff -U0` of one file -> (removed [(base line no, text)], added [(head line no, text)])."""
    removed, added, old, new = [], [], 0, 0
    for row in diff.splitlines():
        m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", row)
        if m:
            old, new = int(m.group(1)), int(m.group(2))
        elif row.startswith("-") and not row.startswith("---"):
            removed.append((old, row[1:]))
            old += 1
        elif row.startswith("+") and not row.startswith("+++"):
            added.append((new, row[1:]))
            new += 1
    return removed, added


def touched(rule, base, head, removed, added):
    """The set of lanes whose lines this file change touches (a pure function of the two file texts and the changed lines)."""
    b, h, lanes = base.splitlines(), head.splitlines(), set()
    for n, text in removed:
        lanes.add(line_lane(rule, text, b, n - 1))
    for n, text in added:
        lanes.add(line_lane(rule, text, h, n - 1))
    return lanes or {rule.get("lane")}


def parse_pr(body):
    lane = re.search(r"^Lane:\s*(\S+)\s*$", body or "", re.M)
    return {"lane": lane.group(1) if lane else None, "handoffs": HANDOFF.findall(body or ""),
            "coordinated": {int(n) for n in re.findall(r"^Coordinated-with:\s*#(\d+)", body or "", re.M)}}


def handoff_ok(handoff, lanes, pr_lane, path):
    glob, owner, url = handoff
    host = url.split("/")[2].lower() if url.count("/") >= 2 else ""
    return owner in lanes and owner != pr_lane and host in HOSTS and fnmatch.fnmatchcase(path, glob)


def check(doc, pr, files, others=()):
    """files: [{path, base, head, removed, added}] (base/head "" when absent). others: [{number, lane, files: [{path, lanes}]}]. -> (violations, warnings), each a list of fixed strings."""
    bad, warn, lanes, mine = [], [], doc["lanes"], pr["lane"]
    if mine not in lanes:
        return ["PR_LANE_MISSING_OR_UNKNOWN: put `Lane: <lane>` in the PR body"], warn
    seen = {}
    for f in files:
        rule = rule_for(doc, f["path"])
        if rule is None:
            bad.append(f"UNKNOWN_OWNERSHIP {f['path']}")
            continue
        kind = rule.get("kind")
        if kind == "generated":
            continue
        if kind == "append":
            if f["removed"]:
                bad.append(f"APPEND_ONLY_VIOLATED {f['path']}")
            continue
        if kind == "unassigned":
            warn.append(f"UNASSIGNED_PATH {f['path']} (owner not yet decided)")
            continue
        owners = {r for r in lanes if r != mine} if kind == "both" else touched(rule, f["base"], f["head"], f["removed"], f["added"]) - {mine}
        seen[f["path"]] = touched(rule, f["base"], f["head"], f["removed"], f["added"]) if kind != "both" else {mine}
        for owner in sorted(owners):
            if not any(handoff_ok(h, lanes, mine, f["path"]) and h[1] == owner for h in pr["handoffs"]):
                bad.append(f"OUT_OF_LANE {f['path']} owned by {owner}: needs `Handoff: path=<glob> owner={owner} approval=<https link>`")
    for h in pr["handoffs"]:
        if h[1] not in lanes or h[1] == mine or h[2].split("/")[2].lower() not in HOSTS:
            bad.append(f"HANDOFF_INVALID owner={h[1]}")
    for other in others:
        if other["lane"] == mine or other["number"] in pr["coordinated"]:
            continue
        for o in other["files"]:
            if o["path"] in seen and (o["lanes"] & seen[o["path"]]) and rule_for(doc, o["path"]).get("kind") not in ("generated", "append", "unassigned"):
                bad.append(f"OVERLAPPING_PR #{other['number']} ({other['lane']}) changes {o['path']}: coordinate with `Coordinated-with: #{other['number']}`")
    return bad, warn


def _git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _show(rev, path):
    out = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def _api(repo, path, token):
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def others_from_api(doc, repo, token, mine):
    """Open PRs of a different lane -> [{number, lane, files}]. Their lanes come from their bodies; changed lines are attributed from the API patch (line scope only)."""
    out = []
    for pr in _api(repo, "pulls?state=open&per_page=100", token):
        if pr["number"] == mine:
            continue
        lane = parse_pr(pr.get("body"))["lane"]
        files = []
        for f in _api(repo, f"pulls/{pr['number']}/files?per_page=100", token):
            rule = rule_for(doc, f["filename"])
            removed, added = changed_lines(f.get("patch", "") if "@@" in f.get("patch", "") else "")
            files.append({"path": f["filename"], "lanes": touched(rule, "", "", removed, added) if rule and rule.get("sections") else {rule.get("lane") if rule else None}})
        out.append({"number": pr["number"], "lane": lane, "files": files})
    return out


def main(environ=os.environ):
    try:
        event = json.loads(Path(environ["GITHUB_EVENT_PATH"]).read_text())["pull_request"]
        base, head = event["base"]["sha"], event["head"]["sha"]
        text = _show(base, OWNERSHIP)                                           # the map on the BASE decides: a PR cannot grant itself ownership
        if not text:                                                            # bootstrap, once: the PR that ADDS the map is judged by the map it adds
            text = _show(head, OWNERSHIP)
            if text:
                print("lane-guard: BOOTSTRAP (the base has no map; using the head's, validated)")
        doc = validate(parse_ownership(text))
        files = []
        for path in _git("diff", "--name-only", f"{base}...{head}").splitlines():
            removed, added = changed_lines(_git("diff", "-U0", f"{base}...{head}", "--", path))
            files.append({"path": path, "base": _show(base, path), "head": _show(head, path), "removed": removed, "added": added})
        pr = parse_pr(event.get("body"))
        others = others_from_api(doc, environ["GITHUB_REPOSITORY"], environ["GITHUB_TOKEN"], event["number"])
    except (GuardError, KeyError, ValueError, IndexError, TypeError, OSError, subprocess.CalledProcessError) as error:
        print(f"lane-guard: FAILED CLOSED ({type(error).__name__}: {str(error)[:60]})")
        return 1
    bad, warn = check(doc, pr, files, others)
    for line in warn:
        print(f"lane-guard warning: {line}")
    for line in bad:
        print(f"lane-guard violation: {line}")
    print(f"lane-guard: lane={pr['lane']} files={len(files)} violations={len(bad)} warnings={len(warn)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
