"""Production switches in the repository (Jim, Oct 24 2026): the file decides, a repository variable "off" is Jim's kill switch, "on" turns one on by hand."""
import json
import unittest
from unittest import mock

from lifeos.platform import switches


class Switches(unittest.TestCase):
    def test_the_file_decides_unless_the_variable_says_off_or_on(self):
        with mock.patch.object(switches, "FILE", mock.Mock(read_text=lambda: json.dumps({"A": {"state": "on"}, "B": {"state": "off"}}))):
            self.assertEqual([switches.state("A", e) for e in ({}, {"A": ""}, {"A": "typo"}, {"A": "off"}, {"A": " OFF "}, {"A": "on"})], [True, True, True, False, False, True])
            self.assertEqual([switches.state("B", e) for e in ({}, {"B": "on"})], [False, True])
            self.assertFalse(switches.state("C", {}))                                      # a switch the file does not name is off

    def test_an_unreadable_file_is_off(self):
        for broken in (OSError("gone"), ValueError("not json")):
            with mock.patch.object(switches, "FILE", mock.Mock(read_text=mock.Mock(side_effect=broken))):
                self.assertFalse(switches.state("A", {}))

    def test_every_switch_states_its_bounds_and_only_the_known_ones_exist(self):
        doc = json.loads(switches.FILE.read_text())
        self.assertEqual(sorted(doc), ["BOARDS_BACKFILL", "HIRING_AUTO_CLOSE"])
        for name, entry in doc.items():
            self.assertIn(entry["state"], ("on", "off"), name)
            self.assertGreater(len(entry["bounds"]), 40, name)                              # a switch is never on without its written bounds
