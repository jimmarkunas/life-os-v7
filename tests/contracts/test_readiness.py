"""LIFE-225 (ARCH-04), simplified by delivery flow v3: the readiness validator (three refusals, everything else a warning), proven through its real entrypoint (`python -m lifeos.sources.devtools.readiness`, the command AGENTS.md tells every coding agent to run) and, for the repo check, on the real tree in CI.
Offline: temp-file fixtures only, invented keys and names."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ISSUE = {"key": "LIFE-901", "owner": "Example Owner", "labels": ["lane-career-v7", "example-label"], "origin": {"los": "LOS-901", "approved": True},
         "host": "isolated cloud worktree", "source": "docs/life-os-development-policy.md", "blockers": [{"key": "LIFE-900", "status": "Done"}],
         "authorization": {"approved_by": "Example Owner", "provenance": "issue description"}, "permissions": ["repo:write"],
         "acceptance": [{"given": "a ready issue", "when": "the validator runs", "then": "it reports ready"}]}


def entry(*args):
    """Run the real entrypoint in a fresh process from an unrelated cwd, exactly as an agent or CI does."""
    done = subprocess.run([sys.executable, "-m", "lifeos.sources.devtools.readiness", *map(str, args)], cwd=ROOT, capture_output=True, text=True)
    return done.returncode, done.stdout, done.stderr


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, name, data):
        path = self.dir / name
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def issue(self, mutate):
        data = copy.deepcopy(ISSUE)
        mutate(data)
        return entry("issue", self.write("issue.json", data))


class IssueEligibility(Fixture):
    def test_a_complete_issue_is_ready(self):
        self.assertEqual(entry("issue", self.write("i.json", ISSUE)), (0, "ready v1\n", ""))

    def test_only_the_three_checks_refuse(self):
        cases = {
            "key_invalid": lambda d: d.update(key="nonsense"),
            "blocker_not_done": lambda d: d.update(blockers=[{"key": "LIFE-900", "status": "To Do"}]),
            "gwt_missing": lambda d: d.update(acceptance=[]),
            "field_absent": lambda d: d.pop("acceptance"),
            "blockers_unreadable": lambda d: d.update(blockers="none"),
        }
        for code, mutate in cases.items():
            with self.subTest(code=code):
                status, out, err = self.issue(mutate)
                self.assertEqual((status, out), (2, ""))
                self.assertIn(f"refused v1 {code} ", err)

    def test_every_other_gap_is_a_warning_and_still_ready(self):
        ok = {"what": "Bitwarden project slot", "proof": "plan shows a free slot", "status": "done"}
        cases = {
            "origin_missing": lambda d: d.update(origin={}),
            "origin_unapproved": lambda d: d.update(origin={"los": "LOS-901", "approved": False}),
            "owner_missing": lambda d: d.update(owner=" "),
            "lane_missing": lambda d: d.update(labels=["example-label"]),
            "lane_unknown": lambda d: d.update(labels=["lane-unknown"]),
            "component_missing": lambda d: d.update(components=[]),
            "host_missing": lambda d: d.update(host=""),
            "source_missing": lambda d: d.update(source=""),
            "authorization_missing": lambda d: d.update(authorization={"approved_by": "Example Owner"}),
            "authorization_denied": lambda d: d["authorization"].update(denied=True),
            "permissions_missing": lambda d: d.update(permissions=[]),
            "prerequisite_open": lambda d: d.update(needs=[{**ok, "status": "open"}]),
            "decision_open": lambda d: d.update(decisions=[{**ok, "proof": " "}]),
            "needs_unreadable": lambda d: d.update(needs="none"),
            "requirement_page_missing": lambda d: d.update(requirement={"los": "LOS-901", "pages": []}),
            "gwt_incomplete": lambda d: d.update(acceptance=[{"given": "x", "when": "y", "then": ""}]),
            "field_absent": lambda d: d.pop("authorization"),
            "description_long": lambda d: d.update(description_chars=4001),
        }
        for code, mutate in cases.items():
            with self.subTest(code=code):
                status, out, err = self.issue(mutate)
                self.assertEqual((status, out), (0, "ready v1\n"))
                self.assertIn(f"warn v1 {code} ", err)
                self.assertNotIn("refused", err)

    def test_delivery_flow_v3_acceptance_cases_a_to_c(self):
        """(a) key + one Done-when bullet + no blockers is ready; (b) an unfinished Blocks predecessor refuses; (c) no Done-when refuses."""
        base = {"key": "LIFE-902", "blockers": [], "acceptance": [{"done_when": "the cell holds the text"}]}
        self.assertEqual(entry("issue", self.write("a.json", base))[:2], (0, "ready v1\n"))
        status, out, err = entry("issue", self.write("b.json", {**base, "blockers": [{"key": "LIFE-900", "status": "In Progress"}]}))
        self.assertEqual((status, out), (2, ""))
        self.assertIn("refused v1 blocker_not_done blockers LIFE-900=In Progress", err)
        status, out, err = entry("issue", self.write("c.json", {**base, "acceptance": []}))
        self.assertEqual((status, out), (2, ""))
        self.assertIn("refused v1 gwt_missing acceptance", err)

    def test_a_ticket_missing_every_old_field_prints_warn_lines_and_still_exits_0(self):
        """Delivery flow v3 (d): only the key, one Done-when item and the blockers list remain."""
        status, out, err = entry("issue", self.write("i.json", {"key": "LIFE-902", "blockers": [], "acceptance": [{"done_when": "the cell holds the text"}]}))
        self.assertEqual((status, out), (0, "ready v1\n"))
        for code in ("field_absent labels", "field_absent origin", "field_absent authorization", "field_absent permissions"):
            self.assertIn(f"warn v1 {code}", err)
        self.assertNotIn("refused", err)

    def test_owner_host_and_source_are_optional_but_checked_when_present(self):
        status, out, err = self.issue(lambda d: [d.pop(k) for k in ("owner", "host", "source")])
        self.assertEqual((status, out, err), (0, "ready v1\n", ""))

    def test_authorized_issues_without_denial_stay_ready_without_warnings(self):
        for value in (None, False):
            with self.subTest(denied=value):
                mutate = (lambda d: None) if value is None else (lambda d: d["authorization"].update(denied=False))
                self.assertEqual(self.issue(mutate), (0, "ready v1\n", ""))

    def test_maintenance_cites_its_parent_and_an_unapproved_parent_only_warns(self):
        self.assertEqual(self.issue(lambda d: d.update(origin={"parent": "LIFE-220", "approved": True})), (0, "ready v1\n", ""))
        status, _, err = self.issue(lambda d: d.update(origin={"parent": "LIFE-220", "approved": False}))
        self.assertEqual(status, 0)
        self.assertIn("warn v1 origin_unapproved", err)

    def test_unreadable_input_fails_closed(self):
        for args in (("issue", self.dir / "absent.json"), ("issue", self.write("bad.json", "{")), ("issue", self.write("list.json", "[]")), ("nonsense",), ("preflight", "a", "b"), ()):
            with self.subTest(args=args):
                self.assertEqual(entry(*args)[0], 2)


class RepositoryIndex(Fixture):
    def tree(self):
        shutil.copytree(ROOT, self.dir / "r", ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"))
        return self.dir / "r"

    def test_the_real_repository_is_ready(self):
        """This is the CI gate: a change that breaks the index, a link, an owner or a size limit fails this test on the PR head."""
        self.assertEqual(entry("repo", ROOT), (0, "ready v1\n", ""))

    def test_invalid_repository_changes_are_rejected(self):
        for name, break_it, code in (
                ("link", lambda r: (r / "docs" / "DECISIONS_INDEX.md").unlink(), "link_broken docs/DECISIONS_INDEX.md"),
                ("owner", lambda r: (r / "INDEX.md").write_text((r / "INDEX.md").read_text().replace("| Jim (change by reviewed PR; Q04) |", "|  |", 1)), "owner_or_authority_missing"),
                ("size", lambda r: (r / "docs" / "development-policy" / "06-locked-architecture.md").write_text("x" * 16000), "size_over_limit docs/development-policy/06-locked-architecture.md"),
                ("destination", lambda r: (r / "INDEX.md").write_text((r / "INDEX.md").read_text().replace("[`AGENTS.md`](AGENTS.md)", "[`AGENTS.md`](docs/SETUP.md)", 1)), "link_destination_mismatch AGENTS.md docs/SETUP.md"),
                ("stripped", lambda r: (r / "INDEX.md").write_text((r / "INDEX.md").read_text().replace(next(l for l in (r / "INDEX.md").read_text().splitlines() if l.startswith("| [`AGENTS.md`]")), "| [`AGENTS.md`](AGENTS.md) |")), "index_row_malformed INDEX.md [`AGENTS.md`]"),
                ("index", lambda r: (r / "INDEX.md").write_text("# empty\n"), "index_empty"),
                ("schema", lambda r: (r / "docs" / "jira" / "issue-schema.v1.json").write_text(json.dumps({"required": ["key"]})), "schema_fields_lost")):
            with self.subTest(name=name):
                repo = self.tree()
                break_it(repo)
                status, out, err = entry("repo", repo)
                self.assertEqual((status, out), (2, ""))
                self.assertIn(code, err)
                shutil.rmtree(repo)

    def test_the_agent_entrypoint_names_the_validator(self):
        agents = (ROOT / "AGENTS.md").read_text()
        for command in ("python -m lifeos.sources.devtools.readiness", "python -m lifeos.sources.devtools.readiness repo"):          # AGENTS.md (Jim-owned) names the real module (LIFE-631: the old-name shim is gone)
            self.assertIn(command, agents)
        self.assertNotIn("readiness preflight", agents)             # delivery flow v3: no claim file, no preflight


if __name__ == "__main__":
    unittest.main()
