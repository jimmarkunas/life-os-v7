"""LIFE-764: .github/workflows/domains.yml is the fragments in .github/workflows-src/domains joined in file-name order, one fragment per job plus the header; tools/regen.py --check fails when either side is edited alone."""
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

PARTS = ROOT / ".github/workflows-src/domains"
GENERATED = ROOT / ".github/workflows/domains.yml"


def check():
    out = io.StringIO()
    with redirect_stdout(out):
        code = regen.main(["--check"])
    return code, out.getvalue()


class DomainsParts(unittest.TestCase):
    def test_the_generated_file_is_the_fragments_joined(self):
        self.assertEqual(regen.domains_text(), GENERATED.read_text(), "run python tools/regen.py")

    def test_each_fragment_holds_exactly_the_job_its_file_name_says(self):
        names = [p.name for p in sorted(PARTS.glob("*.yml"))]
        self.assertEqual(names[0], "000-header.yml")
        for path in sorted(PARTS.glob("*.yml"))[1:]:
            keys = re.findall(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$", path.read_text(), re.M)
            self.assertEqual(keys[:1], [path.stem.split("-", 1)[1]], path.name)
            self.assertNotIn("\njobs:", path.read_text(), path.name)
        header = (PARTS / "000-header.yml").read_text()
        self.assertTrue(header.endswith("\njobs:\n"))

    def test_the_fragments_define_the_same_jobs_the_workflow_runs(self):
        import yaml
        jobs = set(yaml.safe_load(GENERATED.read_text())["jobs"])
        self.assertEqual(jobs, {p.stem.split("-", 1)[1] for p in PARTS.glob("*.yml")} - {"header"})

    def test_the_fragments_sit_outside_the_workflows_folder_so_github_never_runs_them(self):
        self.assertEqual(list((ROOT / ".github/workflows").glob("*domains*")), [GENERATED])

    def test_a_fragment_edited_alone_fails_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = pathlib.Path(tmp) / "domains"
            shutil.copytree(PARTS, copy)
            (copy / "020-jira.yml").write_text((copy / "020-jira.yml").read_text() + "    # edited\n")
            with mock.patch.object(regen, "DOMAINS_PARTS", copy):
                code, out = check()
        self.assertEqual(code, 1)
        self.assertIn("domains.yml", out)

    def test_the_generated_file_edited_alone_fails_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            edited = pathlib.Path(tmp) / "domains.yml"
            edited.write_text(GENERATED.read_text() + "# edited by hand\n")
            with mock.patch.object(regen, "DOMAINS", edited):
                code, out = check()
        self.assertEqual(code, 1)
        self.assertIn("domains.yml", out)


if __name__ == "__main__":
    unittest.main()
