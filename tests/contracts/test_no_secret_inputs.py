"""Step outputs are never passed as action inputs: nothing under.github may contain toJSON(steps.…)."""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2] / ".github"
PATTERN = re.compile(r"toJSON\(\s*steps\.", re.IGNORECASE)


class NoStepOutputsAsInputs(unittest.TestCase):
    def test_no_file_under_dot_github_passes_a_step_context_through_toJSON(self):
        files = [p for p in ROOT.rglob("*") if p.is_file()]
        self.assertGreater(len(files), 50)
        offenders = [
            f"{p.relative_to(ROOT)}:{n}"
            for p in files
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1)
            if PATTERN.search(line)
        ]
        self.assertEqual(offenders, [])

    def test_the_pattern_catches_the_forms_it_exists_to_stop(self):
        for bad in (
            "bitwarden: ${{ toJSON(steps.a.outputs) }}",
            "x: ${{toJSON( steps.first.outputs)}}",
            "${{ tojson(steps.a.outcome == 'x' && steps.a.outputs) }}",
        ):
            self.assertTrue(PATTERN.search(bad), bad)
        self.assertFalse(PATTERN.search("bitwarden: '{}'"))


if __name__ == "__main__":
    unittest.main()
