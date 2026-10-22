"""LIFE-78: the lane guard decides ownership from docs/OWNERSHIP.yml, never from a promise. Synthetic repositories and pull requests only; nothing is read from GitHub or production."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lifeos import lane_guard as G

try:
    import yaml
except ImportError:                                                        # the guard itself is stdlib only
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
OWNERSHIP = (ROOT / G.OWNERSHIP).read_text()
DOC = G.validate(G.parse_ownership(OWNERSHIP))
APPROVAL = "https://jimmarkunas.atlassian.net/browse/LIFE-41?focusedCommentId=10515"
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


def change(path, base="", head="", removed=(), added=()):
    return {"path": path, "base": base, "head": head, "removed": list(removed), "added": list(added)}


def pr(lane="career-v7", handoffs=(), coordinated=()):
    return {"lane": lane, "handoffs": list(handoffs), "coordinated": set(coordinated)}


class Map(unittest.TestCase):
    def test_the_committed_map_parses_like_yaml_and_validates(self):
        if yaml is not None:
            self.assertEqual(G.parse_ownership(OWNERSHIP), yaml.safe_load(OWNERSHIP))
        self.assertEqual(DOC["lanes"], ["career-v7", "edge-oracle"])

    def test_every_tracked_file_has_an_owner(self):
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual([p for p in files if not (ROOT / p).is_symlink() and G.rule_for(DOC, p) is None], [])

    def test_the_most_specific_rule_wins(self):
        self.assertEqual(G.rule_for(DOC, "lifeos/jobs/fit/stage.py")["lane"], "edge-oracle")
        self.assertEqual(G.rule_for(DOC, "lifeos/jobs/lanes.py")["lane"], "career-v7")
        self.assertEqual(G.rule_for(DOC, "lifeos/jobs/alertstate.py")["lane"], "edge-oracle")
        self.assertIsNone(G.rule_for(DOC, "somewhere/new.py"))

    def test_malformed_maps_fail_closed(self):
        for text in ("", "  indented: 1\n", "version: 1\nlanes: [a]\nrules:\n  - path: x\n    lane: b\n", "version: 2\nlanes: [a]\nrules: []\n"):
            with self.assertRaises(G.GuardError):
                G.validate(G.parse_ownership(text))
        with self.assertRaises(G.GuardError):
            G.validate(G.parse_ownership("version: 1\nlanes: [a]\nrules:\n  - path: x\n    lane: a\n    kind: both\n"))      # a rule has a lane or a kind, not both


class Check(unittest.TestCase):
    def test_a_path_in_the_prs_own_lane_passes(self):
        bad, warn = G.check(DOC, pr(), [change("lifeos/jobs/lanes.py", "a\n", "b\n", [(1, "a")], [(1, "b")])])
        self.assertEqual((bad, warn), ([], []))

    def test_unknown_ownership_fails(self):
        bad, _ = G.check(DOC, pr(), [change("brand_new/area.py")])
        self.assertEqual(bad, ["UNKNOWN_OWNERSHIP brand_new/area.py"])

    def test_missing_or_unknown_pr_lane_fails(self):
        for lane in (None, "someone-else"):
            bad, _ = G.check(DOC, pr(lane), [change("lifeos/jobs/lanes.py")])
            self.assertTrue(bad[0].startswith("PR_LANE_MISSING_OR_UNKNOWN"))

    def test_another_lanes_path_fails_until_a_valid_handoff_names_it(self):
        files = [change("lifeos/jobs/fit/stage.py", "a\n", "b\n", [(1, "a")], [(1, "b")])]
        self.assertTrue(G.check(DOC, pr(), files)[0][0].startswith("OUT_OF_LANE lifeos/jobs/fit/stage.py owned by edge-oracle"))
        ok = pr(handoffs=[("lifeos/jobs/fit/**", "edge-oracle", APPROVAL)])
        self.assertEqual(G.check(DOC, ok, files)[0], [])

    def test_invalid_handoffs_do_not_clear_anything(self):
        files = [change("lifeos/jobs/fit/stage.py", "a\n", "b\n", [(1, "a")], [(1, "b")])]
        for handoff in (("lifeos/jobs/fit/**", "career-v7", APPROVAL),                              # owner is the PR's own lane
                        ("lifeos/jobs/fit/**", "edge-oracle", "https://evil.example/approve"),    # host not approved
                        ("lifeos/jobs/lanes.py", "edge-oracle", APPROVAL),                          # glob does not cover the changed path
                        ("lifeos/jobs/fit/**", "nobody", APPROVAL)):
            bad, _ = G.check(DOC, pr(handoffs=[handoff]), files)
            self.assertTrue(any(b.startswith("OUT_OF_LANE") for b in bad), handoff)
            self.assertEqual(any(b.startswith("HANDOFF_INVALID") for b in bad), handoff[0] != "lifeos/jobs/lanes.py", handoff)     # a glob that misses the path is only unhelpful

    def test_a_both_path_needs_every_other_lane_named(self):
        files = [change("docs/OWNERSHIP.yml", "a\n", "b\n", [(1, "a")], [(1, "b")])]
        self.assertTrue(G.check(DOC, pr(), files)[0][0].startswith("OUT_OF_LANE docs/OWNERSHIP.yml owned by edge-oracle"))
        self.assertEqual(G.check(DOC, pr(handoffs=[("docs/OWNERSHIP.yml", "edge-oracle", APPROVAL)]), files)[0], [])

    def test_generated_append_and_unassigned_kinds(self):
        self.assertEqual(G.check(DOC, pr("edge-oracle"), [change("docs/CODEMAP.md", "a", "b", [(1, "a")], [(1, "b")])]), ([], []))
        self.assertEqual(G.check(DOC, pr(), [change("docs/DECISIONS.md", "a\n", "a\nb\n", [], [(2, "b")])])[0], [])
        self.assertEqual(G.check(DOC, pr(), [change("docs/DECISIONS.md", "a\n", "b\n", [(1, "a")], [(1, "b")])])[0], ["APPEND_ONLY_VIOLATED docs/DECISIONS.md"])
        bad, warn = G.check(DOC, pr(), [change("lifeos/bills/card.py", "a", "b", [(1, "a")], [(1, "b")])])
        self.assertEqual((bad, warn), ([], ["UNASSIGNED_PATH lifeos/bills/card.py (owner not yet decided)"]))


class SharedFileSections(unittest.TestCase):
    head = HOURLY.replace("fit-cloud-backlog", "fit-cloud-backlog --limit 5")

    def line_no(self, text, needle):
        return next(i for i, row in enumerate(text.splitlines(), 1) if needle in row)

    def test_a_career_pr_may_edit_a_career_step_but_not_a_cloud_fit_step(self):
        edit = HOURLY.replace("sync-seen", "sync-seen --live")
        n = self.line_no(edit, "sync-seen")
        ok = change(".github/workflows/hourly.yml", HOURLY, edit, [(n, "        run: python -m lifeos.run sync-seen")], [(n, "        run: python -m lifeos.run sync-seen --live")])
        self.assertEqual(G.check(DOC, pr(), [ok])[0], [])
        n = self.line_no(self.head, "fit-cloud-backlog")
        cloud = change(".github/workflows/hourly.yml", HOURLY, self.head, [(n, "          python -m lifeos.run fit-cloud-backlog")], [(n, "          python -m lifeos.run fit-cloud-backlog --limit 5")])
        self.assertTrue(G.check(DOC, pr(), [cloud])[0][0].startswith("OUT_OF_LANE .github/workflows/hourly.yml owned by edge-oracle"))

    def test_the_block_not_just_the_changed_line_decides(self):
        """A line inside the Cloud-Fit step that does not itself mention cloud fit still belongs to that step."""
        edit = HOURLY.replace("run: |\n          python -m lifeos.run fit-cloud-backlog", "run: |\n          set -e\n          python -m lifeos.run fit-cloud-backlog")
        n = self.line_no(edit, "set -e")
        change_ = change(".github/workflows/hourly.yml", HOURLY, edit, [], [(n, "          set -e")])
        self.assertEqual(G.touched(G.rule_for(DOC, ".github/workflows/hourly.yml"), HOURLY, edit, [], [(n, "          set -e")]), {"edge-oracle"})
        self.assertTrue(G.check(DOC, pr(), [change_])[0])

    def test_store_cloud_fields_belong_to_edge_oracle_and_the_rest_to_career(self):
        rule = G.rule_for(DOC, "lifeos/jobs/store.py")
        self.assertEqual(G.touched(rule, "", "cloud_status TEXT\nnotion_page_id TEXT\n", [], [(1, "cloud_status TEXT")]), {"edge-oracle"})
        self.assertEqual(G.touched(rule, "", "cloud_status TEXT\nnotion_page_id TEXT\n", [], [(2, "notion_page_id TEXT")]), {"career-v7"})

    def test_block_text_bounds(self):
        lines = HOURLY.splitlines()
        self.assertIn("Cloud-Fit shadow", G.block_text(lines, 7, 6))
        self.assertNotIn("Sync", G.block_text(lines, 7, 6))
        self.assertIsNone(G.block_text(lines, 1, 6))


class PullRequestBodyAndOverlap(unittest.TestCase):
    def test_the_body_declares_lane_handoffs_and_coordination(self):
        body = f"x\nLane: edge-oracle\nHandoff: path=lifeos/jobs/** owner=career-v7 approval={APPROVAL}\nCoordinated-with: #12\nCoordinated-with: #13\n"
        self.assertEqual(G.parse_pr(body), {"lane": "edge-oracle", "handoffs": [("lifeos/jobs/**", "career-v7", APPROVAL)], "coordinated": {12, 13}})
        self.assertEqual(G.parse_pr(None), {"lane": None, "handoffs": [], "coordinated": set()})

    def test_changed_lines_from_a_zero_context_diff(self):
        diff = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -3,2 +3 @@\n-a\n-b\n+c\n@@ -9 +8,2 @@\n+d\n+e\n"
        self.assertEqual(G.changed_lines(diff), ([(3, "a"), (4, "b")], [(3, "c"), (8, "d"), (9, "e")]))

    def other(self, number=7, lane="edge-oracle", path="lifeos/jobs/store.py", lanes=("edge-oracle",)):
        return {"number": number, "lane": lane, "files": [{"path": path, "lanes": set(lanes)}]}

    def test_a_different_lane_pr_touching_the_same_section_fails_unless_coordinated(self):
        files = [change("lifeos/jobs/store.py", "", "cloud_x\n", [], [(1, "cloud_x")])]
        mine = pr(handoffs=[("lifeos/jobs/store.py", "edge-oracle", APPROVAL)])
        bad, _ = G.check(DOC, mine, files, [self.other()])
        self.assertEqual(bad, ["OVERLAPPING_PR #7 (edge-oracle) changes lifeos/jobs/store.py: coordinate with `Coordinated-with: #7`"])
        self.assertEqual(G.check(DOC, {**mine, "coordinated": {7}}, files, [self.other()])[0], [])

    def test_same_lane_or_different_section_or_generated_files_do_not_overlap(self):
        files = [change("lifeos/jobs/store.py", "", "notion_page_id TEXT\n", [], [(1, "notion_page_id TEXT")])]
        self.assertEqual(G.check(DOC, pr(), files, [self.other()])[0], [])                                   # other PR touched the cloud section only
        self.assertEqual(G.check(DOC, pr(), files, [self.other(lane="career-v7", lanes=("career-v7",))])[0], [])
        gen = [change("docs/CODEMAP.md", "a", "b", [(1, "a")], [(1, "b")])]
        self.assertEqual(G.check(DOC, pr(), gen, [self.other(path="docs/CODEMAP.md", lanes=("edge-oracle",))])[0], [])


class OtherPullRequests(unittest.TestCase):
    def test_another_prs_patch_is_attributed_without_the_file_text(self):
        """Regression (CI, PR 1): block-scope sections have no file to read when only another PR's patch is known; they fall back to the changed line."""
        patch = "@@ -10,0 +11 @@\n+        run: python -m lifeos.run fit-cloud-backlog\n"
        pages = {"pulls?state=open&per_page=100": [{"number": 5, "body": "Lane: edge-oracle"}],
                 "pulls/5/files?per_page=100": [{"filename": ".github/workflows/hourly.yml", "patch": patch}, {"filename": "docs/CODEMAP.md", "patch": ""}]}
        with mock.patch.object(G, "_api", lambda repo, path, token: pages[path]):
            [other] = G.others_from_api(DOC, "o/r", "t", 1)
        self.assertEqual((other["number"], other["lane"]), (5, "edge-oracle"))
        self.assertEqual([(f["path"], f["lanes"]) for f in other["files"]], [(".github/workflows/hourly.yml", {"edge-oracle"}), ("docs/CODEMAP.md", {None})])

    def test_a_crash_while_reading_other_prs_fails_closed(self):
        with mock.patch.object(G, "_api", side_effect=OSError("network")):
            self.assertEqual(Entrypoint.run_main(Entrypoint(), {G.OWNERSHIP: OWNERSHIP, "lifeos/jobs/lanes.py": "a\n"}, {"lifeos/jobs/lanes.py": "b\n"}, "Lane: career-v7", real_api=True), 1)


class Entrypoint(unittest.TestCase):
    """main() against a real temporary git repository: the base's map decides, and any unreadable input fails closed."""

    def run_main(self, base_files, head_files, body, others=(), real_api=False):
        with tempfile.TemporaryDirectory() as tmp:
            git = lambda *a: subprocess.run(["git", *a], cwd=tmp, check=True, capture_output=True, text=True).stdout.strip()      # noqa: E731
            git("init", "-q")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            for path, text in base_files.items():
                Path(tmp, path).parent.mkdir(parents=True, exist_ok=True)
                Path(tmp, path).write_text(text)
            git("add", "-A")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            for path, text in head_files.items():
                Path(tmp, path).parent.mkdir(parents=True, exist_ok=True)
                Path(tmp, path).write_text(text)
            git("add", "-A")
            git("commit", "-qm", "head")
            event = Path(tmp, "event.json")
            event.write_text(json.dumps({"pull_request": {"number": 1, "body": body, "base": {"sha": base}, "head": {"sha": git("rev-parse", "HEAD")}}}))
            env = {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}
            before = os.getcwd()
            os.chdir(tmp)
            try:
                if real_api:
                    return G.main(env)
                with mock.patch.object(G, "others_from_api", lambda *a: list(others)):
                    return G.main(env)
            finally:
                os.chdir(before)

    def test_in_lane_change_passes_and_out_of_lane_change_fails(self):
        base = {G.OWNERSHIP: OWNERSHIP, "lifeos/jobs/lanes.py": "a\n", "lifeos/jobs/fit/stage.py": "a\n"}
        self.assertEqual(self.run_main(base, {"lifeos/jobs/lanes.py": "b\n"}, "Lane: career-v7"), 0)
        self.assertEqual(self.run_main(base, {"lifeos/jobs/fit/stage.py": "b\n"}, "Lane: career-v7"), 1)
        self.assertEqual(self.run_main(base, {"lifeos/jobs/lanes.py": "b\n"}, "no lane declared"), 1)

    def test_a_pr_cannot_grant_itself_ownership(self):
        base = {G.OWNERSHIP: OWNERSHIP, "lifeos/jobs/fit/stage.py": "a\n"}
        grant = OWNERSHIP.replace("  - path: lifeos/jobs/fit/**\n    lane: edge-oracle", "  - path: lifeos/jobs/fit/**\n    lane: career-v7")
        self.assertNotEqual(grant, OWNERSHIP)
        self.assertEqual(self.run_main(base, {G.OWNERSHIP: grant, "lifeos/jobs/fit/stage.py": "b\n"}, "Lane: career-v7"), 1)

    def test_unreadable_inputs_fail_closed(self):
        self.assertEqual(G.main({}), 1)
        self.assertEqual(self.run_main({"x.py": "a\n"}, {"x.py": "b\n"}, "Lane: career-v7"), 1)               # neither the base nor the head has a map
        self.assertEqual(self.run_main({"x.py": "a\n"}, {G.OWNERSHIP: "version: 1\nlanes: [a]\nrules:\n  - path: x\n    lane: b\n"}, "Lane: a"), 1)   # a bootstrap map is validated too

    def test_the_pr_that_adds_the_map_is_judged_by_it_once(self):
        head = {G.OWNERSHIP: OWNERSHIP, "lifeos/lane_guard.py": "x = 1\n", "tests/contracts/test_lane_guard.py": "y = 1\n"}
        self.assertEqual(self.run_main({"README.md": "r\n"}, head, "Lane: career-v7", ), 1)      # both-kind files still need the other lane named
        body = f"Lane: career-v7\nHandoff: path=docs/OWNERSHIP.yml owner=edge-oracle approval={APPROVAL}\nHandoff: path=lifeos/lane_guard.py owner=edge-oracle approval={APPROVAL}\nHandoff: path=tests/contracts/** owner=edge-oracle approval={APPROVAL}"
        self.assertEqual(self.run_main({"README.md": "r\n"}, head, body), 0)

    def test_an_overlapping_open_pr_fails_the_run(self):
        base = {G.OWNERSHIP: OWNERSHIP, "lifeos/jobs/store.py": "x\n"}
        other = [{"number": 9, "lane": "edge-oracle", "files": [{"path": "lifeos/jobs/store.py", "lanes": {"career-v7"}}]}]
        self.assertEqual(self.run_main(base, {"lifeos/jobs/store.py": "y\n"}, "Lane: career-v7", other), 1)
        self.assertEqual(self.run_main(base, {"lifeos/jobs/store.py": "y\n"}, "Lane: career-v7\nCoordinated-with: #9", other), 0)


if __name__ == "__main__":
    unittest.main()
