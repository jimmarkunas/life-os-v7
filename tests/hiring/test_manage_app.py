"""LIFE-88 E14 (Jim, Oct 24): Manage (12a, a read-only hub of the existing actions) and Reopen (12b, a signed review and confirm through the S1 decision
rules), driven the way the Edge gate calls them, against the same synthetic Ledger and Notes stand-ins as the Close screen. Every name is invented."""
import html
import re
import unittest
from unittest import mock

from lifeos.hiring import decision as D
from lifeos.sources import close_app, manage_app
from tests.hiring import test_decision as TD
from tests.hiring.test_close_app import Both, Harness
from tests.hiring.test_notes_decision import COMPANY, ROLE


class H(Harness):
    def call(self, method, path="/", query=None, form=None, **kw):
        with mock.patch.object(manage_app, "Client", lambda cfg: Both(self.world)):
            return super().call(method, path, query, form, **kw)

    def hub(self, pid):
        return self.call("GET", "/manage", query={"o": D._page_id(pid)})

    def reopen(self, pid, step="confirm", **extra):
        status, body, _ = self.hub(pid)
        assert "Reopen this opportunity" in body, body
        form = self.fields(body)
        status, body, _ = self.call("POST", "/manage", form={**form, **extra, "step": "review"})
        assert status == 200, body
        return self.call("POST", "/manage", form={**self.fields(body), "step": step})


def links(body):
    return [html.unescape(u) for u in re.findall(r'href="([^"]+)"', body)]


class Hub(unittest.TestCase):
    def test_an_open_application_links_its_existing_actions_and_get_writes_nothing(self):
        h = H([(TD.TARGET, COMPANY, ROLE)])
        page = h.world.ws.target
        status, body, _ = h.hub(page)
        self.assertEqual(status, 200, body)
        lid = D._page_id(TD.TARGET)
        self.assertIn(f"/?o={lid}", links(body))                                           # Close aims at the record that carries the decision: the Ledger row
        self.assertIn(f"/resume?j={lid}", links(body))
        self.assertIn(f"/sent?j={lid}", links(body))
        self.assertNotIn("Reopen this opportunity", body)
        self.assertIn(f'<a class="status" href="https://www.notion.so/{lid}" target="_blank" rel="noopener">Open · Job Ledger ↗</a>', body)     # LIFE-169: the record label opens it in Notion
        self.assertEqual(h.writes(), (0, 0))

    def test_a_notes_only_pursuit_links_close_on_its_own_page_and_no_ledger_actions(self):
        h = H()
        status, body, _ = h.hub(h.world.ws.target)
        self.assertEqual(status, 200, body)
        self.assertIn(f"/?o={D._page_id(h.world.ws.target)}", links(body))
        self.assertFalse([u for u in links(body) if u.startswith(("/resume", "/sent"))])

    def test_the_open_primary_action_is_linked_to_mark_complete_until_it_is_done(self):
        h = H()
        ws, page = h.world.ws, h.world.ws.target
        fold = ws.add("toggle", page, close_app.FOLD)
        ws.kids[page].remove(fold)
        ws.kids[page].insert(0, fold)
        for text in (close_app.OPP_MARKER, "v7-action:1;key=0123456789abcdef;text=Prepare for Interview 1 on 2026-11-02."):
            ws.add("paragraph", fold, text)
        self.assertIn(f"/complete?o={D._page_id(page)}&a=0123456789abcdef", links(h.hub(page)[1]))
        ws.add("paragraph", fold, "v7-done:1;key=0123456789abcdef;on=2026-10-24")
        self.assertFalse([u for u in links(h.hub(page)[1]) if u.startswith("/complete")])

    def test_an_unknown_page_or_no_link_is_refused(self):
        h = H()
        self.assertEqual(h.call("GET", "/manage")[0], 400)
        self.assertEqual(h.hub("f" * 32)[0], 404)


class Reopen(unittest.TestCase):
    def test_a_closed_application_is_reopened_on_its_ledger_row_after_review_and_read_back(self):
        h = H([(TD.TARGET, COMPANY, ROLE)])
        self.assertEqual(h.close(TD.TARGET, "Rejected")[0], 200)
        writes = h.writes()
        status, body, _ = h.hub(h.world.ws.target)
        self.assertIn("Closed · Job Ledger", body)
        self.assertNotIn(f"/?o={D._page_id(TD.TARGET)}", links(body))                      # no Close on a closed record
        status, body, _ = h.reopen(h.world.ws.target)
        self.assertEqual(status, 200, body)
        self.assertIn("Opportunity reopened", body)
        self.assertIsNone(h.world.source.decision())
        self.assertEqual([(e["action"], e["outcome"]) for e in D.entries(h.world.source.history())], [(D.ACTION, "Rejected"), (D.REOPEN, "Rejected")])   # the close stays
        self.assertEqual(h.after, ["hiring", "hiring"])                                   # the close and the reopen each ask for the Hiring redraw
        self.assertGreater(h.writes(), writes)

    def test_a_closed_notes_page_moves_back_to_active_opportunities(self):
        h = H()
        ws = h.world.ws
        self.assertEqual(h.close(D._page_id(ws.target), "Withdrew")[0], 200)
        self.assertEqual(ws.where(ws.target), ws.retired[0])
        status, body, _ = h.reopen(ws.target)
        self.assertEqual(status, 200, body)
        self.assertEqual(ws.where(ws.target), ws.active[0])
        self.assertIn("Hiring decision (V7) · Reopened · ", ws.digest(ws.target)[-1][2])

    def test_a_notes_page_closed_as_hired_can_be_reopened_with_its_tick(self):
        h = H()
        ws = h.world.ws
        self.assertEqual(h.close(D._page_id(ws.target), "Hired")[0], 200)
        self.assertIn("If it was recorded as Hired: reopen it anyway", h.hub(ws.target)[1])
        self.assertEqual(h.reopen(ws.target)[0], 409)
        self.assertEqual(ws.where(ws.target), ws.retired[0])
        status, body, _ = h.reopen(ws.target, h="1")
        self.assertEqual(status, 200, body)
        self.assertEqual(ws.where(ws.target), ws.active[0])

    def test_reopen_is_offered_only_when_the_reopen_routing_reaches_the_same_record(self):
        h = H([(TD.TARGET, COMPANY, ROLE)])
        h.close(TD.TARGET, "Rejected")
        with mock.patch.object(D, "route", side_effect=[("ledger", {"id": TD.TARGET, "decision": "Rejected"}), D.fail("AMBIGUOUS")]):
            body = h.hub(h.world.ws.target)[1]
        self.assertIn("Reopen is not available here: DECISION_AMBIGUOUS", body)
        self.assertNotIn("Reopen this opportunity", body)

    def test_a_screen_the_release_does_not_ship_is_a_named_503_not_a_blank_error(self):
        h = H()
        real = close_app.importlib.import_module
        with mock.patch.object(close_app.importlib, "import_module", side_effect=lambda name: (_ for _ in ()).throw(ImportError()) if name.endswith("manage_app") else real(name)):
            status, body, _ = h.call("GET", "/manage", query={"o": "a" * 32})
        self.assertEqual(status, 503)
        self.assertIn("not on the Close service yet", body)

    def test_review_and_cancel_write_nothing_and_a_replay_is_already_recorded(self):
        h = H([(TD.TARGET, COMPANY, ROLE)])
        h.close(TD.TARGET, "Rejected")
        writes = h.writes()
        self.assertEqual(h.reopen(h.world.ws.target, step="cancel")[0], 200)
        self.assertEqual(h.writes(), writes)
        self.assertEqual(h.world.source.decision(), {"name": "Rejected"})

    def test_a_hired_outcome_needs_its_own_tick(self):
        h = H([(TD.TARGET, COMPANY, ROLE)])
        h.close(TD.TARGET, "Hired")
        self.assertIn("If it was recorded as Hired: reopen it anyway", h.hub(h.world.ws.target)[1])
        status, body, _ = h.reopen(h.world.ws.target)
        self.assertEqual(status, 409)
        self.assertIn("recorded as Hired", body)
        self.assertEqual(h.world.source.decision(), {"name": "Hired"})
        status, body, _ = h.reopen(h.world.ws.target, h="1")
        self.assertEqual(status, 200, body)
        self.assertIsNone(h.world.source.decision())

    def test_a_forged_expired_or_changed_form_writes_nothing(self):
        h = H([(TD.TARGET, COMPANY, ROLE)])
        h.close(TD.TARGET, "Rejected")
        form = h.fields(h.hub(h.world.ws.target)[1])
        writes = h.writes()
        for patch in ({"t": "0" * 64}, {"co": "Mallory Inc"}, {"exp": "1"}, {"o": "e" * 32}, {"k": "notes"}):
            self.assertEqual(h.call("POST", "/manage", form={**form, **patch, "step": "confirm"})[0], 403, patch)
        self.assertEqual(h.call("POST", "/manage", form={**form, "step": "nope"})[0], 403)
        self.assertEqual(h.writes(), writes)
        self.assertEqual(h.world.source.decision(), {"name": "Rejected"})

    def test_reopening_an_open_record_changes_nothing(self):
        h = H([(TD.TARGET, COMPANY, ROLE)])
        h.close(TD.TARGET, "Rejected")
        form = h.fields(h.hub(h.world.ws.target)[1])
        self.assertEqual(h.call("POST", "/manage", form={**form, "step": "confirm"})[0], 200)
        writes = h.writes()
        status, body, _ = h.call("POST", "/manage", form={**form, "step": "confirm"})              # the same signed form again: already open
        self.assertIn(status, (200, 409), body)
        self.assertEqual(h.writes(), writes)


if __name__ == "__main__":
    unittest.main()
