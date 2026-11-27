"""LIFE-1010: tools/wfgraph.py proves a workflow refactor changed no behaviour: the lean steps inline back to the old ones, and any real difference shows."""
import contextlib
import io
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import wfgraph  # noqa: E402

OLD = """
name: w
on: {workflow_dispatch: {}}
jobs:
  a:
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout
        run: echo checkout
      - uses: ./.github/actions/python
      - name: Bitwarden helper (long history)
        uses: ./.github/actions/bws-helper
        with:
          wait_seconds: "300"
      - id: bws_a
        name: Load (long history)
        uses: ./.github/actions/bws-load
        with:
          access_token: ${{ secrets.T }}
          secrets: |
            1 > B_NAME
            2 > A_NAME
      - id: doppler_bws_a
        if: ${{ steps.bws_a.outcome == 'success' }}
        continue-on-error: true
        name: Parity
        uses: ./.github/actions/doppler-load
        with:
          token: ${{ secrets.D }}
          names: A_NAME,B_NAME
          bitwarden: '{}'
      - run: echo go   # comment
"""
NEW = """
name: w
on: {workflow_dispatch: {}}
jobs:
  a:
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout (short)
        run: echo checkout
      - name: Python and the helper
        uses: ./.github/actions/job-start
        with:
          wait_seconds: "300"
      - id: bws_a
        name: Load
        uses: ./.github/actions/bws-load
        with:
          access_token: ${{ secrets.T }}
          doppler_compare: ${{ secrets.D }}
          secrets: |
            1 > B_NAME
            2 > A_NAME
      - run: echo go
"""


class Graph(unittest.TestCase):
    def test_the_lean_steps_inline_to_the_old_graph(self):
        self.assertEqual(wfgraph.graph(OLD), wfgraph.graph(NEW))

    def test_a_changed_command_shows_in_the_graph(self):
        self.assertNotEqual(wfgraph.graph(OLD), wfgraph.graph(NEW.replace("echo go", "echo gone")))

    def test_a_dropped_compare_or_a_changed_wait_shows_in_the_graph(self):
        self.assertNotEqual(wfgraph.graph(OLD), wfgraph.graph(NEW.replace("          doppler_compare: ${{ secrets.D }}\n", "")))
        self.assertNotEqual(wfgraph.graph(OLD), wfgraph.graph(NEW.replace('"300"', '"200"')))

    def test_the_working_tree_graph_equals_itself(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wfgraph.main(["--base", "HEAD"]), 0)


if __name__ == "__main__":
    unittest.main()
