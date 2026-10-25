"""LIFE-78: the lane guard decides ownership from the DEFAULT branch's docs/OWNERSHIP.yml and docs/HANDOFFS.yml, reads a pull request only as data, and fails closed on anything it cannot prove.
Synthetic pull requests and a fake GitHub API only; nothing is read from GitHub or production. CHECK CODE = lifeos/lane_guard.py and the default branch's maps; PR DATA = everything a pull request supplies."""
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from lifeos import lane_guard as G

try:
    import yaml
except ImportError:                                                        # the guard itself is stdlib only
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
DOC = G.validate(G.parse_map((ROOT / G.OWNERSHIP).read_text()))
APPROVAL = "https://jimmarkunas.atlassian.net/browse/LIFE-41?focusedCommentId=10515"
TODAY = date(2026, 10, 22)
HOURLY = """jobs:
  finish:
    steps:
      - name: Publish READY jobs
        run: python -m lifeos.run publish
      - name: Cloud-Fit shadow only (D214)
        run: |
          python -m lifeos.run fit-cloud-backlog
      - name: Sync
        run: python -m lifeos.run sync-seen
"""
CLOUD_EDIT = HOURLY.replace("fit-cloud-backlog", "fit-cloud-backlog --limit 5")
CAREER_EDIT = HOURLY.replace("sync-seen", "sync-seen --live")


def handoff(pr=7, path="lifeos/jobs/fit/**", owner="edge-oracle", expires="2026-12-31"):
    return {"pr": pr, "path": path, "owner": owner, "approval": APPROVAL, "expires": expires}


def pr(lane="career-v7", number=7):
    return {"number": number, "lane": lane}


def f(path, base="", head=""):
    return {"path": path, "base": base, "head": head}


def run(lane, files, handoffs=(), others=(), number=7):
    return G.check(DOC, list(handoffs), pr(lane, number), files, list(others), TODAY)


class Map(unittest.TestCase):
    def test_the_committed_maps_parse_like_yaml_and_validate(self):
        for name in (G.OWNERSHIP, G.HANDOFFS):
            if yaml is not None:
                self.assertEqual(G.parse_map((ROOT / name).read_text()), yaml.safe_load((ROOT / name).read_text()))
        entries = G.validate_handoffs(G.parse_map((ROOT / G.HANDOFFS).read_text()), DOC["lanes"])      # raises on any malformed entry; a live entry is Jim's merged, PR-specific, expiring handoff
        self.assertIsInstance(entries, list)
        self.assertEqual(DOC["lanes"], ["career-v7", "edge-oracle"])

    def test_every_tracked_file_has_exactly_one_answer(self):
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual([p for p in files if not (ROOT / p).is_symlink() and G.rule_for(DOC, p) is None], [])
        for text in ("version: 1\nlanes: [a]\nrules:\n  - path: x\n    lane: a\n    kind: protected\n",      # a lane AND a kind
                     "version: 1\nlanes: [a]\nrules:\n  - path: x\n",                                           # neither
                     "version: 1\nlanes: [a]\nrules:\n  - path: x\n    kind: unassigned\n"):                  # the old warn-then-pass kind no longer exists
            with self.assertRaises(G.GuardError):
                G.validate(G.parse_map(text))

    def test_jim_assigned_all_formerly_unresolved_paths(self):
        self.assertEqual([r["path"] for r in DOC["rules"] if r.get("kind") == "unresolved"], [])
        for path in ("lifeos/platform/notion_client.py", "tests/contracts/test_codemap.py",
                     "tests/kit/builders.py", "docs/life-os-development-policy.md",
                     "tests/platform/test_notion.py", "tests/test_example.py",
                     "widgets/card.py", "requirements.txt", ".github/actions/setup/action.yml",
                     "lifeos/bills/card.py"):
            self.assertEqual(G.rule_for(DOC, path)["lane"], "career-v7", path)
        self.assertEqual(sorted(r["path"] for r in DOC["rules"] if r.get("kind") == "protected"),
                         sorted([".github/workflows/lane-guard.yml", ".github/workflows/pr-tests.yml",
                                 "docs/HANDOFFS.yml", "docs/OWNERSHIP.yml", "lifeos/lane_guard.py",
                                 "tests/contracts/test_lane_guard.py"]))

    def test_named_boundaries_have_one_owner_or_are_blocked(self):
        for path in ("lifeos/jobs/ledger.py", "lifeos/recruiters/notion.py", "lifeos/recruiters/outreach.py", "lifeos/hiring/decision.py"):          # Notion schema definitions in code
            self.assertEqual(G.rule_for(DOC, path)["lane"], "career-v7", path)
        self.assertEqual(G.rule_for(DOC, "lifeos/platform/alerts.py")["lane"], "edge-oracle")
        self.assertEqual(G.rule_for(DOC, "lifeos/platform/notion_client.py")["lane"], "career-v7")
        close = G.rule_for(DOC, "lifeos/sources/close_app.py")
        self.assertEqual(G.line_lane(close, '           "lifeos/sources/outreach_app.py", "lifeos/recruiters/__init__.py",', [], 0), "edge-oracle")      # the Close bundle list
        self.assertEqual(G.line_lane(close, "def handle(req, who, secrets):", [], 0), "career-v7")
        gate = G.rule_for(DOC, "lifeos/edge/close_gate.py")
        self.assertEqual(G.line_lane(gate, '    if policy.get("recruiters"):', [], 0), "edge-oracle")                # policy.json field boundary
        self.assertEqual(G.line_lane(gate, "def respond(out, code):", [], 0), "edge-oracle")


class Check(unittest.TestCase):
    def test_a_path_in_the_prs_own_lane_passes(self):
        self.assertEqual(run("career-v7", [f("lifeos/jobs/lanes.py", "a\n", "b\n")]), [])

    def test_only_the_named_claude_settings_path_is_shared_by_both_lanes(self):
        path = ".claude/settings.json"
        for lane in DOC["lanes"]:
            self.assertEqual(run(lane, [f(path, "{}\n", '{"permissions": {}}\n')]), [])
        with self.assertRaises(G.GuardError):
            G.validate(G.parse_map("version: 1\nlanes: [a]\nrules:\n  - path: .claude/**\n    kind: shared\n"))

    def test_shared_settings_conflict_across_other_or_undeclared_lanes(self):
        path = ".claude/settings.json"
        files = [f(path, "{}\n", '{"permissions": {}}\n')]
        for other_lane in ("edge-oracle", None, "unknown"):
            other = {"number": 8, "lane": other_lane,
                     "files": [f(path, "{}\n", '{"permissions": {"allow": []}}\n')]}
            self.assertEqual(len(run("career-v7", files, others=[other])), 1)
            self.assertIn("OVERLAPPING_PR #8", run("career-v7", files, others=[other])[0])

    def test_unknown_ownership_and_a_missing_or_unknown_lane_fail(self):
        self.assertEqual(run("career-v7", [f("brand_new/area.py", "", "x\n")]), ["UNKNOWN_OWNERSHIP brand_new/area.py"])
        for lane in (None, "someone-else"):
            self.assertTrue(run(lane, [f("lifeos/jobs/lanes.py")])[0].startswith("PR_LANE_MISSING_OR_UNKNOWN"))

    def test_explicit_unresolved_rules_still_fail_closed(self):
        unresolved = G.validate(G.parse_map("version: 1\nlanes: [career-v7, edge-oracle]\nrules:\n  - path: sample/**\n    kind: unresolved\n"))
        files = [f("sample/item.py", "a\n", "b\n")]
        for lane in DOC["lanes"]:
            self.assertEqual(G.check(unresolved, [], pr(lane), files, [], TODAY),
                             ["UNRESOLVED_OWNERSHIP sample/item.py: no owner decided; blocked until Jim decides"])

    def test_protected_paths_fail_even_with_a_handoff_so_the_guard_cannot_be_tampered_with(self):
        for path in ("lifeos/lane_guard.py", "docs/OWNERSHIP.yml", "docs/HANDOFFS.yml", ".github/workflows/lane-guard.yml", ".github/workflows/pr-tests.yml", "tests/contracts/test_lane_guard.py"):
            for lane in DOC["lanes"]:
                got = run(lane, [f(path, "a\n", "b\n")], [handoff(path=path, owner=o) for o in DOC["lanes"]])
                self.assertEqual(got, [f"PROTECTED_PATH {path}: needs Jim's protected review"], (path, lane))

    def test_another_lanes_path_needs_a_default_branch_handoff_for_this_pr_owner_and_path(self):
        files = [f("lifeos/jobs/fit/stage.py", "a\n", "b\n")]
        self.assertTrue(run("career-v7", files)[0].startswith("OUT_OF_LANE lifeos/jobs/fit/stage.py owned by edge-oracle"))
        self.assertEqual(run("career-v7", files, [handoff()]), [])
        for bad in (handoff(pr=8), handoff(owner="career-v7"), handoff(path="lifeos/jobs/lanes.py"), handoff(expires="2026-10-21")):
            self.assertTrue(run("career-v7", files, [bad])[0].startswith("OUT_OF_LANE"), bad)

    def test_a_ticket_handoff_covers_that_lanes_prs_naming_the_key_and_nothing_else(self):
        """Jim, Oct 25 2026: one entry for a multi-PR ticket instead of one handoff PR per batch."""
        ticket = {"jira": "LIFE-72", "lane": "career-v7", "owner": "edge-oracle", "path": "lifeos/jobs/fit/**", "approval": APPROVAL, "expires": "2026-12-31"}
        files = [f("lifeos/jobs/fit/stage.py", "a\n", "b\n")]
        go = lambda lane, body, h=ticket: G.check(DOC, [h], {"number": 9, "lane": lane, "body": body}, files, [], TODAY)
        self.assertEqual(go("career-v7", "Lane: career-v7\nJira: LIFE-72\n"), [])
        self.assertEqual(len(go("career-v7", "Lane: career-v7\nJira: LIFE-73\n")), 1)                     # another ticket
        self.assertEqual(len(go("career-v7", "Lane: career-v7\n<!-- Jira: LIFE-72 -->\n")), 1)          # template guidance is never a claim
        self.assertEqual(len(go("career-v7", "Lane: career-v7\nJira: LIFE-72\n", {**ticket, "expires": "2026-10-21"})), 1)
        self.assertEqual(len(G.check(DOC, [ticket], {"number": 9, "lane": "career-v7", "body": "Jira: LIFE-72"}, [f("lifeos/jobs/lanes.py", "a\n", "b\n")], [], TODAY)), 0)
        self.assertEqual(len(go("career-v7", "Lane: career-v7\nJira: LIFE-72\n", {**ticket, "path": "lifeos/jobs/fit/edge.py"})), 1)   # only its paths
        self.assertEqual(G.validate_handoffs({"version": 1, "handoffs": [ticket]}, DOC["lanes"]), [ticket])
        for broken in ({**ticket, "pr": 9}, {**ticket, "lane": "edge-oracle"}, {**ticket, "lane": "nobody"}, {**ticket, "jira": "72"}, {k: v for k, v in ticket.items() if k != "lane"}):
            with self.assertRaises(G.GuardError):
                G.validate_handoffs({"version": 1, "handoffs": [broken]}, DOC["lanes"])

    def test_handoffs_are_validated_when_read(self):
        ok = {"version": 1, "handoffs": [handoff()]}
        self.assertEqual(G.validate_handoffs(ok, DOC["lanes"]), [handoff()])
        for broken in ({**handoff(), "approval": "https://evil.example/x"}, {**handoff(), "approval": "http://github.com/x"}, {**handoff(), "owner": "nobody"}, {**handoff(), "expires": "soon"}, {**handoff(), "pr": "7"}):
            with self.assertRaises(G.GuardError):
                G.validate_handoffs({"version": 1, "handoffs": [broken]}, DOC["lanes"])

    def test_generated_and_append_kinds(self):
        self.assertEqual(run("edge-oracle", [f("docs/CODEMAP.md", "a", "b")]), [])
        self.assertEqual(run("career-v7", [f("docs/DECISIONS.md", "a\n", "a\nb\n")]), [])
        self.assertEqual(run("career-v7", [f("docs/DECISIONS.md", "a\n", "b\n")]), ["APPEND_ONLY_VIOLATED docs/DECISIONS.md"])


class SharedFileSections(unittest.TestCase):
    hourly = ".github/workflows/hourly.yml"

    def test_a_career_pr_may_edit_a_career_step_but_not_a_cloud_fit_step(self):
        self.assertEqual(run("career-v7", [f(self.hourly, HOURLY, CAREER_EDIT)]), [])
        got = run("career-v7", [f(self.hourly, HOURLY, CLOUD_EDIT)])
        self.assertTrue(got[0].startswith("OUT_OF_LANE .github/workflows/hourly.yml owned by edge-oracle"), got)
        self.assertEqual(run("career-v7", [f(self.hourly, HOURLY, CLOUD_EDIT)], [handoff(path=self.hourly)]), [])

    def test_the_block_not_just_the_changed_line_decides(self):
        edit = HOURLY.replace("run: |\n          python -m", "run: |\n          set -e\n          python -m")
        self.assertTrue(run("career-v7", [f(self.hourly, HOURLY, edit)])[0].startswith("OUT_OF_LANE"))

    def test_store_cloud_fields_belong_to_edge_oracle_and_the_rest_to_career(self):
        self.assertTrue(run("career-v7", [f("lifeos/jobs/store.py", "x\n", "x\ncloud_status TEXT\n")])[0].startswith("OUT_OF_LANE"))
        self.assertEqual(run("career-v7", [f("lifeos/jobs/store.py", "x\n", "x\nnotion_page_id TEXT\n")]), [])

    def test_close_bundle_section_is_owned_by_edge_oracle(self):
        base = 'CLOSURE = ("lifeos/hiring/close_screen.py",\n           "lifeos/sources/outreach_app.py")\nTTL = 900\n'
        head = base.replace('"lifeos/sources/outreach_app.py")', '"lifeos/sources/outreach_app.py", "lifeos/sources/new_screen.py")')
        got = run("career-v7", [f("lifeos/sources/close_app.py", base, head)])
        self.assertTrue(got[0].startswith("OUT_OF_LANE lifeos/sources/close_app.py owned by edge-oracle"), got)
        self.assertEqual(run("edge-oracle", [f("lifeos/sources/close_app.py", base, head)]), [])

    def test_ambiguous_attribution_fails_closed(self):
        doc = G.validate({"version": 1, "lanes": ["a", "b"], "rules": [{"path": "shared.txt", "lane": "a", "sections": [{"lane": "a", "match": "zebra"}, {"lane": "b", "match": "quokka"}]}]})
        self.assertEqual(G.check(doc, [], {"number": 1, "lane": "a"}, [f("shared.txt", "", "zebra and quokka\n")], [], TODAY), ["AMBIGUOUS_SECTION shared.txt"])
        self.assertEqual(G.check(doc, [], {"number": 1, "lane": "a"}, [f("shared.txt", "", "zebra alone\n")], [], TODAY), [])

    def test_a_block_that_cannot_be_read_fails_closed_and_outside_a_block_is_the_default_lane(self):
        with self.assertRaises(G.Unverifiable):
            G.block_text(["a"], 5, 6)
        lines = HOURLY.splitlines()
        self.assertIsNone(G.block_text(lines, 1, 6))
        self.assertIn("Cloud-Fit shadow", G.block_text(lines, 7, 6))
        self.assertNotIn("Sync", G.block_text(lines, 7, 6))


class OverlappingPullRequests(unittest.TestCase):
    hourly = ".github/workflows/hourly.yml"

    def other(self, lane="edge-oracle", number=9, base=HOURLY, head=CLOUD_EDIT):
        return {"number": number, "lane": lane, "files": [f(self.hourly, base, head)]}

    def test_a_different_lane_pr_in_the_same_section_fails_with_no_coordination_bypass(self):
        mine = [f(self.hourly, HOURLY, CLOUD_EDIT)]
        got = run("career-v7", mine, [handoff(path=self.hourly)], [self.other()])
        self.assertEqual(got, ["OVERLAPPING_PR #9 (edge-oracle) changes .github/workflows/hourly.yml in the same section: merge one first, then update the other"])

    def test_a_different_section_the_same_lane_or_a_generated_file_does_not_overlap(self):
        self.assertEqual(run("career-v7", [f(self.hourly, HOURLY, CAREER_EDIT)], [], [self.other()]), [])
        self.assertEqual(run("career-v7", [f(self.hourly, HOURLY, CAREER_EDIT)], [], [self.other(lane="career-v7", head=CAREER_EDIT)]), [])
        gen = {"number": 9, "lane": "edge-oracle", "files": [f("docs/CODEMAP.md", "a", "b")]}
        self.assertEqual(run("career-v7", [f("docs/CODEMAP.md", "a", "c")], [], [gen]), [])

    def test_a_pr_with_no_declared_lane_counts_as_another_lane(self):
        got = run("career-v7", [f(self.hourly, HOURLY, CAREER_EDIT)], [], [self.other(lane=None, head=CAREER_EDIT)])
        self.assertEqual(got, ["OVERLAPPING_PR #9 (no lane declared) changes .github/workflows/hourly.yml in the same section: merge one first, then update the other"])

    def test_the_prs_own_text_grants_nothing(self):
        self.assertIsNone(G.parse_lane("Handoff: path=lifeos/jobs/fit/** owner=edge-oracle approval=" + APPROVAL))
        self.assertEqual(G.parse_lane("x\nLane: edge-oracle\n"), "edge-oracle")
        self.assertIsNone(G.parse_lane("<!--\nLane: edge-oracle\n-->\n"))                               # hidden guidance is never a claim
        self.assertIsNone(G.parse_lane("x\n<!-- unclosed\nLane: edge-oracle\n"))
        self.assertEqual(G.jira_keys("Jira: LIFE-72\n<!-- unclosed\nJira: LIFE-73\n"), {"LIFE-72"})


class FakeGitHub:
    """A fake of the read-only API: pulls, their files (paged), open-PR pages, and file texts by SHA."""

    def __init__(self, prs, texts, page=100):
        self.prs, self.texts, self.page, self.calls = prs, texts, page, []

    def api(self, repo, path, token):
        self.calls.append(path)
        url = urlparse(path)
        query, parts = parse_qs(url.query), url.path.split("/")
        if parts[0] == "pulls" and len(parts) == 1:
            numbers = sorted(n for n, p in self.prs.items() if p.get("open", True))
            return [{"number": n, "body": self.prs[n].get("body", ""), "base": {"sha": self.prs[n]["base"]}, "head": {"sha": self.prs[n]["head"]}} for n in numbers][self.slice(query)]
        number = int(parts[1])
        pr = self.prs[number]
        if len(parts) == 2:
            return {"changed_files": pr.get("count", len(pr["files"])), "number": number}
        return [{"filename": a, "status": b, **({"previous_filename": c} if c else {})} for a, b, c in pr["files"]][self.slice(query)]

    def slice(self, query):
        size, page = int(query["per_page"][0]), int(query["page"][0])
        return slice((page - 1) * min(size, self.page), page * min(size, self.page))

    def text(self, repo, path, ref, token, missing_ok):
        if (path, ref) in self.texts:
            return self.texts[(path, ref)]
        if missing_ok:
            return ""
        raise G.Unverifiable("CONTENT_UNREADABLE")


class Run(unittest.TestCase):
    """main() end to end against the fake API, with the trusted maps read from the working directory."""

    def go(self, prs, texts, event=None, page=100, body="Lane: career-v7\nJira: LIFE-59\nEvidence: SYNTHETIC PASS", cwd=None, env=None):
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp, "event.json")
            event_path.write_text(json.dumps({"pull_request": {"number": 1, "body": body, "base": {"sha": "B1"}, "head": {"sha": "H1"}, **(event or {})}}))
            fake = FakeGitHub(prs, texts, page)
            before = os.getcwd()
            os.chdir(cwd or ROOT)
            try:
                with mock.patch.object(G, "PAGE", page):
                    code = G.main({"GITHUB_EVENT_PATH": str(event_path), "GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", **(env or {})}, fake.api, fake.text)
            finally:
                os.chdir(before)
        return code, fake

    def mine(self, files, **kw):
        return {1: {"base": "B1", "head": "H1", "files": files, **kw}}

    def test_in_lane_passes_and_out_of_lane_fails(self):
        texts = {("lifeos/jobs/lanes.py", "B1"): "a\n", ("lifeos/jobs/lanes.py", "H1"): "b\n", ("lifeos/jobs/fit/stage.py", "B1"): "a\n", ("lifeos/jobs/fit/stage.py", "H1"): "b\n"}
        self.assertEqual(self.go(self.mine([("lifeos/jobs/lanes.py", "modified", None)]), texts)[0], 0)
        self.assertEqual(self.go(self.mine([("lifeos/jobs/fit/stage.py", "modified", None)]), texts)[0], 1)

    def test_a_false_approval_url_in_the_pr_body_grants_nothing(self):
        texts = {("lifeos/jobs/fit/stage.py", "B1"): "a\n", ("lifeos/jobs/fit/stage.py", "H1"): "b\n"}
        body = f"Lane: career-v7\nHandoff: path=lifeos/jobs/fit/** owner=edge-oracle approval={APPROVAL}\nCoordinated-with: #9"
        self.assertEqual(self.go(self.mine([("lifeos/jobs/fit/stage.py", "modified", None)]), texts, body=body)[0], 1)

    def test_a_pr_that_edits_the_guard_the_workflow_or_the_maps_cannot_pass(self):
        changed = ("lifeos/lane_guard.py", ".github/workflows/lane-guard.yml", "docs/OWNERSHIP.yml", "docs/HANDOFFS.yml")
        texts = {(p, r): t for p in changed for r, t in (("B1", "a\n"), ("H1", "return 0\n"))}
        for path in changed:
            self.assertEqual(self.go(self.mine([(path, "modified", None)]), texts)[0], 1, path)

    def test_the_map_comes_from_the_working_directory_the_default_branch_not_the_pr(self):
        """The PR rewrites OWNERSHIP.yml to give itself fit/**; the guard still judges it by the trusted copy."""
        grant = (ROOT / G.OWNERSHIP).read_text().replace("  - path: lifeos/jobs/fit/**\n    lane: edge-oracle", "  - path: lifeos/jobs/fit/**\n    lane: career-v7")
        texts = {("lifeos/jobs/fit/stage.py", "B1"): "a\n", ("lifeos/jobs/fit/stage.py", "H1"): "b\n", (G.OWNERSHIP, "B1"): "a\n", (G.OWNERSHIP, "H1"): grant}
        self.assertEqual(self.go(self.mine([("lifeos/jobs/fit/stage.py", "modified", None), (G.OWNERSHIP, "modified", None)]), texts)[0], 1)

    def test_a_trusted_handoff_clears_exactly_its_pr_owner_and_path(self):
        texts = {("lifeos/jobs/fit/stage.py", "B1"): "a\n", ("lifeos/jobs/fit/stage.py", "H1"): "b\n"}
        files = self.mine([("lifeos/jobs/fit/stage.py", "modified", None)])
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "docs").mkdir()
            (Path(tmp) / G.OWNERSHIP).write_text((ROOT / G.OWNERSHIP).read_text())
            for pr_number, want in ((1, 0), (2, 1)):
                (Path(tmp) / G.HANDOFFS).write_text(f"version: 1\nhandoffs:\n  - pr: {pr_number}\n    path: lifeos/jobs/fit/**\n    owner: edge-oracle\n    approval: {APPROVAL}\n    expires: 2999-01-01\n")
                self.assertEqual(self.go(files, texts, cwd=tmp)[0], want, pr_number)

    def test_a_rerun_reads_the_default_branchs_current_handoffs_not_the_checkout(self):
        """Oct 25 2026: a handoff Jim merges after the PR event clears a plain re-run (no description edit). The PR's own HANDOFFS.yml still counts for nothing."""
        texts = {("lifeos/jobs/fit/stage.py", "B1"): "a\n", ("lifeos/jobs/fit/stage.py", "H1"): "b\n"}
        files = self.mine([("lifeos/jobs/fit/stage.py", "modified", None)])
        merged = f"version: 1\nhandoffs:\n  - pr: 1\n    path: lifeos/jobs/fit/**\n    owner: edge-oracle\n    approval: {APPROVAL}\n    expires: 2999-01-01\n"
        event = {"base": {"sha": "B1", "repo": {"default_branch": "main"}}}
        self.assertEqual(self.go(files, {**texts, (G.HANDOFFS, "main"): merged}, event=event)[0], 0)
        self.assertEqual(self.go(files, {**texts, (G.HANDOFFS, "H1"): merged}, event=event)[0], 1)       # unreadable current handoffs fail closed; the PR's copy is never read

    def test_a_rename_judges_both_paths(self):
        texts = {("lifeos/jobs/fit/old.py", "B1"): "a\n", ("lifeos/jobs/lanes2.py", "H1"): "a\n"}
        self.assertEqual(self.go(self.mine([("lifeos/jobs/lanes2.py", "renamed", "lifeos/jobs/fit/old.py")]), texts)[0], 1)

    def test_incomplete_evidence_fails_closed(self):
        texts = {("lifeos/jobs/lanes.py", "B1"): "a\n", ("lifeos/jobs/lanes.py", "H1"): "b\n"}
        files = [("lifeos/jobs/lanes.py", "modified", None)]
        self.assertEqual(self.go(self.mine(files), texts)[0], 0)
        self.assertEqual(self.go(self.mine(files, count=2), texts)[0], 1)                                 # the file list is shorter than the PR's own changed_files
        self.assertEqual(self.go(self.mine(files), {("lifeos/jobs/lanes.py", "H1"): "b\n"})[0], 1)         # a base text that cannot be read
        self.assertEqual(self.go(self.mine(files), texts, cwd=tempfile.gettempdir())[0], 1)                # no trusted map in the working directory
        self.assertEqual(G.main({}, None, None), 1)

    def test_open_pr_discovery_must_be_complete(self):
        texts = {("lifeos/jobs/lanes.py", "B1"): "a\n", ("lifeos/jobs/lanes.py", "H1"): "b\n"}
        prs = self.mine([("lifeos/jobs/lanes.py", "modified", None)])
        for n in range(2, 6):                                                                              # four more open PRs, none touching the same path
            prs[n] = {"base": f"B{n}", "head": f"H{n}", "files": [("README.md", "modified", None)]}
        self.assertEqual(self.go(prs, texts, page=2)[0], 0)                                                # complete across three pages
        with mock.patch.object(G, "PAGES", 2):
            self.assertEqual(self.go(prs, texts, page=2)[0], 1)                                            # the page cap is reached with a full last page: never read as "no overlap"

    def test_an_overlapping_open_pr_fails_and_its_missing_or_truncated_evidence_fails_closed(self):
        texts = {(".github/workflows/hourly.yml", "B1"): HOURLY, (".github/workflows/hourly.yml", "H1"): CLOUD_EDIT,
                 (".github/workflows/hourly.yml", "B9"): HOURLY, (".github/workflows/hourly.yml", "H9"): CLOUD_EDIT.replace("--limit 5", "--limit 9")}
        prs = self.mine([(".github/workflows/hourly.yml", "modified", None)])
        prs[9] = {"base": "B9", "head": "H9", "body": "Lane: edge-oracle", "files": [(".github/workflows/hourly.yml", "modified", None)]}
        body = "Lane: edge-oracle"
        self.assertEqual(self.go(prs, texts, body=body)[0], 0)                                             # same lane: no overlap finding
        self.assertEqual(self.go(prs, texts)[0], 1)                                                        # career-v7 touching the edge-oracle section needs a handoff AND overlaps
        prs[9]["count"] = 5                                                                                # the other PR's file list is shorter than its changed_files
        self.assertEqual(self.go(prs, texts, body=body)[0], 1)
        prs[9]["count"] = 1
        prs[8] = {"base": "B8", "head": "H8", "files": [("README.md", "modified", None)], "count": 4}         # an unrelated PR whose file list is incomplete still cannot be read as "no shared path"
        self.assertEqual(self.go(prs, texts, body=body)[0], 1)
        del prs[8]
        self.assertEqual(self.go(prs, {k: v for k, v in texts.items() if k[1] != "H9"}, body=body)[0], 1)   # the other PR's head text is missing

    def test_a_non_list_api_answer_fails_closed(self):
        with self.assertRaises(G.Unverifiable):
            G.paged("o/r", "pulls?state=open", "t", lambda *a: {"message": "rate limited"})


class Http(unittest.TestCase):
    def test_a_404_is_absence_only_where_the_caller_expects_it(self):
        error = urllib.error.HTTPError("u", 404, "nf", {}, None)
        with mock.patch.object(G, "_request", side_effect=error):
            self.assertEqual(G._text("o/r", "p", "sha", "t", True), "")
            with self.assertRaises(G.Unverifiable):
                G._text("o/r", "p", "sha", "t", False)
        with mock.patch.object(G, "_request", side_effect=urllib.error.HTTPError("u", 500, "x", {}, None)), self.assertRaises(G.Unverifiable):
            G._text("o/r", "p", "sha", "t", True)
        with mock.patch.object(G, "_request", return_value=b"\xff\xfe"), self.assertRaises(G.Unverifiable):
            G._text("o/r", "p", "sha", "t", True)



class Train(unittest.TestCase):
    """LIFE-109: while OWNERSHIP.yml names an active train for a lane, a PR of that lane must name one of its issues, change one of its product paths and state its evidence level.
    The two self-tests the issue asks for: a Platform-only change dressed as Jobs work fails, a real Jobs change passes."""
    TRAINS = G.validate_trains(DOC)
    JOBS = "Lane: career-v7\nJira: LIFE-59\nEvidence: SYNTHETIC PASS"

    def judge(self, body, paths, lane="career-v7", number=1, exemptions=()):
        return G.train_check(self.TRAINS, list(exemptions), {"number": number, "lane": lane, "body": body}, [{"path": p} for p in paths], TODAY)

    def test_the_trusted_map_names_one_jobs_train_for_career(self):
        self.assertEqual([(t["name"], t["lane"]) for t in self.TRAINS], [("jobs-e2e", "career-v7")])
        self.assertIn("LIFE-59", self.TRAINS[0]["issues"])
        self.assertNotIn("LIFE-23", self.TRAINS[0]["issues"])                           # Platform consolidation is paused (LIFE-3 comment 11087)

    def test_self_test_a_platform_only_change_presented_as_jobs_fails(self):
        got = self.judge(self.JOBS + "\nJobs OS end-to-end delivery", ["lifeos/platform/notion_client.py", "tests/platform/test_x.py", "docs/MASTER_ROADMAP.md"])
        self.assertEqual([v.split(":")[0] for v in got], ["TRAIN_NO_PRODUCT_CHANGE"])

    def test_self_test_a_real_jobs_change_passes(self):
        self.assertEqual(self.judge(self.JOBS, ["lifeos/jobs/hiring_pipeline.py", "tests/jobs/test_x.py"]), [])
        self.assertEqual(self.judge(self.JOBS.replace("SYNTHETIC PASS", "LIVE READ-BACK"), ["lifeos/interview/create.py"]), [])

    def test_a_handoffs_only_pr_needs_no_product_path_but_still_its_key_and_evidence(self):
        """Oct 25 2026 (#552): an exemption-only PR could never pass the product rule its exemption lifts; it stays a protected path."""
        self.assertEqual(self.judge(self.JOBS, [G.HANDOFFS]), [])
        self.assertEqual([v.split(":")[0] for v in self.judge("Lane: career-v7\nEvidence: CODE LANDED", [G.HANDOFFS])], ["TRAIN_JIRA_KEY_MISSING"])
        self.assertEqual([v.split(":")[0] for v in self.judge(self.JOBS, [G.HANDOFFS, "docs/MASTER_ROADMAP.md"])], ["TRAIN_NO_PRODUCT_CHANGE"])
        self.assertEqual([v.split(":")[0] for v in self.judge(self.JOBS, [G.OWNERSHIP])], ["TRAIN_NO_PRODUCT_CHANGE"])     # only the handoffs file

    def test_the_issue_must_be_one_of_the_trains(self):
        for body in ("Lane: career-v7\nEvidence: CODE LANDED", "Lane: career-v7\nJira: LIFE-23\nEvidence: CODE LANDED", "Lane: career-v7\nSee LIFE-59\nEvidence: CODE LANDED"):
            self.assertEqual([v.split(":")[0] for v in self.judge(body, ["lifeos/jobs/a.py"])], ["TRAIN_JIRA_KEY_MISSING"], body)

    def test_evidence_is_one_stated_level_and_jim_accepted_is_never_self_claimed(self):
        jobs = ["lifeos/jobs/a.py"]
        self.assertEqual([v.split(":")[0] for v in self.judge("Lane: career-v7\nJira: LIFE-59", jobs)], ["TRAIN_EVIDENCE_MISSING"])
        self.assertEqual([v.split(":")[0] for v in self.judge(self.JOBS + "\nEvidence: LIVE READ-BACK", jobs)], ["TRAIN_EVIDENCE_MISSING"])
        self.assertEqual([v.split(":")[0] for v in self.judge("Lane: career-v7\nJira: LIFE-59\nEvidence: DONE", jobs)], ["TRAIN_EVIDENCE_MISSING"])
        self.assertEqual([v.split(":")[0] for v in self.judge("Lane: career-v7\nJira: LIFE-59\nEvidence: JIM ACCEPTED", jobs)], ["TRAIN_SELF_ACCEPTANCE", "TRAIN_EVIDENCE_MISSING"])

    def test_the_pr_template_filled_in_passes_and_its_guidance_comment_is_never_a_claim(self):
        body = (ROOT / ".github" / "pull_request_template.md").read_text().replace("Jira: LIFE-\n", "Jira: LIFE-59\n")
        self.assertEqual(self.judge(body, ["lifeos/jobs/a.py"]), [])
        self.assertEqual([v.split(":")[0] for v in self.judge(body.replace("Jira: LIFE-59\n", "Jira:\nLIFE-59\n"), ["lifeos/jobs/a.py"])], ["TRAIN_JIRA_KEY_MISSING"])
        self.assertIn("TRAIN_SELF_ACCEPTANCE", [v.split(":")[0] for v in self.judge(body + "\nStatus: JIM ACCEPTED", ["lifeos/jobs/a.py"])])

    def test_another_lane_is_untouched_and_an_approved_repair_passes_only_by_its_exemption(self):
        self.assertEqual(self.judge("Lane: edge-oracle", ["lifeos/edge/close_gate.py"], lane="edge-oracle"), [])     # an edge-oracle hotfix keeps its own path
        repair = {"pr": 7, "approval": APPROVAL, "expires": "2026-12-31"}
        self.assertEqual(self.judge("Lane: career-v7", ["lifeos/platform/http.py"], number=7, exemptions=[repair]), [])
        self.assertEqual(len(self.judge("Lane: career-v7", ["lifeos/platform/http.py"], number=8, exemptions=[repair])), 3)    # it exempts exactly its PR
        self.assertEqual(len(self.judge("Lane: career-v7", ["lifeos/platform/http.py"], number=7, exemptions=[{**repair, "expires": "2026-10-01"}])), 3)

    def test_malformed_trains_or_exemptions_fail_closed(self):
        for trains in ({"x": 1}, ["x"], [{"name": "t", "lane": "nope", "issues": ["LIFE-1"], "product": ["a"]}], [{"name": "t", "lane": "career-v7", "issues": ["JFM-1"], "product": ["a"]}],
                       [{"name": "t", "lane": "career-v7", "issues": ["LIFE-1"], "product": []}],
                       [{"name": "a", "lane": "career-v7", "issues": ["LIFE-1"], "product": ["x"]}, {"name": "b", "lane": "career-v7", "issues": ["LIFE-2"], "product": ["y"]}]):
            with self.assertRaises(G.GuardError):
                G.validate_trains({**DOC, "trains": trains})
        for bad in (["x"], [{"pr": "7", "approval": APPROVAL, "expires": "2026-12-31"}], [{"pr": 7, "approval": "http://evil.example/x", "expires": "2026-12-31"}], [{"pr": 7, "approval": APPROVAL}]):
            with self.assertRaises(G.GuardError):
                G.validate_exemptions({"exemptions": bad})
        self.assertEqual(G.validate_trains({k: v for k, v in DOC.items() if k != "trains"}), [])           # no train: nothing extra is checked

    def test_main_runs_the_train_check_on_the_pr_body(self):
        texts = {("lifeos/platform/http.py", "B1"): "a\n", ("lifeos/platform/http.py", "H1"): "b\n", ("lifeos/jobs/lanes.py", "B1"): "a\n", ("lifeos/jobs/lanes.py", "H1"): "b\n"}
        run = Run()
        self.assertEqual(run.go(run.mine([("lifeos/platform/http.py", "modified", None)]), texts)[0], 1)            # Platform-only under the Jobs train: refused
        self.assertEqual(run.go(run.mine([("lifeos/jobs/lanes.py", "modified", None)]), texts)[0], 0)
        self.assertEqual(run.go(run.mine([("lifeos/jobs/lanes.py", "modified", None)]), texts, body="Lane: career-v7")[0], 1)    # no issue, no evidence

if __name__ == "__main__":
    unittest.main()
