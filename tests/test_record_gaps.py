"""LIFE-666: the read-only record-gap lister, worked by hand on a saved export (no network, no credentials, no clock)."""
import io
import json
import os
import tempfile
import unittest

from lifeos.sources.devtools import record_gaps as rg

NOW = "2026-11-13T20:00:00Z"
EXPORT = {
    "LIFE-1": [["2026-11-13T12:00:00Z", "[claim] session-a"], ["2026-11-13T12:30:00Z", "progress"], ["2026-11-13T14:00:00Z", "done"]],      # 30 min (short), then 90 min (open), then 360 min to NOW (open)
    "LIFE-2": [["2026-11-13T12:00:00Z", "[waiting on] CI"], ["2026-11-13T19:30:00Z", "[claim] session-b"]],                                  # 450 min explained, then 30 min (short)
    "LIFE-3": [],
}


def run(*extra):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(EXPORT, handle)
    out = io.StringIO()
    try:
        rg.main([handle.name, "--now", NOW, *extra], out)
    finally:
        os.unlink(handle.name)
    return out.getvalue().splitlines()


class RecordGaps(unittest.TestCase):
    def test_unexplained_and_explained_stretches_are_listed_in_central_time(self):
        self.assertEqual(run(), ["LIFE-1 Nov 13 2026, 6:30 AM CT 90 min UNEXPLAINED", "LIFE-1 Nov 13 2026, 8:00 AM CT 360 min UNEXPLAINED",
                                 "LIFE-2 Nov 13 2026, 6:00 AM CT 450 min EXPLAINED", "LIFE-3 NO_ENTRIES", "unexplained stretches: 2"])

    def test_a_longer_limit_hides_the_shorter_stretches(self):
        self.assertEqual(run("--minutes", "400"), ["LIFE-2 Nov 13 2026, 6:00 AM CT 450 min EXPLAINED", "LIFE-3 NO_ENTRIES", "unexplained stretches: 0"])

    def test_the_text_of_an_entry_is_never_printed(self):
        self.assertNotIn("session-a", "".join(run()))


if __name__ == "__main__":
    unittest.main()
