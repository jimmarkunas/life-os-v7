"""Lane guard (LIFE-78, Jim-approved Oct 21 2026, concurrent lane ownership). RUNS ONLY from trusted default-branch code: .github/workflows/lane-guard.yml is a pull_request_target job that checks out the DEFAULT
branch, never the PR, and reads the PR as DATA through the read-only GitHub API (file lists, file contents at the PR's base and head SHAs, open PRs). It executes nothing from the PR, so a PR cannot change the check that judges it.
docs/OWNERSHIP.yml (read from the default branch) gives every path exactly one answer: a lane (career-v7, edge-oracle); `generated` (mechanically verified by an existing test, any lane); `append` (additions only);
`protected` (needs Jim's protected review: always fails here); `unresolved` (Jim has not decided: always fails here, never warns). A path no rule covers fails. In a shared file, each changed line (compared as full base/head texts) takes
the lane of the section(s) whose regex matches it or its enclosing YAML block, else the rule's lane; sections that disagree, or a block that cannot be read, fail as ambiguous. A section lane may itself be `unresolved`.
A change to another lane's path passes only if docs/HANDOFFS.yml ON THE DEFAULT BRANCH (a file only Jim's merge can change) names this PR number, the owning lane and a glob covering the path, unexpired, with an https approval link:
the PR body's own words (`Lane:` is provenance, not authority) never count. Another open PR of a different or undeclared lane that changes the same file and section fails. Any incomplete evidence (a truncated list or
pagination, a missing file, a non-text file, an API error) is a failure, never a pass. Stdlib only, read only, no secret beyond the read token. python -I lifeos/lane_guard.py

LIFE-109 delivery train (Jim, Oct 24 2026). While OWNERSHIP.yml on the default branch names an active train for a lane, every PR of that lane must (a) name one of the train's Jira keys
(`Jira: LIFE-n`), (b) change at least one of the train's product paths (a Platform- or docs-only change never counts as train delivery), and (c) state its evidence level
(`Evidence: CODE LANDED`, `SYNTHETIC PASS` or `LIVE READ-BACK`); `JIM ACCEPTED` is Jim's alone and is refused as a self-claim. An approved emergency repair passes only through an
`exemptions` entry in HANDOFFS.yml on the default branch (PR number, https approval, expiry). The guard cannot read Jira (no credential, by design): it checks the key against the
train's list, never the issue's live status."""
import difflib
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

OWNERSHIP, HANDOFFS = "docs/OWNERSHIP.yml", "docs/HANDOFFS.yml"
KINDS = ("generated", "append", "protected", "unresolved", "shared")
FAILS = ("protected", "unresolved")
SHARED_PATHS = frozenset((".claude/settings.json",))
HOSTS = ("jimmarkunas.atlassian.net", "github.com", "app.notion.com", "www.notion.so")
PAGE, PAGES = 100, 30


class GuardError(Exception):
    """Fixed reason only; never a body, token or private value."""


class Unverifiable(GuardError):
    """The evidence is incomplete: the run fails, it never passes on part of the data."""


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
            raise GuardError("MAP_SHAPE")
        if value.strip():
            out[key.strip()] = _scalar(value)
        elif i < len(rows) and rows[i][0] > indent:
            out[key.strip()], i = _block(rows, i, rows[i][0])
        else:
            raise GuardError("MAP_EMPTY_VALUE")
    return out, i


def parse_map(text):
    """The strict YAML subset of OWNERSHIP.yml / HANDOFFS.yml -> dict. Anything outside it raises (fail closed)."""
    rows = [(len(r) - len(r.lstrip()), r.strip()) for r in text.splitlines() if r.strip() and not r.lstrip().startswith("#")]
    if not rows or rows[0][0] != 0:
        raise GuardError("MAP_INDENT")
    doc, end = _block(rows, 0, 0)
    if end != len(rows):
        raise GuardError("MAP_INDENT")
    return doc


def validate(doc):
    lanes = doc.get("lanes")
    if doc.get("version") != 1 or not isinstance(lanes, list) or not lanes or not isinstance(doc.get("rules"), list):
        raise GuardError("OWNERSHIP_INVALID")
    for rule in doc["rules"]:
        if not isinstance(rule.get("path"), str) or (rule.get("lane") in lanes) == (rule.get("kind") in KINDS):
            raise GuardError("OWNERSHIP_RULE_INVALID")                              # exactly one answer: a lane XOR a kind
        if rule.get("kind") == "shared" and rule["path"] not in SHARED_PATHS:
            raise GuardError("SHARED_PATH_INVALID")
        for s in rule.get("sections", []):
            if s.get("lane") not in lanes + ["unresolved"] or not s.get("match") or s.get("scope", "line") not in ("line", "block"):
                raise GuardError("OWNERSHIP_SECTION_INVALID")
            re.compile(s["match"])
    return doc


def validate_handoffs(doc, lanes):
    if doc.get("version") != 1 or not isinstance(doc.get("handoffs"), list):
        raise GuardError("HANDOFFS_INVALID")
    for h in doc["handoffs"]:
        try:
            date.fromisoformat(str(h["expires"]))
        except (KeyError, ValueError):
            raise GuardError("HANDOFFS_INVALID") from None
        link = str(h.get("approval", ""))
        if not isinstance(h.get("pr"), int) or h.get("owner") not in lanes or not isinstance(h.get("path"), str) or not link.startswith("https://") or link.split("/")[2] not in HOSTS:
            raise GuardError("HANDOFFS_INVALID")
    return doc["handoffs"]


EVIDENCE = ("CODE LANDED", "SYNTHETIC PASS", "LIVE READ-BACK")


def validate_trains(doc):
    """The optional `trains` list of OWNERSHIP.yml: each names a lane, its Jira keys and its product path globs."""
    trains = doc.get("trains", [])
    if not isinstance(trains, list):
        raise GuardError("TRAINS_INVALID")
    for t in trains:
        if not isinstance(t, dict):
            raise GuardError("TRAINS_INVALID")
        keys, product = t.get("issues"), t.get("product")
        if (t.get("lane") not in doc["lanes"] or not isinstance(t.get("name"), str) or not isinstance(keys, list) or not keys or not isinstance(product, list) or not product
                or not all(isinstance(k, str) and re.fullmatch(r"LIFE-\d+", k) for k in keys) or not all(isinstance(g, str) and g for g in product)):
            raise GuardError("TRAINS_INVALID")
    if len({t["lane"] for t in trains}) != len(trains):
        raise GuardError("TRAINS_INVALID")                                       # one active train per lane
    return trains


def validate_exemptions(doc, today=None):
    out = doc.get("exemptions", [])
    if not isinstance(out, list):
        raise GuardError("HANDOFFS_INVALID")
    for e in out:
        if not isinstance(e, dict):
            raise GuardError("HANDOFFS_INVALID")
        try:
            date.fromisoformat(str(e["expires"]))
        except (KeyError, ValueError):
            raise GuardError("HANDOFFS_INVALID") from None
        link = str(e.get("approval", ""))
        if not isinstance(e.get("pr"), int) or not link.startswith("https://") or link.split("/")[2] not in HOSTS:
            raise GuardError("HANDOFFS_INVALID")
    return out


def train_check(trains, exemptions, pr, files, today=None):
    """LIFE-109: the active train of the PR's lane, if any. -> violations (fixed strings). PR text supplies only the claims being checked, never authority."""
    today, body = today or date.today(), re.sub(r"<!--.*?-->", "", pr.get("body") or "", flags=re.S)     # template guidance in an HTML comment is never a claim
    train = next((t for t in trains if t["lane"] == pr["lane"]), None)
    if train is None or any(e["pr"] == pr["number"] and date.fromisoformat(str(e["expires"])) >= today for e in exemptions):
        return []
    bad = []
    keys = set(re.findall(r"^Jira:[ \t]*(LIFE-\d+)\b", body, re.M))
    if not keys & set(train["issues"]):
        bad.append(f"TRAIN_JIRA_KEY_MISSING: name one of the {train['name']} train's issues as `Jira: LIFE-n` in the PR body")
    if not any(fnmatch.fnmatchcase(f["path"], g) for f in files for g in train["product"]):
        bad.append(f"TRAIN_NO_PRODUCT_CHANGE: no {train['name']} product path changed; Platform-, test- or docs-only work is not train delivery (an approved repair needs an exemption in {HANDOFFS})")
    levels = re.findall(r"^Evidence:[ \t]*(.+?)[ \t\r]*$", body, re.M)
    if any(re.search(r"\bJIM ACCEPTED\b", level, re.I) for level in levels) or re.search(r"^(?:Status|Acceptance):[ \t]*JIM ACCEPTED\b", body, re.I | re.M):
        bad.append("TRAIN_SELF_ACCEPTANCE: JIM ACCEPTED is recorded by Jim, never claimed in a PR")
    if len(levels) != 1 or levels[0].upper() not in EVIDENCE:
        bad.append("TRAIN_EVIDENCE_MISSING: state exactly one `Evidence: CODE LANDED | SYNTHETIC PASS | LIVE READ-BACK` line")
    return bad


def handoff_ok(handoffs, number, owner, path, today):
    """True only when the DEFAULT branch's HANDOFFS.yml names this PR, the owning lane and a glob covering the path, unexpired. PR text is never consulted."""
    return any(h["pr"] == number and h["owner"] == owner and fnmatch.fnmatchcase(path, h["path"]) and date.fromisoformat(str(h["expires"])) >= today for h in handoffs)


def rule_for(doc, path):
    """The most specific rule: the longest literal prefix of a matching glob; None = unknown ownership."""
    hits = [r for r in doc["rules"] if fnmatch.fnmatchcase(path, r["path"])]
    return max(hits, key=lambda r: len(r["path"].split("*")[0]), default=None)


def block_text(lines, i, indent):
    """The block around line i whose first line is the nearest one at exactly `indent` spaces; None when line i lies outside such a block; Unverifiable when there is no text to read it from."""
    depth = lambda s: len(s) - len(s.lstrip())                                       # noqa: E731
    if not 0 <= i < len(lines):
        raise Unverifiable("SECTION_CONTEXT_MISSING")
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
    """The one lane a changed line belongs to: every matching section must agree; disagreement is ambiguity, which fails."""
    found = set()
    for s in rule.get("sections", []):
        subject = text
        if s.get("scope", "line") == "block":
            subject = block_text(lines, i, s.get("item_indent", 0))
        if subject is not None and re.search(s["match"], subject):
            found.add(s["lane"])
    if len(found) > 1:
        raise Unverifiable("AMBIGUOUS_SECTION")
    return found.pop() if found else rule.get("lane")


def diff_lines(base, head):
    """Full base/head texts -> (removed [(base line no, text)], added [(head line no, text)]); a pure function of the two texts."""
    a, b, removed, added = base.splitlines(), head.splitlines(), [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            removed += [(n + 1, a[n]) for n in range(i1, i2)]
            added += [(n + 1, b[n]) for n in range(j1, j2)]
    return removed, added


def touched(rule, base, head):
    """The set of lanes (or `unresolved`) whose lines this change touches, from the full texts."""
    removed, added = diff_lines(base, head)
    b, h = base.splitlines(), head.splitlines()
    lanes = {line_lane(rule, text, b, n - 1) for n, text in removed} | {line_lane(rule, text, h, n - 1) for n, text in added}
    return lanes or {rule.get("lane")}, bool(removed)


def parse_lane(body):
    m = re.search(r"^Lane:\s*(\S+)\s*$", body or "", re.M)
    return m.group(1) if m else None


def check(doc, handoffs, pr, files, others=(), today=None):
    """pr: {number, lane}. files: [{path, base, head}] complete for the PR. others: [{number, lane, files: [{path, base, head}]}] for open PRs that changed any of the same paths. -> violations, each a fixed string."""
    today, bad, seen = today or date.today(), [], {}
    if pr["lane"] not in doc["lanes"]:
        return ["PR_LANE_MISSING_OR_UNKNOWN: put `Lane: <lane>` in the PR body (provenance only; it grants nothing)"]
    for f in files:
        rule = rule_for(doc, f["path"])
        if rule is None:
            bad.append(f"UNKNOWN_OWNERSHIP {f['path']}")
            continue
        kind = rule.get("kind")
        if kind in FAILS:
            bad.append(f"{'PROTECTED_PATH' if kind == 'protected' else 'UNRESOLVED_OWNERSHIP'} {f['path']}: " + ("needs Jim's protected review" if kind == "protected" else "no owner decided; blocked until Jim decides"))
            continue
        if kind == "shared":
            seen[f["path"]] = {"shared"}
            continue
        try:
            lanes, removed = touched(rule, f["base"], f["head"])
        except Unverifiable as error:
            bad.append(f"{error} {f['path']}")
            continue
        if kind == "append":
            if removed:
                bad.append(f"APPEND_ONLY_VIOLATED {f['path']}")
            continue
        if kind == "generated":
            continue
        seen[f["path"]] = lanes
        for owner in sorted(lanes - {pr["lane"]}):
            if owner == "unresolved":
                bad.append(f"UNRESOLVED_OWNERSHIP {f['path']}: a shared section has no owner decided; blocked until Jim decides")
            elif not handoff_ok(handoffs, pr["number"], owner, f["path"], today):
                bad.append(f"OUT_OF_LANE {f['path']} owned by {owner}: no approved handoff for PR #{pr['number']} in {HANDOFFS} on the default branch")
    for other in others:
        if other["lane"] == pr["lane"] and pr["lane"] in doc["lanes"]:
            continue
        for o in other["files"]:
            rule = rule_for(doc, o["path"])
            if o["path"] not in seen or rule is None or rule.get("kind") in ("generated", "append") + FAILS:
                continue
            if rule.get("kind") == "shared":
                bad.append(f"OVERLAPPING_PR #{other['number']} ({other['lane'] or 'no lane declared'}) changes {o['path']} (shared path): merge one first, then update the other")
                continue
            try:
                theirs, _ = touched(rule, o["base"], o["head"])
            except Unverifiable as error:
                bad.append(f"{error} {o['path']} (PR #{other['number']})")
                continue
            if theirs & seen[o["path"]]:
                bad.append(f"OVERLAPPING_PR #{other['number']} ({other['lane'] or 'no lane declared'}) changes {o['path']} in the same section: merge one first, then update the other")
    return bad


def _request(repo, path, token, raw=False):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}
    with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}", headers=headers), timeout=30) as response:
        data = response.read()
    return data if raw else json.loads(data)


def _api(repo, path, token):
    return _request(repo, path, token)


def _text(repo, path, ref, token, missing_ok):
    """A file's text at one SHA. Absent -> "" only where the caller expects absence; anything else (error, not UTF-8) is Unverifiable."""
    try:
        return _request(repo, f"contents/{path}?ref={ref}", token, raw=True).decode("utf-8")
    except urllib.error.HTTPError as error:
        if error.code == 404 and missing_ok:
            return ""
        raise Unverifiable("CONTENT_UNREADABLE") from None
    except (UnicodeDecodeError, OSError, ValueError):
        raise Unverifiable("CONTENT_UNREADABLE") from None


def paged(repo, path, token, api=None):
    """Every item of a list endpoint; a cap reached with a full last page, or a non-list answer, is Unverifiable (never a short list read as the whole)."""
    api, out = api or _api, []
    for page in range(1, PAGES + 1):
        chunk = api(repo, f"{path}{'&' if '?' in path else '?'}per_page={PAGE}&page={page}", token)
        if not isinstance(chunk, list):
            raise Unverifiable("API_SHAPE")
        out += chunk
        if len(chunk) < PAGE:
            return out
    raise Unverifiable("PAGINATION_INCOMPLETE")


def pr_files(repo, number, base, head, token, only=None, api=None, text=None):
    """The complete changed-file list of one PR with base and head texts. The count must equal the PR's own changed_files; a rename is a removal plus an addition (both paths are judged)."""
    api, text = api or _api, text or _text
    detail = api(repo, f"pulls/{number}", token)
    listed = paged(repo, f"pulls/{number}/files", token, api)
    if not isinstance(detail.get("changed_files"), int) or len(listed) != detail["changed_files"]:
        raise Unverifiable("FILE_LIST_INCOMPLETE")
    out = []
    for f in listed:
        status, path = f["status"], f["filename"]
        steps = [(f.get("previous_filename"), "removed"), (path, "added")] if status == "renamed" else [(path, status)]
        for name, state in steps:
            if name and (only is None or name in only):
                out.append({"path": name, "base": "" if state == "added" else text(repo, name, base, token, False), "head": "" if state == "removed" else text(repo, name, head, token, False)})
    return out


def others_from_api(repo, number, mine, token, api=None, text=None):
    """Open PRs (every page) that changed any path in `mine`, with full evidence for those paths only."""
    api, wanted = api or _api, {f["path"] for f in mine}
    out = []
    for pr in paged(repo, "pulls?state=open", token, api):
        if pr["number"] == number:
            continue
        theirs = paged(repo, f"pulls/{pr['number']}/files", token, api)
        if len(theirs) != api(repo, f"pulls/{pr['number']}", token).get("changed_files"):
            raise Unverifiable("FILE_LIST_INCOMPLETE")                           # a list shorter than the PR's own count must not read as "no shared path"
        listed = {f["filename"] for f in theirs} | {f.get("previous_filename") for f in theirs}
        if listed & wanted:
            out.append({"number": pr["number"], "lane": parse_lane(pr.get("body")),
                        "files": pr_files(repo, pr["number"], pr["base"]["sha"], pr["head"]["sha"], token, listed & wanted, api, text)})
    return out


def main(environ=os.environ, api=None, text=None):
    try:
        event = json.loads(Path(environ["GITHUB_EVENT_PATH"]).read_text())["pull_request"]
        repo, token, number = environ["GITHUB_REPOSITORY"], environ["GITHUB_TOKEN"], event["number"]
        doc = validate(parse_map(Path(OWNERSHIP).read_text()))                    # the DEFAULT branch's map and handoffs (the workflow checks that branch out): never the PR's
        handoff_doc = parse_map(Path(HANDOFFS).read_text())
        handoffs, exemptions = validate_handoffs(handoff_doc, doc["lanes"]), validate_exemptions(handoff_doc)
        trains = validate_trains(doc)
        files = pr_files(repo, number, event["base"]["sha"], event["head"]["sha"], token, None, api, text)
        pr = {"number": number, "lane": parse_lane(event.get("body")), "body": event.get("body") or ""}
        bad = check(doc, handoffs, pr, files, others_from_api(repo, number, files, token, api, text))
        bad += train_check(trains, exemptions, pr, files) if pr["lane"] in doc["lanes"] else []
    except (GuardError, KeyError, ValueError, IndexError, TypeError, OSError) as error:
        print(f"lane-guard: FAILED CLOSED ({type(error).__name__}: {str(error)[:60]})")
        return 1
    for line in bad:
        print(f"lane-guard violation: {line}")
    print(f"lane-guard: PR #{number} lane={pr['lane']} files={len(files)} violations={len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
