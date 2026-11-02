"""LIFE-225 (ARCH-04): the readiness validator, proven through its real entrypoint (`python -m lifeos.readiness`, the command AGENTS.md tells every coding agent to run) and, for the repo check, on the real tree in CI.
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
SHA_A, SHA_B = "a" * 40, "b" * 40

ISSUE = {"key": "LIFE-901", "owner": "Example Owner", "labels": ["lane-career-v7", "example-label"], "origin": {"los": "LOS-901", "approved": True},
         "host": "isolated cloud worktree", "source": "docs/life-os-development-policy.md", "blockers": [{"key": "LIFE-900", "status": "Done"}],
         "authorization": {"approved_by": "Example Owner", "provenance": "issue description"}, "permissions": ["repo:write"],
         "acceptance": [{"given": "a ready issue", "when": "the validator runs", "then": "it reports ready"}]}
CLAIM = {"writer": "agent-one", "issue": "LIFE-901", "base_sha": SHA_A, "surfaces": ["lifeos/readiness.py"]}
STATE = {"main_sha": SHA_A, "main_verified": True, "claims": []}


def entry(*args):
    """Run the real entrypoint in a fresh process from an unrelated cwd, exactly as an agent or CI does."""
    done = subprocess.run([sys.executable, "-m", "lifeos.readiness", *map(str, args)], cwd=ROOT, capture_output=True, text=True)
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

    def test_each_gap_is_refused_precisely(self):
        cases = {
            "origin_missing": lambda d: d.update(origin={}),
            "origin_unapproved": lambda d: d.update(origin={"los": "LOS-901", "approved": False}),
            "owner_missing": lambda d: d.update(owner=" "),
            "lane_missing": lambda d: d.update(labels=["example-label"]),
            "lane_unknown": lambda d: d.update(labels=["lane-unknown"]),
            "host_missing": lambda d: d.update(host=""),
            "source_missing": lambda d: d.update(source=""),
            "blocker_not_done": lambda d: d.update(blockers=[{"key": "LIFE-900", "status": "To Do"}]),
            "authorization_missing": lambda d: d.update(authorization={"approved_by": "Example Owner"}),
            "permissions_missing": lambda d: d.update(permissions=[]),
            "gwt_missing": lambda d: d.update(acceptance=[]),
            "gwt_incomplete": lambda d: d.update(acceptance=[{"given": "x", "when": "y", "then": ""}]),
            "field_absent": lambda d: d.pop("host"),
        }
        for code, mutate in cases.items():
            with self.subTest(code=code):
                status, out, err = self.issue(mutate)
                self.assertEqual((status, out), (2, ""))
                self.assertIn(f"refused v1 {code} ", err)

    def test_maintenance_cites_its_approved_parent_instead_of_a_los_origin(self):
        self.assertEqual(self.issue(lambda d: d.update(origin={"parent": "LIFE-220", "approved": True}))[0], 0)
        self.assertEqual(self.issue(lambda d: d.update(origin={"parent": "LIFE-220", "approved": False}))[0], 2)

    def test_unreadable_input_fails_closed(self):
        for args in (("issue", self.dir / "absent.json"), ("issue", self.write("bad.json", "{")), ("issue", self.write("list.json", "[]")), ("nonsense",), ()):
            with self.subTest(args=args):
                self.assertEqual(entry(*args)[0], 2)


class WriterPreflight(Fixture):
    def run_preflight(self, claim=None, state=None):
        return entry("preflight", self.write("claim.json", CLAIM if claim is None else claim), self.write("state.json", STATE if state is None else state))

    def test_valid_claim_with_unchanged_baseline_proceeds(self):
        self.assertEqual(self.run_preflight(), (0, "ready v1\n", ""))
        other = dict(STATE, claims=[{"writer": "agent-two", "issue": "LIFE-902", "surfaces": ["docs/other.md"]}])
        self.assertEqual(self.run_preflight(state=other)[0], 0)                      # a different writer on a different issue and surface does not conflict
        self.assertEqual(self.run_preflight(state=dict(STATE, claims=[dict(CLAIM)]))[0], 0)    # my own claim is not a conflict

    def test_a_different_writer_on_the_same_issue_or_surface_is_refused_before_mutation(self):
        for held in ({"writer": "agent-two", "issue": "LIFE-901", "surfaces": []},
                     {"writer": "agent-two", "issue": "LIFE-902", "surfaces": ["lifeos/readiness.py"]},
                     {"writer": "agent-two", "issue": "LIFE-902", "surfaces": [], "exclusive": True}):
            with self.subTest(held=held):
                status, out, err = self.run_preflight(state=dict(STATE, claims=[held]))
                self.assertEqual((status, out), (2, ""))
                self.assertIn("refused v1 writer_conflict claims agent-two holds", err)

    def test_a_changed_or_unverified_baseline_is_refused(self):
        self.assertIn("baseline_changed base_sha claimed aaaaaaa, main bbbbbbb", self.run_preflight(state=dict(STATE, main_sha=SHA_B))[2])
        for state in (dict(STATE, main_verified=False), {k: v for k, v in STATE.items() if k != "main_sha"}, dict(STATE, main_sha="main")):
            with self.subTest(state=state):
                status, _, err = self.run_preflight(state=state)
                self.assertEqual(status, 2)
                self.assertIn("baseline_unverified", err)

    def test_an_unread_claim_record_or_missing_claim_is_refused(self):
        self.assertIn("claims_unverified", self.run_preflight(state={k: v for k, v in STATE.items() if k != "claims"})[2])
        self.assertIn("claim_missing", self.run_preflight(claim={"issue": "LIFE-901"})[2])
        self.assertEqual(entry("preflight", self.write("c.json", CLAIM))[0], 2)      # no state file at all


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
        for command in ("python -m lifeos.readiness issue", "python -m lifeos.readiness preflight", "python -m lifeos.readiness repo"):
            self.assertIn(command, agents)
        self.assertIn("procedural", agents)                      # the ChatGPT connector preflight is not claimed as code-enforced (Q30)


if __name__ == "__main__":
    unittest.main()
