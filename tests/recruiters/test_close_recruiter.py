"""LIFE-20 (Jim, Oct 23 2026), synthetic only: Close recruiter record from the Daily Report's Recruiter Outreach table. One open row (Needs Action or Waiting) becomes
Completed or Dismissed through the signed review-and-confirm screen; only Action Status is written, read back, and nothing else changes. GET and Review never write."""
import unittest
from copy import deepcopy

from lifeos.platform.notion_client import NotionError
from lifeos.recruiters import notion as store
from tests.kit.recruiters import FakeRecruiters, page
from tests.recruiters.test_outreach import RECRUITERS, Ledger, Service, fields

ROW = "c" * 32


class Rows(FakeRecruiters):
    """The shared fake plus one exact page read, as Notion returns it (its parent is the data source)."""
    def get_page(self, page_id):
        found = [p for p in self.pages if p["id"] == page_id]
        if not found:
            raise NotionError("NOTION_HTTP_404")
        return {"parent": {"type": "data_source_id", "data_source_id": self.source}, **found[0]}


def world(action="Needs Action", **kw):
    rec = Rows([page("Avery Synthetic", company="Example Staffing", role="Program Manager", action=action, pid=ROW, **kw)])
    rec.source = RECRUITERS
    return rec


class Svc(Service):
    """The gate's GET reader reads the Recruiters row (the real ReadOnly client reads any page its token can see)."""
    def __call__(self, method, path, query=None, form=None):
        if method == "GET":
            self.ledger = self.recruiters
        return super().__call__(method, path, query, form)


def close(svc, status):
    st, _, body, _ = svc("GET", "/recruiter", {"r": ROW})
    assert st == 200, body
    review = svc("POST", "/recruiter", form={**fields(body), "status": status, "step": "review"})
    assert review[0] == 200, review[2]
    return svc("POST", "/recruiter", form={**fields(review[2]), "step": "confirm"})


class CloseRow(unittest.TestCase):
    def test_an_open_row_closes_and_only_action_status_changes(self):
        for start in ("Needs Action", "Waiting"):
            for status in store.CLOSE_TO:
                rec = world(start, follow_up="2026-10-21")
                before = store.read(deepcopy(rec.pages[0]))
                done = store.close_row(rec, ROW, status)
                self.assertEqual((done["status"], done["verified"]), ("recorded", True))
                self.assertEqual(rec.writes, [("update", {"Action Status": {"select": {"name": status}}})])
                after = store.read(rec.pages[0])
                self.assertEqual(after["action"], status)
                self.assertEqual({k: v for k, v in after.items() if k != "action"}, {k: v for k, v in before.items() if k != "action"})

    def test_replay_and_closed_or_unclassified_rows_write_nothing(self):
        self.assertEqual(store.close_row(world("Completed"), ROW, "Completed")["status"], "already_recorded")
        for action, kw in (("Dismissed", {}), ("Declined", {}), ("Replied", {}), (None, {}), ("Needs Action", {"done": True}), ("Needs Action", {"active": False})):
            rec = world(action, **kw)
            self.assertEqual(store.close_row(rec, ROW, "Completed")["status"], "not_changed", (action, kw))
            self.assertEqual(rec.writes, [])

    def test_a_page_outside_the_recruiters_source_or_a_bad_status_is_refused(self):
        rec = world()
        rec.pages[0]["parent"] = {"type": "data_source_id", "data_source_id": "e" * 32}            # a page of another data source
        for args, code in (((rec, ROW, "Completed"), "RECRUITERS_ROW_NOT_IN_SOURCE"), ((world(), ROW, "Waiting"), "RECRUITERS_CLOSE_INVALID"), ((world(), "d" * 32, "Completed"), "RECRUITERS_ROW_UNREADABLE")):
            with self.assertRaises(store.RecruitersError) as caught:
                store.close_row(*args)
            self.assertEqual(str(caught.exception), code)
        self.assertEqual(rec.writes, [])

    def test_a_lost_write_is_a_readback_mismatch_not_success(self):
        rec = world()
        rec.drop_writes = True
        with self.assertRaises(store.RecruitersError) as caught:
            store.close_row(rec, ROW, "Dismissed")
        self.assertEqual(str(caught.exception), "RECRUITERS_READBACK_MISMATCH")


class Screen(unittest.TestCase):
    def test_signed_flow_closes_and_get_review_never_write(self):
        rec = world()
        svc = Svc(Ledger(), rec)
        st, _, body, _ = svc("GET", "/recruiter", {"r": ROW})
        self.assertEqual(st, 200)
        self.assertIn(b"Avery Synthetic", body)
        svc("POST", "/recruiter", form={**fields(body), "status": "Dismissed", "step": "review"})
        self.assertEqual(rec.writes, [])
        status, _, done, _ = close(svc, "Dismissed")
        self.assertEqual(status, 200, done)
        self.assertIn(b"Closed: Dismissed", done)
        self.assertEqual(store.read(rec.pages[0])["action"], "Dismissed")
        st, _, body, _ = svc("GET", "/recruiter", {"r": ROW})                                 # now closed: nothing to close, no form
        self.assertEqual(st, 409)
        self.assertNotIn(b"<form", body)

    def test_a_recorded_close_asks_for_the_recruiter_outreach_redraw_once_and_a_replay_never_does(self):
        svc = Svc(Ledger(), world())
        self.assertEqual(close(svc, "Dismissed")[0], 200)
        self.assertEqual(svc.after, ["recruiters"])
        svc("POST", "/recruiter", form={"step": "confirm"})                                   # a refused form asks nothing
        self.assertEqual(svc.after, ["recruiters"])

    def test_tampered_fields_or_signatures_write_nothing(self):
        rec = world()
        svc = Svc(Ledger(), rec)
        _, _, body, _ = svc("GET", "/recruiter", {"r": ROW})
        review = svc("POST", "/recruiter", form={**fields(body), "status": "Completed", "step": "review"})
        confirm = fields(review[2])
        for patch in ({"status": "Dismissed"}, {"r": "d" * 32}, {"pe": "Mallory"}, {"ac": "Waiting"}, {"c": "bad"}, {"t": "bad"}, {"exp": "1"}):
            self.assertIn(svc("POST", "/recruiter", form={**confirm, **patch, "step": "confirm"})[0], (400, 403), patch)
        self.assertEqual(rec.writes, [])

    def test_a_row_changed_after_the_form_opened_is_a_conflict(self):
        rec = world()
        svc = Svc(Ledger(), rec)
        _, _, body, _ = svc("GET", "/recruiter", {"r": ROW})
        review = svc("POST", "/recruiter", form={**fields(body), "status": "Completed", "step": "review"})
        rec.pages[0]["properties"]["Action Status"] = {"type": "select", "select": {"name": "Waiting"}}
        status, _, done, _ = svc("POST", "/recruiter", form={**fields(review[2]), "step": "confirm"})
        self.assertEqual(status, 409)
        self.assertIn(b"Record changed", done)
        self.assertEqual(rec.writes, [])

    def test_cancel_and_a_missing_or_bad_link_write_nothing(self):
        rec = world()
        svc = Svc(Ledger(), rec)
        self.assertEqual(svc("GET", "/recruiter", {})[0], 400)
        self.assertEqual(svc("GET", "/recruiter", {"r": "not-an-id"})[0], 400)
        _, _, body, _ = svc("GET", "/recruiter", {"r": ROW})
        self.assertEqual(svc("POST", "/recruiter", form={**fields(body), "step": "cancel"})[0], 200)
        self.assertEqual(rec.writes, [])


if __name__ == "__main__":
    unittest.main()
