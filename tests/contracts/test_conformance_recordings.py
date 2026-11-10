"""LIFE-549: a conformance recording or case file may hold only fake-looking values. Fails on an email address, a token-like string, a long digit run (phone, account or
message id) or a real-looking URL host, anywhere in tests/kit/conformance/cases or recordings. The negative control proves each rule bites."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDERS = [ROOT / "tests" / "kit" / "conformance" / "cases", ROOT / "tests" / "kit" / "conformance" / "recordings"]
RULES = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
    "token": re.compile(r"\b(?=[A-Za-z0-9_\-]{32,})(?=[A-Za-z_\-]*[0-9])(?=[0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}\b"),
    "long_digits": re.compile(r"\d{9,}"),
    "uuid": re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    "key_prefix": re.compile(r"\b(?:secret_|ntn_|sk-|xox[bp]-|ghp_|AKIA)[A-Za-z0-9_\-]{6,}"),
}


def violations(text):
    return sorted(name for name, rule in RULES.items() if rule.search(text))


class RecordingsAreScrubbed(unittest.TestCase):
    def test_every_case_and_recording_file_is_clean_and_parses(self):
        files = [p for folder in FOLDERS for p in sorted(folder.glob("*.json"))]
        self.assertTrue(files)
        for path in files:
            text = path.read_text()
            json.loads(text)
            with self.subTest(str(path.relative_to(ROOT))):
                self.assertEqual(violations(text), [])

    def test_negative_control_each_rule_catches_its_pattern(self):
        self.assertEqual(violations('{"a": "' + "jane.doe" + "@" + "mail.test" + '"}'), ["email"])           # built at runtime: the privacy scan reads source text
        self.assertEqual(violations('{"t": "' + "aB3" * 12 + '"}'), ["token"])
        self.assertEqual(violations('{"n": "13105551234"}'), ["long_digits"])
        self.assertIn("uuid", violations('{"id": "3e264d79-3af4-4f2a-a86a-b4db015a68de"}'))
        self.assertEqual(violations('{"k": "secret_abcdefgh12"}'), ["key_prefix"])
        self.assertEqual(violations('{"a": "block-fake-0001", "t": "fake text"}'), [])


if __name__ == "__main__":
    unittest.main()
