"""LIFE-10 §2, synthetic only: the outreach table on a Job Ledger job page, fed by Mark as Sent (D221). Every name, id and date is invented; nothing is live."""
import unittest
from datetime import timedelta

from lifeos.network import surface
from lifeos.platform import report_region as R
from lifeos.sources import job_page_outreach as P
from tests.hiring.test_hiring import writes
from tests.kit.recruiters import FakeRecruiters
from tests.kit.tree import Tree
from tests.recruiters.test_outreach import JOB_A, JOB_B, LEDGER, NOW, SCHEMA, Ledger, Service, ledger_page, mark

ON = {"JOB_PAGE_OUTREACH": "on"}


def job_page(job_id=JOB_A, leads=True):
    """A Job Ledger page as V7 writes it: the description marker, the job description, the Network Leads toggle (when there are leads), then Jim's own notes."""
    t = Tree()
    t.pages.add(job_id)
    t.add("jd-mark", "paragraph", job_id, text="v7-jd:1 | key=invented")
    t.add("jd", "paragraph", job_id, text="Synthetic job description")
    if leads:
        t.add("net", "toggle", job_id, text="Network Leads (1)")
        t.add("net-mark", "paragraph", "net", text=f"{surface.MARK} abc123")
        t.add("net-lead", "paragraph", "net", text="Avery Synthetic · Northwind Labs")
    t.add("notes", "paragraph", job_id, text="Jim's own notes")
    return t


def region(t, job_id=JOB_A):
    (heading,) = [b for b in t.kids[job_id] if t.blocks[b]["type"] == "heading_3"]
    table = t.kids[job_id][t.kids[job_id].index(heading) + 1]
    return heading, table, [R.live_view(t.blocks[r]) for r in t.kids[table][1:]]


class JobPageOutreach(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(ledger_page(JOB_A, "Northwind Labs", "Senior Technical Program Manager"), ledger_page(JOB_B, "Northwind Labs", "Technical Program Manager"))
        self.rec = FakeRecruiters(schema=SCHEMA)
        self.svc = Service(self.ledger, self.rec)

    def run_it(self, t, live=True, environ=ON, now=NOW):
        return P.run(0, live, environ=environ, recruiters=self.rec, ledger=self.ledger, client=t, now=now)

    def test_without_the_switch_or_live_it_only_reports(self):
        mark(self.svc, JOB_A, "Avery Synthetic")
        for live, env in ((False, ON), (True, {}), (True, {"JOB_PAGE_OUTREACH": "off"})):
            t = job_page()
            done = self.run_it(t, live, env)
            self.assertEqual((done["job_page_outreach"], done["codes"], writes(t)), ("dry_run", {"would_create": 1}, []))

    def test_live_creates_the_region_after_network_leads_styled_and_leaves_the_rest_alone(self):
        mark(self.svc, JOB_A, "Avery Synthetic")
        t = job_page()
        before = {k: v for k, v in t.snapshot().items()}
        done = self.run_it(t)
        self.assertEqual((done["job_page_outreach"], done["jobs"], done["conversations"], done["codes"], done["failed"]), ("live", 1, 1, {"created": 1}, 0))
        heading, table, rows = region(t)
        self.assertEqual(t.kids[JOB_A], ["jd-mark", "jd", "net", heading, table, "notes"])                 # right after the Network Leads block
        self.assertEqual(R.plain(t.blocks[heading]), "🤝 Outreach (Updated 10/15 @ 10:00 AM CT)")
        self.assertEqual(R.cells_of(t.blocks[t.kids[table][0]]), list(P.HEADERS))
        bold, gray, br = (lambda x: (x, None, "bold")), (lambda x: (x, None, "gray")), ("\n", None, None)
        self.assertEqual(rows, [[[bold("Avery Synthetic")], [bold("LinkedIn"), br, gray("Senior Technical Program Manager")],
                                 [bold("Initial outreach: Oct 15"), br, gray("Waiting · reply unknown (LinkedIn inbox not connected)")],
                                 [bold("Follow up"), br, gray("Due Oct 19")]]])
        self.assertEqual({k: v for k, v in t.snapshot().items() if k in before}, before)                  # every existing block untouched
        count = len(writes(t))
        self.assertEqual(self.run_it(t)["codes"], {"unchanged": 1})                                       # replay: nothing to change
        self.assertEqual(len(writes(t)), count)

    def test_a_page_without_network_leads_gets_the_region_at_the_end(self):
        mark(self.svc, JOB_A, "Avery Synthetic")
        t = job_page(leads=False)
        self.run_it(t)
        heading, table, _ = region(t)
        self.assertEqual(t.kids[JOB_A], ["jd-mark", "jd", "notes", heading, table])

    def test_a_new_conversation_updates_rows_in_place_and_restamps_the_heading(self):
        mark(self.svc, JOB_A, "Avery Synthetic")
        t = job_page()
        self.run_it(t)
        heading, table, _ = region(t)
        mark(self.svc, JOB_A, "Blake Synthetic")
        later = NOW + timedelta(hours=2)
        done = self.run_it(t, now=later)
        self.assertEqual(done["codes"], {"updated": 1})
        self.assertEqual(region(t)[:2], (heading, table))                                                  # the same blocks, edited in place
        self.assertEqual([r[0][0][0] for r in region(t)[2]], ["Avery Synthetic", "Blake Synthetic"])
        self.assertEqual(R.plain(t.blocks[heading]), "🤝 Outreach (Updated 10/15 @ 12:00 PM CT)")

    def test_needs_action_comes_first_and_closed_conversations_last(self):
        rows = [{"person": "Casey", "company": "", "medium": "LinkedIn", "role": "TPM", "last_contact": "2026-10-13", "action": "Needs Action", "follow_up": "2026-10-14",
                 "done": False, "active": True},
                {"person": "Avery", "company": "Northwind Labs", "medium": "LinkedIn", "role": "TPM", "last_contact": "2026-10-15", "action": "Completed", "follow_up": "",
                 "done": True, "active": False},
                {"person": "Blake", "company": "", "medium": "LinkedIn", "role": "TPM", "last_contact": "2026-10-16", "action": "Waiting", "follow_up": "2026-10-20",
                 "done": False, "active": True}]
        out = [R.want_view(r) for r in P.cells(rows, NOW.date())]
        self.assertEqual([r[0][0][0] for r in out], ["Casey", "Blake", "Avery"])
        self.assertEqual(out[0][3], [("Needs action: follow up", None, "bold"), ("\n", None, None), ("Overdue since Oct 14", None, "gray")])
        self.assertEqual(out[2][0], [("Avery", None, "bold"), ("\n", None, None), ("Northwind Labs", None, "gray")])
        self.assertEqual(out[2][3], [("No follow-up", None, "bold"), ("\n", None, None), ("Completed", None, "gray")])

    def test_a_page_outside_the_ledger_or_with_two_regions_is_refused_and_counted(self):
        mark(self.svc, JOB_A, "Avery Synthetic")
        self.ledger.pages[JOB_A]["parent"]["data_source_id"] = "9" * 32
        t = job_page()
        done = self.run_it(t)
        self.assertEqual((done["failed"], done["codes"], writes(t)), (1, {"JOB_PAGE_OUTREACH_NOT_A_LEDGER_PAGE": 1}, []))
        self.ledger.pages[JOB_A]["parent"]["data_source_id"] = LEDGER
        t.add("h1", "heading_3", JOB_A, text="🤝 Outreach (Updated 10/14 @ 9:00 AM CT)")
        t.add("h2", "heading_3", JOB_A, text="🤝 Outreach")
        done = self.run_it(t)
        self.assertEqual((done["failed"], done["codes"], writes(t)), (1, {"JOB_PAGE_OUTREACH_REGION_NOT_UNIQUE": 1}, []))

    def test_a_neighbour_edited_during_the_write_fails_the_proof(self):
        mark(self.svc, JOB_A, "Avery Synthetic")
        t = job_page()
        t.trip = ("/children", lambda tree: tree.blocks["notes"]["paragraph"]["rich_text"][0].update(plain_text="edited by Jim mid-write"))
        done = self.run_it(t)
        self.assertEqual((done["failed"], done["codes"]), (1, {"JOB_PAGE_OUTREACH_PROTECTED_CONTENT_CHANGED": 1}))

    def test_a_job_with_no_conversation_gets_nothing(self):
        t = job_page()
        done = self.run_it(t)
        self.assertEqual((done["jobs"], done["codes"], writes(t)), (0, {}, []))

    def test_an_unreadable_recruiters_source_fails_the_whole_run(self):
        mark(self.svc, JOB_A, "Avery Synthetic")
        with self.assertRaises(Exception):
            P.run(0, True, environ=ON, recruiters=FakeRecruiters(schema=SCHEMA, fail_query=True), ledger=self.ledger, client=job_page(), now=NOW)


if __name__ == "__main__":
    unittest.main()
