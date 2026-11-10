"""LIFE-560: the environment-variable registry (CONFIG in lifeos/platform/credential_list.py) must agree with the code and the workflows. Names only; no read site is changed.

Fails when: code reads a name the registry does not list; a workflow sets a name that is in neither the registry nor the provided group (GITHUB_*, RUNNER_*, CI and the host names);
a registry name is neither read by code, set by a workflow, nor covered by a declared run-time pattern (stale); or a file reads a name built at run time without a declared pattern.
The scanner is tests/config_scan.py (AST over lifeos/, YAML over .github/workflows). To add a variable, add one row to CONFIG; docs/platform/config-registry.md says how."""
import copy
import re
import tempfile
import unittest
from pathlib import Path

from lifeos.platform import credentials
from tests import config_scan as scan

KINDS = ("input", "flag", "secret", "workflow_only")
SECRET_SHAPED = re.compile(r"[A-Za-z0-9+/_=-]{32,}|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|/Users/|/home/|\bsk-|\bghp_|\bgithub_pat_")


def tree(code=None, workflow=None):
    """A throwaway repo root with one module and one workflow, for the negative controls."""
    root = Path(tempfile.mkdtemp())
    (root / "lifeos").mkdir()
    (root / "lifeos" / "mod.py").write_text(code or "import os\nX = os.environ.get('KNOWN_NAME')\n")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "w.yml").write_text(workflow or "name: w\nenv:\n  KNOWN_NAME: '1'\njobs:\n  j:\n    runs-on: x\n    steps:\n      - run: echo\n")
    return root


def config(*names, patterns=(), provided=None):
    return {"provided": provided or {"prefixes": ["GITHUB_"], "names": ["PATH"]},
            "names": [{"name": n, "kind": "input", "owner": "o", "purpose": "p"} for n in names], "patterns": list(patterns), "not_environment": {}}


class RegistryAgreesWithTheRepo(unittest.TestCase):
    def test_every_environment_name_in_code_and_workflows_is_listed_and_nothing_is_stale(self):
        self.assertEqual(scan.problems(credentials.config()), [])

    def test_every_row_has_a_valid_kind_an_owner_a_purpose_and_no_value_shaped_text(self):
        rows = credentials.config()["names"]
        names = [r["name"] for r in rows]
        self.assertEqual(names, sorted(names))                                                      # sorted, so a merge conflict is a one-line fix
        self.assertEqual(len(names), len(set(names)))
        for row in rows:
            self.assertEqual(set(row), {"name", "kind", "owner", "purpose"}, row["name"])
            self.assertRegex(row["name"], scan.NAME)
            self.assertIn(row["kind"], KINDS, row["name"])
            self.assertTrue(row["owner"] and row["purpose"], row["name"])
            self.assertIsNone(SECRET_SHAPED.search(row["purpose"] + row["owner"]), row["name"])     # a purpose is words, never a value, an id or a path

    def test_the_provided_group_is_not_listed_one_by_one(self):
        group = credentials.config()["provided"]
        for row in credentials.config()["names"]:
            self.assertFalse(row["name"].startswith(tuple(group["prefixes"])) or row["name"] in group["names"], row["name"])


class NegativeControls(unittest.TestCase):
    def test_an_unlisted_read_is_caught(self):
        root = tree(code="import os\nX = os.environ.get('KNOWN_NAME')\nY = os.environ['PLANTED_UNLISTED_READ']\n")
        found = scan.problems(config("KNOWN_NAME"), root)
        self.assertTrue(any("PLANTED_UNLISTED_READ" in p and "code reads" in p for p in found), found)

    def test_a_read_through_an_environ_parameter_is_caught_too(self):
        root = tree(code="def f(environ):\n    return environ.get('PLANTED_PARAM_READ')\n")
        found = scan.problems(config("KNOWN_NAME"), root)
        self.assertTrue(any("PLANTED_PARAM_READ" in p for p in found), found)

    def test_an_unlisted_workflow_name_is_caught_and_a_provided_name_is_not(self):
        root = tree(workflow="name: w\nenv:\n  KNOWN_NAME: '1'\n  PLANTED_UNLISTED_SET: '1'\n  GITHUB_ANYTHING: '1'\njobs:\n  j:\n    runs-on: x\n    steps:\n      - run: echo\n")
        found = scan.problems(config("KNOWN_NAME"), root)
        self.assertTrue(any("PLANTED_UNLISTED_SET" in p for p in found), found)
        self.assertFalse(any("GITHUB_ANYTHING" in p for p in found), found)

    def test_a_stale_registry_name_is_caught(self):
        found = scan.problems(config("KNOWN_NAME", "PLANTED_STALE_NAME"), tree())
        self.assertTrue(any("PLANTED_STALE_NAME" in p and "stale" in p for p in found), found)

    def test_a_name_built_at_run_time_needs_a_declared_pattern_and_the_pattern_must_still_be_used(self):
        code = "import os\nPREFIX = 'RT_'\nX = os.environ.get(PREFIX + 'ONE')\n"
        root = tree(code=code)
        self.assertTrue(any("built at run time" in p for p in scan.problems(config("KNOWN_NAME", "RT_ONE"), root)))
        declared = config("KNOWN_NAME", "RT_ONE", patterns=[{"file": "lifeos/mod.py", "match": ["RT_*"]}])
        self.assertEqual(scan.problems(declared, root), [])
        plain = tree()                                                                              # the same declaration with no run-time read left is stale
        self.assertTrue(any("no run-time read any more" in p for p in scan.problems(declared, plain)))
        loose = config("KNOWN_NAME", patterns=[{"file": "lifeos/mod.py", "match": ["ZZ_*"]}])
        self.assertTrue(any("matches no listed name" in p for p in scan.problems(loose, root)))

    def test_a_write_to_a_dict_called_env_is_not_a_read(self):
        root = tree(code="import os\nX = os.environ.get('KNOWN_NAME')\nenv = {}\nenv['k'] = 'v'\n")
        self.assertEqual(scan.problems(config("KNOWN_NAME"), root), [])

    def test_the_real_registry_with_one_row_removed_fails(self):
        broken = copy.deepcopy(credentials.config())
        broken["names"] = [r for r in broken["names"] if r["name"] != "NTFY_TOPIC"]
        self.assertTrue(any("NTFY_TOPIC" in p for p in scan.problems(broken)))


if __name__ == "__main__":
    unittest.main()
