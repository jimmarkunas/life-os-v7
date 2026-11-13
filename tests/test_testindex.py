"""LIFE-548: the test index builder, the selector and the staleness check. Pure logic over small fake trees; the real index is only read for its shape."""
import contextlib
import datetime
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from tests import testindex as ti

NOW = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)


def tree(files):
    """A temp repo root holding empty files at the given repo-relative paths."""
    root = pathlib.Path(tempfile.mkdtemp())
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    return root


def index(covers, tests=("tests.jobs.test_a.A.test_one", "tests.jobs.test_b.B.test_two", "tests.sources.test_c.C.test_three"), made=NOW, uncovered=()):
    return {"schema": 1, "generated_at": made.strftime("%Y-%m-%dT%H:%M:%SZ"), "commit": "abc", "tests": list(tests), "modules": {}, "skipped": [],
            "covers": covers, "uncovered": list(uncovered)}


FILES = ["tests/contracts/test_privacy.py", "tests/contracts/test_boundaries.py", "tests/jobs/test_a.py", "tests/jobs/test_b.py", "tests/sources/test_c.py",
         "lifeos/jobs/a.py", "lifeos/jobs/b.py", "lifeos/sources/c.py", "lifeos/run.py", "tests/test_run.py", "lifeos/newpkg/z.py"]
CONTRACTS = ["tests.contracts.test_boundaries", "tests.contracts.test_privacy"]


class Build(unittest.TestCase):
    def test_contexts_become_sorted_indexes_and_paths_are_relative(self):
        cov = {"files": {"lifeos/jobs/a.py": {"contexts": {"1": ["tests.jobs.test_b.B.test_two", ""], "2": ["tests.jobs.test_a.A.test_one"]}},
                         "lifeos/jobs/__init__.py": {"contexts": {"1": ["tests.jobs.test_a.A.test_one"]}},
                         "/abs/elsewhere.py": {"contexts": {}}}}
        built = ti.build(cov, ["tests.jobs.test_b.B.test_two", "tests.jobs.test_a.A.test_one"], commit="abc", now=NOW)
        self.assertEqual(built["tests"], ["tests.jobs.test_a.A.test_one", "tests.jobs.test_b.B.test_two"])
        self.assertEqual(built["covers"], {"jobs/a.py": [0, 1]})                         # relative to lifeos/, no package markers, no absolute paths
        self.assertEqual(built["generated_at"], "2026-11-10T12:00:00Z")

    def test_dump_is_deterministic_and_has_no_absolute_paths(self):
        cov = {"files": {"lifeos/jobs/a.py": {"contexts": {"1": ["tests.jobs.test_a.A.test_one"]}}}}
        one = ti.dump(ti.build(cov, ["tests.jobs.test_a.A.test_one"], now=NOW))
        two = ti.dump(ti.build(cov, ["tests.jobs.test_a.A.test_one"], now=NOW))
        self.assertEqual(one, two)
        self.assertNotIn("/Users/", one)
        self.assertEqual(json.loads(one)["covers"], {"jobs/a.py": [0]})


class Select(unittest.TestCase):
    def setUp(self):
        self.root = tree(FILES)
        self.idx = index({"jobs/a.py": [0], "jobs/b.py": [1], "sources/c.py": [2], "run.py": [2]})

    def pick(self, *changed):
        return ti.select(list(changed), self.idx, self.root)

    def test_a_production_file_runs_the_tests_the_index_maps_plus_the_contracts(self):
        self.assertEqual(self.pick("lifeos/jobs/a.py"), sorted(CONTRACTS + ["tests.jobs.test_a"]))

    def test_a_test_file_the_change_deleted_is_not_a_target_even_when_a_stale_index_still_maps_it(self):
        (self.root / "tests/jobs/test_b.py").unlink()                                         # the change deletes it; the index (days old) still lists its test
        picked = ti.select(["lifeos/jobs/b.py"], index({"jobs/b.py": [1]}), self.root)
        self.assertNotIn("tests.jobs.test_b", picked)
        self.assertFalse([t for t in picked if t.startswith("tests.jobs.test_b")])

    def test_a_test_inside_a_selected_module_is_not_listed_twice(self):
        picked = self.pick("lifeos/jobs/a.py")
        self.assertIn("tests.jobs.test_a", picked)
        self.assertNotIn("tests.jobs.test_a.A.test_one", picked)                          # the module already runs it

    def test_the_contracts_always_run_even_for_a_docs_only_change(self):
        self.assertEqual(self.pick("docs/INDEX.md"), CONTRACTS)
        self.assertEqual(self.pick("README.md"), CONTRACTS)

    def test_a_changed_test_file_runs_that_file(self):
        self.assertIn("tests.sources.test_c", self.pick("tests/sources/test_c.py"))

    def test_the_kit_the_harness_workflows_locks_privacy_and_the_platform_layer_run_everything(self):
        for path in ("tests/kit/db.py", "tests/shard.py", "tests/testindex.py", ".github/workflows/pr-tests.yml", "requirements-test.lock", "requirements.txt",
                     "docs/PRIVACY.md", ".claude/settings.json", "lifeos/platform/db.py", "pyproject.toml"):
            self.assertEqual(self.pick(path), ti.FULL, path)

    def test_a_new_production_file_never_silently_runs_nothing(self):
        picked = self.pick("lifeos/jobs/brand_new.py")                                    # not in the index: the whole mirror folder
        self.assertEqual(picked, sorted(CONTRACTS + ["tests.jobs.test_a", "tests.jobs.test_b"]))
        self.assertEqual(self.pick("lifeos/newpkg/z.py"), ti.FULL)                         # no mirror folder either: everything
        self.assertEqual(self.pick("lifeos/unknown_top.py"), ti.FULL)

    def test_a_top_level_module_uses_its_top_level_test_and_unknown_files_run_everything(self):
        self.assertIn("tests.test_run", self.pick("lifeos/run.py"))
        self.assertEqual(self.pick("some_new_tool.sh"), ti.FULL)
        self.assertEqual(self.pick("lifeos/jobs/data.json"), sorted(CONTRACTS + ["tests.jobs.test_a", "tests.jobs.test_b"]))
        self.assertEqual(self.pick("scripts/x/y.sh"), ti.FULL)

    def test_a_missing_or_stale_format_index_runs_everything(self):
        self.assertEqual(ti.select(["lifeos/jobs/a.py"], None, self.root), ti.FULL)

    def test_one_full_trigger_among_many_changes_wins(self):
        self.assertEqual(self.pick("lifeos/jobs/a.py", "tests/kit/db.py", "docs/x.md"), ti.FULL)

    def test_an_indexed_file_with_no_covering_test_falls_back_to_its_mirror_folder(self):
        idx = index({"jobs/a.py": []})
        self.assertEqual(ti.select(["lifeos/jobs/a.py"], idx, self.root), sorted(CONTRACTS + ["tests.jobs.test_a", "tests.jobs.test_b"]))


    def test_a_test_the_index_names_but_a_change_deleted_is_not_selected(self):
        idx = index({"jobs/a.py": [0, 3]}, tests=("tests.jobs.test_a.A.test_one", "tests.jobs.test_b.B.test_two", "tests.sources.test_c.C.test_three", "tests.jobs.test_gone.G.test_old"))
        self.assertEqual(ti.select(["lifeos/jobs/a.py"], idx, self.root), sorted(CONTRACTS + ["tests.jobs.test_a"]))


class Check(unittest.TestCase):
    def test_a_fresh_index_passes(self):
        self.assertEqual(ti.check(index({"jobs/a.py": [0], "x.py": [1]}), NOW), [])

    def test_an_old_index_is_reported_with_its_age(self):
        idx = index({"jobs/a.py": [0], "x.py": [1]}, made=NOW - datetime.timedelta(days=8))
        self.assertEqual(ti.check(idx, NOW), ["the test index is 8 days old (limit 7)"])
        self.assertEqual(ti.check(index({"jobs/a.py": [0], "x.py": [1]}, made=NOW - datetime.timedelta(days=7)), NOW), [])

    def test_production_files_added_or_deleted_since_the_build_are_not_a_problem(self):
        """LIFE-588: adding a source must not need a rebuild of the index; `select` sends an unlisted file to its mirror folder, or to FULL."""
        self.assertEqual(ti.check(index({"jobs/a.py": [0], "jobs/deleted.py": [0]}), NOW), [])

    def test_a_missing_index_is_reported(self):
        self.assertEqual(ti.check(None, NOW), ["the test index is missing or unreadable"])


class SelectCommand(unittest.TestCase):
    """LIFE-644: the index is restored from a cache in pull requests, so a missing, old, damaged or lying one must never make a test disappear from a run."""

    def run_select(self, loaded, changed=("lifeos/sources/web/boards.py",)):
        out = io.StringIO()
        with mock.patch.object(ti, "load", return_value=loaded), mock.patch.object(ti, "changed_files", return_value=list(changed)), contextlib.redirect_stdout(out):
            self.assertEqual(ti.main(["select", "--base", "HEAD^1"]), 0)
        return out.getvalue().split()

    def test_no_index_an_old_one_or_a_damaged_one_prints_full(self):
        fresh_now = datetime.datetime.now(datetime.timezone.utc)
        idx = index({"sources/web/boards.py": [0]}, tests=("tests.sources.web.test_boards.B.test_one",), made=fresh_now)
        self.assertNotEqual(self.run_select(idx), ["FULL"])                                       # control: a fresh, sound index gives a subset
        self.assertEqual(self.run_select(None), ["FULL"])                                         # no cache restored, no file, or a file load() rejected
        self.assertEqual(self.run_select({**idx, "generated_at": (fresh_now - datetime.timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")}), ["FULL"])
        self.assertEqual(self.run_select(index({"sources/web/boards.py": [99]}, tests=("tests.x.A.t",), made=fresh_now)), ["FULL"])      # a number outside the test list
        self.assertEqual(self.run_select({"schema": 1, "generated_at": idx["generated_at"]}), ["FULL"])                              # keys missing

    def test_an_index_that_lies_about_a_file_still_runs_the_tests_named_after_it(self):
        idx = index({"jobs/a.py": [2]})                                                           # says only a sources test covers jobs/a.py
        picked = ti.select(["lifeos/jobs/a.py"], idx, tree(FILES))
        self.assertIn("tests.jobs.test_a", picked)                                                # the mirror test of the module runs regardless


class LoaderAndModules(unittest.TestCase):
    def test_test_modules_are_dotted_and_sorted(self):
        root = tree(["tests/b/test_z.py", "tests/a/test_y.py", "tests/a/helper.py"])
        self.assertEqual(ti.test_modules(root), ["tests.a.test_y", "tests.b.test_z"])

    def test_load_rejects_a_wrong_schema(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "i.json"
        path.write_text(json.dumps({"schema": 99}))
        self.assertIsNone(ti.load(path))
        self.assertIsNone(ti.load(path.with_name("missing.json")))


if __name__ == "__main__":
    unittest.main()
