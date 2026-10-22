"""LIFE-78, synthetic only: recording a reply or a referral on a conversation Mark as Sent already recorded. Offered is never Confirmed, nothing is created, nothing moves
backwards, a closed conversation is untouched, an option the schema lacks refuses before any write, and the job-page table and roll-up say what happened."""
import unittest
from datetime import date

from lifeos.recruiters import notion as store, outreach as O
from lifeos.sources import job_page_outreach as JP, outreach_app
from tests.kit.recruiters import FakeRecruiters, page
from tests.recruiters.test_outreach import (JOB_A, JOB_B, LEDGER, SCHEMA, NOW, Ledger, Service, fields, ledger_page, mark)

OPTIONS = {"Action Status": {"type": "select", "select": {"options": [{"name": n} for n in ("Needs Action", "Waiting", *store.CONVERSATION, store.DECLINED)]}}}
JOB = {"id": JOB_A, "company": "Northwind Labs", "role": "Senior Technical Program Manager"}


def table_blocks(page_id, people):
    row = lambda name: {"id": f"r-{name}", "type": "table_row", "table_row": {"cells": [[{"type": "text", "plain_text": name}, {"type": "text", "plain_text": "\n"}], [], [], []]}}   # noqa: E731
    top = [{"id": "h", "type": "heading_3", "heading_3": {"rich_text": [{"plain_text": "🤝 Outreach (Updated 10/15 @ 10:00 AM CT)"}]}, "has_children": False},
           {"id": "tbl", "type": "table", "table": {"table_width": 4, "has_column_header": True}, "has_children": True}]
    header = {"id": "r-h", "type": "table_row", "table_row": {"cells": [[{"plain_text": "Person — Employer"}], [], [], []]}}
    return {page_id: top, "tbl": [header] + [row(n) for n in people]}


def world(action="Waiting", schema=None):
    rec = FakeRecruiters([_row(action)], schema=SCHEMA | (schema if schema is not None else OPTIONS))
    ledger = Ledger(ledger_page(JOB_A, "Northwind Labs", "Senior Technical Program Manager"), blocks=table_blocks(JOB_A, ["Avery Synthetic"]))
    return ledger, rec


def _row(action):
    row = page("Avery Synthetic", client="Northwind Labs", role="Senior Technical Program Manager", action=action, follow_up="2026-10-19")
    row["properties"]["Job"] = {"type": "relation", "relation": [{"id": JOB_A}]}
    return row


def record(svc, person_index, status):
    st, _, body, _ = svc("GET", "/referral", {"j": JOB_A})
    assert st == 200, body
    review = svc("POST", "/referral", form={**fields(body), "who": person_index, "status": status, "step": "review"})
    assert review[0] == 200, review[2]
    return svc("POST", "/referral", form={**fields(review[2]), "step": "confirm"})


class SetConversation(unittest.TestCase):
    def test_offered_then_confirmed_are_two_distinct_recorded_states(self):
        _, rec = world()
        self.assertEqual(O.set_conversation(rec, JOB, "Avery Synthetic", store.OFFERED, LEDGER)["status"], "recorded")
        self.assertEqual(store.read(rec.pages[0])["action"], "Referral Offered")
        self.assertEqual(O.set_conversation(rec, JOB, "avery  synthetic", store.CONFIRMED, LEDGER)["action"], "Referral Confirmed")
        self.assertEqual(store.read(rec.pages[0])["follow_up"], "2026-10-19")

    def test_replay_writes_nothing_and_confirmed_never_moves_back_to_offered(self):
        _, rec = world("Referral Confirmed")
        for status, expected in ((store.CONFIRMED, "already_recorded"), (store.OFFERED, "not_changed"), (store.REPLIED, "not_changed")):
            self.assertEqual(O.set_conversation(rec, JOB, "Avery Synthetic", status, LEDGER)["status"], expected)
        self.assertEqual(rec.writes, [])

    def test_a_closed_conversation_is_never_changed(self):
        for action in ("Declined", "Completed", "Dismissed"):
            _, rec = world(action)
            self.assertEqual(O.set_conversation(rec, JOB, "Avery Synthetic", store.CONFIRMED, LEDGER)["status"], "not_changed")
            self.assertEqual(rec.writes, [])

    def test_no_recorded_conversation_creates_nothing(self):
        _, rec = world()
        with self.assertRaises(store.RecruitersError) as caught:
            O.set_conversation(rec, JOB, "Someone Else", store.CONFIRMED, LEDGER)
        self.assertEqual(str(caught.exception), "OUTREACH_NOT_RECORDED")
        self.assertEqual(rec.writes, [])

    def test_a_missing_option_refuses_before_any_write(self):
        _, rec = world(schema={"Action Status": {"type": "select", "select": {"options": [{"name": "Waiting"}]}}})
        with self.assertRaises(store.RecruitersError) as caught:
            O.set_conversation(rec, JOB, "Avery Synthetic", store.OFFERED, LEDGER)
        self.assertEqual(str(caught.exception), "OUTREACH_STATUS_UNAVAILABLE")
        self.assertEqual(rec.writes, [])

    def test_a_lost_write_is_a_readback_mismatch_not_success(self):
        _, rec = world()
        rec.drop_writes = True
        with self.assertRaises(store.RecruitersError) as caught:
            O.set_conversation(rec, JOB, "Avery Synthetic", store.CONFIRMED, LEDGER)
        self.assertEqual(str(caught.exception), "OUTREACH_READBACK_MISMATCH")

    def test_the_follow_up_pass_leaves_replied_offered_and_confirmed_alone(self):
        for action in store.CONVERSATION:
            self.assertEqual(store.follow_up_plan(store.read(_row(action)), date(2026, 12, 1)), (None, None))


class Projection(unittest.TestCase):
    def test_table_and_roll_up_distinguish_offered_from_confirmed(self):
        rows = [store.read(_row(a)) for a in ("Referral Offered", "Referral Confirmed", "Replied")]
        for r, text in zip(rows, ("Confirm the referral", "No follow-up", "Reply received")):
            self.assertEqual(JP._next(r, date(2026, 10, 15))[0][0], text)
        self.assertEqual(JP._conversation(rows[0]), "Referral Offered")
        self.assertEqual(JP._conversation(rows[1]), "Referral Confirmed")
        rolled = O.project(rows, {O._id(JOB_A): {"company": "Northwind Labs", "role": "TPM", "url": "u"}}, date(2026, 10, 15))
        self.assertEqual(rolled[0]["outreach"], "3 contacted · 0 need action · 3 replied · 1 referral confirmed")

    def test_declined_is_closed_history(self):
        row = store.read(_row("Declined"))
        self.assertEqual(JP._next(row, date(2026, 10, 15))[0][0], "No follow-up")
        self.assertEqual(O.project([row], {O._id(JOB_A): {"company": "x", "role": "y", "url": "u"}}, date(2026, 10, 15)), [])


class Screen(unittest.TestCase):
    def test_signed_flow_records_confirmed_and_get_review_never_write(self):
        ledger, rec = world()
        svc = Service(ledger, rec)
        st, _, body, _ = svc("GET", "/referral", {"j": JOB_A})
        svc("POST", "/referral", form={**fields(body), "who": "0", "status": "Referral Confirmed", "step": "review"})
        self.assertEqual((rec.writes, ledger.writes), ([], []))
        status, _, done, _ = record(svc, "0", "Referral Confirmed")
        self.assertEqual(status, 200, done)
        self.assertIn(b"Recorded: Referral Confirmed", done)
        self.assertEqual(store.read(rec.pages[0])["action"], "Referral Confirmed")
        self.assertEqual(len(rec.pages), 1)
        self.assertEqual(ledger.writes, [])
        self.assertIn(b"Already recorded", record(svc, "0", "Referral Confirmed")[2])

    def test_tampered_person_status_or_signature_writes_nothing(self):
        ledger, rec = world()
        svc = Service(ledger, rec)
        _, _, body, _ = svc("GET", "/referral", {"j": JOB_A})
        good = fields(body)
        review = svc("POST", "/referral", form={**good, "who": "0", "status": "Referral Offered", "step": "review"})
        confirm = fields(review[2])
        for patch in ({"l0": "Mallory"}, {"status": "Referral Confirmed"}, {"who": "5"}, {"c": "bad"}, {"t": "bad"}):
            self.assertEqual(svc("POST", "/referral", form={**confirm, **patch, "step": "confirm"})[0] in (400, 403), True, patch)
        self.assertEqual(rec.writes, [])

    def test_no_conversation_on_the_page_offers_nothing(self):
        ledger = Ledger(ledger_page(JOB_B, "Northwind Labs", "TPM"))
        status, _, body, _ = Service(ledger, FakeRecruiters(schema=SCHEMA | OPTIONS))("GET", "/referral", {"j": JOB_B})
        self.assertEqual(status, 409)
        self.assertIn(b"No recorded conversation", body)

    def test_a_status_the_schema_lacks_is_refused_with_nothing_written(self):
        ledger, rec = world(schema={"Action Status": {"type": "select", "select": {"options": [{"name": "Waiting"}]}}})
        status, _, body, _ = record(Service(ledger, rec), "0", "Referral Offered")
        self.assertEqual(status, 409)
        self.assertIn(b"OUTREACH_STATUS_UNAVAILABLE", body)
        self.assertEqual(rec.writes, [])

    def test_mark_as_sent_then_referral_is_one_row_end_to_end(self):
        ledger = Ledger(ledger_page(JOB_A, "Northwind Labs", "Senior Technical Program Manager"), blocks=table_blocks(JOB_A, ["Avery Synthetic"]))
        rec = FakeRecruiters(schema=SCHEMA | OPTIONS)
        svc = Service(ledger, rec)
        self.assertEqual(mark(svc, JOB_A, "Avery Synthetic")[0], 200)
        self.assertEqual(record(svc, "0", "Referral Offered")[0], 200)
        self.assertEqual(record(svc, "0", "Referral Confirmed")[0], 200)
        [row] = [store.read(p) for p in rec.pages]
        self.assertEqual((row["action"], row["jobs"], len(rec.pages)), ("Referral Confirmed", [JOB_A], 1))


if __name__ == "__main__":
    unittest.main()
