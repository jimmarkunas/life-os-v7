"""LIFE-703 (synthetic): `python -m lifeos.platform.settings` as a workflow pre-step: fills $GITHUB_ENV, publishes chosen switches to $GITHUB_OUTPUT, never writes a safety name or a value with a line break."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from lifeos.platform import settings
from tests.platform.test_settings import Page

GOOD = "4183c5a0-5926-81be-9e19-e85714dc256f"


class Step(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env_file, self.out_file = os.path.join(directory.name, "env"), os.path.join(directory.name, "out")
        for path in (self.env_file, self.out_file):
            Path(path).write_text("")

    def run_step(self, lines, environ, *argv, broken=False):
        environ = {"GITHUB_ENV": self.env_file, "GITHUB_OUTPUT": self.out_file, "LIFEOS_SETTINGS": "on", **environ}
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = settings.main(list(argv), environ, client=Page(lines, broken=broken))
        return code, Path(self.env_file).read_text().splitlines(), Path(self.out_file).read_text().splitlines(), printed.getvalue()

    def test_a_name_the_step_declares_empty_is_filled_into_the_environment_file(self):
        code, env, out, printed = self.run_step([f"BILLS_CARD_BLOCK_ID {GOOD}"], {"BILLS_CARD_BLOCK_ID": ""})
        self.assertEqual((code, env, out), (0, [f"BILLS_CARD_BLOCK_ID={GOOD}"], []))
        self.assertIn(f"::add-mask::{GOOD}", printed)                                                          # masked, so a log can never show it
        self.assertIn('"filled": ["BILLS_CARD_BLOCK_ID"]', printed)

    def test_an_output_name_publishes_its_effective_value_held_or_filled(self):
        _, env, out, _ = self.run_step(["HIRING_AUTO_CLOSE on", "GRANOLA_AUTO on"], {"HIRING_AUTO_CLOSE": "", "GRANOLA_AUTO": "off"}, "--output", "HIRING_AUTO_CLOSE", "--output", "GRANOLA_AUTO")
        self.assertEqual(env, ["HIRING_AUTO_CLOSE=on"])                                                              # filled because it was empty
        self.assertEqual(out, ["HIRING_AUTO_CLOSE=on", "GRANOLA_AUTO=off"])                                          # a held value wins; the page's "on" is only a mismatch count

    def test_a_plain_switch_is_never_masked_so_a_job_output_is_never_dropped(self):
        """Nov 15 2026: masking the value "on" or "true" made GitHub skip every gate output that contained those letters, so every domain job was skipped."""
        _, env, out, printed = self.run_step(["HIRING_AUTO_CLOSE on", "GRANOLA_AUTO true"], {"HIRING_AUTO_CLOSE": "", "GRANOLA_AUTO": ""}, "--output", "HIRING_AUTO_CLOSE", "--output", "GRANOLA_AUTO")
        self.assertEqual((sorted(env), sorted(out)), (["GRANOLA_AUTO=true", "HIRING_AUTO_CLOSE=on"], ["GRANOLA_AUTO=true", "HIRING_AUTO_CLOSE=on"]))
        self.assertNotIn("::add-mask::", printed)

    def test_a_safety_or_unknown_name_is_never_written(self):
        _, env, out, printed = self.run_step(["V7_LIVE on"], {"V7_LIVE": ""}, "--output", "V7_LIVE", "--output", "NOT_A_SETTING")
        self.assertEqual((env, out), ([], []))
        self.assertIn('"not_written": ["NOT_A_SETTING", "V7_LIVE"]', printed)

    def test_a_value_with_a_line_break_is_refused(self):
        _, _, out, printed = self.run_step([], {"HIRING_AUTO_CLOSE": "on\nV7_LIVE=on"}, "--output", "HIRING_AUTO_CLOSE")
        self.assertEqual(out, [])
        self.assertIn("not_written", printed)

    def test_an_unreadable_page_exits_zero_and_changes_nothing(self):
        code, env, out, printed = self.run_step([], {"HIRING_AUTO_CLOSE": ""}, "--output", "HIRING_AUTO_CLOSE", broken=True)
        self.assertEqual((code, env), (0, []))
        self.assertIn("unreadable", printed)

    def test_the_counts_line_carries_names_only(self):
        _, _, _, printed = self.run_step([f"BILLS_CARD_BLOCK_ID {GOOD}"], {"BILLS_CARD_BLOCK_ID": GOOD.upper()})
        self.assertNotIn(GOOD, printed.replace(f"::add-mask::{GOOD}", ""))
        self.assertIn('"matched": 1', printed)


if __name__ == "__main__":
    unittest.main()
