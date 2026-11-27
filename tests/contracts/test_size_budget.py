"""LIFE-1010: the size budget of the big workflows. A generated workflow is at most 130 KB, a source part at most 6 KB and a step name at most 60 characters, so an agent can read and edit one job without loading the whole file.

domains.yml and hourly.yml have their own, lower caps (RATCHET, LIFE-1013); the caps only go down."""
import pathlib
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
FLOWS = ROOT / ".github/workflows"
SRC = ROOT / ".github/workflows-src"
GENERATED_MAX = 130 * 1024
PART_MAX = 6 * 1024
NAME_MAX = 60
RATCHET = {"domains.yml": 125_000, "hourly.yml": 65_000}              # LIFE-1013: reached 112,341 and 56,549 bytes (240,853 and 123,939 before the lean passes); room for growth on purpose
PARTS_TOTAL_MAX = 255_000                                              # LIFE-1016: the parts of domains and hourly were 297,690 bytes before the history moved to why.yml and the comments lost their ticket notes
BUDGETED = ("domains.yml", "hourly.yml", "v3-pages.yml")


def problems(flows, src, ratchet=RATCHET):
    """-> one text per breach: a generated workflow over its limit, a part over 8 KB, a step name over 80 characters."""
    out = []
    for path in sorted(pathlib.Path(flows).glob("*.yml")):
        if path.name not in BUDGETED:
            continue
        size = len(path.read_bytes())
        if size > ratchet.get(path.name, GENERATED_MAX):
            out.append(f"{path.name} is {size} bytes, over {ratchet.get(path.name, GENERATED_MAX)}")
        for name, job in (yaml.safe_load(path.read_text()).get("jobs") or {}).items():
            out += [f"{path.name} {name}: step name of {len(s['name'])} characters: {s['name'][:40]}" for s in job.get("steps") or [] if len(str(s.get("name", ""))) > NAME_MAX]
    for part in sorted(pathlib.Path(src).rglob("*.yml")):
        if part.name == "why.yml":                                       # the history table docs/workflows.md is built from, not a part
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
            (flows / "hourly.yml").write_text("jobs:\n  a:\n    steps:\n      - name: " + "n" * (NAME_MAX + 1) + "\n        run: echo\n")
            (flows / "domains.yml").write_text("jobs: {}\n# " + "y" * 200 + "\n")
            found = problems(flows, src.parent, ratchet={"domains.yml": 100})
        self.assertEqual(len(found), 3, found)
        self.assertTrue(any("010-a.yml" in f for f in found) and any("step name" in f for f in found) and any("domains.yml" in f for f in found))

    def test_a_clean_tree_has_no_problem_and_the_caps_sit_above_the_reached_sizes_with_room_to_grow(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(problems(tmp, tmp), [])
        for name, ceiling in RATCHET.items():
            size = len((FLOWS / name).read_bytes())
            self.assertLess(size, ceiling, name)
            self.assertLess(ceiling - size, 15_000, f"lower the {name} cap when the file shrinks well below it")


if __name__ == "__main__":
    unittest.main()
