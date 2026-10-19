"""LIFE-47 (D222), synthetic only: the Job Outreach floating region beneath Active Opportunities (heading, compact status line, four-column table). The Daily Report
shape is the Hiring Pipeline test page plus this region under it; the outreach rows come from the D221 Mark as Sent flow. Nothing is live."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.amazon import card as amazon
from lifeos.hiring import card as hiring_card
from lifeos.platform import report_region as R, router
from lifeos.sources import job_outreach as J
from tests.hiring.test_hiring import ENV as HIRING_ENV, WANT, page as hiring_page, table_state, writes
from tests.kit.recruiters import FakeRecruiters
from tests.recruiters.test_outreach import JOB_A, JOB_B, SCHEMA, Ledger, Service, ledger_page, mark

HEAD, STATUS, TABLE = "0000000000000000000000000000c001", "0000000000000000000000000000c002", "0000000000000000000000000000c003"
ENV = {**HIRING_ENV, "JOB_OUTREACH_CARD_BLOCK_ID": HEAD, "JOB_OUTREACH_TABLE_BLOCK_ID": TABLE}
NOW = datetime(2026, 10, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))
ROW = {"job": "Northwind Labs — Senior TPM", "outreach": "2 contacted · replies not tracked · referrals not tracked", "next": "Oct 19",
       "link": "https://www.notion.so/" + JOB_A, "sort": "2026-10-19", "contacted": 2}


def report(headers=J.HEADERS, rows=0, heading=J.TITLE, status="0 jobs"):
    """The Hiring Pipeline test page with the Job Outreach region inserted right after the Hiring table, in the same column."""
    t = hiring_page(extra_rows=1)
    right = t.kids["right"]
    at = right.index("0000000000000000000000000000a003") + 1
    t.add(HEAD, "heading_3", "right", text=heading)
    if status is not None:
        t.add(STATUS, "paragraph", "right", text=status)
    t.table(TABLE, "right", [list(headers)] + [["old job", "x", "y", "z"] for _ in range(rows)])
    mine = [HEAD] + ([STATUS] if status is not None else []) + [TABLE]
    for block in mine:                                                  # Tree.add appends; move the region to sit under the Hiring table
        right.remove(block)
    right[at:at] = mine
    return t


def status(t):
    return R.plain(t.blocks[STATUS])


class Region(unittest.TestCase):
    def test_dry_run_validates_and_writes_nothing(self):
        t = report()
        self.assertEqual(J.present(t, ENV, "Updated 2:00 PM CT", J.cells([]), False, J.status_line([]))["verified"], False)
        self.assertEqual(writes(t), [])

    def test_live_writes_status_and_rows_reads_back_and_leaves_the_hiring_table_alone(self):
        t = report(rows=2)
        hiring_before = table_state(t)
        done = J.present(t, ENV, "Updated 2:00 PM CT", J.cells([ROW]), True, J.status_line([ROW]))
        self.assertEqual((done["verified"], done["rows_changed"], done["rows_removed"]), (True, 1, 1))
        self.assertEqual(table_state(t, TABLE), [[("Northwind Labs — Senior TPM", None), ("2 contacted · replies not tracked · referrals not tracked", None),
                                                  ("Oct 19", None), ("Job Ledger ↗", "https://www.notion.so/" + JOB_A)]])
        self.assertEqual(status(t), "1 job · 2 contacted · replies and referrals not tracked yet")
        self.assertEqual(R.plain(t.blocks[HEAD]), "🤝 Job Outreach (Updated 2:00 PM CT)")
        self.assertEqual(table_state(t), hiring_before)
        again = len(writes(t))
        J.present(t, ENV, "Updated 2:00 PM CT", J.cells([ROW]), True, J.status_line([ROW]))       # replay: nothing to change
        self.assertEqual(len(writes(t)), again)

    def test_empty_state_is_the_status_line_and_no_pseudo_job(self):
        t = report(rows=1)
        J.present(t, ENV, "Updated 2:00 PM CT", J.cells([]), True, J.status_line([]))
        self.assertEqual((table_state(t, TABLE), status(t)), ([], J.EMPTY))
        self.assertTrue(J.EMPTY.startswith("0 jobs · "))

    def test_unknown_replies_and_referrals_are_never_zero(self):
        text = " ".join([J.status_line([ROW]), ROW["outreach"]])
        self.assertNotIn("0 replied", text)
        self.assertNotIn("0 referral", text)
        self.assertIn("not tracked", ROW["outreach"])

    def test_degraded_keeps_rows_and_names_only_a_proven_last_stamp(self):
        t = report(rows=1, heading="🤝 Job Outreach (Updated 1:00 PM CT)", status="1 job · 1 contacted · replies and referrals not tracked yet")
        rows_before = table_state(t, TABLE)
        J.present(t, ENV, "DEGRADED", None, True)
        self.assertEqual(table_state(t, TABLE), rows_before)
        self.assertEqual(R.plain(t.blocks[HEAD]), "🤝 Job Outreach (DEGRADED)")
        self.assertIn("⚠️ DEGRADED", status(t))
        self.assertIn("showing last accepted state (Updated 1:00 PM CT)", status(t))
        J.present(t, ENV, "DEGRADED", None, True)                                          # a second failure carries the same proven stamp forward
        self.assertIn("showing last accepted state (Updated 1:00 PM CT)", status(t))
        fresh = report(rows=1, heading=J.TITLE)
        J.present(fresh, ENV, "DEGRADED", None, True)                                      # nothing proves a last good time: say so, never invent one
        self.assertIn("last accepted time unknown", status(fresh))

    def test_wrong_shape_or_ids_write_nothing(self):
        for t, env, code in ((report(headers=("Job", "Outreach", "Next", "Notes")), ENV, "HEADERS_CHANGED"),
                             (report(), {**ENV, "JOB_OUTREACH_TABLE_BLOCK_ID": "0000000000000000000000000000a003"}, "PINNED_BLOCK_MISMATCH"),
                             (report(), {**ENV, "JOB_OUTREACH_CARD_BLOCK_ID": "not-an-id"}, "ID_INVALID"),
                             (report(heading="Job Outreach"), ENV, "FLOATING_HEADING_INVALID"),
                             (report(status=None), ENV, "FLOATING_SHAPE_INVALID")):
            with self.subTest(code=code):
                with self.assertRaises(J.JobOutreachError) as caught:
                    J.present(t, env, "Updated 2:00 PM CT", J.cells([]), True, J.status_line([]))
                self.assertIn(code, str(caught.exception))
                self.assertEqual(writes(t), [])

    def test_the_region_has_one_owner(self):
        self.assertEqual(router.OWNERS[router.JOB_OUTREACH_REGION], "v7-job-outreach")
        self.assertFalse(router.is_callout(router.JOB_OUTREACH_REGION))
        with self.assertRaises(router.RouterError):
            router.check_write("v7-hiring", [router.JOB_OUTREACH_REGION])


class Neighbours(unittest.TestCase):
    """Reciprocal protection: the new writer proves every other region, and the other writers prove this one."""

    def test_the_job_outreach_writer_refuses_when_another_region_changes_under_it(self):
        t = report(rows=1)
        real = R.write_floating

        def tamper(client, region, status_text, rows, fail):
            t.blocks["jira-p"]["paragraph"]["rich_text"][0]["plain_text"] = "changed by someone else"
            return real(client, region, status_text, rows, fail)
        R.write_floating = tamper
        try:
            with self.assertRaises(J.JobOutreachError) as caught:
                J.present(t, ENV, "Updated 2:00 PM CT", J.cells([ROW]), True, J.status_line([ROW]))
        finally:
            R.write_floating = real
        self.assertIn("PROTECTED_REGION_CHANGED", str(caught.exception))

    def test_the_hiring_writer_preserves_the_job_outreach_region(self):
        t = report(rows=1)
        before = (R.plain(t.blocks[HEAD]), status(t), table_state(t, TABLE))
        self.assertTrue(hiring_card.present(t, HIRING_ENV, "Updated 2:00 PM CT", WANT, True)["verified"])
        self.assertEqual((R.plain(t.blocks[HEAD]), status(t), table_state(t, TABLE)), before)

    def test_the_neighbour_digest_covers_heading_status_and_table(self):
        t = report(rows=1)
        fail = lambda code: RuntimeError(code)                                                     # noqa: E731
        ids = {router.JIRA_REGION: "0000000000000000000000000000a004", router.JOB_OUTREACH_REGION: HEAD}
        for block, field in ((HEAD, "heading_3"), (STATUS, "paragraph")):
            before = R.protected(t, ids, fail)
            t.blocks[block][field]["rich_text"][0]["plain_text"] += " edited"
            self.assertFalse(router.protected_intact("v7-mail-alerts", before, R.protected(t, ids, fail)), block)
        before = R.protected(t, ids, fail)
        t.blocks[TABLE + "-r1"]["table_row"]["cells"][0][0]["plain_text"] = "edited"
        self.assertFalse(router.protected_intact("v7-mail-alerts", before, R.protected(t, ids, fail)))
        self.assertEqual(set(R.protected(t, {router.JIRA_REGION: "0000000000000000000000000000a004", router.JOB_OUTREACH_REGION: ""}, fail)), {router.JIRA_REGION})

    def test_every_card_that_proves_its_neighbours_lists_job_outreach(self):
        from lifeos.attention import card as attention
        from lifeos.dcc import card as dcc
        from lifeos.imessage import comms_card
        from lifeos.oraclecloud import card as oracle
        from lifeos.physmail import card as mail
        for module in (amazon, attention, mail, comms_card, oracle, dcc):
            self.assertIn(("JOB_OUTREACH_CARD_BLOCK_ID", router.JOB_OUTREACH_REGION), module.PROTECTED, module.__name__)
        self.assertNotIn(router.JOB_OUTREACH_REGION, [region for _, _, region, _ in dcc.AREAS])        # proven by the Command Center, not summarised by it


class Stage(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(ledger_page(JOB_A, "Northwind Labs", "Senior Technical Program Manager"), ledger_page(JOB_B, "Northwind Labs", "Technical Program Manager"))
        self.rec = FakeRecruiters(schema=SCHEMA)

    def stage(self, t, env=ENV, recruiters=None):
        return J.run(0, True, environ=env, recruiters=recruiters or self.rec, ledger=self.ledger, client=t, now=NOW)

    def test_unconfigured_is_dormant_and_touches_nothing(self):
        t = report()
        self.assertEqual(J.run(0, True, environ=HIRING_ENV, recruiters=self.rec, ledger=self.ledger, client=t), {"job_outreach": "NOT_CONFIGURED"})
        self.assertEqual((t.log, self.rec.queries), ([], 0))

    def test_mark_as_sent_appears_as_one_row_per_exact_job(self):
        svc = Service(self.ledger, self.rec)
        for job_id, person in ((JOB_A, "Avery Synthetic"), (JOB_A, "Blake Synthetic"), (JOB_B, "Avery Synthetic")):
            mark(svc, job_id, person)
        t = report()
        done = self.stage(t)
        self.assertEqual((done["jobs"], done["conversations"], done["verified"]), (2, 3, True))
        self.assertEqual([r[0][0] for r in table_state(t, TABLE)], ["Northwind Labs — Senior Technical Program Manager", "Northwind Labs — Technical Program Manager"])
        self.assertEqual(table_state(t, TABLE)[0][1:3], [("2 contacted · replies not tracked · referrals not tracked", None), ("Oct 19", None)])
        self.assertEqual(status(t), "2 jobs · 3 contacted · replies and referrals not tracked yet")

    def test_a_job_closed_after_outreach_drops_out(self):
        mark(Service(self.ledger, self.rec), JOB_A, "Avery Synthetic")
        self.ledger.pages[JOB_A]["properties"]["Hiring Decision"]["select"] = {"name": "Rejected"}
        t = report()
        self.assertEqual(self.stage(t)["jobs"], 0)
        self.assertEqual((table_state(t, TABLE), status(t)), ([], J.EMPTY))

    def test_an_unreadable_source_marks_degraded_and_keeps_the_rows(self):
        t = report(rows=1, heading="🤝 Job Outreach (Updated 1:00 PM CT)")
        before = table_state(t, TABLE)
        with self.assertRaises(J.JobOutreachError) as caught:
            self.stage(t, recruiters=FakeRecruiters(schema=SCHEMA, fail_query=True))
        self.assertIn("SOURCE_UNREADABLE", str(caught.exception))
        self.assertEqual(R.plain(t.blocks[HEAD]), "🤝 Job Outreach (DEGRADED)")
        self.assertIn("showing last accepted state (Updated 1:00 PM CT)", status(t))
        self.assertEqual(table_state(t, TABLE), before)


if __name__ == "__main__":
    unittest.main()
