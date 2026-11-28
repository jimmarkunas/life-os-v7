"""the generated domains.yml and hourly.yml write repeated values and repeated env/with blocks once, as YAML anchors and
aliases (GitHub Actions reads.
"""

import pathlib
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import wfsrc  # noqa: E402

SAMPLE = """jobs:
  a:
    steps:
      - name: one
        env:
          ALPHA: ${{ steps.bws_a.outputs.ALPHA_VALUE_NAME }}
          BETA: ${{ steps.bws_a.outputs.BETA_VALUE_NAME_LONG }}
          GAMMA: ${{ steps.bws_a.outputs.GAMMA_VALUE_NAME_LONG }}
        run: |
          echo ALPHA: ${{ steps.bws_a.outputs.ALPHA_VALUE_NAME }}
      - name: two
        env:
          ALPHA: ${{ steps.bws_a.outputs.ALPHA_VALUE_NAME }}
          BETA: ${{ steps.bws_a.outputs.BETA_VALUE_NAME_LONG }}
          GAMMA: ${{ steps.bws_a.outputs.GAMMA_VALUE_NAME_LONG }}
        run: echo two
      - name: three
        if: ${{ github.event_name == 'workflow_run' && inputs.all }}
        run: echo three
      - name: four
        if: ${{ github.event_name == 'workflow_run' && inputs.all }}
        run: echo four
"""


class Aliases(unittest.TestCase):
    def test_repeats_become_aliases_the_document_is_the_same_and_the_text_comes_back(self):
        packed = wfsrc.compress(SAMPLE)
        self.assertLess(len(packed), len(SAMPLE))
        self.assertIn("env: *m1", packed)
        self.assertEqual(yaml.safe_load(packed), yaml.safe_load(SAMPLE))
        self.assertEqual(wfsrc.expand_aliases(packed), SAMPLE)

    def test_a_script_line_that_looks_like_a_value_is_never_touched(self):
        packed = wfsrc.compress(SAMPLE)
        self.assertIn("          echo ALPHA: ${{ steps.bws_a.outputs.ALPHA_VALUE_NAME }}", packed)

    def test_the_real_generated_workflows_round_trip_and_the_big_ones_are_much_smaller_than_their_plain_text(self):
        flows = ROOT / ".github/workflows"
        names = sorted(p.name for p in flows.glob("*.yml") if p.name.startswith(("domain", "hourly", "tick-")))
        self.assertIn("domain-run.yml", names)
        for name in names:
            text = (flows / name).read_text()
            plain = wfsrc.expand_aliases(text)
            self.assertEqual(wfsrc.compress(plain), text, name)  # the writer is deterministic
            self.assertEqual(yaml.safe_load(plain), yaml.safe_load(text), name)
        for name in ("domain-run.yml", "hourly.yml", "tick-finish.yml"):  # the cut files that repeat the most
            text = (flows / name).read_text()
            plain = wfsrc.expand_aliases(text)
            self.assertNotEqual(plain, text, name)  # aliases are in use
            self.assertLess(len(text), len(plain) * 0.9, name)


if __name__ == "__main__":
    unittest.main()
