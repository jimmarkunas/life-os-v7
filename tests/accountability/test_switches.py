"""LIFE-918: the six cutover rows are registered, default off and fail closed. Nothing here reads Notion or changes behavior."""
import unittest
from pathlib import Path

from lifeos.accountability import switches
from lifeos.platform import settings

ROOT = Path(__file__).resolve().parents[2]
BLOCK = "[removed]"
NAMES = ("MEJ_SPRINT_AUTOMATION", "V3_WEEK_VIEW", "V2_QUARTER_SHADOW", "V3_PREVIEW_BLOCK_ID", "V2_PROMOTION", "V2_OFFICIAL")


class Registration(unittest.TestCase):
    def test_every_row_is_movable_and_none_is_a_safety_name(self):
        for name in NAMES:
            self.assertTrue(settings.movable(name), name)
            self.assertNotIn(name, settings.SAFETY)

    def test_the_preview_block_is_the_only_address_row(self):
        self.assertEqual([n for n in NAMES if settings.is_address(n)], ["V3_PREVIEW_BLOCK_ID"])

    def test_every_row_is_in_the_accountability_config_registry(self):
        text = (ROOT / "lifeos/accountability/config.txt").read_text()
        for name in NAMES:
            self.assertIn(name + " ", text)


class FailClosed(unittest.TestCase):
    def test_everything_is_off_with_nothing_set(self):
        for name in switches.SWITCHES:
            self.assertFalse(switches.on(name, {}), name)
        self.assertEqual(switches.preview_block({}), "")
        self.assertEqual(switches.snapshot({}), {**{n: False for n in switches.SWITCHES}, "preview_block": False})

    def test_only_exactly_on_turns_a_switch_on(self):
        for value, want in (("on", True), (" ON ", True), ("On", True), ("off", False), ("", False), ("true", False), ("1", False), ("yes", False), ("on please", False), (None, False)):
            self.assertEqual(switches.on("V3_WEEK_VIEW", {"V3_WEEK_VIEW": value}), want, repr(value))

    def test_an_unknown_name_is_never_on(self):
        self.assertFalse(switches.on("V7_LIVE", {"V7_LIVE": "on"}))
        self.assertFalse(switches.on("NOT_A_ROW", {"NOT_A_ROW": "on"}))

    def test_an_unreadable_environment_is_off(self):
        self.assertFalse(switches.on("V2_OFFICIAL", object()))
        self.assertEqual(switches.preview_block(object()), "")

    def test_the_preview_block_must_be_a_valid_id(self):
        self.assertEqual(switches.preview_block({"V3_PREVIEW_BLOCK_ID": BLOCK.upper()}), BLOCK)
        self.assertEqual(switches.preview_block({"V3_PREVIEW_BLOCK_ID": "[removed]"}), BLOCK)
        for bad in ("not-an-id", "4203", "https://example.invalid", "on"):
            self.assertEqual(switches.preview_block({"V3_PREVIEW_BLOCK_ID": bad}), "", bad)

    def test_the_rows_are_independent(self):
        env = {"MEJ_SPRINT_AUTOMATION": "on"}
        self.assertTrue(switches.on("MEJ_SPRINT_AUTOMATION", env))
        for name in ("V3_WEEK_VIEW", "V2_QUARTER_SHADOW", "V2_PROMOTION", "V2_OFFICIAL"):
            self.assertFalse(switches.on(name, env), name)


if __name__ == "__main__":
    unittest.main()
