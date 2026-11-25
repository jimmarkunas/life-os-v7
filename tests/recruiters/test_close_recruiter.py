"""LIFE-20 (Jim, Oct 23 2026), synthetic only: Close recruiter record from the Daily Report's Recruiter Outreach table. One open row (Needs Action or Waiting) becomes
Completed or Dismissed through the signed review-and-confirm screen; Action Status, Done, Active and Needs review are written together (LIFE-971), read back, and nothing else changes. GET and Review never write."""
import contextlib
import io
import unittest
from unittest import mock
from copy import deepcopy
from datetime import timedelta

from lifeos.platform.notion_client import NotionError
from lifeos.recruiters import notion as store
from lifeos.sources.apps import close_app
from tests.recruiters.fakes import FakeRecruiters, page
from tests.recruiters.test_outreach import RECRUITERS, Ledger, Service, fields, walk

ROW = "c" * 32
RECRUITERS_ID = RECRUITERS


class Rows(FakeRecruiters):
    """The shared fake plus one exact page read, as Notion returns it (its parent is the data source)."""
    def get_page(self, page_id):
        found = [p for p in self.pages if p["id"].replace("-", "") == page_id.replace("-", "")]
        if not found:
            raise NotionError("NOTION_HTTP_404")
        return {"parent": {"type": "data_source_id", "data_source_id": self.source}, **found[0]}


def world(action="Needs Action", **kw):
    rec = Rows([page("Avery Synthetic", company="Example Staffing", role="Program Manager", action=action, pid=ROW, **kw)])
    rec.source = RECRUITERS
    return rec


class Svc(Service):
    """The gate's GET reader reads the Recruiters row (the real ReadOnly client reads any page its token can see)."""
    def __call__(self, method, path, query=None, form=None, headers=None):
        if method == "GET":
            self.ledger = self.recruiters
        return super().__call__(method, path, query, form, headers)


def close(svc, status):
    return walk(svc, "/recruiter", {"r": ROW}, status=status)


class CloseRow(unittest.TestCase):
    def test_an_open_row_closes_and_only_action_status_changes(self):
        for start in ("Needs Action", "Waiting"):
            for status in store.CLOSE_TO:
                rec = world(start, follow_up="2026-10-21")
                before = store.read(deepcopy(rec.pages[0]))
                done = store.close_row(rec, ROW, status)
                self.assertEqual((done["status"], done["verified"]), ("recorded", True))
                self.assertEqual(rec.writes, [("update", {"Action Status": {"select": {"name": status}}, "Done": {"checkbox": True}, "Active": {"checkbox": False}, "Needs review": {"checkbox": False}})])
                after = store.read(rec.pages[0])
                self.assertEqual((after["action"], after["done"], after["active"], after["needs_review"]), (status, True, False, False))     # LIFE-971: all three flags move with the status
                gone = ("action", "done", "active", "needs_review")
                self.assertEqual({k: v for k, v in after.items() if k not in gone}, {k: v for k, v in before.items() if k not in gone})

    def test_replay_and_closed_or_unclassified_rows_write_nothing(self):
        self.assertEqual(store.close_row(world("Completed", done=True, active=False), ROW, "Completed")["status"], "already_recorded")
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

    def test_a_form_older_than_fifteen_minutes_says_it_expired_and_logs_why(self):
        """LIFE-684: Nov 14 2026, an old form showed 'no longer valid' with no reason; an expired form now says so, a tampered one keeps the old screen, and each logs a fixed code."""
        self.assertEqual(close_app.TTL, 900)                                                  # the screen says 15 minutes
        rec = world()
        svc = Svc(Ledger(), rec)
        _, _, body, _ = svc("GET", "/recruiter", {"r": ROW})
        form = {**fields(body), "status": "Completed", "step": "review"}
        svc.clock += timedelta(minutes=16)
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            status, _, page, _ = svc("POST", "/recruiter", form=form)
        self.assertEqual(status, 403)
        self.assertIn(b"This form expired", page)
        self.assertIn(b"15 minutes", page)
        self.assertEqual(log.getvalue().strip(), "refused: FORM_EXPIRED")
        svc.clock -= timedelta(minutes=16)
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            status, _, page, _ = svc("POST", "/recruiter", form={**form, "t": "bad"})
        self.assertEqual(status, 403)
        self.assertIn(b"no longer valid", page)
        self.assertEqual(log.getvalue().strip(), "refused: FORM_ALTERED")
        self.assertEqual(rec.writes, [])

    def test_a_row_with_line_breaks_closes_when_the_browser_sends_them_as_crlf(self):
        """LIFE-695: Nov 14 2026, closing an old recruiter always showed 'no longer valid': a browser submits line breaks in hidden fields as CR+LF, and the fingerprint of the person, company and role no longer matched."""
        rec = Rows([page("Avery\nSynthetic", company="Example\nStaffing\nInc", role="Program Manager\n", action="Needs Action", pid=ROW)])
        rec.source = RECRUITERS
        svc = Svc(Ledger(), rec)

        def browser(form):
            return {k: v.replace("\r\n", "\n").replace("\n", "\r\n") for k, v in form.items()}
        _, _, body, _ = svc("GET", "/recruiter", {"r": ROW})
        review = svc("POST", "/recruiter", form=browser({**fields(body), "status": "Completed", "step": "review"}))
        self.assertEqual(review[0], 200, review[2])
        done = svc("POST", "/recruiter", form=browser({**fields(review[2]), "step": "confirm"}))
        self.assertEqual(done[0], 200, done[2])
        self.assertEqual(store.read(rec.pages[0])["action"], "Completed")
        self.assertEqual(close_app._fingerprint("o", ROW, "A\r\nB", "C\rD", False), close_app._fingerprint("o", ROW, "A\nB", "C\nD", False))

    def test_a_row_whose_notion_id_has_dashes_closes(self):
        """LIFE-698: Nov 14 2026, every recruiter close was refused: Notion returns the page id with dashes, the form carries it without, and the row fingerprint hashed one on each side. Earlier tests used ids with no dashes."""
        dashed = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        rec = Rows([page("Avery Synthetic", company="Example Staffing", role="Program Manager", action="Needs Action", pid=dashed)])
        rec.source = RECRUITERS
        svc = Svc(Ledger(), rec)
        _, _, body, _ = svc("GET", "/recruiter", {"r": dashed})
        review = svc("POST", "/recruiter", form={**fields(body), "status": "Completed", "step": "review"})
        self.assertEqual(review[0], 200, review[2])
        done = svc("POST", "/recruiter", form={**fields(review[2]), "step": "confirm"})
        self.assertEqual(done[0], 200, done[2])
        self.assertEqual(store.read(rec.pages[0])["action"], "Completed")
        _, _, body, _ = svc("GET", "/recruiter", {"r": dashed})                                   # now closed: no form
        rec2 = Rows([page("Avery Synthetic", company="Example Staffing", role="Program Manager", action="Needs Action", pid=dashed)])
        rec2.source = RECRUITERS
        svc2 = Svc(Ledger(), rec2)
        _, _, body, _ = svc2("GET", "/recruiter", {"r": dashed})
        self.assertEqual(svc2("POST", "/recruiter", form={**fields(body), "pe": "Mallory", "status": "Completed", "step": "review"})[0], 403)   # a tampered field still fails closed
        self.assertEqual(rec2.writes, [])

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


class RepairAndOverdue(unittest.TestCase):
    """LIFE-971 (Jim, Nov 24 2026): a row closed by Action Status alone (Done off, Active on) is repaired; the overdue list is what the card marks overdue."""
    def test_a_completed_row_with_done_off_and_active_on_is_repaired_to_consistent_and_a_second_close_writes_nothing(self):
        rec = world("Completed")
        self.assertEqual(store.close_row(rec, ROW, "Completed")["status"], "recorded")
        after = store.read(rec.pages[0])
        self.assertEqual((after["done"], after["active"], after["needs_review"]), (True, False, False))
        self.assertEqual(store.close_row(rec, ROW, "Completed")["status"], "already_recorded")
        self.assertEqual(len(rec.writes), 1)

    def test_a_completed_row_is_never_flipped_to_dismissed(self):
        rec = world("Completed")
        self.assertEqual(store.close_row(rec, ROW, "Dismissed")["status"], "not_changed")
        self.assertEqual(rec.writes, [])

    def test_overdue_rows_are_the_open_needs_action_rows_past_their_reply_day(self):
        from datetime import date
        rows = [store.read(p) for p in (page_row("a", "Needs Action", "2026-11-20"), page_row("b", "Needs Action", "2026-11-24"), page_row("c", "Waiting", "2026-11-01"), page_row("d", "Needs Action", "2026-11-25"))]
        self.assertEqual([r["person"] for r in store.overdue_rows(rows, date(2026, 11, 24))], ["a"])


def page_row(person, action, follow_up):
    return page(person, action=action, follow_up=follow_up)


class OneClickAndCloseAll(unittest.TestCase):
    """LIFE-971 (Jim, Nov 24 2026): Done and Dismiss are one click from the card; the page opened by the link saves on its own, and Close all overdue lists names and closes them on one confirm."""
    def one(self, rec, do="done", headers=None):
        svc = Svc(Ledger(), rec)
        status, _, body, _ = svc("GET", "/recruiter", {"r": ROW, "do": do}, headers=headers)
        return svc, status, body

    def test_the_done_link_opens_a_page_that_saves_by_itself_and_sets_all_three_fields(self):
        rec = world()
        svc, status, body = self.one(rec)
        self.assertEqual(status, 200)
        self.assertEqual(rec.writes, [])                                                      # opening the link wrote nothing
        self.assertIn(b"data-auto", body)
        form = fields(body)
        self.assertEqual((form["status"], form["step"]), ("Completed", "confirm"))
        status, _, done, _ = svc("POST", "/recruiter", form=form)
        self.assertEqual(status, 200, done)
        self.assertIn(b"Closed: Completed", done)
        row = store.read(rec.pages[0])
        self.assertEqual((row["action"], row["done"], row["active"], row["needs_review"]), ("Completed", True, False, False))
        self.assertEqual(len(rec.writes), 1)

    def test_the_dismiss_link_dismisses_and_a_second_click_changes_nothing(self):
        rec = world()
        svc, status, body = self.one(rec, "dismiss")
        form = fields(body)
        self.assertEqual(svc("POST", "/recruiter", form=form)[0], 200)
        self.assertEqual(store.read(rec.pages[0])["action"], "Dismissed")
        self.assertEqual(svc("POST", "/recruiter", form=form)[0], 409)                        # the same signed form again: the record has changed, nothing is written
        self.assertEqual(len(rec.writes), 1)
        self.assertEqual(svc("GET", "/recruiter", {"r": ROW, "do": "done"})[0], 409)          # and the link of a closed row says nothing is open

    def test_an_embedded_fetch_or_an_unknown_action_never_gets_the_auto_saving_page(self):
        for headers in ({"sec-fetch-dest": "image"}, {"sec-fetch-dest": "iframe"}, {"sec-fetch-dest": "empty"}):
            _, status, body = self.one(world(), headers=headers)
            self.assertEqual(status, 403)
            self.assertNotIn(b"<form", body)
        self.assertEqual(self.one(world(), "delete")[1], 403)
        self.assertEqual(self.one(world(), headers={"sec-fetch-dest": "document"})[1], 200)

    def test_jitenders_case_a_completed_row_with_done_off_and_active_on_is_repaired_by_the_link(self):
        rec = world("Completed", review=True)
        svc, status, body = self.one(rec)
        self.assertEqual(status, 200)
        self.assertEqual(svc("POST", "/recruiter", form=fields(body))[0], 200)
        row = store.read(rec.pages[0])
        self.assertEqual((row["action"], row["done"], row["active"], row["needs_review"]), ("Completed", True, False, False))

    def test_the_answer_never_waits_for_the_card_and_says_a_few_seconds_when_the_worker_is_there(self):
        """LIFE-972 (Jim, Nov 24 11:35 PM CT): save, answer at once, redraw in the background. The app only asks (`after`); it never runs the writer."""
        for worker, text, asked in ((True, b"Done. The card updates in a few seconds.", ["card"]), (False, b"Done. The card updates on the next refresh.", ["recruiters"])):
            rec = world()
            svc = Svc(Ledger(), rec)
            _, _, body, _ = svc("GET", "/recruiter", {"r": ROW, "do": "done"})
            with mock.patch("lifeos.platform.close_redraw.available", return_value=worker), mock.patch("lifeos.recruiters.outreach_card.run") as writer:
                status, _, done, _ = svc("POST", "/recruiter", form=fields(body))
            self.assertEqual(status, 200)
            self.assertIn(text, done)
            self.assertEqual(svc.after, asked)
            writer.assert_not_called()                                                         # nothing in the request redraws the card
            self.assertEqual(store.read(rec.pages[0])["action"], "Completed")

    def test_dismiss_says_dismissed(self):
        svc = Svc(Ledger(), world())
        _, _, body, _ = svc("GET", "/recruiter", {"r": ROW, "do": "dismiss"})
        with mock.patch("lifeos.platform.close_redraw.available", return_value=True):
            done = svc("POST", "/recruiter", form=fields(body))[2]
        self.assertIn(b"Dismissed. The card updates in a few seconds.", done)

    def test_close_all_overdue_lists_the_overdue_names_and_dismisses_exactly_those_on_one_confirm(self):
        rec = Rows([page("Olga Overdue", action="Needs Action", follow_up="2026-10-10", pid="a" * 32), page("Nora Today", action="Needs Action", follow_up="2026-10-15", pid="b" * 32),
                              page("Wanda Waiting", action="Waiting", follow_up="2026-10-01", pid="c" * 32), page("Otto Overdue", action="Needs Action", follow_up="2026-10-12", pid="d" * 32)])
        rec.source = RECRUITERS_ID
        svc = Svc(Ledger(), rec)
        status, _, body, _ = svc("GET", "/recruiter", {"all": "overdue"})
        self.assertEqual(status, 200)
        self.assertIn(b"Olga Overdue", body)
        self.assertIn(b"Otto Overdue", body)
        self.assertNotIn(b"Nora Today", body)                                                 # due today is not overdue
        self.assertNotIn(b"Wanda Waiting", body)                                              # only Needs Action
        self.assertEqual(rec.writes, [])
        status, _, done, _ = svc("POST", "/recruiter", form={**fields(body), "step": "confirm"})
        self.assertEqual(status, 200, done)
        self.assertIn(b"Dismissed 2 overdue", done)
        self.assertNotIn(b"Closed. The card is updated", done)
        states = {store.read(p)["person"]: (store.read(p)["action"], store.read(p)["done"], store.read(p)["active"]) for p in rec.pages}
        self.assertEqual(states, {"Olga Overdue": ("Dismissed", True, False), "Nora Today": ("Needs Action", False, True),
                                  "Wanda Waiting": ("Waiting", False, True), "Otto Overdue": ("Dismissed", True, False)})
        self.assertEqual(svc.after, ["recruiters"])                                           # no worker in the test: the workflow refresh is asked
        status, _, again, _ = svc("GET", "/recruiter", {"all": "overdue"})                    # nothing overdue any more: no form
        self.assertNotIn(b"<form", again)

    def test_a_tampered_or_expired_close_all_form_writes_nothing(self):
        rec = Rows([page("Olga Overdue", action="Needs Action", follow_up="2026-10-10", pid="a" * 32)])
        rec.source = RECRUITERS_ID
        svc = Svc(Ledger(), rec)
        _, _, body, _ = svc("GET", "/recruiter", {"all": "overdue"})
        form = {**fields(body), "step": "confirm"}
        for patch in ({"i0": "e" * 32}, {"t": "bad"}, {"n": "2"}, {"exp": "1"}):
            self.assertIn(svc("POST", "/recruiter", form={**form, **patch})[0], (400, 403), patch)
        svc.clock += timedelta(minutes=16)
        self.assertEqual(svc("POST", "/recruiter", form=form)[0], 403)
        self.assertEqual(rec.writes, [])
