"""LIFE-541: every Bitwarden load step has exactly one retry step whose PARSED `with:` block equals the load's (it may be a YAML alias of it). Names and counts only; never a secret value."""
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:          # PyYAML is a dev convenience, not a runtime dependency
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
SM = "bitwarden/sm-action@"
PINNED = "bitwarden/sm-action@1238aae8fc64b212641190a9227c8a734ab1a793"


def pairs():
    """-> [(workflow file, job, load step, retry step)] for every job that uses the action; fails the test run if the steps are not strictly load, retry."""
    out, loose = [], []
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        data = yaml.safe_load(path.read_text())
        for job, body in (data.get("jobs") or {}).items():
            steps = [s for s in body.get("steps", []) if str(s.get("uses", "")).startswith(SM)]
            if len(steps) % 2:
                loose.append((path.name, job))
            for load, retry in zip(steps[0::2], steps[1::2]):
                out.append((path.name, job, load, retry))
    return out, loose


@unittest.skipIf(yaml is None, "PyYAML not installed")
class BitwardenPairs(unittest.TestCase):
    def test_every_load_step_has_exactly_one_retry(self):
        found, loose = pairs()
        self.assertEqual(loose, [], "a job has an unpaired Bitwarden step")
        self.assertEqual(len(found), 45)                                    # 45 pairs, 90 steps (LIFE-540; LIFE-571 added the amazon_retention job); a new pair must be added here on purpose

    def test_the_retry_block_equals_the_load_block_after_expansion(self):
        for name, job, load, retry in pairs()[0]:
            self.assertEqual(retry["with"], load["with"], "%s/%s: the retry's with: differs from the load's" % (name, job))
            self.assertIn("secrets", load["with"], "%s/%s" % (name, job))

    def test_the_retry_runs_only_after_a_failed_load_and_both_are_the_pinned_action(self):
        for name, job, load, retry in pairs()[0]:
            where = "%s/%s" % (name, job)
            self.assertEqual(load["uses"].split()[0], PINNED, where)
            self.assertEqual(retry["uses"].split()[0], PINNED, where)
            self.assertTrue(load.get("continue-on-error"), where)           # D209: one retry, and the retry fails the job
            self.assertFalse(retry.get("continue-on-error"), where)
            self.assertEqual(retry["id"], load["id"] + "_retry", where)
            self.assertEqual(retry["if"].replace(" ", ""), "${{steps.%s.outcome=='failure'}}" % load["id"], where)


if __name__ == "__main__":
    unittest.main()
