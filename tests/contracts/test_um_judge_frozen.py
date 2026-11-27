"""The J1 judge set is frozen before the judge prompt runs on it: it cannot change to make the judge pass."""

import hashlib
import json
import pathlib
import unittest

PATH = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "unified_memory" / "acceptance_judge.json"
FROZEN_SHA256 = "f358a2726c38fc09111e9f5a57805baed72f228858a993ff75f6262a46548857"


class Frozen(unittest.TestCase):
    def test_the_fixture_is_byte_identical_to_the_frozen_version(self):
        self.assertEqual(hashlib.sha256(PATH.read_bytes()).hexdigest(), FROZEN_SHA256)

    def test_the_set_is_large_enough_to_measure_silence(self):
        qs = json.loads(PATH.read_text())["sets"]["J1"]
        kinds = [q["kind"] for q in qs]
        self.assertGreaterEqual(kinds.count("hard_negative"), 45)
        self.assertGreaterEqual(kinds.count("absent"), 20)
        self.assertEqual(len({q["id"] for q in qs}), len(qs))

    def test_every_expected_answer_is_a_current_record_of_the_querys_tenant(self):
        doc = json.loads(PATH.read_text())
        src = {s["id"]: s for s in doc["registry"]["sources"]}
        for q in doc["sets"]["J1"]:
            for a in q["expect"]["answers"]:
                self.assertEqual((src[a["ref"]]["status"], src[a["ref"]]["tenant"]), ("current", q["tenant"]), q["id"])
            self.assertEqual(q["expect"]["abstain"], not q["expect"]["answers"], q["id"])


if __name__ == "__main__":
    unittest.main()
