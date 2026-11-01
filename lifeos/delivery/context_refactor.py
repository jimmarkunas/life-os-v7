"""LIFE-79: dry-run planner for the Jira/Notion context refactor; it reads snapshots and writes nothing.

Input is a snapshot the authorized agent already read (Jira issues, the existing linked Notion pages fetched by id, writer claims). The tool splits each Jira
Description into sections, classifies them, proposes moving only clearly detailed requirements into the issue's ONE existing Notion page (never a new page), and
returns a plan with an old-to-new map, diffs and a digest. `validate` turns a plan plus an explicit approval into ordered operations for the authorized agent to run
through its own connectors; `verify` checks a fresh read-back. No network, credential, datastore, scheduler or Notion query lives here. Anything unclear stays in Jira."""
import argparse
import difflib
import hashlib
import json
import re
import sys

CLASSES = ("CURRENT_SCOPE", "DECISION", "DETAILED_REQUIREMENT", "HISTORICAL_EVIDENCE", "CONFLICT", "POINTER", "UNCLASSIFIED")
DEFAULT_EXCLUDED = ("LOS-70",)                                  # the reference implementation
HARD_BATCH_CAP = 10
HEADING = re.compile(r"^#{1,6} ", re.M)
POINTER = "> **Moved to Notion:**"
NEW_URL = "{NEW_PAGE_URL}"        # filled from the created page's own read-back; the plan never invents a URL
DISCOVERY = re.compile(r"Discovery log\s+—\s+Tranche|DISCOVERY CAPTURE PROTOCOL", re.I)
SECRET = re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*\S{8,}")
SIGNALS = {
    "HISTORICAL_EVIDENCE": re.compile(r"(?i)superseded|historical\s+[—-]|cancelled|read[- ]back|verified merged|\bPR #\d+|squash|\b[0-9a-f]{40}\b|github result"),
    "DECISION": re.compile(r"(?i)approved|decision|owner answer|answer \d|authority:"),
    "CURRENT_SCOPE": re.compile(r"(?i)\bgiven\b.*\bwhen\b.*\bthen\b|acceptance|scope|outcome|dependenc|non-goal|stop condition|execution"),
}
APPROVAL_HEADING = re.compile(r"(?i)\bapproved\b|\bdecision\b|\bauthori[sz]ation\b")
DETAIL_HEADING = re.compile(r"(?i)architecture|specification|requirements|capabilit|constraints|interface|infrastructure|matrix|proposal")
MIN_DETAIL_CHARS = 600


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_sections(text):
    """Exact slices at heading lines, so that "".join(sections) == text."""
    starts = [m.start() for m in HEADING.finditer(text)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    return [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)]) if text[a:b]]


def classify(section, retired_terms=()):
    """Return (class, underlying, conflicting terms). Signals that disagree leave the section where it is; only a clean detailed-requirement block can move."""
    if POINTER in section:
        return "POINTER", "POINTER", []
    heading = section.split("\n", 1)[0]
    hits = [name for name, rx in SIGNALS.items() if rx.search(section)]
    if APPROVAL_HEADING.search(heading) and "HISTORICAL_EVIDENCE" not in hits:
        underlying = "DECISION"                                      # an approved decision or authorization is never filed as history
    elif hits:
        underlying = "HISTORICAL_EVIDENCE" if "HISTORICAL_EVIDENCE" in hits else hits[0]
    elif DETAIL_HEADING.search(heading) and len(section) >= MIN_DETAIL_CHARS:
        underlying = "DETAILED_REQUIREMENT"
    else:
        underlying = "UNCLASSIFIED"
    conflicts = [t for t in retired_terms if underlying != "HISTORICAL_EVIDENCE" and re.search(r"(?i)\b%s\b" % re.escape(t), section)]
    return ("CONFLICT" if conflicts else underlying), underlying, conflicts


def norm(text):
    """Lower-case text with HTML tags, Markdown marks and link targets removed, so Jira Markdown and Notion markup can be compared."""
    t = re.sub(r"<[^>]+>", " ", text)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    return re.sub(r"\s+", " ", re.sub(r"[*_`#>|]", " ", t)).lower().strip()


def overlap(section, notion_text):
    """Share of the section's sentences (40+ chars) that already appear in the Notion page. A paraphrase scores low: that case needs a person."""
    pieces = [n for n in (norm(x) for x in re.split(r"(?<=[.;:])\s+|\n+", section)) if len(n) >= 40]
    page = norm(notion_text)
    return sum(x in page for x in pieces) / len(pieces) if pieces else 0.0


def links_sha(links):
    return sha(json.dumps(sorted((l.get("type", ""), l.get("direction", ""), l.get("key", "")) for l in links or [])))


def plan_issue(issue, notion, config):
    key, text = issue["key"], issue.get("description") or ""
    base = {"key": key, "source_revision": issue.get("updated"), "reasons": [], "sections": [], "mapping": []}
    claims, writer = config.get("writer_claims", {}), config.get("writer")
    excluded = set(config.get("excluded_keys", DEFAULT_EXCLUDED))
    if key in excluded:
        return dict(base, status="EXCLUDED", reasons=["EXCLUDED_BY_CONFIG"])
    if issue.get("active_discovery") or DISCOVERY.search(text):
        return dict(base, status="EXCLUDED", reasons=["ACTIVE_DISCOVERY"])
    if claims.get(key) not in (None, writer):
        return dict(base, status="BLOCKED", reasons=["CLAIMED_BY_OTHER_WRITER"])
    if SECRET.search(text):
        return dict(base, status="BLOCKED", reasons=["SECRET_LIKE_VALUE"])
    pages = issue.get("notion_pages") or []
    owner = pages[0] if len(pages) == 1 else None
    page = (notion or {}).get(owner["id"]) if owner else None
    if len(pages) > 1:
        base["reasons"].append("AMBIGUOUS_NOTION_OWNER")
    elif not pages and not issue.get("new_page"):
        base["reasons"].append("NO_NOTION_OWNER")
    elif pages and page is None:
        base["reasons"].append("NOTION_PAGE_NOT_READ")
    new = issue.get("new_page") if not pages else None           # a proposal only: the owner confirmed no existing page fits; nothing is created here
    if new:
        owner, page = {"id": "NEW:" + new["title"], "url": NEW_URL}, {"last_edited": new.get("parent_revision"), "text": ""}
    picks = tuple(issue.get("owner_moves", ()))                     # headings the owner chose to move; they still cannot override a conflict or a pointer
    sections = split_sections(text)
    assert "".join(sections) == text
    notion_text = (page or {}).get("text", "")
    out, moved = [], []
    for i, sec in enumerate(sections):
        cls, underlying, conflicts = classify(sec, config.get("retired_terms", ()))
        heading = sec.split("\n", 1)[0].strip()
        wanted = cls == "DETAILED_REQUIREMENT" or (cls not in ("CONFLICT", "POINTER") and any(heading.lstrip("# ").startswith(x) for x in picks))
        ov = round(overlap(sec, notion_text), 2) if wanted and page and not new else 0.0
        partial = wanted and 0.2 < ov < 0.8                          # part of it is already in Notion: do not copy a second version
        move = wanted and page is not None and not partial
        entry = {"id": "%s#%d" % (key, i), "heading": heading, "sha": sha(sec), "class": cls, "underlying": underlying, "conflicts": conflicts,
                 "destination": "NOTION" if move else "JIRA", "chars": len(sec), "overlap": ov, "partial_duplicate": partial}
        base["sections"].append(entry)
        if move:
            head, _ = (sec.split("\n", 1) + [""])[:2]
            swap = next((v for k, v in issue.get("replacements", {}).items() if heading.lstrip("# ").startswith(k)), None)   # owner-approved text in place of the usual stub (may be empty)
            out.append(head + "\n\n%s %s — %s (verbatim there; earlier text stays in this issue's history).\n\n" % (POINTER, owner["url"], head.lstrip("# ").strip()) if swap is None else swap)
            moved.append(sec)
            base["mapping"].append({"from": entry["id"], "to": "NOTION:%s#%s" % (owner["id"], entry["heading"]), "sha": entry["sha"]})
        else:
            out.append(sec)
            base["mapping"].append({"from": entry["id"], "to": "JIRA:%s#%d" % (key, len(out) - 1), "sha": entry["sha"]})
    base["exceptions"] = [{"id": s["id"], "conflicts": s["conflicts"]} for s in base["sections"] if s["class"] == "CONFLICT"]
    base["exceptions"] += [{"id": s["id"], "partial_duplicate": s["overlap"]} for s in base["sections"] if s["partial_duplicate"]]
    base["preserved"] = {"key": key, "status": issue.get("status"), "links_sha": links_sha(issue.get("links")), "comment_count": issue.get("comment_count")}
    if not moved:
        return dict(base, status="NOOP", reasons=base["reasons"] or ["NOTHING_TO_MOVE"], proposed_description=text)
    proposed = "".join(out)
    fresh = [x for x in moved if x not in notion_text and overlap(x, notion_text) < 0.8]       # already present: point to it, never duplicate
    head = "# %s\n\n%s\n\n" % (new["title"], issue.get("notion_preface", "").strip()) if new else ""
    append = (head + "## Migrated from %s (Jira revision %s)\n\n" % (key, issue.get("updated")) + "".join(x.rstrip("\n") + "\n\n" for x in fresh)) if fresh or new else ""
    if SECRET.search(append):
        return dict(base, status="BLOCKED", reasons=["SECRET_LIKE_VALUE"])
    diff = "".join(difflib.unified_diff(text.splitlines(True), proposed.splitlines(True), "%s (current)" % key, "%s (proposed)" % key))
    extra = {"create_page": {"title": new["title"], "parent_id": new["parent_id"], "parent_revision": new.get("parent_revision")}} if new else {}
    anchor = {"after_heading": issue["notion_anchor"]} if issue.get("notion_anchor") and not new else {}
    return dict(base, status="PLANNED", proposed_description=proposed, jira_diff=diff, notion_page=dict({"id": owner["id"], "revision": page.get("last_edited")}, **anchor),
                notion_append=append, moved=[{"sha": sha(x), "text": x} for x in moved], expected={"description_sha": sha(proposed.replace(NEW_URL, "")) if new else sha(proposed)},
                **extra)


def build_plan(snapshot, config):
    issues = [plan_issue(i, snapshot.get("notion", {}), config) for i in snapshot.get("issues", [])]
    core = [{"key": p["key"], "status": p["status"], "rev": p["source_revision"], "desc": p.get("expected", {}).get("description_sha"),
             "append": sha(p.get("notion_append", "")), "page": p.get("notion_page")} for p in issues]
    return {"issues": issues, "config": {k: config.get(k) for k in ("writer", "excluded_keys", "retired_terms")},
            "digest": sha(json.dumps(core, sort_keys=True)), "counts": {s: sum(p["status"] == s for p in issues) for s in ("PLANNED", "NOOP", "EXCLUDED", "BLOCKED")}}


def validate(plan, approval, live):
    """Turn a plan and an explicit approval into ordered operations, or the reasons it must not run. Writes nothing."""
    problems, ops = [], []
    planned = {p["key"]: p for p in plan["issues"] if p["status"] == "PLANNED"}
    targets = list(approval.get("targets", []))
    if not (approval.get("approved_by") and approval.get("source")):
        problems.append("APPROVAL_MISSING_ATTRIBUTION")
    if approval.get("plan_digest") != plan["digest"]:
        problems.append("PLAN_DIGEST_MISMATCH")
    if set(targets) != set(planned) or len(targets) != len(set(targets)):
        problems.append("TARGETS_NOT_EXACT")
    cap = min(int(approval.get("max_batch", 0)), HARD_BATCH_CAP)
    if not cap or len(targets) > cap:
        problems.append("BATCH_TOO_LARGE")
    writer = plan["config"].get("writer")
    for key in targets:
        p, now = planned.get(key), live.get("issues", {}).get(key)
        if p is None or now is None:
            problems.append("TARGET_UNREADABLE:%s" % key)
            continue
        claim = live.get("writer_claims", {}).get(key)
        if claim not in (None, writer):
            problems.append("CLAIMED_BY_OTHER_WRITER:%s" % key)
        made = live.get("created_pages", {}).get(key)             # a page this plan already created: its own read-back, never a second page
        url = (made or {}).get("url", "")
        if sha((now.get("description") or "").replace(url, "") if url else now.get("description") or "") == p["expected"]["description_sha"]:
            continue                                                   # already applied: idempotent, nothing to do
        if now.get("updated") != p["source_revision"]:
            problems.append("JIRA_REVISION_CHANGED:%s" % key)
        if p.get("create_page"):
            cp = p["create_page"]
            if key not in approval.get("page_creation", []):
                problems.append("PAGE_CREATION_NOT_APPROVED:%s" % key)
            if not made and any(t == cp["title"] for t in live.get("children", {}).get(cp["parent_id"], [])):
                problems.append("EXISTING_PAGE_WITH_TITLE:%s" % key)    # a page already holds this title: link it, do not create a duplicate
            if not made:
                ops.append({"op": "notion_create_page", "parent": cp["parent_id"], "title": cp["title"], "text": p["notion_append"]})
                ops.append({"op": "notion_readback_created", "parent": cp["parent_id"], "title": cp["title"], "must_contain": p["notion_append"]})
            ops.append({"op": "jira_edit_description", "key": key, "expected_revision": p["source_revision"], "description": p["proposed_description"], "substitute": {NEW_URL: "url of the created page"}})
            ops.append({"op": "jira_readback", "key": key, "expected_description_sha": p["expected"]["description_sha"], "after_removing": "url of the created page"})
            continue
        page = live.get("notion", {}).get(p["notion_page"]["id"], {})
        anchor = p["notion_page"].get("after_heading")
        if anchor and page.get("text", "").count(anchor) != 1:
            problems.append("ANCHOR_NOT_UNIQUE:%s" % key)               # the insertion point must exist exactly once, or nothing is written
        appended = not p["notion_append"] or p["notion_append"] in page.get("text", "")        # a half-finished earlier run resumes instead of repeating
        if not appended and page.get("last_edited") != p["notion_page"]["revision"]:
            problems.append("NOTION_REVISION_CHANGED:%s" % key)
        if not appended:
            ops.append({"op": "notion_append", "page": p["notion_page"]["id"], "expected_revision": p["notion_page"]["revision"], "text": p["notion_append"],
                        **({"after_heading": p["notion_page"]["after_heading"]} if p["notion_page"].get("after_heading") else {})})
            ops.append({"op": "notion_readback", "page": p["notion_page"]["id"], "must_contain": p["notion_append"]})
        ops.append({"op": "jira_edit_description", "key": key, "expected_revision": p["source_revision"], "description": p["proposed_description"]})
        ops.append({"op": "jira_readback", "key": key, "expected_description_sha": p["expected"]["description_sha"]})
    return {"ok": not problems, "problems": problems, "operations": [] if problems else ops}


def verify(plan, after):
    """Independent read-back: the Description is exactly the proposal, nothing was lost, and key, status, links and comments are untouched."""
    results = []
    for p in (p for p in plan["issues"] if p["status"] == "PLANNED"):
        now = after["issues"].get(p["key"], {})
        made = after.get("created_pages", {}).get(p["key"], {})
        page = made if p.get("create_page") else after.get("notion", {}).get(p["notion_page"]["id"], {})
        desc, notion_text = now.get("description") or "", page.get("text", "")
        if made.get("url"):
            desc = desc.replace(made["url"], "")
        new_shas = {sha(s) for s in split_sections(desc)}
        in_notion = {m["sha"] for m in p["moved"] if m["text"].rstrip("\n") in notion_text or overlap(m["text"], notion_text) >= 0.8}
        lost = [s["id"] for s in p["sections"] if s["sha"] not in new_shas and s["sha"] not in in_notion]
        checks = {"description_exact": sha(desc) == p["expected"]["description_sha"], "page_exists": bool(page.get("text")) if p.get("create_page") else True, "nothing_lost": not lost,
                  "key_status_unchanged": (now.get("key"), now.get("status")) == (p["preserved"]["key"], p["preserved"]["status"]),
                  "links_unchanged": links_sha(now.get("links")) == p["preserved"]["links_sha"], "comments_unchanged": now.get("comment_count") == p["preserved"]["comment_count"]}
        results.append({"key": p["key"], "ok": all(checks.values()), "checks": checks, "lost": lost})
    return {"ok": bool(results) and all(r["ok"] for r in results), "results": results}


def report(plan):
    lines = ["# Dry-run migration map", "", "Digest `%s`; %s" % (plan["digest"][:12], ", ".join("%s %d" % kv for kv in plan["counts"].items())), ""]
    for p in plan["issues"]:
        lines += ["## %s — %s %s" % (p["key"], p["status"], ", ".join(p["reasons"])), ""]
        for s in p["sections"]:
            lines.append("- `%s` %s → %s (%s%s)" % (s["id"], s["heading"][:70], s["destination"], s["class"], " conflicts: " + ",".join(s["conflicts"]) if s["conflicts"] else ""))
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, args in (("plan", ("snapshot", "config")), ("report", ("plan",)), ("validate", ("plan", "approval", "live")), ("verify", ("plan", "after"))):
        s = sub.add_parser(name)
        for a in args:
            s.add_argument(a)
    ns = ap.parse_args(argv)
    def load(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    if ns.cmd == "plan":
        out = build_plan(load(ns.snapshot), load(ns.config))
    elif ns.cmd == "report":
        print(report(load(ns.plan)))
        return 0
    elif ns.cmd == "validate":
        out = validate(load(ns.plan), load(ns.approval), load(ns.live))
    else:
        out = verify(load(ns.plan), load(ns.after))
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
