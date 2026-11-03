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
SHA_B = "b" * 40

ISSUE = {"key": "LIFE-901", "owner": "Example Owner", "labels": ["lane-career-v7", "example-label"], "origin": {"los": "LOS-901", "approved": True},
         "host": "isolated cloud worktree", "source": "docs/life-os-development-policy.md", "blockers": [{"key": "LIFE-900", "status": "Done"}],
         "authorization": {"approved_by": "Example Owner", "provenance": "issue description"}, "permissions": ["repo:write"],
         "acceptance": [{"given": "a ready issue", "when": "the validator runs", "then": "it reports ready"}]}
CLAIM = {"writer": "agent-one", "issue": "LIFE-901", "surfaces": ["lifeos/readiness.py"]}


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
    """main is fetched by the validator itself from a real (temporary) git origin; the state file only carries the writer-claim record."""

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd or self.clone, check=True, capture_output=True, text=True).stdout.strip()

    def setUp(self):
        super().setUp()
        origin, self.clone = self.dir / "origin.git", self.dir / "clone"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], check=True)
        subprocess.run(["git", "clone", "-q", origin, self.clone], check=True, capture_output=True)
        (self.clone / "f").write_text("1")
        self.git("add", "f"), self.git("commit", "-q", "-m", "one"), self.git("push", "-q", "origin", "HEAD:main")
        self.base = self.git("rev-parse", "HEAD")
        self.claim = dict(CLAIM, base_sha=self.base)
        self.mine = {"writer": "agent-one", "issue": "LIFE-901", "surfaces": ["lifeos/readiness.py", "AGENTS.md"]}

    def run_preflight(self, claim=None, claims=None, pre=False, repo=None, state=None):
        args = ["preflight"] + (["--pre"] if pre else []) + [self.write("claim.json", self.claim if claim is None else claim),
                                                             self.write("state.json", {"claims": [self.mine] if claims is None else claims} if state is None else state), repo or self.clone]
        return entry(*args)

    def test_valid_recorded_claim_with_unchanged_baseline_proceeds(self):
        self.assertEqual(self.run_preflight(), (0, "ready v1\n", ""))
        other = {"writer": "agent-two", "issue": "LIFE-902", "surfaces": ["docs/other.md"]}
        self.assertEqual(self.run_preflight(claims=[self.mine, other])[0], 0)         # a different writer on a different issue and surface does not conflict

    def test_no_recorded_claim_blocks_mutation_but_not_the_pre_claim_check(self):
        for claims in ([], [dict(self.mine, writer="agent-two")], [dict(self.mine, issue="LIFE-902")], [dict(self.mine, surfaces=["docs/other.md"])]):
            with self.subTest(claims=claims):
                status, out, err = self.run_preflight(claims=claims)
                self.assertEqual((status, out), (2, ""))
                self.assertTrue("claim_not_recorded" in err or "writer_conflict" in err, err)
        self.assertEqual(self.run_preflight(claims=[], pre=True)[0], 0)                # may I record this claim? yes, nobody holds it
        self.assertEqual(self.run_preflight(claims=[dict(self.mine, writer="agent-two")], pre=True)[0], 2)

    def test_a_different_writer_on_the_same_issue_or_surface_is_refused_before_mutation(self):
        for held in ({"writer": "agent-two", "issue": "LIFE-901", "surfaces": []},
                     {"writer": "agent-two", "issue": "LIFE-902", "surfaces": ["lifeos/readiness.py"]},
                     {"writer": "agent-two", "issue": "LIFE-902", "surfaces": [], "exclusive": True}):
            for pre in (False, True):
                with self.subTest(held=held, pre=pre):
                    status, out, err = self.run_preflight(claims=[self.mine, held], pre=pre)
                    self.assertEqual((status, out), (2, ""))
                    self.assertIn("refused v1 writer_conflict claims agent-two holds", err)

    def test_a_changed_baseline_is_refused(self):
        (self.clone / "f").write_text("2")
        self.git("add", "f"), self.git("commit", "-q", "-m", "two"), self.git("push", "-q", "origin", "HEAD:main")
        status, out, err = self.run_preflight()
        self.assertEqual((status, out), (2, ""))
        self.assertIn(f"baseline_changed base_sha claimed {self.base[:7]}, main {self.git('rev-parse', 'HEAD')[:7]}", err)

    def test_main_is_fetched_live_never_asserted_by_the_caller(self):
        self.assertEqual(self.run_preflight(state={"claims": [self.mine], "main_sha": SHA_B, "main_verified": True})[0], 0)   # asserted fields are ignored
        (self.clone / "f").write_text("2")
        self.git("add", "f"), self.git("commit", "-q", "-m", "two"), self.git("push", "-q", "origin", "HEAD:main")
        stale = self.run_preflight(state={"claims": [self.mine], "main_sha": self.base, "main_verified": True})   # a caller claiming the old main is verified still fails
        self.assertEqual(stale[0], 2)
        self.assertIn("baseline_changed", stale[2])

    def test_an_unreachable_or_unfetchable_main_is_refused_as_unverified(self):
        for repo in (self.dir / "absent", self.write("notrepo.txt", "x")):
            with self.subTest(repo=repo):
                status, _, err = self.run_preflight(repo=repo)
                self.assertEqual(status, 2)
                self.assertIn("baseline_unverified", err)
        self.git("remote", "set-url", "origin", str(self.dir / "gone.git"))
        self.assertIn("baseline_unverified", self.run_preflight()[2])

    def test_an_unread_claim_record_or_missing_claim_is_refused(self):
        self.assertIn("claims_unverified", self.run_preflight(state={})[2])
        self.assertIn("claim_missing", self.run_preflight(claim={"issue": "LIFE-901"})[2])
        self.assertEqual(entry("preflight", self.write("c.json", self.claim))[0], 2)  # no state file at all


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
        for command in ("python -m lifeos.readiness issue", "python -m lifeos.readiness preflight --pre", "python -m lifeos.readiness preflight CLAIM", "python -m lifeos.readiness repo"):
            self.assertIn(command, agents)
        self.assertIn("procedural", agents)                      # the ChatGPT connector preflight is not claimed as code-enforced (Q30)


if __name__ == "__main__":
    unittest.main()
