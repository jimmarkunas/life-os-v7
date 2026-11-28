"""The behavioral acceptance cases are frozen: their expectations cannot change to make the retrieval code pass."""

import hashlib
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = ROOT / "tests" / "fixtures" / "unified_memory" / "behavioral.json"
FROZEN_SHA256 = "9ad81572c268d3642a1c8d68be9dd349ad34957ed3de64517d08c4b211c500b4"


class Frozen(unittest.TestCase):
    def test_the_fixture_is_byte_identical_to_the_frozen_version(self):
        self.assertEqual(hashlib.sha256(PATH.read_bytes()).hexdigest(), FROZEN_SHA256)

    def test_the_case_mix_meets_the_acceptance_floor(self):
        cases = json.loads(PATH.read_text())["cases"]
        n = lambda prefix: sum(c["id"].startswith(prefix) for c in cases)  # noqa: E731
        self.assertGreaterEqual(n("B"), 5)
        self.assertEqual((n("C"), n("A"), n("R"), n("M")), (3, 3, 3, 2))
        self.assertGreaterEqual(sum(c["split"] == "heldout" for c in cases), 9)

    def test_everything_is_fabricated(self):
        text = PATH.read_text()
        for pattern in (r"[\w.+-]+@[\w-]+\.[\w.]+", r"https?://", r"\b[A-Fa-f0-9]{32,}\b"):
            self.assertIsNone(re.search(pattern, text), pattern)
        doc = json.loads(text)
        self.assertTrue(all(s["id"].startswith("FAKE-") for s in doc["sources"]))


if __name__ == "__main__":
    unittest.main()
