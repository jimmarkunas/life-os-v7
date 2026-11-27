"""The live behavioral and structured expectations are frozen before any live query: they cannot change to make the retrieval code pass."""

import hashlib
import json
import pathlib
import unittest

FIX = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "unified_memory"
FROZEN = {
    "behavioral_live.json": "a91402bce6e240ab665a475b0cc489d9e34f6e3ca439ee72bb829d0e9e7352f4",
    "structured_live.json": "4a84e5a4c4d783e450376bcc39df9fcd12f2aeea65980e4552bdc4f3beeee8a5",
}


class Frozen(unittest.TestCase):
    def test_each_fixture_is_byte_identical_to_the_frozen_version(self):
        for name, digest in FROZEN.items():
            self.assertEqual(hashlib.sha256((FIX / name).read_bytes()).hexdigest(), digest, name)

    def test_the_seed_fits_the_authorized_row_limit_and_the_denominators_are_explicit(self):
        b = json.loads((FIX / "behavioral_live.json").read_text())
        rows = len(b["seed"]["facts"]) + len(b["seed"]["tombstones"])
        self.assertLessEqual(rows, b["seed"]["max_rows"])
        self.assertEqual(b["seed"]["max_rows"], 24)
        self.assertGreaterEqual(sum(q["split"] == "heldout" for q in b["queries"]), 12)
        s = json.loads((FIX / "structured_live.json").read_text())
        self.assertEqual((len(s["covered_ids"]), len(s["not_covered_ids"])), (19, 6))
        self.assertEqual(len(s["eligible_question_ids"]) + len(s["not_eligible_question_ids"]), 20)


if __name__ == "__main__":
    unittest.main()
