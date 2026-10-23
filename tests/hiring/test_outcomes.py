"""D190: rejection and offer mail for the companies in the Hiring Pipeline is found by fixed wording, matched to one opportunity or not at all, and shown as a line to confirm."""
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


def msg(subject="Your application", body="", sender="Recruiting Team", address="jobs@example.com", days_ago=3, mid="m1"):
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

    def test_decided_not_to_move_forward_with_your_application_is_a_rejection_and_the_positive_forms_are_not(self):
        for text in ("After careful review, we have decided not to move forward with your application.", "We've decided not to proceed forward at this time.", "We have decided not to go forward with your candidacy."):
            self.assertEqual(outcomes.kind_of(text), outcomes.REJECTED, text)
        for text in ("We have decided to move forward with your application.", "We have decided to move forward with your candidacy and would like to schedule an interview.",
                     "Please decide whether to move forward with the assessment.", "We have received your application and decided nothing yet."):
            self.assertIsNone(outcomes.kind_of(text), text)

    def test_the_company_must_appear_in_order_in_the_sender_subject_domain_or_opening_of_the_body(self):
        self.assertTrue(outcomes.mentions(msg(subject="Update from Walmart Global Tech"), "Walmart"))
        self.assertTrue(outcomes.mentions(msg(sender="Walmart Talent"), "Walmart Inc."))
        self.assertTrue(outcomes.mentions(msg(address="jobs@linkedin.com"), "LinkedIn"))
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

    def test_a_decided_not_to_move_forward_rejection_is_matched_to_its_opportunity_and_routine_mail_is_not(self):
        letter = "Dear Candidate, thank you for applying to Walmart. After careful review, we have decided not to move forward with your application. Senior Manager, Operations and Implementation."
        routine = "Dear Candidate, thank you for applying to Walmart. We have decided to move forward with your application and will be in touch."
        found, stats = outcomes.find([msg(subject="Your application update", sender="Walmart Talent", body=letter, days_ago=3, mid="a"),
                                      msg(subject="Application received", sender="Walmart Talent", body=routine, days_ago=1, mid="b"),
                                      msg(subject="Application received", sender="Uber Talent", body="Thanks for applying to Uber.", days_ago=1, mid="c")], [WALMART, UBER])
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
        self.assertEqual(self.action(result), f"Rejection email received {(NOW - timedelta(days=3)).strftime('%b %-d')} · confirm, then move the Notes page to Retired")
        self.assertEqual(result["rows"][0]["stage"], "Interviewing")
        self.assertEqual(len(result["rows"]), 1)                                  # still listed: V7 closes nothing

    def test_a_company_only_rejection_is_possible_and_a_row_without_a_notes_page_keeps_applied_as_history(self):
        result = self.compiled({"kind": "REJECTED", "received": (NOW - timedelta(days=3)).isoformat(), "match": "company"}, parent=False)
        self.assertTrue(self.action(result).startswith("Possible rejection email"))
        self.assertTrue(self.action(result).endswith("· confirm (Applied stays as history)"))

    def test_no_outcome_line_ever_advises_unticking_applied_or_changing_the_application_record(self):
        for kind in ("REJECTED", "OFFER"):
            for match in ("role", "company"):
                for parent in (True, False):
                    text = self.action(self.compiled({"kind": kind, "received": (NOW - timedelta(days=2)).isoformat(), "match": match}, parent=parent))
                    self.assertNotRegex(text.lower(), r"untick|uncheck|clear applied|remove applied|applied in the ledger", (kind, match, parent))

    def test_an_offer_says_to_review_and_respond(self):
        result = self.compiled({"kind": "OFFER", "received": (NOW - timedelta(days=1)).isoformat(), "match": "role"})
        self.assertTrue(self.action(result).endswith("· review and respond"))
        self.assertTrue(self.action(result).startswith("Offer email received"))

    def test_an_outcome_older_than_the_rows_last_evidence_is_ignored_and_the_row_ages_as_before(self):
        result = self.compiled({"kind": "REJECTED", "received": (NOW - timedelta(days=60)).isoformat(), "match": "role"})
        self.assertNotIn("outcome", result["rows"][0])
        self.assertEqual(self.action(result), "Stale · no news in 35 days · follow up (due Sep 15)")

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
    def __init__(self, records, broken=False, unreadable=()):
        self.records, self.broken, self.queries, self.unreadable = records, broken, [], set(unreadable)

    def list_ids_complete(self, query, limit, include_spam=False):
        self.queries.append(query)
        if self.broken:
            raise GmailError("GMAIL_LISTING_INCOMPLETE")
        return [r["id"] for r in self.records]

    def mail_record(self, message_id):
        if message_id in self.unreadable:
            raise GmailError("GMAIL_MESSAGE_UNREADABLE")
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
        for private in ("Walmart", "Uber", "example.com", "Unfortunately"):
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


class ContactTests(unittest.TestCase):
    """LIFE-26: the newest direct, non-automated message for one pipeline row resets its Stale clock; automated senders and ambiguous mail never do."""
    def test_a_person_writing_after_the_last_evidence_is_a_contact_and_the_newest_wins(self):
        found = outcomes.contacts([msg(subject="Uber next steps", address="maria@example.com", days_ago=9, mid="a"),
                                   msg(subject="Uber next steps", address="maria@example.com", days_ago=2, mid="b")], [UBER])
        self.assertEqual(found, {UBER["key"]: (NOW - timedelta(days=2)).astimezone(outcomes.timezone.utc).isoformat()})

    def test_automated_ats_or_unattributable_mail_is_never_contact(self):
        for address in ("no-reply@example.com", "noreply@example.com", "notifications@example.com", "jobs@example.com", "talent@example.com",
                        "recruiter@linkedin.com", "broken-address"):
            self.assertEqual(outcomes.contacts([msg(subject="Uber update", address=address)], [UBER]), {}, address)
        self.assertEqual(outcomes.contacts([msg(subject="Uber and Walmart roles", address="pat@example.com")], [UBER, WALMART]), {})     # two companies: never guessed
        self.assertEqual(outcomes.contacts([msg(subject="Uber update", address="pat@example.com", days_ago=20)], [UBER]), {})          # before the interview

    def test_a_scan_saves_the_contacts_and_an_older_save_without_them_still_loads(self):
        counts, store, _ = RunTests().go([msg(subject="Uber next steps", address="maria@example.com", days_ago=2)])
        self.assertEqual((counts["contacts"], list(store.doc["contacts"])), (1, [UBER["key"]]))
        with mock.patch.object(outcomes, "STORE", store), mock.patch.object(outcomes, "_exists", lambda c: True):
            self.assertEqual(outcomes.load(object())[2], store.doc["contacts"])
        old = FakeStore({"schema": 1, "scanned_at": NOW.isoformat(), "taken_at": NOW.isoformat(), "outcomes": {}})
        with mock.patch.object(outcomes, "STORE", old), mock.patch.object(outcomes, "_exists", lambda c: True):
            self.assertEqual(outcomes.load(object()), ({}, NOW.isoformat(), {}))
        self.assertNotIn("example.com", json.dumps(counts))

    def test_the_compiler_carries_the_contact_onto_its_row(self):
        got = C.compile_rows([{"company": "Uber", "role": UBER["role"], "applied_on": "2026-09-01"}], [], [], None,
                             {"ledger": True, "parents": True, "calendar": True}, NOW, contacts={UBER["key"]: NOW.isoformat()})
        self.assertEqual(got["rows"][0]["contact"], NOW.isoformat())


class EntryPointTests(unittest.TestCase):
    """D190 through the production entry point `lifeos.run.main(["hiring-outcomes", ...])` -> STAGES -> outcomes.run -> the shared snapshot store (real SQL against an in-memory table);
    only Gmail, Outlook and the database connection are faked."""

    PIPELINE = {"schema": 1, "taken_at": NOW.isoformat(), "accepted_at": NOW.isoformat(), "rows": RunTests.PRIOR["rows"]}

    class Tables:
        def __init__(self, mismatch=False):
            self.rows, self.statements, self.mismatch = {}, [], mismatch

        def handler(self, sql, args, cur):
            self.statements.append(sql.split()[0])
            if sql.startswith("INSERT INTO"):
                self.rows[(sql.split()[2], args[0])] = args[-1]
            elif sql.startswith("SHOW TABLES"):
                cur.row = (args[0],) if any(k[0] == args[0] for k in self.rows) else None
            elif sql.startswith("SELECT payload"):
                table = sql.split("FROM")[1].split()[0]
                got = self.rows.get((table, args[0]))
                cur.row = ((got.replace("REJECTED", "OFFER") if self.mismatch and table == "v7_hiring_outcomes" else got),) if got else None

    def go(self, records=(), live=True, broken=False, tables=None, accounts=None, prior=True, unreadable=()):
        import contextlib
        import io
        from lifeos import run as entry
        from lifeos.platform import db
        from tests.kit.db import FakeConn
        tables = tables or self.Tables()
        if prior:
            tables.rows.setdefault(("v7_hiring_pipeline", 1), json.dumps(self.PIPELINE))
        conn, fake, out, err = FakeConn(handler=tables.handler), FakeGmail(list(records), broken, unreadable), io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(db, "connect", lambda: conn))
            stack.enter_context(mock.patch.object(outcomes.Gmail, "from_env", classmethod(lambda cls, *a, **k: fake)))
            stack.enter_context(mock.patch.object(mail, "outlook_accounts", lambda environ: accounts or []))
            stack.enter_context(mock.patch.object(outcomes, "datetime", mock.Mock(now=lambda tz=None: NOW, fromisoformat=datetime.fromisoformat)))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = entry.main(["hiring-outcomes", "--limit", "0"] + (["--live"] if live else []))
        line = [l for l in out.getvalue().splitlines() if l.startswith("hiring-outcomes:")]
        return code, (json.loads(line[0].split(":", 1)[1]) if line else None), err.getvalue().strip(), fake, tables, conn

    REC = [msg(subject="Your Walmart application", body=REJECTION, days_ago=3)]

    def test_a_live_scan_stores_the_outcome_with_read_back_and_a_second_scan_within_12_hours_reads_no_mail(self):
        code, counts, err, _, tables, conn = self.go(self.REC)
        self.assertEqual((code, err, counts["status"], counts["saved"], counts["rejected"]), (0, "", "fresh", 1, 1))
        stored = json.loads(tables.rows[("v7_hiring_outcomes", 1)])
        self.assertEqual(sorted(next(iter(stored["outcomes"].values()))), ["kind", "match", "received"])            # bounded evidence only: no company, sender, subject or text
        code, counts, err, fake, tables, _ = self.go(self.REC, tables=tables)
        self.assertEqual((code, counts["status"], counts["saved"], fake.queries), (0, "not_due", 0, []))

    def test_a_decided_not_to_move_forward_rejection_is_stored_through_the_entry_point_and_a_routine_message_stores_nothing(self):
        letter = "Dear Candidate, thank you for applying to Walmart. After careful review, we have decided not to move forward with your application."
        code, counts, err, _, tables, _ = self.go([msg(subject="Your application update", sender="Walmart Talent", body=letter, days_ago=3)])
        self.assertEqual((code, err, counts["status"], counts["saved"], counts["rejected"], counts["matched"]), (0, "", "fresh", 1, 1, 1))
        self.assertEqual(next(iter(json.loads(tables.rows[("v7_hiring_outcomes", 1)])["outcomes"].values()))["kind"], "REJECTED")
        routine = "Dear Candidate, thank you for applying to Walmart. We have decided to move forward with your application."
        code, counts, _, _, tables, _ = self.go([msg(subject="Your application update", sender="Walmart Talent", body=routine, days_ago=3)])
        self.assertEqual((code, counts["status"], counts["matched"], counts["rejected"]), (0, "fresh", 0, 0))
        self.assertEqual(json.loads(tables.rows[("v7_hiring_outcomes", 1)])["outcomes"], {})

    def test_a_dry_run_makes_zero_writes_and_creates_no_table(self):
        code, counts, err, _, tables, _ = self.go(self.REC, live=False)
        self.assertEqual((code, counts["matched"], counts["saved"]), (0, 1, 0))
        self.assertNotIn(("v7_hiring_outcomes", 1), tables.rows)
        self.assertNotIn("INSERT", tables.statements)
        self.assertNotIn("CREATE", tables.statements)

    def test_an_incomplete_gmail_or_outlook_scan_fails_closed_and_keeps_the_last_accepted_outcomes(self):
        seeded = self.Tables()
        seeded.rows[("v7_hiring_outcomes", 1)] = json.dumps({"schema": 1, "taken_at": NOW.isoformat(), "scanned_at": (NOW - timedelta(hours=30)).isoformat(), "outcomes": {WALMART["key"]: {"kind": "REJECTED", "received": NOW.isoformat(), "match": "role"}}})
        before = seeded.rows[("v7_hiring_outcomes", 1)]
        for kwargs in ({"broken": True}, {"accounts": [("work", None)]}, {"accounts": [("work", BrokenOutlook())]}):
            code, counts, err, _, tables, _ = self.go(self.REC, tables=seeded, **kwargs)
            self.assertEqual((code, counts["status"], counts["saved"], err), (1, "degraded", 0, "HIRING-OUTCOMES FAILED: OUTCOMES_DEGRADED_SOURCE_INCOMPLETE"), kwargs)
            self.assertEqual(tables.rows[("v7_hiring_outcomes", 1)], before)

    def seeded(self):
        tables = self.Tables()
        tables.rows[("v7_hiring_outcomes", 1)] = json.dumps({"schema": 1, "taken_at": NOW.isoformat(), "scanned_at": (NOW - timedelta(hours=30)).isoformat(),
                                                              "outcomes": {WALMART["key"]: {"kind": "REJECTED", "received": NOW.isoformat(), "match": "role"}}})
        return tables, tables.rows[("v7_hiring_outcomes", 1)]

    def assert_degraded_and_untouched(self, result, tables, before, unreadable):
        code, counts, err, _, tables, _ = result
        self.assertEqual((code, counts["status"], counts["unreadable"], counts["saved"]), (1, "degraded", unreadable, 0))
        self.assertEqual(err, "HIRING-OUTCOMES FAILED: OUTCOMES_DEGRADED_SOURCE_INCOMPLETE")
        self.assertEqual(tables.rows[("v7_hiring_outcomes", 1)], before)                       # the last accepted snapshot is preserved byte for byte
        self.assertEqual([s for s in tables.statements if s in ("INSERT", "CREATE", "UPDATE", "DELETE", "REPLACE")], [])      # and nothing was written

    def test_a_complete_gmail_listing_with_one_failed_message_read_is_degraded_not_fresh(self):
        tables, before = self.seeded()
        records = self.REC + [msg(subject="Another", body="Hello", mid="m2", days_ago=2)]
        self.assert_degraded_and_untouched(self.go(records, tables=tables, unreadable=("m2",)), tables, before, 1)

    def test_malformed_outlook_messages_in_a_complete_listing_are_degraded_not_fresh(self):
        from tests.kit.recruiters import ReadOnlyOutlook
        good = {"id": "g1", "receivedDateTime": (NOW - timedelta(days=2)).isoformat(), "subject": "Hello", "from": {"emailAddress": {"name": "Recruiting", "address": "jobs@example.com"}}, "body": {"content": ""}}
        for bad, count in (({"id": "x1", "subject": "no received time"}, 1), ({"receivedDateTime": (NOW - timedelta(days=2)).isoformat(), "subject": "no id"}, 1)):
            tables, before = self.seeded()
            outlook = ReadOnlyOutlook(inbox=[good, bad])
            self.assert_degraded_and_untouched(self.go(self.REC, tables=tables, accounts=[("work", outlook)]), tables, before, count)
        tables, before = self.seeded()
        both = ReadOnlyOutlook(inbox=[good], junk=[{"subject": "neither"}])
        self.assert_degraded_and_untouched(self.go(self.REC, tables=tables, accounts=[("work", both)]), tables, before, 1)

    def test_a_clean_listing_with_nothing_unreadable_is_still_fresh_and_saved(self):
        from tests.kit.recruiters import ReadOnlyOutlook
        good = {"id": "g1", "receivedDateTime": (NOW - timedelta(days=2)).isoformat(), "subject": "Hello", "from": {"emailAddress": {"name": "Recruiting", "address": "jobs@example.com"}}, "body": {"content": ""}}
        code, counts, err, _, tables, _ = self.go(self.REC, accounts=[("work", ReadOnlyOutlook(inbox=[good]))])
        self.assertEqual((code, counts["status"], counts["unreadable"], counts["saved"], err), (0, "fresh", 0, 1, ""))

    def test_a_snapshot_write_that_does_not_read_back_cannot_report_success(self):
        code, counts, err, _, tables, _ = self.go(self.REC, tables=self.Tables(mismatch=True))
        self.assertEqual(code, 1)
        self.assertIsNone(counts)                                                    # no counts line claims a save
        self.assertEqual(err, "HIRING-OUTCOMES FAILED: OUTCOMES_READBACK_MISMATCH")

    def test_the_stage_has_no_notion_or_ledger_write_path_and_never_names_the_applied_property(self):
        import pathlib
        text = (pathlib.Path(outcomes.__file__)).read_text()
        for banned in ("Client(", "query_data_source", "update_page", ".call(", "PATCH", "\"Applied\"", "'Applied'"):
            self.assertNotIn(banned, text, banned)


class BrokenOutlook:
    def __getattr__(self, name):
        raise mail.MailError("MAIL_OUTLOOK_LISTING_INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
