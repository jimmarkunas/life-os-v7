"""every job that reads the Google calendar may request a GitHub OIDC token (`id-token: write`), and the retired key is
loaded nowhere.
"""

import pathlib
import re
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parents[2] / ".github"
CALENDAR_READER = re.compile(r"GCAL_CALENDAR_ID|FOLLOWUP_GCAL_")


@unittest.skipIf(yaml is None, "PyYAML not installed")
class CalendarKeyless(unittest.TestCase):
    def jobs(self):
        for path in sorted((ROOT / "workflows").glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            for job, body in (doc.get("jobs") or {}).items():
                yield path.stem, job, doc, body, yaml.safe_dump(body)

    def test_every_job_that_reads_the_calendar_has_id_token_write(self):
        readers = [(w, j, doc, body) for w, j, doc, body, text in self.jobs() if CALENDAR_READER.search(text)]
        self.assertGreaterEqual(len(readers), 6)
        for workflow, job, doc, body in readers:
            allowed = body.get("permissions", doc.get("permissions")) or {}
            self.assertEqual(allowed.get("id-token"), "write", (workflow, job))

    def test_no_other_job_asks_for_id_token(self):
        for workflow, job, doc, body, text in self.jobs():
            has = (body.get("permissions") or {}).get("id-token") == "write" or (
                "permissions" not in body and (doc.get("permissions") or {}).get("id-token") == "write"
            )
            if has:
                self.assertRegex(text, CALENDAR_READER, (workflow, job))

    def test_the_retired_key_is_loaded_nowhere(self):
        offenders = [
            str(p.relative_to(ROOT)) for p in ROOT.rglob("*.yml") if "GCAL_SERVICE_ACCOUNT_JSON" in p.read_text()
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
