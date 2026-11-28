"""The fresh held-out acceptance sets are frozen before any gate code or query: they cannot change to make the retrieval pass."""

import hashlib
import json
import pathlib
import unittest

PATH = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "unified_memory" / "acceptance_heldout.json"
FROZEN_SHA256 = "312bb40c6d40539c8ea263c32e141ae804330fb886577c0192234c79db46e332"


class Frozen(unittest.TestCase):
    def test_the_fixture_is_byte_identical_to_the_frozen_version(self):
        self.assertEqual(hashlib.sha256(PATH.read_bytes()).hexdigest(), FROZEN_SHA256)

    def test_the_seed_fits_the_row_limit_and_the_two_sets_cover_disjoint_facts(self):
        doc = json.loads(PATH.read_text())
        self.assertLessEqual(len(doc["seed"]["facts"]) + len(doc["seed"]["tombstones"]), doc["seed"]["max_rows"])
        targets = {n: {a["ref"] for q in qs for a in q["expect"]["answers"]} for n, qs in doc["sets"].items()}
        self.assertFalse(targets["H1"] & targets["H2"])
        for name, qs in doc["sets"].items():
            self.assertGreaterEqual(len(qs), 30, name)
            quiet = sum(not q["expect"]["answers"] for q in qs)
            self.assertGreaterEqual(quiet * 4, len(qs), name)


if __name__ == "__main__":
    unittest.main()
