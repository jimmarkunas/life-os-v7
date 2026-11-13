"""LIFE-648: one unreadable mail source no longer fails the hiring-outcomes stage or discards what the readable sources found (the LIFE-646 shape, for outcome mail)."""
import json
import unittest
from datetime import timedelta, timezone
from unittest import mock

from lifeos.hiring import outcomes, render
from tests.hiring import test_hiring as hiring
from tests.hiring.support import FakeStore
from tests.hiring import test_outcomes as base                # module import: a class imported by name would be collected and run here again
from tests.hiring.test_outcomes import NOW, REJECTION, UBER, WALMART, msg
from tests.recruiters.fakes import ReadOnlyOutlook

STORED = ("v7_hiring_outcomes", 1)
OLD_UBER = {"kind": "REJECTED", "received": (NOW - timedelta(days=9)).isoformat(), "match": "role", "exact": False}


def outlook_rejection(days_ago=2):
    return ReadOnlyOutlook(inbox=[{"id": "o1", "receivedDateTime": (NOW - timedelta(days=days_ago)).isoformat(), "subject": "Your Walmart application", "body": {"content": REJECTION},
                                   "from": {"emailAddress": {"name": "Walmart Talent", "address": "jobs@example.com"}}}])


class PartialSourceTests(unittest.TestCase):
    Tables, PIPELINE, go, REC = base.EntryPointTests.Tables, base.EntryPointTests.PIPELINE, base.EntryPointTests.go, base.EntryPointTests.REC

    def accepted(self, outcome_map, scanned_hours_ago=30):
        tables = self.Tables()
        tables.rows[STORED] = json.dumps({"schema": 1, "taken_at": NOW.isoformat(), "scanned_at": (NOW - timedelta(hours=scanned_hours_ago)).isoformat(), "outcomes": outcome_map})
        return tables

    def stored(self, tables):
        return json.loads(tables.rows[STORED])

    def test_one_outlook_account_down_still_saves_what_gmail_found_and_names_the_unread_source(self):
        for down in (None, base.BrokenOutlook()):
            code, counts, err, _, tables, _ = self.go(self.REC, accounts=[("work", down)])
            self.assertEqual((code, err, counts["status"], counts["saved"], counts["sources_failed"], counts["unread"], counts["matched"]), (0, "", "partial", 1, 1, ["Outlook work"], 1))
            record = self.stored(tables)
            self.assertEqual((list(record["outcomes"]), record["unread"], record["scanned_at"]), ([WALMART["key"]], ["Outlook work"], NOW.astimezone(timezone.utc).isoformat()))

    def test_gmail_down_and_outlook_read_saves_the_outlook_finds_and_names_gmail(self):
        code, counts, err, _, tables, _ = self.go([], broken=True, accounts=[("work", outlook_rejection())])
        self.assertEqual((code, err, counts["status"], counts["unread"], counts["saved"]), (0, "", "partial", ["Gmail"], 1))
        self.assertEqual((list(self.stored(tables)["outcomes"]), self.stored(tables)["unread"]), ([WALMART["key"]], ["Gmail"]))

    def test_nothing_is_concluded_from_the_unread_source_so_prior_outcomes_stay(self):
        tables = self.accepted({UBER["key"]: OLD_UBER})
        code, counts, *_ = self.go([], tables=tables, accounts=[("work", base.BrokenOutlook())])           # Gmail read fine and holds no outcome mail
        self.assertEqual((code, counts["status"], counts["matched"]), (0, "partial", 0))
        self.assertEqual(self.stored(tables)["outcomes"], {UBER["key"]: OLD_UBER})                  # not turned into "no outcome"

    def test_new_finds_are_laid_over_prior_outcomes_and_a_newer_accepted_one_is_not_replaced_by_an_older_find(self):
        newer = {"kind": "OFFER", "received": (NOW - timedelta(days=1)).isoformat(), "match": "role", "exact": False}
        tables = self.accepted({UBER["key"]: OLD_UBER, WALMART["key"]: newer})                       # Walmart's find below is 3 days old: the accepted offer is newer
        self.go(self.REC, tables=tables, accounts=[("work", None)])
        self.assertEqual(self.stored(tables)["outcomes"], {UBER["key"]: OLD_UBER, WALMART["key"]: newer})
        older = {"kind": "REJECTED", "received": (NOW - timedelta(days=20)).isoformat(), "match": "company", "exact": False}
        tables = self.accepted({UBER["key"]: OLD_UBER, WALMART["key"]: older})
        self.go(self.REC, tables=tables, accounts=[("work", None)])
        saved = self.stored(tables)["outcomes"]
        self.assertEqual((saved[UBER["key"]], saved[WALMART["key"]]["match"]), (OLD_UBER, "company"))

    def test_a_message_that_could_not_be_read_in_a_readable_listing_keeps_the_finds_and_names_that_source(self):
        records = self.REC + [msg(subject="Another", body="Hello", mid="m2", days_ago=2)]
        code, counts, err, _, tables, _ = self.go(records, unreadable=("m2",))
        self.assertEqual((code, err, counts["status"], counts["unreadable"], counts["unread"], counts["saved"]), (0, "", "partial", 1, ["Gmail"], 1))
        self.assertEqual((list(self.stored(tables)["outcomes"]), self.stored(tables)["unread"]), ([WALMART["key"]], ["Gmail"]))

    def test_every_source_unread_still_fails_closed_and_saves_nothing(self):
        tables = self.accepted({UBER["key"]: OLD_UBER})
        before = tables.rows[STORED]
        code, counts, err, _, tables, _ = self.go(self.REC, tables=tables, broken=True, accounts=[("work", None)])
        self.assertEqual((code, counts["status"], counts["saved"], err), (1, "degraded", 0, "HIRING-OUTCOMES FAILED: OUTCOMES_DEGRADED_SOURCE_INCOMPLETE"))
        self.assertEqual(tables.rows[STORED], before)

    def test_a_dry_run_with_a_source_down_writes_nothing_and_creates_no_table(self):
        code, counts, _, _, tables, _ = self.go(self.REC, live=False, accounts=[("work", None)])
        self.assertEqual((code, counts["status"], counts["matched"], counts["saved"]), (0, "partial", 1, 0))
        self.assertNotIn(STORED, tables.rows)
        self.assertNotIn("INSERT", tables.statements)

    def test_a_fully_read_scan_clears_the_unread_list_and_replaces_the_outcomes_as_before(self):
        tables = self.accepted({UBER["key"]: OLD_UBER})
        _, counts, *_ = self.go(self.REC, tables=tables, accounts=[("work", ReadOnlyOutlook())])
        record = self.stored(tables)
        self.assertEqual((counts["status"], record["unread"], list(record["outcomes"])), ("fresh", [], [WALMART["key"]]))

    def test_the_next_scan_is_due_in_twelve_hours_not_the_next_hour(self):
        store = FakeStore()
        with mock.patch.object(outcomes, "STORE", store), mock.patch.object(outcomes, "_exists", lambda c: True), mock.patch.object(outcomes.hiring_snapshot, "load", lambda c: base.RunTests.PRIOR):
            first = outcomes.run(0, True, environ={}, gmail=base.FakeGmail(list(self.REC)), outlook_accounts=[("work", None)], connection=object(), now=NOW)
            self.assertEqual((first["status"], store.saves, store.row["unread"]), ("partial", 1, ["Outlook work"]))
            for hours, status in ((1, "not_due"), (11, "not_due"), (13, "partial")):
                gmail = base.FakeGmail(list(self.REC))
                counts = outcomes.run(0, True, environ={}, gmail=gmail, outlook_accounts=[("work", None)], connection=object(), now=NOW + timedelta(hours=hours))
                self.assertEqual((counts["status"], bool(gmail.queries)), (status, status != "not_due"), hours)

    def test_a_stored_record_without_an_unread_list_loads_as_before_and_a_bad_one_is_refused(self):
        store = FakeStore({"schema": 1, "scanned_at": NOW.isoformat(), "outcomes": {}})
        with mock.patch.object(outcomes, "STORE", store), mock.patch.object(outcomes, "_exists", lambda c: True):
            self.assertEqual(outcomes.load(object()), ({}, NOW.isoformat(), {}, []))
            store.row["unread"] = "Gmail"
            with self.assertRaises(outcomes.OutcomeError):
                outcomes.load(object())


class CardTests(unittest.TestCase):
    """The Hiring card reads the record: DEGRADED naming the unread source, not fresh, and not OUTCOME_MAIL_STALE while the other sources keep succeeding."""
    consumer = hiring.OutcomeConsumerTests

    def card(self, unread, scanned=None):
        """-> (the status line the stage rendered on its last run, its counts)."""
        lines, real = [], render.render

        def spy(*args, **kwargs):
            out = real(*args, **kwargs)
            lines.append(out[0])
            return out
        with mock.patch.object(hiring.stage.render, "render", spy):
            _, _, counts = self.consumer.seeded(self, lambda conn: (self.consumer.FOUND, scanned or self.consumer.SCANNED, {}, unread))
        return lines[-1], counts

    def test_a_partial_record_shows_degraded_with_the_unread_source_named_in_fixed_words(self):
        text, counts = self.card(["Gmail", "Outlook work"])
        self.assertEqual((counts["status"], counts["reasons"], counts["mail_outcomes"]), ("degraded", ["OUTCOME_MAIL_PARTIAL"], 1))      # the accepted outcome is still shown
        self.assertIn("outcome mail partly read (Gmail, Outlook work unread)", text)

    def test_a_partial_record_is_not_stale_while_scans_keep_saving(self):
        self.assertEqual(self.card(["Outlook work"], hiring.NOW.astimezone(hiring.timezone.utc).isoformat())[1]["reasons"], ["OUTCOME_MAIL_PARTIAL"])

    def test_an_old_partial_record_is_both_partial_and_stale(self):
        stale = (hiring.NOW - timedelta(hours=19)).astimezone(hiring.timezone.utc).isoformat()
        self.assertEqual(self.card(["Outlook work"], stale)[1]["reasons"], ["OUTCOME_MAIL_PARTIAL", "OUTCOME_MAIL_STALE"])

    def test_only_fixed_source_names_ever_reach_the_card(self):
        text, _ = self.card(["pat@example.com", "Outlook Work Name", 7, "Gmail"])
        self.assertIn("(Gmail unread)", text)
        self.assertNotIn("example.com", text)
        self.assertEqual(self.card(["pat@example.com"])[1]["status"], "fresh")


if __name__ == "__main__":
    unittest.main()
