"""LIFE-541, reshaped by LIFE-631: every Bitwarden load is the one local action (`bws-load`), which holds the pinned load and its one retry (that pair is pinned in test_workflow.py).
Names and counts only; never a secret value."""
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:          # PyYAML is a dev convenience, not a runtime dependency
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
LOADER = "./.github/actions/bws-load"


def loaders():
    """-> [(workflow file, job, loader step)] for every job that loads credentials."""
    return [(p.name, job, s) for p in sorted((ROOT / ".github/workflows").glob("*.yml")) for job, body in (yaml.safe_load(p.read_text()).get("jobs") or {}).items()
            for s in body.get("steps", []) if s.get("uses") == LOADER]


@unittest.skipIf(yaml is None, "PyYAML not installed")
class BitwardenLoaders(unittest.TestCase):
    def test_every_job_that_loads_credentials_uses_the_one_local_loader(self):
        found = loaders()
        self.assertEqual(len(found), 45)                                    # 45 loads (LIFE-540; LIFE-571 added amazon_retention); a new one must be added here on purpose
        for name, job, step in found:
            self.assertIn("secrets", step["with"], "%s/%s" % (name, job))

    def test_no_workflow_calls_the_third_party_action_directly(self):
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            self.assertNotIn("bitwarden/sm-action", path.read_text(), path.name)


if __name__ == "__main__":
    unittest.main()
