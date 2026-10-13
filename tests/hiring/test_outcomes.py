"""D187: rejection and offer mail for the companies in the Hiring Pipeline is found by fixed wording, matched to one opportunity or not at all, and shown as a line to confirm."""
import json
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from lifeos.hiring import compile as C, models as M, outcomes, render
from lifeos.platform import mail
from lifeos.platform.gmail import GmailError

CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=CT)


def msg(subject="Your application", body="", sender="Recruiting Team", address="jobs@example.invalid", days_ago=3, mid="m1"):
    return {"provider": "gmail", "id": mid, "received": (NOW - timedelta(days=days_ago)).isoformat(), "sender_name": sender, "sender_address": address,
            "subject": subject, "body": body, "folder": "INBOX", "markers": {}, "url": ""}


def row(company, role, event_days_ago=None, applied="2026-09-01"):
    return {"key": "|".join(outcomes._tokens(company)) + "|" + "|".join(sorted(outcomes._tokens(role))), "company": company, "role": role,
            "event_start": (NOW - timedelta(days=event_days_ago)).isoformat() if event_days_ago is not None else None, "applied_on": applied}


WALMART = row("Walmart", "Senior Manager, Operations and Implementation", 35)
SMITH = row("Smith", "Senior Project Manager", 35)
UBER = row("Uber", "Technical Project Manager", 14)
REJECTION = "Thank you for interviewing. Unfortunately we will not be moving forward with your candidacy at Walmart."


class WordingTests(unittest.TestCase):
    def test_rejection_and_offer_wording_is_recognised_and_ordinary_mail_is_not(self):
        for text in ("We will not be moving forward with your application.", "We have decided to move forward with other candidates.", "We regret to inform you that the position has been filled.",
                     "You were not selected for this role.", "We are pursuing other candidates at this time."):
            self.assertEqual(outcomes.kind_of(text), outcomes.REJECTED, text)
        for text in ("We are pleased to offer you the role.", "Please find your offer letter attached.", "We would like to extend an offer of employment."):
            self.assertEqual(outcomes.kind_of(text), outcomes.OFFER, text)
        for text in ("Thanks for applying - we will be in touch.", "Your interview is confirmed for Thursday.", "Move forward with your application by completing the assessment.", "Jobs like this one"):
            self.assertIsNone(outcomes.kind_of(text), text)

    def test_the_company_must_appear_in_order_in_the_sender_subject_domain_or_opening_of_the_body(self):
        self.assertTrue(outcomes.mentions(msg(subject="Update from Walmart Global Tech"), "Walmart"))
        self.assertTrue(outcomes.mentions(msg(sender="Walmart Talent"), "Walmart Inc."))
        self.assertTrue(outcomes.mentions(msg(address="no-reply@walmart.com"), "Walmart"))
        self.assertTrue(outcomes.mentions(msg(body="Dear Jim, thank you for your interest in Smith Consulting."), "Smith Consulting"))
        self.assertFalse(outcomes.mentions(msg(subject="Update", body="Consulting at Smith"), "Smith Consulting"))
        self.assertFalse(outcomes.mentions(msg(subject="Unrelated"), "Walmart"))
        self.assertFalse(outcomes.mentions(msg(subject="Hello"), "ab"))                              # too short to mean anything


class FindTests(unittest.TestCase):
    def test_a_rejection_after_the_interview_is_matched_to_that_row_and_marks_a_role_match(self):
        found, stats = outcomes.find([msg(subject="Your application to Walmart", body=REJECTION + " Senior Manager, Operations and Implementation role.", days_ago=3)], [WALMART, SMITH, UBER])
        self.assertEqual(list(found), [WALMART["key"]])
        self.assertEqual((found[WALMART["key"]]["kind"], found[WALMART["key"]]["match"]), (outcomes.REJECTED, "role"))
        self.assertEqual((stats["outcome_messages"], stats["ambiguous"], stats["unmatched"]), (1, 0, 0))

    def test_a_company_only_match_is_marked_as_possible(self):
        found, _ = outcomes.find([msg(subject="Walmart application update", body=REJECTION, days_ago=3)], [WALMART])
        self.assertEqual(found[WALMART["key"]]["match"], "company")

    def test_mail_older_than_the_rows_last_evidence_is_not_an_outcome_for_it(self):
        found, stats = outcomes.find([msg(subject="Walmart", body=REJECTION, days_ago=40)], [WALMART])          # the interview was 35 days ago
        self.assertEqual((found, stats["unmatched"]), ({}, 1))

    def test_a_message_naming_two_pipeline_companies_or_two_unreadable_roles_is_never_guessed(self):
        both, stats = outcomes.find([msg(subject="Walmart and Uber", body=REJECTION)], [WALMART, UBER])
        self.assertEqual((both, stats["ambiguous"]), ({}, 1))
        twin = row("Uber", "Senior Program Manager", 14)
        none_named, stats = outcomes.find([msg(subject="Uber application", body=REJECTION)], [UBER, twin])
        self.assertEqual((none_named, stats["ambiguous"]), ({}, 1))
        named, _ = outcomes.find([msg(subject="Uber application", body=REJECTION + " Technical Project Manager.")], [UBER, twin])
        self.assertEqual(list(named), [UBER["key"]])

    def test_the_newest_outcome_mail_wins_and_an_offer_beats_a_rejection_in_one_message(self):
        found, _ = outcomes.find([msg(subject="Uber", body="We are pleased to offer you the role.", days_ago=5, mid="a"), msg(subject="Uber", body=REJECTION, days_ago=2, mid="b")], [UBER])
        self.assertEqual(found[UBER["key"]]["kind"], outcomes.REJECTED)
        self.assertEqual(outcomes.kind_of("We are pleased to offer you the role. We will not be moving forward with other candidates."), outcomes.OFFER)

    def test_the_window_reaches_back_to_the_oldest_evidence_within_bounds(self):
        self.assertEqual(outcomes.window_days([UBER], NOW), 16)
        self.assertEqual(outcomes.window_days([WALMART], NOW), 37)
        self.assertEqual(outcomes.window_days([row("Old", "Role", 400)], NOW), outcomes.MAX_DAYS)
        self.assertEqual(outcomes.window_days([], NOW), outcomes.MIN_DAYS)
        self.assertEqual(outcomes.window_days([row("New", "Role", 1)], NOW), outcomes.MIN_DAYS)


class ShownTests(unittest.TestCase):
    """The pipeline says what to do; it never closes anything itself."""
    def compiled(self, outcome, parent=True, role="Senior Manager, Operations and Implementation"):
        parents = [{"id": "p1", "company": "Walmart", "role": role, "rounds": 1}] if parent else []
        applied = [{"company": "Walmart", "role": role, "applied_on": "2026-09-01"}]
        events = [{"id": "e1", "title": "Interview Walmart", "start": (NOW - timedelta(days=35)).isoformat()}]
        return C.compile_rows(applied, parents, events, None, {"ledger": True, "parents": True, "calendar": True}, NOW, outcomes={WALMART["key"]: outcome} if outcome else None)

    def action(self, result):
        return [t for t, _ in render.row_cells(result["rows"][0], NOW)][2]

    def test_a_rejection_after_the_interview_says_to_confirm_and_retire_and_the_stage_is_untouched(self):
        result = self.compiled({"kind": "REJECTED", "received": (NOW - timedelta(days=3)).isoformat(), "match": "role"})
        self.assertEqual(self.action(result), f"Rejection email {(NOW - timedelta(days=3)).strftime('%b %-d')} · confirm, then move the Notes page to Retired")
        self.assertEqual(result["rows"][0]["stage"], "Interviewing")
        self.assertEqual(len(result["rows"]), 1)                                  # still listed: V7 closes nothing

    def test_a_company_only_rejection_is_possible_and_a_row_without_a_notes_page_says_to_untick_applied(self):
        result = self.compiled({"kind": "REJECTED", "received": (NOW - timedelta(days=3)).isoformat(), "match": "company"}, parent=False)
        self.assertTrue(self.action(result).startswith("Possible rejection email"))
        self.assertTrue(self.action(result).endswith("confirm, then untick Applied in the Ledger"))

    def test_an_offer_says_to_review_and_respond(self):
        result = self.compiled({"kind": "OFFER", "received": (NOW - timedelta(days=1)).isoformat(), "match": "role"})
        self.assertTrue(self.action(result).endswith("· review and respond"))
        self.assertTrue(self.action(result).startswith("Offer email"))

    def test_an_outcome_older_than_the_rows_last_evidence_is_ignored_and_the_row_ages_as_before(self):
        result = self.compiled({"kind": "REJECTED", "received": (NOW - timedelta(days=60)).isoformat(), "match": "role"})
        self.assertNotIn("outcome", result["rows"][0])
        self.assertEqual(self.action(result), "No news in 35 days · follow up?")

    def test_a_retired_page_still_closes_the_pursuit_whatever_the_mail_says(self):
        result = C.compile_rows([{"company": "Walmart", "role": "Senior Manager, Operations and Implementation", "applied_on": "2026-09-01"}],
                                [{"id": "p1", "company": "Walmart", "role": "Senior Manager, Operations and Implementation", "rounds": 1, "retired": True}], [], None,
                                {"ledger": True, "parents": True, "calendar": True}, NOW, outcomes={WALMART["key"]: {"kind": "REJECTED", "received": NOW.isoformat(), "match": "role"}})
        self.assertEqual((result["rows"], result["closed"]), ([], 1))


class FakeStore:
    table = "v7_hiring_outcomes"

    def __init__(self, doc=None):
        self.doc, self.saves = doc, 0

    def load(self, connection, key):
        return self.doc

    def save_verified(self, connection, key, snapshot, fail):
        self.doc, self.saves = json.loads(json.dumps(snapshot)), self.saves + 1


class FakeGmail:
    def __init__(self, records, broken=False):
        self.records, self.broken, self.queries = records, broken, []

    def list_ids_complete(self, query, limit, include_spam=False):
        self.queries.append(query)
        if self.broken:
            raise GmailError("GMAIL_LISTING_INCOMPLETE")
        return [r["id"] for r in self.records]

    def mail_record(self, message_id):
        r = next(x for x in self.records if x["id"] == message_id)
        return {"id": r["id"], "received_at": r["received"], "headers": {"from": f"{r['sender_name']} <{r['sender_address']}>", "subject": r["subject"]}, "body_text": r["body"], "label_ids": [], "thread": r["id"]}


class RunTests(unittest.TestCase):
    PRIOR = {"rows": [{"key": WALMART["key"], "company": "Walmart", "role": WALMART["role"], "event_start": WALMART["event_start"], "applied_on": "2026-09-01"},
                      {"key": UBER["key"], "company": "Uber", "role": UBER["role"], "event_start": UBER["event_start"], "applied_on": "2026-09-01"}]}

    def go(self, records=(), live=True, store=None, prior=PRIOR, broken=False, accounts=None):
        store = store or FakeStore()
        gmail = FakeGmail(list(records), broken)
        with mock.patch.object(outcomes, "STORE", store), mock.patch.object(outcomes, "_exists", lambda c: True), mock.patch.object(outcomes.hiring_snapshot, "load", lambda c: prior):
            return outcomes.run(0, live, environ={}, gmail=gmail, outlook_accounts=[] if accounts is None else accounts, connection=object(), now=NOW), store, gmail

    def test_a_scan_finds_the_rejection_saves_it_and_reports_counts_only(self):
        counts, store, gmail = self.go([msg(subject="Your Walmart application", body=REJECTION, days_ago=3)])
        self.assertEqual((counts["status"], counts["matched"], counts["rejected"], counts["saved"], counts["roster"]), ("fresh", 1, 1, 1, 2))
        self.assertEqual(list(store.doc["outcomes"]), [WALMART["key"]])
        self.assertIn('"Walmart"', gmail.queries[0])                                       # Gmail is asked only about the pipeline's companies
        self.assertIn("newer_than:37d", gmail.queries[0])
        text = json.dumps(counts)
        for private in ("Walmart", "Uber", "example.invalid", "Unfortunately"):
            self.assertNotIn(private, text)

    def test_a_dry_run_reads_and_saves_nothing(self):
        counts, store, _ = self.go([msg(subject="Walmart", body=REJECTION)], live=False)
        self.assertEqual((counts["matched"], counts["saved"], store.saves), (1, 0, 0))

    def test_a_scan_that_is_not_due_reads_no_mail(self):
        store = FakeStore({"schema": 1, "scanned_at": (NOW - timedelta(hours=3)).isoformat(), "taken_at": NOW.isoformat(), "outcomes": {}})
        counts, _, gmail = self.go([msg(subject="Walmart", body=REJECTION)], store=store)
        self.assertEqual((counts["status"], gmail.queries, store.saves), ("not_due", [], 0))
        old = FakeStore({"schema": 1, "scanned_at": (NOW - timedelta(hours=13)).isoformat(), "taken_at": NOW.isoformat(), "outcomes": {}})
        self.assertEqual(self.go([], store=old)[0]["status"], "fresh")

    def test_a_source_that_cannot_be_read_in_full_saves_nothing_and_fails_with_a_fixed_code(self):
        store = FakeStore({"schema": 1, "scanned_at": (NOW - timedelta(hours=30)).isoformat(), "taken_at": NOW.isoformat(), "outcomes": {WALMART["key"]: {"kind": "REJECTED", "received": NOW.isoformat(), "match": "role"}}})
        with self.assertRaises(outcomes.OutcomeError) as error:
            self.go([], store=store, broken=True)
        self.assertEqual(str(error.exception), "OUTCOMES_DEGRADED_SOURCE_INCOMPLETE")
        self.assertEqual(list(store.doc["outcomes"]), [WALMART["key"]])                   # the last accepted outcomes stay

    def test_an_empty_pipeline_reads_no_mail(self):
        counts, _, gmail = self.go([], prior={"rows": []})
        self.assertEqual((counts["status"], gmail.queries), ("empty", []))

    def test_the_stage_is_registered_and_the_hiring_stage_reads_what_it_found(self):
        from lifeos import run as entry
        self.assertEqual(entry.STAGES["hiring-outcomes"].target, ("lifeos.hiring.outcomes", "run"))
        self.assertEqual(mail.WINDOW_DAYS, 14)                                           # the Recruiters scan's own window is unchanged


if __name__ == "__main__":
    unittest.main()
