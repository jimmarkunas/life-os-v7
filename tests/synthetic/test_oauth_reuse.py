"""LIFE-379 / GWT-T08-03: the reuse review is real (every referenced file exists), complete (custody owner for every consumer), honest (unverified stays unverified, gaps are named) and requests nothing new."""
import re
import unittest
from pathlib import Path

from lifeos.synthetic import oauth_reuse as R

ROOT = Path(__file__).resolve().parents[2]
SHA = re.compile(r"^[0-9a-f]{40}$")


class Review(unittest.TestCase):
    def test_every_consumer_is_referenced_by_a_real_path_and_a_git_revision(self):
        self.assertRegex(R.REVISION, SHA)
        ids = set()
        for c in R.CONSUMERS:
            with self.subTest(consumer=c["id"]):
                self.assertTrue((ROOT / c["path"]).is_file(), c["path"])
                self.assertRegex(c["blob"], SHA)
                if c["supplied_by"]:
                    self.assertTrue((ROOT / c["supplied_by"]).is_file(), c["supplied_by"])
                    self.assertRegex(c["supplied_by_blob"], SHA)
                self.assertIn(c["status"], R.STATUSES)
                self.assertTrue(c["custody_owner"].strip() and c["custody_evidence"].strip())
                ids.add(c["id"])
        self.assertEqual(len(ids), len(R.CONSUMERS))

    def test_the_actual_current_consumers_are_all_there(self):
        ids = {c["id"] for c in R.CONSUMERS}
        self.assertTrue({"v7_gmail_reader", "v7_drive_close_service", "drive_consent_tool", "openclaw_gmail", "openclaw_drive", "openclaw_calendar", "v7_calendar_reader"} <= ids)

    def test_credential_names_are_names_only_and_the_v7_ones_really_are_read_by_the_code(self):
        by_id = {c["id"]: c for c in R.CONSUMERS}
        for c in R.CONSUMERS:
            for name in c["credentials"]:
                if "/" not in name:
                    self.assertEqual(name, name.lower())                                                                # env names in lowercase here; a Bitwarden id keeps its real spelling

                self.assertNotRegex(name, r"[A-Za-z0-9+/]{30,}")
        gmail = (ROOT / by_id["v7_gmail_reader"]["path"]).read_text()
        for name in by_id["v7_gmail_reader"]["credentials"]:
            self.assertIn(name.upper(), gmail)
        gcal = (ROOT / by_id["v7_calendar_reader"]["path"]).read_text()
        for name in by_id["v7_calendar_reader"]["credentials"]:
            self.assertIn(name.upper(), gcal)
        openclaw = (ROOT / by_id["openclaw_gmail"]["path"]).read_text()
        for name in by_id["openclaw_gmail"]["credentials"]:
            self.assertIn(name, openclaw)

    def test_what_was_not_seen_is_marked_unverified_and_not_guessed(self):
        by_id = {c["id"]: c for c in R.CONSUMERS}
        self.assertEqual(by_id["v7_calendar_reader"]["status"], "custody_unverified")
        self.assertEqual(by_id["openclaw_calendar"]["status"], "custody_unverified")
        self.assertFalse((ROOT / "lifeos/edge/oracle/openclaw-drive.mjs").exists())                                    # the gap below is only true while these stay off the repo
        self.assertFalse((ROOT / "lifeos/edge/oracle/openclaw-gcal.mjs").exists())

    def test_unsafe_or_duplicate_paths_stay_named_gaps_with_an_owner_and_a_proof(self):
        ids = {g["id"] for g in R.GAPS}
        self.assertTrue({"gmail_credential_in_two_stores", "two_drive_grants", "one_oauth_client_for_gmail_and_drive", "no_tenant_b_consumer"} <= ids)
        for g in R.GAPS:
            self.assertEqual(g["status"], "named_gap")
            self.assertTrue(g["owner"] and g["proof_needed"].strip() and g["summary"].strip())

    def test_nothing_new_is_requested(self):
        self.assertIs(R.NEW_GOOGLE_PROJECT_REQUESTED, False)
        self.assertIs(R.NEW_REFRESH_TOKEN_REQUESTED, False)


if __name__ == "__main__":
    unittest.main()
