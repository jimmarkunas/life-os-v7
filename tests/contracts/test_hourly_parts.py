"""LIFE-1010: .github/workflows/hourly.yml is the parts in .github/workflows-src/hourly joined in file-name order, like domains.yml; tools/regen.py --check fails when either side is edited alone."""
import io
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import regen  # noqa: E402

PARTS = ROOT / ".github/workflows-src/hourly"
GENERATED = ROOT / ".github/workflows/hourly.yml"


def check():
    out = io.StringIO()
    with redirect_stdout(out):
        code = regen.main(["--check"])
    return code, out.getvalue()


class HourlyParts(unittest.TestCase):
    def test_the_generated_file_is_the_parts_joined_without_comments(self):
        self.assertEqual(regen.hourly_text(), GENERATED.read_text(), "run python tools/regen.py")
        self.assertNotRegex(GENERATED.read_text(), r"(?m)^ {0,8}#")

    def test_each_part_holds_the_job_its_file_name_says_and_long_jobs_continue_in_lettered_files(self):
        files = sorted(PARTS.glob("*.yml"))
        self.assertEqual(files[0].name, "000-header.yml")
        self.assertTrue(files[0].read_text().endswith("\njobs:\n"))
        job = None
        for path in files[1:]:
            found = re.match(r"^(\d{3})([b-z]?)-(.+)\.yml$", path.name)
            self.assertTrue(found, path.name)
            keys = re.findall(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$", path.read_text(), re.M)
            if found.group(2):
                self.assertEqual((keys, found.group(3)), ([], job), path.name)
            else:
                job = found.group(3)
                self.assertEqual(keys[:1], [job], path.name)

    def test_the_parts_define_the_jobs_the_workflow_runs(self):
        import yaml
        jobs = list(yaml.safe_load(GENERATED.read_text())["jobs"])
        self.assertEqual(jobs, [p.stem.split("-", 1)[1] for p in sorted(PARTS.glob("*.yml")) if re.match(r"^\d{3}-", p.name) and p.name != "000-header.yml"])

    def test_the_parts_sit_outside_the_workflows_folder_so_github_never_runs_them(self):
        self.assertEqual(sorted(p.name for p in (ROOT / ".github/workflows").glob("*hourly*")), ["hourly.yml"])

    def test_a_part_edited_alone_fails_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = pathlib.Path(tmp) / "hourly"
            shutil.copytree(PARTS, copy)
            (copy / "080-report.yml").write_text((copy / "080-report.yml").read_text() + "      - run: echo edited\n")
            with mock.patch.object(regen, "HOURLY_PARTS", copy):
                code, out = check()
        self.assertEqual(code, 1)
        self.assertIn("hourly.yml", out)

    def test_the_generated_file_edited_alone_fails_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            edited = pathlib.Path(tmp) / "hourly.yml"
            edited.write_text(GENERATED.read_text() + "# edited by hand\n")
            with mock.patch.object(regen, "HOURLY", edited):
                code, out = check()
        self.assertEqual(code, 1)
        self.assertIn("hourly.yml", out)


if __name__ == "__main__":
    unittest.main()
