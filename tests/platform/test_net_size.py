"""LIFE-625: the net-size helper counts lines added and removed per area, from a real git diff, and flags growth in lifeos/."""
import contextlib
import io
import os
import subprocess
import tempfile
import unittest

from lifeos.platform import net_size


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=Example", "-c", "user.email=example@example.com", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})


def write(cwd, name, lines):
    path = os.path.join(cwd, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write("".join(f"{line}\n" for line in lines))


class Areas(unittest.TestCase):
    def test_each_path_belongs_to_one_area(self):
        self.assertEqual(net_size.area_of("lifeos/platform/gate.py"), "lifeos/")
        self.assertEqual(net_size.area_of("tests/platform/test_gate.py"), "tests/")
        self.assertEqual(net_size.area_of("docs/CODEMAP.md"), "docs/")
        self.assertEqual(net_size.area_of(".github/workflows/domains.yml"), "workflows")
        self.assertEqual(net_size.area_of(".github/actions/bws-helper/action.yml"), "workflows")
        self.assertEqual(net_size.area_of("AGENTS.md"), "other")
        self.assertEqual(net_size.area_of(".github/pull_request_template.md"), "other")


class Parse(unittest.TestCase):
    def test_numstat_lines_become_rows_and_a_binary_file_counts_zero(self):
        rows = net_size.parse_numstat("3\t1\tlifeos/a.py\n-\t-\tdocs/logo.png\nnot a numstat line\n0\t5\ttests/t.py\n")
        self.assertEqual(rows, [(3, 1, "lifeos/a.py"), (0, 0, "docs/logo.png"), (0, 5, "tests/t.py")])

    def test_totals_add_up_per_area(self):
        found = net_size.totals([(3, 1, "lifeos/a.py"), (4, 0, "lifeos/b.py"), (0, 5, "tests/t.py"), (2, 2, "AGENTS.md")])
        self.assertEqual(found["lifeos/"], [7, 1])
        self.assertEqual(found["tests/"], [0, 5])
        self.assertEqual(found["other"], [2, 2])
        self.assertEqual(found["docs/"], [0, 0])


class Render(unittest.TestCase):
    def test_growth_in_lifeos_gets_the_reminder(self):
        text = net_size.render([(10, 4, "lifeos/a.py"), (0, 5, "tests/t.py")])
        self.assertIn("- lifeos/: added 10, removed 4, net +6", text)
        self.assertIn("- tests/: added 0, removed 5, net -5", text)
        self.assertIn("- all: added 10, removed 9, net +1", text)
        self.assertIn(net_size.REMINDER, text)

    def test_no_growth_in_lifeos_means_no_reminder(self):
        self.assertNotIn(net_size.REMINDER, net_size.render([(2, 6, "lifeos/a.py")]))
        self.assertNotIn(net_size.REMINDER, net_size.render([(9, 0, "tests/t.py")]))
        self.assertNotIn(net_size.REMINDER, net_size.render([]))

    def test_an_empty_diff_prints_every_area_at_zero(self):
        text = net_size.render([])
        for name, _ in net_size.AREAS:
            self.assertIn(f"- {name}: added 0, removed 0, net +0", text)


class RealRepository(unittest.TestCase):
    """A scratch repository: a base commit on main, then a branch that edits, adds and deletes files in three areas."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cwd = self.cwd = self.tmp.name
        git(cwd, "init", "-q", "-b", "main")
        write(cwd, "lifeos/a.py", ["one", "two", "three"])
        write(cwd, "tests/t.py", ["x", "y"])
        git(cwd, "add", "-A")
        git(cwd, "commit", "-q", "-m", "base")
        git(cwd, "checkout", "-q", "-b", "branch")
        write(cwd, "lifeos/a.py", ["one", "TWO", "three", "four"])               # +2 -1
        write(cwd, "docs/d.md", ["a", "b", "c", "d"])                            # +4
        os.remove(os.path.join(cwd, "tests/t.py"))                               # -2
        git(cwd, "add", "-A")
        git(cwd, "commit", "-q", "-m", "change")

    def test_the_diff_against_main_is_counted_per_area(self):
        found = net_size.totals(net_size.parse_numstat(net_size.diff("main", cwd=self.cwd)))
        self.assertEqual(found["lifeos/"], [2, 1])
        self.assertEqual(found["docs/"], [4, 0])
        self.assertEqual(found["tests/"], [0, 2])

    def test_the_command_prints_the_block_for_the_current_directory(self):
        out = io.StringIO()
        here = os.getcwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, here)
        with contextlib.redirect_stdout(out):
            code = net_size.main(["main"])
        self.assertEqual(code, 0)
        self.assertIn("- lifeos/: added 2, removed 1, net +1", out.getvalue())
        self.assertIn(net_size.REMINDER, out.getvalue())

    def test_a_missing_base_fails_with_a_fixed_message_and_no_traceback(self):
        out = io.StringIO()
        here = os.getcwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, here)
        with contextlib.redirect_stdout(out):
            code = net_size.main(["no-such-branch"])
        self.assertEqual(code, 2)
        self.assertIn("git diff failed", out.getvalue())


class CeilingWarnings(unittest.TestCase):
    """LIFE-631 (advisory): a package over its ceiling that this change makes bigger gets a warning line; shrinking it, or a package under its ceiling, gets none. Never an error."""
    def tree(self, lines):
        folder = tempfile.mkdtemp()
        write(folder, "lifeos/sources/m.py", ["x"] * lines)
        return folder

    def test_a_growing_package_over_its_ceiling_is_warned_about(self):
        root = self.tree(10)
        got = net_size.ceiling_warnings([(5, 0, "lifeos/sources/m.py")], root, {"sources": 8})
        self.assertEqual(len(got), 1)
        self.assertIn("lifeos/sources is 10 lines against its ceiling of 8 and this change adds +5", got[0])

    def test_shrinking_a_package_under_its_ceiling_or_touching_another_package_is_not_warned_about(self):
        root = self.tree(10)
        self.assertEqual(net_size.ceiling_warnings([(0, 4, "lifeos/sources/m.py")], root, {"sources": 8}), [])
        self.assertEqual(net_size.ceiling_warnings([(5, 0, "lifeos/sources/m.py")], root, {"sources": 99}), [])
        self.assertEqual(net_size.ceiling_warnings([(5, 0, "lifeos/jobs/x.py"), (5, 0, "tests/a.py")], root, {"sources": 8}), [])

    def test_the_approved_ceilings_are_the_ones_jim_approved(self):
        self.assertEqual(net_size.CEILINGS, {"sources": 9400, "jobs": 8250, "platform": 7050, "interview": 4900, "edge": 3200, "jira": 1550})


if __name__ == "__main__":
    unittest.main()
