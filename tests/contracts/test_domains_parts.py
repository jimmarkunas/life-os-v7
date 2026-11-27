""".github/workflows/domains.yml is the fragments in.github/workflows-src/domains joined in file-name order, one fragment
per job plus the header
"""
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
        """a long job continues in lettered files (020b-jira.yml) that start at a step and hold no job key"""
        files = sorted(PARTS.glob("*.yml"))
        self.assertEqual([p.name for p in files[:2]], ["000-header.yml", "001-header-inputs.yml"])
        header = "".join(p.read_text() for p in files[:2])
        self.assertTrue(header.endswith("\njobs:\n"))
        self.assertNotIn("\njobs:", "".join(p.read_text() for p in files[:1]) )
        job = None
        for path in files[2:]:
            found = re.match(r"^(\d{3})([b-z]?)-(.+)\.yml$", path.name)
            self.assertTrue(found, path.name)
            keys = re.findall(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$", path.read_text(), re.M)
            self.assertNotIn("\njobs:", path.read_text(), path.name)
            if found.group(2):
                self.assertEqual((keys, found.group(3)), ([], job), path.name)
                self.assertTrue(path.read_text().lstrip().startswith(("-", "#")), path.name)  # starts at a step
            else:
                job = found.group(3)
                self.assertEqual(keys[:1], [job], path.name)

    def test_the_fragments_define_the_same_jobs_the_workflow_runs(self):
        import yaml
        jobs = set(yaml.safe_load(GENERATED.read_text())["jobs"])
        self.assertEqual(jobs, {p.stem.split("-", 1)[1] for p in PARTS.glob("*.yml") if re.match(r"^\d{3}-", p.name) and not p.name.startswith(("000-", "001-"))})

    def test_the_fragments_sit_outside_the_workflows_folder_so_github_never_runs_them(self):
        self.assertEqual(list((ROOT / ".github/workflows").glob("*domains*")), [GENERATED])

    def test_the_generated_file_has_no_comments_and_the_history_is_in_the_workflow_doc(self):
        self.assertNotRegex(GENERATED.read_text(), r"(?m)^ {0,8}#")
        self.assertEqual(regen.workflow_doc_text(), (ROOT / "docs/workflows.md").read_text(), "run python tools/regen.py")

    def test_a_fragment_edited_alone_fails_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = pathlib.Path(tmp) / "domains"
            shutil.copytree(PARTS, copy)
            (copy / "020-jira.yml").write_text((copy / "020-jira.yml").read_text() + "      - run: echo edited\n")
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
