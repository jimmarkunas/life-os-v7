"""the test index builder, the selector and the staleness check."""

import contextlib
import datetime
import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import testindex as ti
from tests.kit import clock

NOW = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)


def tree(files):
    """A temp repo root holding empty files at the given repo-relative paths."""
    root = pathlib.Path(tempfile.mkdtemp())
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    return root


def index(
    covers,
    tests=("tests.jobs.test_a.A.test_one", "tests.jobs.test_b.B.test_two", "tests.sources.test_c.C.test_three"),
    made=NOW,
    uncovered=(),
):
    return {
        "schema": 1,
        "generated_at": made.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": "abc",
        "tests": list(tests),
        "modules": {},
        "skipped": [],
        "covers": covers,
        "uncovered": list(uncovered),
    }


FILES = [
    "tests/contracts/test_privacy.py",
    "tests/contracts/test_boundaries.py",
    "tests/jobs/test_a.py",
    "tests/jobs/test_b.py",
    "tests/sources/test_c.py",
    "lifeos/jobs/a.py",
    "lifeos/jobs/b.py",
    "lifeos/sources/c.py",
    "lifeos/run.py",
    "tests/test_run.py",
    "lifeos/newpkg/z.py",
]
CONTRACTS = ["tests.contracts.test_boundaries", "tests.contracts.test_privacy"]


class Build(unittest.TestCase):
    def test_contexts_become_sorted_indexes_and_paths_are_relative(self):
        cov = {
            "files": {
                "lifeos/jobs/a.py": {
                    "contexts": {"1": ["tests.jobs.test_b.B.test_two", ""], "2": ["tests.jobs.test_a.A.test_one"]}
                },
                "lifeos/jobs/__init__.py": {"contexts": {"1": ["tests.jobs.test_a.A.test_one"]}},
                "/abs/elsewhere.py": {"contexts": {}},
            }
        }
        built = ti.build(cov, ["tests.jobs.test_b.B.test_two", "tests.jobs.test_a.A.test_one"], commit="abc", now=NOW)
        self.assertEqual(built["tests"], ["tests.jobs.test_a.A.test_one", "tests.jobs.test_b.B.test_two"])
        self.assertEqual(built["covers"], {"jobs/a.py": [0, 1]})
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
        (self.root / "tests/jobs/test_b.py").unlink()  # the change deletes it
        picked = ti.select(["lifeos/jobs/b.py"], index({"jobs/b.py": [1]}), self.root)
        self.assertNotIn("tests.jobs.test_b", picked)
        self.assertFalse([t for t in picked if t.startswith("tests.jobs.test_b")])

    def test_a_test_id_whose_method_was_renamed_runs_as_its_module_and_a_live_one_stays_precise(self):
        (self.root / "tests/jobs/test_b.py").write_text(
            "class B:\n    def test_renamed(self):\n        pass\n"
        )  # the change renamed test_two
        (self.root / "tests/sources/test_c.py").write_text("class C:\n    def test_three(self):\n        pass\n")
        picked = ti.select(["lifeos/jobs/b.py", "lifeos/sources/c.py"], self.idx, self.root)
        self.assertIn("tests.jobs.test_b", picked)
        self.assertFalse([t for t in picked if t.startswith("tests.jobs.test_b.")])
        known = ti.module_set(self.root)
        self.assertEqual(
            ti._current("tests.sources.test_c.C.test_three", known, self.root), "tests.sources.test_c.C.test_three"
        )  # an id whose method still exists stays precise
        self.assertEqual(
            ti._current("tests.jobs.test_b.B.test_two", known, self.root), "tests.jobs.test_b"
        )  # one that was renamed becomes its module

    def test_a_test_inside_a_selected_module_is_not_listed_twice(self):
        picked = self.pick("lifeos/jobs/a.py")
        self.assertIn("tests.jobs.test_a", picked)
        self.assertNotIn("tests.jobs.test_a.A.test_one", picked)  # the module already runs it

    def test_the_contracts_always_run_even_for_a_docs_only_change(self):
        self.assertEqual(self.pick("docs/INDEX.md"), CONTRACTS)
        self.assertEqual(self.pick("README.md"), CONTRACTS)

    def test_a_changed_test_file_runs_that_file(self):
        self.assertIn("tests.sources.test_c", self.pick("tests/sources/test_c.py"))

    def test_the_kit_the_harness_workflows_locks_privacy_and_the_platform_layer_run_everything(self):
        for path in (
            "tests/kit/db.py",
            "tests/shard.py",
            "tests/testindex.py",
            ".github/workflows/pr-tests.yml",
            "requirements-test.lock",
            "requirements.txt",
            "docs/PRIVACY.md",
            ".claude/settings.json",
            "lifeos/platform/db.py",
            "pyproject.toml",
        ):
            self.assertEqual(self.pick(path), ti.FULL, path)

    def test_a_new_production_file_never_silently_runs_nothing(self):
        picked = self.pick("lifeos/jobs/brand_new.py")  # not in the index: the whole mirror folder
        self.assertEqual(picked, sorted(CONTRACTS + ["tests.jobs.test_a", "tests.jobs.test_b"]))
        self.assertEqual(self.pick("lifeos/newpkg/z.py"), ti.FULL)  # no mirror folder either: everything
        self.assertEqual(self.pick("lifeos/unknown_top.py"), ti.FULL)

    def test_a_change_to_any_file_also_runs_the_repo_wide_scanners(self):
        root = tree(FILES + ["tests/platform/test_docstyle.py"])
        self.assertIn("tests.platform.test_docstyle", ti.select(["lifeos/jobs/brand_new.py"], self.idx, root))
        self.assertIn("tests.platform.test_docstyle", ti.select(["lifeos/jobs/a.py"], self.idx, root))
        self.assertIn(
            "tests.platform.test_docstyle", ti.select(["docs/x.md"], self.idx, root)
        )  # the scanners always run

    def test_a_top_level_module_uses_its_top_level_test_and_unknown_files_run_everything(self):
        self.assertIn("tests.test_run", self.pick("lifeos/run.py"))
        self.assertEqual(self.pick("some_new_tool.sh"), ti.FULL)
        self.assertEqual(
            self.pick("lifeos/jobs/data.json"), sorted(CONTRACTS + ["tests.jobs.test_a", "tests.jobs.test_b"])
        )
        self.assertEqual(self.pick("scripts/x/y.sh"), ti.FULL)

    def test_a_missing_or_stale_format_index_runs_everything(self):
        self.assertEqual(ti.select(["lifeos/jobs/a.py"], None, self.root), ti.FULL)

    def test_one_full_trigger_among_many_changes_wins(self):
        self.assertEqual(self.pick("lifeos/jobs/a.py", "tests/kit/db.py", "docs/x.md"), ti.FULL)

    def test_an_indexed_file_with_no_covering_test_falls_back_to_its_mirror_folder(self):
        idx = index({"jobs/a.py": []})
        self.assertEqual(
            ti.select(["lifeos/jobs/a.py"], idx, self.root),
            sorted(CONTRACTS + ["tests.jobs.test_a", "tests.jobs.test_b"]),
        )


TEN_FILES = FILES + [
    "tests/platform/test_docstyle.py",
    "tests/platform/test_tool_one_name_long.py",
    "tools/tool_one_name_long.py",
    "docs/guide.md",
    "docs/data.json",
    "lifeos/jobs/data.json",
    "tests/fixtures/sample.txt",
    "lifeos/platform/db.py",
    "tests/platform/test_db.py",
]
ROW_TOOL = "tests.platform.test_tool_one_name_long"


def reach(modules):
    """The local gate's import-graph lookup, faked: a change to lifeos.jobs.a reaches the sources test."""
    return {"tests.sources.test_c"} if "lifeos.jobs.a" in modules else set()


class TenChanges(unittest.TestCase):
    """The same ten representative changes through both modes of the one selector (PR CI with an index, the local gate
    with the import graph). Each must pick the expected tests or everything; an extra test is fine, a missed one is not.
    """

    def setUp(self):
        self.root = tree(TEN_FILES)
        (self.root / "tests/platform/test_tool_one_name_long.py").write_text("import tool_one_name_long\n")
        self.idx = index({"jobs/a.py": [0], "jobs/b.py": [1], "sources/c.py": [2], "run.py": [2]})
        ti.test_texts.cache_clear()

    def both(self, *changed):
        return ti.select(list(changed), self.idx, self.root), ti.select(list(changed), None, self.root, reach)

    def need(self, wanted, *changed):
        for picked in self.both(*changed):
            self.assertNotEqual(picked, ti.FULL, changed)
            self.assertLessEqual(set(wanted), set(picked), changed)

    def test_1_a_lifeos_file_runs_the_tests_of_its_module(self):
        self.need(CONTRACTS + ["tests.jobs.test_a"], "lifeos/jobs/a.py")

    def test_2_a_test_only_change_runs_that_test(self):
        self.need(CONTRACTS + ["tests.sources.test_c"], "tests/sources/test_c.py")

    def test_3_a_workflow_file_runs_everything(self):
        self.assertEqual(self.both(".github/workflows/pr-tests.yml"), (ti.FULL, ti.FULL))

    def test_4_a_doc_runs_the_contracts_and_the_scanners_only(self):
        for picked in self.both("docs/guide.md"):
            self.assertEqual(picked, sorted(CONTRACTS + ["tests.platform.test_docstyle"]))

    def test_5_a_new_file_runs_its_mirror_folder_or_everything(self):
        self.need(CONTRACTS + ["tests.jobs.test_a", "tests.jobs.test_b"], "lifeos/jobs/brand_new.py")
        self.assertEqual(self.both("lifeos/newpkg/fresh.py"), (ti.FULL, ti.FULL))  # no tests mirror it: unsure

    def test_6_a_rename_counts_the_old_path_and_the_new_path(self):
        self.need(
            CONTRACTS + ["tests.jobs.test_a", "tests.jobs.test_b"], "lifeos/jobs/a.py", "lifeos/jobs/renamed_a.py"
        )

    def test_7_a_deleted_file_still_runs_the_tests_that_covered_it(self):
        (self.root / "lifeos/jobs/b.py").unlink()
        self.need(CONTRACTS + ["tests.jobs.test_b"], "lifeos/jobs/b.py")

    def test_8_a_config_file_runs_everything(self):
        for path in ("pyproject.toml", "requirements-test.lock", "some_new_tool.sh", "lifeos/platform/db.py"):
            self.assertEqual(ti.select([path], self.idx, self.root), ti.FULL, path)
        self.assertEqual(ti.select(["pyproject.toml"], None, self.root, reach), ti.FULL)

    def test_9_an_unknown_file_type_runs_everything(self):
        for path in ("docs/blob.xyz", "tools/blob.bin", "scripts/run.sh", "tests/fixtures/orphan.dat"):
            self.assertEqual(self.both(path), (ti.FULL, ti.FULL), path)

    def test_10_an_empty_diff_runs_the_contracts_and_the_scanners(self):
        for picked in self.both():
            self.assertEqual(picked, sorted(CONTRACTS + ["tests.platform.test_docstyle"]))

    def test_a_tool_runs_the_test_that_names_it_and_a_fixture_the_test_that_reads_it(self):
        self.need(CONTRACTS + [ROW_TOOL], "tools/tool_one_name_long.py")
        (self.root / "tests/jobs/test_a.py").write_text("PATH = 'tests/fixtures/sample.txt'\n")
        ti.test_texts.cache_clear()
        self.need(["tests.jobs.test_a"], "tests/fixtures/sample.txt")

    def test_the_local_gate_follows_the_import_graph_where_the_index_follows_coverage(self):
        self.assertIn("tests.sources.test_c", ti.select(["lifeos/jobs/a.py"], None, self.root, reach))
        self.assertNotIn("tests.sources.test_c", ti.select(["lifeos/jobs/b.py"], None, self.root, reach))

    def test_the_local_gate_does_not_treat_the_platform_layer_as_everything(self):
        self.assertIn("tests.platform.test_db", ti.select(["lifeos/platform/db.py"], None, self.root, reach))

    def test_docs_of_any_kind_never_force_a_full_run(self):
        """the production contract, the archive, any other docs file and a top-level markdown file run the contracts, the
        repo-wide scanners and the tests that name the file; never everything (docs/PRIVACY.md is the one deliberate
        exception)."""
        (self.root / "tests/platform/test_tool_one_name_long.py").write_text(
            "NAMED = 'docs/archive/decisions-old.md'\n"
        )
        ti.test_texts.cache_clear()
        for path in (
            "docs/production-contract/01-overrides.md",
            "docs/archive/decisions-old.md",
            "docs/guide.md",
            "docs/data.json",
            "README.md",
        ):
            for picked in (ti.select([path], self.idx, self.root), ti.select([path], None, self.root, reach)):
                self.assertNotEqual(picked, ti.FULL, path)
                self.assertLessEqual(set(CONTRACTS), set(picked), path)
        picked = ti.select(["docs/archive/decisions-old.md"], self.idx, self.root)
        self.assertIn(ROW_TOOL, picked)  # the test that names the file runs
        self.assertEqual(ti.select(["docs/PRIVACY.md"], self.idx, self.root), ti.FULL)

    def test_deploy_and_hermes_paths_run_the_contracts_and_named_tests_while_the_repo_wide_paths_stay_full(self):
        for path in (
            "deploy/buzz-mcp/release-manifest.txt",
            "hermes/connector-allowlists.txt",
            "hermes/plugins/p/__init__.py",
        ):
            for picked in (ti.select([path], self.idx, self.root), ti.select([path], None, self.root, reach)):
                self.assertNotEqual(picked, ti.FULL, path)
                self.assertLessEqual(set(CONTRACTS), set(picked), path)
        for path in (
            ".github/workflows/pr-tests.yml",
            "requirements-test.lock",
            "tests/kit/db.py",
            "tests/shard.py",
            "pyproject.toml",
        ):
            self.assertEqual(ti.select([path], self.idx, self.root), ti.FULL, path)

    def test_a_full_names_the_path_and_the_rule_for_each_cause(self):
        cases = {
            "pyproject.toml": "a repo-wide file",
            "requirements-test.lock": "a repo-wide file",
            "tests/kit/db.py": "under tests/kit/",
            "tests/shard.py": "a tests/ helper",
            "tools/blob.bin": "a docs/ or tools/ file of a type the selector cannot place (.bin)",
            "lifeos/newpkg/z.py": "a lifeos/ file no test",
            "setup.exe": "a top-level file that is not markdown",
            "scripts/x.py": "a path outside lifeos/",
        }
        for path, rule in cases.items():
            why = []
            self.assertEqual(ti.select([path], None, self.root, reach, why), ti.FULL, path)
            self.assertEqual(len(why), 1, path)
            self.assertTrue(why[0].startswith(path + ": " + rule), why)
        why = []
        self.assertEqual(ti.select(["docs/guide.md"], None, self.root, None, why), ti.FULL)
        self.assertEqual(why, ["no usable test index and no import graph"])
        why = []
        self.assertEqual(
            ti.select(["tests/kit/a.py", ".github/x.yml", "docs/guide.md"], None, self.root, reach, why), ti.FULL
        )
        self.assertEqual(len(why), 2)  # every path that forces FULL is named

    def test_with_neither_an_index_nor_an_import_graph_the_answer_is_everything(self):
        self.assertEqual(ti.select(["docs/guide.md"], None, self.root), ti.FULL)
        self.assertEqual(ti.select([], None, self.root), ti.FULL)


class ChangedFiles(unittest.TestCase):
    """The local mode sees staged, unstaged and untracked files as well as commits; a rename is two paths."""

    def make(self):
        root = pathlib.Path(tempfile.mkdtemp())

        def run(*args):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

        run("init", "-q", "-b", "main")
        run("config", "user.email", "t@example.com")
        run("config", "user.name", "t")
        for name in ("committed.py", "staged.py", "unstaged.py", "gone.py", "old_name.py"):
            (root / name).write_text("x = 1\n")
        run("add", ".")
        run("commit", "-q", "-m", "base")
        run("checkout", "-q", "-b", "work")
        (root / "committed.py").write_text("x = 2\n")
        run("commit", "-qam", "change")
        (root / "staged.py").write_text("x = 3\n")
        run("add", "staged.py")
        (root / "unstaged.py").write_text("x = 4\n")
        (root / "untracked.py").write_text("x = 5\n")
        run("rm", "-q", "gone.py")
        run("mv", "old_name.py", "new_name.py")
        return root, run

    def test_local_mode_adds_staged_unstaged_untracked_deleted_and_both_sides_of_a_rename(self):
        root, _ = self.make()
        got = set(ti.changed_files("main", root, local=True))
        self.assertEqual(
            got, {"committed.py", "staged.py", "unstaged.py", "untracked.py", "gone.py", "old_name.py", "new_name.py"}
        )

    def test_ci_mode_sees_only_commits(self):
        root, _ = self.make()
        self.assertEqual(ti.changed_files("main", root), ["committed.py"])


class Check(unittest.TestCase):
    def test_a_fresh_index_passes(self):
        self.assertEqual(ti.check(index({"jobs/a.py": [0], "x.py": [1]}), NOW), [])

    def test_an_old_index_is_reported_with_its_age(self):
        idx = index({"jobs/a.py": [0], "x.py": [1]}, made=NOW - datetime.timedelta(days=8))
        self.assertEqual(ti.check(idx, NOW), ["the test index is 8 days old (limit 7)"])
        self.assertEqual(
            ti.check(index({"jobs/a.py": [0], "x.py": [1]}, made=NOW - datetime.timedelta(days=7)), NOW), []
        )

    def test_production_files_added_or_deleted_since_the_build_are_not_a_problem(self):
        """adding a source must not need a rebuild of the index"""
        self.assertEqual(ti.check(index({"jobs/a.py": [0], "jobs/deleted.py": [0]}), NOW), [])

    def test_a_missing_index_is_reported(self):
        self.assertEqual(ti.check(None, NOW), ["the test index is missing or unreadable"])


class SelectCommand(unittest.TestCase):
    """the index is restored from a cache in pull requests, so a missing, old, damaged or lying one must never make a
    test disappear from a run.
    """

    def run_select(self, loaded, changed=("lifeos/sources/web/boards.py",)):
        out = io.StringIO()
        with (
            mock.patch.object(ti, "load", return_value=loaded),
            mock.patch.object(ti, "changed_files", return_value=list(changed)),
            contextlib.redirect_stdout(out),
            mock.patch.object(datetime, "datetime", clock.FrozenDateTime),
        ):
            self.assertEqual(ti.main(["select", "--base", "HEAD^1"]), 0)
        return out.getvalue().split("\n")[:-1]

    def test_no_index_or_an_old_one_selects_by_the_import_graph_and_a_damaged_one_prints_full(self):
        fresh_now = clock.now()
        idx = index({"sources/web/boards.py": [0]}, tests=("tests.sources.web.test_boards.B.test_one",), made=fresh_now)
        old = {**idx, "generated_at": (fresh_now - datetime.timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        keys_missing = {"schema": 1, "generated_at": idx["generated_at"]}
        for loaded in (idx, None, old):  # the import graph alone still places a lifeos/ change
            picked = self.run_select(loaded)
            self.assertNotEqual(picked[0], "FULL", loaded)
            self.assertIn("tests.sources.web.test_boards", picked)
            self.assertIn("tests.contracts.test_privacy", picked)
        for loaded in (None, old):  # a path nothing places is FULL with or without an index
            self.assertEqual(self.run_select(loaded, changed=("scripts/x.py",))[0], "FULL")
        self.assertEqual(
            self.run_select(index({"sources/web/boards.py": [99]}, tests=("tests.x.A.t",), made=fresh_now))[0], "FULL"
        )  # a number outside the test list: the index lies, fail open
        self.assertEqual(self.run_select(keys_missing)[0], "FULL")  # a damaged index fails open too

    def test_full_says_why_on_the_lines_after_the_first(self):
        fresh_now = clock.now()
        idx = index({"sources/web/boards.py": [0]}, tests=("tests.sources.web.test_boards.B.test_one",), made=fresh_now)
        liar = index({"sources/web/boards.py": [99]}, tests=("tests.x.A.t",), made=fresh_now)
        self.assertEqual(self.run_select(liar), ["FULL", "FULL because selector error: IndexError"])
        workflow = self.run_select(idx, changed=(".github/workflows/pr-tests.yml", "tests/kit/db.py"))
        self.assertEqual(workflow[0], "FULL")
        self.assertIn("FULL because .github/workflows/pr-tests.yml: under .github/ (run-everything prefix)", workflow)
        self.assertIn("FULL because tests/kit/db.py: under tests/kit/ (run-everything prefix)", workflow)
        self.assertEqual(
            self.run_select(None, changed=("scripts/x.py",)),
            ["FULL", "FULL because scripts/x.py: a path outside lifeos/, tests/, docs/ and tools/"],
        )

    def test_a_platform_change_in_a_pull_request_uses_the_import_graph_not_everything(self):
        fresh_now = clock.now()
        idx = index({}, tests=("tests.platform.test_settings.S.test_one",), made=fresh_now)
        for loaded in (idx, None):  # with a fresh index and without one
            picked = self.run_select(loaded, changed=("lifeos/platform/settings.py",))
            self.assertNotEqual(picked[0], "FULL")
            self.assertIn("tests.platform.test_settings", picked)
            self.assertIn("tests.contracts.test_privacy", picked)

    def test_an_index_that_lies_about_a_file_still_runs_the_tests_named_after_it(self):
        idx = index({"jobs/a.py": [2]})  # says only a sources test covers jobs/a.py
        picked = ti.select(["lifeos/jobs/a.py"], idx, tree(FILES))
        self.assertIn("tests.jobs.test_a", picked)  # the mirror test of the module runs regardless


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
