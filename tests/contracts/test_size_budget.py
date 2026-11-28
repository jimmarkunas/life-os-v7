"""the size budget of the big workflows."""

import pathlib
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
FLOWS = ROOT / ".github/workflows"
SRC = ROOT / ".github/workflows-src"
GENERATED_MAX = 20 * 1024  # no workflow file is over 20 KB; a bigger job is a reusable workflow of its own
PART_MAX = 6 * 1024
NAME_MAX = 60
RATCHET = {}
GENERATED_NAMES = (
    "domain",
    "hourly",
    "tick-",
    "v3-pages",
)  # the files tools/regen.py writes keep their step names short; the size limit is for every workflow
PARTS_TOTAL_MAX = 281_000  # the six manual-stage jobs each carry their own setup steps (about 4 KB each), so the parts grew by that much when finish was cut


def problems(flows, src, ratchet=RATCHET):
    """> one text per breach: a generated workflow over its limit, a part over 8 KB, a step name over 80 characters."""
    out = []
    for path in sorted(pathlib.Path(flows).glob("*.yml")):
        size = len(path.read_bytes())
        if size > ratchet.get(path.name, GENERATED_MAX):
            out.append(f"{path.name} is {size} bytes, over {ratchet.get(path.name, GENERATED_MAX)}")
        if not path.name.startswith(GENERATED_NAMES):
            continue
        for name, job in (yaml.safe_load(path.read_text()).get("jobs") or {}).items():
            out += [
                f"{path.name} {name}: step name of {len(s['name'])} characters: {s['name'][:40]}"
                for s in job.get("steps") or []
                if len(str(s.get("name", ""))) > NAME_MAX
            ]
    for part in sorted(pathlib.Path(src).rglob("*.yml")):
        if part.name == "why.yml":
            continue
        if len(part.read_bytes()) > PART_MAX:
            out.append(f"{part.name} is {len(part.read_bytes())} bytes, over {PART_MAX}")
    return out


class Budget(unittest.TestCase):
    def test_the_parts_together_stay_at_least_fifteen_percent_under_what_they_were(self):
        total = sum(len(p.read_bytes()) for p in SRC.glob("*/*.yml"))
        self.assertLessEqual(total, PARTS_TOTAL_MAX)

    def test_the_workflows_stay_inside_the_size_budget(self):
        self.assertEqual(problems(FLOWS, SRC), [])

    def test_an_oversize_part_a_long_step_name_and_a_big_workflow_are_each_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            flows, src = pathlib.Path(tmp, "flows"), pathlib.Path(tmp, "src", "x")
            flows.mkdir()
            src.mkdir(parents=True)
            (src / "010-a.yml").write_text("# " + "x" * PART_MAX + "\n")
            (flows / "hourly.yml").write_text(
                "jobs:\n  a:\n    steps:\n      - name: " + "n" * (NAME_MAX + 1) + "\n        run: echo\n"
            )
            (flows / "domains.yml").write_text("jobs: {}\n# " + "y" * 200 + "\n")
            found = problems(flows, src.parent, ratchet={"domains.yml": 100, "hourly.yml": 10**6})
        self.assertEqual(len(found), 3, found)
        self.assertTrue(
            any("010-a.yml" in f for f in found)
            and any("step name" in f for f in found)
            and any("domains.yml" in f for f in found)
        )

    def test_a_clean_tree_has_no_problem_and_the_largest_workflow_is_under_twenty_kilobytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(problems(tmp, tmp), [])
        largest = max(FLOWS.glob("*.yml"), key=lambda p: len(p.read_bytes()))
        self.assertLess(len(largest.read_bytes()), 20 * 1024, largest.name)


if __name__ == "__main__":
    unittest.main()
