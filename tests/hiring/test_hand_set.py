"""LIFE-111 / LIFE-112 (Jim, Oct 25 2026): a hand-set hold (Paused, Waiting on employer, back to Active) and a hand-set next action / due date, set on Manage through a
signed review and confirm, stored on the opportunity's own record (its Notes page fold, or the Job Ledger row's HAND_SET property once it exists), read back; the Hiring
Pipeline shows the hold and takes it off the to-do list until new mail or Calendar evidence clears it; the opportunity page shows Jim's action beside the derived one until
Mark Complete clears it. Every name is invented; nothing is live."""
import html
import re
import unittest
from dataclasses import replace
from unittest import mock

from lifeos.hiring import compile as C, decision as D, models as M, render
from lifeos.interview import create, notion, template, view
from lifeos.jobs import hiring_pipeline
from lifeos.sources import close_app, hiring as stage
from tests.hiring import test_decision as TD, test_hiring as T
from tests.hiring.test_manage_app import H
from tests.hiring.test_mark_complete import lines, opp, page_with_fold, texts
from tests.hiring.test_notes_decision import COMPANY, ROLE

SET_AT, OP = "2026-10-05T19:00:00-05:00", "0123456789abcdef"
DERIVED = "Prepare for Interview 1 on 2026-10-28."


def forms(body):
    return [{k: html.unescape(v) for k, v in re.findall(r'type="hidden" name="(\w+)" value="([^"]*)"', chunk)} for chunk in re.findall(r"<form.*?</form>", body, re.S)]


def ledger_ready(source):
    """The Job Ledger once Jim has added the HAND_SET text property: the schema names it and every row carries it."""
    orig = source.call

    def call(method, path, body=None):
        out = orig(method, path, body)
        out.setdefault("properties", {})[D.HAND_SET] = {"type": "rich_text"}
        return out
    source.call = call
    for page in source.pages.values():
        page["properties"][D.HAND_SET] = {"rich_text": []}
    return source


def hand_text(source, pid=TD.TARGET):
    return "".join(r["plain_text"] for r in source.pages[pid]["properties"][D.HAND_SET]["rich_text"])


class M2(H):
    def hand_form(self, act, page=None):
        status, body, _ = self.hub(page or self.world.ws.target)
        assert status == 200, body
        return next(f for f in forms(body) if f.get("act") == act)

    def hand_review(self, act, **values):
        return self.call("POST", "/manage", form={**self.hand_form(act), **values})

    def hand(self, act, step="confirm", **values):
        status, body, _ = self.hand_review(act, **values)
        assert status == 200, body
        return self.call("POST", "/manage", form={**self.fields(body), "step": step})


def notes_page(**kw):
    h = M2(**kw)
    return h, page_with_fold(h, action=DERIVED)


class ManageNotesPage(unittest.TestCase):
    def test_the_hub_shows_the_state_and_both_forms_and_review_shows_without_writing(self):
        h, fold = notes_page()
        status, body, _ = h.hub(h.world.ws.target)
        self.assertEqual(status, 200, body)
        self.assertIn("<b>State:</b> Active", body)
        self.assertEqual(sorted(f.get("act") for f in forms(body) if f.get("act")), ["hold", "next"])
        self.assertIn('value="paused">Pause it', body)
        self.assertNotIn('value="active"', body)                                                       # Active is the state already
        status, body, _ = h.hand_review("hold", v="paused")
        self.assertEqual(status, 200, body)
        self.assertIn("Set it to Paused", body)
        self.assertEqual(h.writes(), (0, 0))

    def test_confirm_appends_one_line_to_the_fold_reads_it_back_and_asks_for_the_pipeline_redraw(self):
        h, fold = notes_page()
        before = lines(h, fold)
        status, body, _ = h.hand("hold", v="waiting")
        self.assertEqual(status, 200, body)
        self.assertIn("Recorded and read back", body)
        after = lines(h, fold)
        self.assertEqual(after[:-1], before)
        self.assertEqual(M.hold_of(after)["state"], M.WAITING)
        self.assertTrue(M.HOLD_LINE.fullmatch(after[-1]))
        self.assertEqual(h.after, ["hiring"])
        self.assertIn("Waiting on employer · set by Jim", h.hub(h.world.ws.target)[1])
        status, body, _ = h.hand("hold", v=M.ACTIVE)                                                   # set back to Active
        self.assertEqual(status, 200, body)
        self.assertIsNone(M.hold_of(lines(h, fold)))

    def test_cancel_writes_nothing_and_a_replayed_confirm_is_already_recorded(self):
        h, fold = notes_page()
        status, body, _ = h.hand_review("hold", v="paused")
        review = {**h.fields(body)}
        self.assertIn("Nothing changed", h.call("POST", "/manage", form={**review, "step": "cancel"})[1])
        self.assertEqual(h.writes(), (0, 0))
        self.assertEqual(h.call("POST", "/manage", form={**review, "step": "confirm"})[0], 200)
        status, body, _ = h.call("POST", "/manage", form={**review, "step": "confirm"})
        self.assertIn("already recorded", body)
        self.assertEqual(len(h.world.ws.writes()), 1)

    def test_a_forged_expired_foreign_or_altered_form_writes_nothing(self):
        h, fold = notes_page()
        review = h.fields(h.hand_review("hold", v="paused")[1])
        for patch in ({"t": "0" * 64}, {"c": "0" * 64}, {"v": "waiting"}, {"exp": "1"}, {"co": "Mallory Inc"}, {"act": "next"}, {"k": "ledger"}, {"o": "e" * 32}):
            self.assertEqual(h.call("POST", "/manage", form={**review, **patch, "step": "confirm"})[0], 403, patch)
        self.assertEqual(h.call("POST", "/manage", form={**review, "step": "confirm"}, now=h.clock.replace(year=h.clock.year + 1))[0], 403)
        self.assertEqual(h.call("POST", "/manage", form={**review, "step": "confirm"}, who={"login": "someone@example.com", "node": "n"})[0], 403)
        self.assertEqual(h.writes(), (0, 0))

    def test_a_next_action_and_due_date_are_stored_exactly_as_typed_and_an_invalid_date_is_refused(self):
        h, fold = notes_page()
        for bad in ({"nx": "Call Pat", "due": "2026-02-30"}, {"nx": "Call Pat", "due": "tomorrow"}, {"nx": "Call Pat", "due": "20261102"}, {"nx": "", "due": ""}, {"nx": "x" * 201}):
            status, body, _ = h.hand_review("next", **bad)
            self.assertEqual(status, 400, bad)
            self.assertIn("Nothing was changed", body)
        self.assertEqual(h.writes(), (0, 0))
        status, body, _ = h.hand("next", nx="Send the  work sample to Pat", due="2026-11-02")
        self.assertEqual(status, 200, body)
        self.assertEqual({k: v for k, v in M.next_of(lines(h, fold)).items() if k in ("text", "due")}, {"text": "Send the work sample to Pat", "due": "2026-11-02"})
        status, body, _ = h.hand("next", nx="Wait for the panel")                                    # no date typed: none is invented
        self.assertIsNone(M.next_of(lines(h, fold))["due"])
        self.assertEqual(h.after, [])                                                                   # the page shows it after the hourly run; no region redraw

    def test_a_write_that_does_not_read_back_is_degraded(self):
        h, fold = notes_page()
        review = h.fields(h.hand_review("hold", v="paused")[1])
        real = h.world.ws.call_once
        with mock.patch.object(h.world.ws, "call_once", lambda method, path, body=None, version=None: {} if path.endswith("/children") else real(method, path, body, version)):
            status, body, _ = h.call("POST", "/manage", form={**review, "step": "confirm"})
        self.assertEqual(status, 500)
        self.assertIn("DEGRADED · DECISION_READBACK_MISMATCH", body)
        self.assertNotIn("Recorded and read back", body)
        h.world.ws.fail.add("append")
        status, body, _ = h.call("POST", "/manage", form={**review, "step": "confirm"})
        self.assertEqual(status, 500)
        self.assertIn("DEGRADED · DECISION_WRITE_FAILED", body)
        self.assertEqual(h.after, [])

    def test_a_pursuit_that_now_routes_to_another_record_or_closed_is_a_409_with_nothing_written(self):
        h, fold = notes_page()
        review = h.fields(h.hand_review("hold", v="paused")[1])
        h.world.source.pages[TD.TARGET] = TD.Source.make(TD.TARGET, COMPANY, ROLE)                       # an Applied Ledger row appeared: the pursuit now lives on it
        status, body, _ = h.call("POST", "/manage", form={**review, "step": "confirm"})
        self.assertEqual(status, 409, body)
        self.assertIn("Opportunity changed", body)
        del h.world.source.pages[TD.TARGET]
        ws = h.world.ws
        ws.relocate(ws.target, ws.retired[0])                                                           # closed (moved to Retired) since the form was opened
        self.assertEqual(h.call("POST", "/manage", form={**review, "step": "confirm"})[0], 409)
        self.assertEqual(h.writes(), (0, 0))

    def test_a_page_without_the_life_os_fold_is_refused(self):
        h = M2()
        status, body, _ = h.hand("hold", v="paused")
        self.assertEqual(status, 409, body)
        self.assertIn("DECISION_NOT_A_LIFE_OS_PAGE", body)
        self.assertEqual(h.writes(), (0, 0))

    def test_a_closed_pursuit_offers_no_hand_set_forms(self):
        h, fold = notes_page()
        h.close(D._page_id(h.world.ws.target), "Withdrew")
        self.assertFalse([f for f in forms(h.hub(h.world.ws.target)[1]) if f.get("act")])


class ManageLedgerRow(unittest.TestCase):
    def test_without_the_ledger_property_nothing_is_offered_and_the_write_is_refused(self):
        h = M2([(TD.TARGET, COMPANY, ROLE)])
        body = h.hub(h.world.ws.target)[1]
        self.assertIn(f"the Job Ledger has no “{D.HAND_SET}” text property", body)
        self.assertFalse([f for f in forms(body) if f.get("act")])
        source = h.world.source
        with self.assertRaises(D.DecisionError) as caught:
            D.set_hand(source, "ledger", {"id": TD.TARGET, "company": COMPANY, "role": ROLE}, M.hold_line(M.PAUSED, SET_AT, OP), True)
        self.assertEqual(str(caught.exception), "DECISION_SCHEMA_MISSING")
        self.assertEqual(source.patches(), [])

    def test_with_the_property_confirm_writes_only_it_and_reads_it_back(self):
        h = M2([(TD.TARGET, COMPANY, ROLE)])
        ledger_ready(h.world.source)
        before = TD.Source.make(TD.TARGET, COMPANY, ROLE)["properties"]
        status, body, _ = h.hand("hold", v="paused")
        self.assertEqual(status, 200, body)
        status, body, _ = h.hand("next", nx="Call Pat", due="2026-11-02")
        self.assertEqual(status, 200, body)
        got = hand_text(h.world.source).splitlines()
        self.assertEqual((M.hold_of(got)["state"], M.next_of(got)["text"], len(got)), (M.PAUSED, "Call Pat", 2))     # the latest of each kind, nothing more
        self.assertEqual([p[2] for p in h.world.source.patches()], [(D.HAND_SET,), (D.HAND_SET,)])
        props = h.world.source.pages[TD.TARGET]["properties"]
        self.assertEqual({k: props[k] for k in before}, before)                                         # the decision, history, Applied and the rest are untouched
        self.assertEqual(h.world.ws.writes(), [])
        self.assertIn("Paused · set by Jim", h.hub(h.world.ws.target)[1])

    def test_a_dropped_ledger_write_is_degraded_and_a_decided_row_is_refused(self):
        h = M2([(TD.TARGET, COMPANY, ROLE)])
        ledger_ready(h.world.source)
        review = h.fields(h.hand_review("hold", v="paused")[1])
        h.world.source.drop_write = True
        status, body, _ = h.call("POST", "/manage", form={**review, "step": "confirm"})
        self.assertEqual(status, 500)
        self.assertIn("DEGRADED · DECISION_READBACK_MISMATCH", body)
        h.world.source.pages[TD.TARGET]["properties"]["Hiring Decision"] = {"select": {"name": "Rejected"}}
        with self.assertRaises(D.DecisionError) as caught:
            D.set_hand(h.world.source, "ledger", {"id": TD.TARGET, "company": COMPANY, "role": ROLE}, M.hold_line(M.PAUSED, SET_AT, OP), True)
        self.assertEqual(str(caught.exception), "DECISION_CLOSED")

    def test_text_in_the_property_that_is_not_ours_is_never_replaced(self):
        source = ledger_ready(TD.Source(rows=[(TD.TARGET, COMPANY, ROLE)]))
        source.pages[TD.TARGET]["properties"][D.HAND_SET] = {"rich_text": [{"plain_text": "Jim's own note"}]}
        with self.assertRaises(D.DecisionError) as caught:
            D.set_hand(source, "ledger", {"id": TD.TARGET, "company": COMPANY, "role": ROLE}, M.hold_line(M.PAUSED, SET_AT, OP), True)
        self.assertEqual(str(caught.exception), "DECISION_HISTORY_UNREADABLE")
        self.assertEqual(source.patches(), [])


def held_row(hand=(M.hold_line(M.PAUSED, SET_AT, OP),), company="Acme", role="Senior Program Manager"):
    return {**T.applied(company, role), "id": TD.TARGET, "hand": hand}


class Pipeline(unittest.TestCase):
    def test_a_held_row_shows_its_state_has_no_to_do_sorts_last_and_is_not_counted_active(self):
        got = T.compile_([held_row(), T.applied("Globex", "Program Director", "2026-09-20")])
        row = next(r for r in got["rows"] if r["company"] == "Acme")
        self.assertEqual((row["stage"], row["hold"]["state"]), (M.SUBMITTED, M.PAUSED))                 # the evidence stage is untouched
        self.assertEqual(T.cells(row)[1:3], ["Paused · Submitted", "No to-do · paused (set by Jim Oct 5)"])
        text, cells, _ = render.render(got, None, T.NOW)
        self.assertEqual([c[0][0] for c in cells], ["Globex — Program Director", "Acme — Senior Program Manager"])     # the Stale Globex row first, the paused one last
        self.assertEqual(text, "Updated 2:00 PM CT · 1 active · 1 paused or waiting")
        waiting = T.compile_([held_row((M.hold_line(M.WAITING, SET_AT, OP),))])["rows"][0]
        self.assertEqual(T.cells(waiting)[1], "Waiting on employer · Submitted")

    def test_a_notes_page_hold_counts_and_a_later_active_line_clears_it(self):
        page = dict(T.parent("p1", "Acme", "Senior Program Manager", rounds=1), holds=(M.hold_line(M.WAITING, SET_AT, OP),))
        self.assertEqual(T.compile_([], [page])["rows"][0]["hold"]["state"], M.WAITING)
        back = dict(page, holds=page["holds"] + (M.hold_line(M.ACTIVE, "2026-10-06T09:00:00-05:00", "f" * 16),))
        self.assertNotIn("hold", T.compile_([], [back])["rows"][0])

    def test_newer_calendar_or_mail_evidence_clears_the_hold_and_the_row_says_why(self):
        event = {"id": "e1", "title": "Interview Acme", "start": T.at(2)}
        old = T.compile_([held_row()], events=[dict(event, updated="2026-10-04T09:00:00-05:00")])["rows"][0]
        self.assertEqual(old["hold"]["state"], M.PAUSED)                                               # the invite was already known when Jim paused it
        new = T.compile_([held_row()], events=[dict(event, updated="2026-10-06T09:00:00-05:00")])["rows"][0]
        self.assertNotIn("hold", new)
        self.assertEqual((new["hold_cleared"]["by"], new["stage"]), ("Calendar", M.INTERVIEWING))
        self.assertIn("Paused cleared by new Calendar Oct 6", T.cells(new)[2])
        key = C._key("Acme", "Senior Program Manager")
        for extra in ({"outcomes": {key: {"kind": "REJECTION", "received": "2026-10-06T08:00:00-05:00"}}}, {"contacts": {key: "2026-10-06T08:00:00-05:00"}}):
            row = C.compile_rows([held_row()], [], [], None, T.OK, T.NOW, **extra)["rows"][0]
            self.assertEqual(row["hold_cleared"]["by"], "mail", extra)
        stale = C.compile_rows([held_row()], [], [], None, T.OK, T.NOW, contacts={key: "2026-10-01T08:00:00-05:00"})["rows"][0]
        self.assertIn("hold", stale)                                                                   # an older message changes nothing

    def test_a_cleared_hold_stays_cleared_after_its_evidence_leaves_the_window_until_jim_sets_a_new_one(self):
        event = {"id": "e1", "title": "Interview Acme", "start": T.at(2), "updated": "2026-10-06T09:00:00-05:00"}
        first = T.compile_([held_row()], events=[event])
        later = T.NOW + T.timedelta(days=40)
        again = C.compile_rows([held_row()], [], [], first, T.OK, later)["rows"][0]
        self.assertNotIn("hold", again)
        self.assertNotIn("cleared", T.cells(again, later)[2])                                          # the note shows for a week only
        fresh = C.compile_rows([held_row((M.hold_line(M.PAUSED, "2026-11-10T09:00:00-06:00", "e" * 16),))], [], [], first, T.OK, later)["rows"][0]
        self.assertEqual(fresh["hold"]["state"], M.PAUSED)

    def test_two_ledger_rows_for_one_pursuit_carry_no_hold(self):
        got = T.compile_([held_row(), {**T.applied("Acme", "Senior Program Manager"), "id": TD.SIBLING}])
        self.assertNotIn("hold", got["rows"][0])

    def test_the_page_reader_collects_hold_lines_from_a_page_with_rounds_and_the_adapter_reads_both_records(self):
        from tests.hiring.test_notes_decision import Workspace
        ws = Workspace()
        fold = ws.add("toggle", ws.target, notion.FOLD)
        ws.kids[ws.target].remove(fold)
        ws.kids[ws.target].insert(0, fold)
        line = M.hold_line(M.PAUSED, SET_AT, OP)
        for text in (notion.MARKERS["opportunity"], "v7-done:1;key=0123456789abcdef;on=2026-10-05", line):
            ws.add("paragraph", fold, text)
        found = {o.page_id: o for o in hiring_pipeline.read(ws, ws.root)}
        self.assertEqual((found[ws.target].rounds, found[ws.target].holds), (1, (line,)))
        self.assertEqual(stage.read_parents(ws, ws.root)[[p["id"] for p in stage.read_parents(ws, ws.root)].index(ws.target)]["holds"], (line,))
        source = ledger_ready(TD.Source(rows=[(TD.TARGET, COMPANY, ROLE)]))
        source.pages[TD.TARGET]["properties"][D.HAND_SET] = {"rich_text": [{"plain_text": line}]}
        self.assertEqual(stage.read_applied(source)[0]["hand"], (line,))
        self.assertEqual(stage.read_applied(TD.Source(rows=[(TD.TARGET, COMPANY, ROLE)]))[0]["hand"], ())       # no property yet: nothing, and nothing breaks

    def test_an_invites_update_time_is_read_and_a_missing_one_is_never_evidence(self):
        class Cal:
            def list_events(self, *window):
                return [{"id": "e1", "summary": "Interview Acme", "start": {"dateTime": "2026-10-08T10:00:00-05:00"}, "updated": "2026-10-06T14:00:00.000Z"},
                        {"id": "e2", "summary": "Interview Globex", "start": {"dateTime": "2026-10-08T11:00:00-05:00"}}]
        got = stage.read_events(Cal(), T.NOW)
        self.assertEqual((got[0]["updated"], "updated" in got[1]), ("2026-10-06T09:00:00-05:00", False))


class OpportunityPage(unittest.TestCase):
    NEXT = M.next_line("Send the work sample to Pat", "2026-11-02", SET_AT, OP)

    def test_jims_action_shows_beside_the_derived_one_with_its_own_label_and_complete_link(self):
        page = opp(close_base="https://edge.example.test", page_id="0" * 32, hand=(self.NEXT,))
        shown = texts(template.opportunity_page(page, "2026-10-24"))
        self.assertIn("PRIMARY ACTION · derived from the linked recruiter message", shown)             # the derived action keeps its own label
        self.assertIn("SET BY JIM · Send the work sample to Pat", shown)
        self.assertTrue(any(t.startswith("Due: 2026-11-02 · set by Jim") for t in shown))
        self.assertIn(f"v7-action:1;key={OP};text=Send the work sample to Pat", shown)                 # completable by its own key
        self.assertIn(self.NEXT, shown)                                                                 # carried forward as written
        self.assertIn(f"https://edge.example.test/complete?o={'0' * 32}&a={OP}", str(template.opportunity_page(page, "2026-10-24")))
        degraded = texts(template.opportunity_page(replace(page, request_url=None, stable_job_key="exampleco-stpm"), "2026-10-24"))
        self.assertTrue(any(t.startswith("DEGRADED · NO VERIFIED ACTION") for t in degraded))
        self.assertIn("SET BY JIM · Send the work sample to Pat", degraded)

    def test_new_evidence_never_overwrites_it_and_no_date_is_ever_invented(self):
        rounds = (view.Round("round-1", 1, "2026-10-28", None, "accepted"),)
        page = opp(hand=(self.NEXT,), rounds=rounds)
        self.assertEqual(view.hand_action(page), (OP, "Send the work sample to Pat", "2026-11-02"))
        self.assertEqual(view.primary_action(page, "2026-10-24")[2], "2026-10-28")                     # the derived action follows the evidence; Jim's stays
        undated = opp(hand=(M.next_line("Ask about the panel", None, SET_AT, OP),))
        self.assertEqual(view.hand_action(undated)[2], None)
        self.assertTrue(any(t.startswith("Due: No date set") for t in texts(template.opportunity_page(undated, "2026-10-24"))))

    def test_the_latest_line_on_either_record_wins(self):
        ledger = M.next_line("From the Ledger", None, "2026-10-06T09:00:00-05:00", "a" * 16)
        self.assertEqual(view.hand_action(opp(hand=(self.NEXT,), hand_ledger=(ledger,)))[1], "From the Ledger")
        self.assertIsNone(view.hand_action(opp(hand=(self.NEXT, M.next_line("", None, "2026-10-07T09:00:00-05:00", "b" * 16)))))

    def test_the_ledger_row_property_is_read_into_the_view(self):
        class Client:
            def call(self, method, path):
                return {"properties": {"Stable Job Key": {"type": "rich_text", "rich_text": [{"plain_text": "k1"}]},
                                       view.HAND_SET: {"type": "rich_text", "rich_text": [{"plain_text": OpportunityPage.NEXT + "\nnoise"}]}}}
        self.assertEqual(view.ledger_facts(Client(), "row", "k1")[1]["hand_ledger"], (self.NEXT,))

    def test_a_line_manage_appended_is_carried_by_the_hourly_refresh_never_counts_as_an_edit_and_shows(self):
        from tests.interview import test_notion as TN, test_ui3c as U
        current = replace(U.opportunity(), stable_job_key="recruiter-exampleco-pm")
        client, page_id = U.written(current)
        fold = client.blocks[page_id][0]["id"]
        hold = M.hold_line(M.PAUSED, SET_AT, "f" * 16)
        client.blocks[fold] += [TN.block(self.NEXT, id="hand-1"), TN.block(hold, id="hand-2")]          # what decision.set_hand appends
        out = {"writes": 0}
        self.assertEqual(create.refresh(client, page_id, current, True, out), "parent_refreshed")
        shown = U.texts(client, page_id)
        self.assertTrue({self.NEXT, hold, "SET BY JIM · Send the work sample to Pat"} <= set(shown), shown)
        self.assertEqual(create.refresh(client, page_id, current, True, out), "parent_unchanged")

    def test_mark_complete_on_its_key_clears_it_on_the_page_and_on_manage(self):
        h = M2()
        fold = page_with_fold(h, action=DERIVED)
        ws = h.world.ws
        ws.add("paragraph", fold, f"v7-action:1;key={OP};text=Send the work sample to Pat")              # what the page writer writes for it
        ws.add("paragraph", fold, self.NEXT)
        hub = h.hub(ws.target)[1]
        self.assertIn("Your next action:</b> Send the work sample to Pat", hub)
        self.assertIn(f"/complete?o={D._page_id(ws.target)}&amp;a={OP}", hub)
        status, body, _ = h.call("GET", "/complete", {"o": ws.target, "a": OP})
        self.assertEqual(status, 200, body)
        status, body, _ = h.call("POST", "/complete", form={**h.fields(body), "step": "confirm"})
        self.assertEqual(status, 200, body)
        blocks = [ws.blocks[k] for k in ws.kids[fold]]
        page = opp(done=create.done_lines(blocks), hand=create.hand_lines(blocks))
        self.assertIsNone(view.hand_action(page))
        self.assertFalse([t for t in texts(template.opportunity_page(page, "2026-10-24")) if "SET BY JIM" in t or t.startswith(f"v7-action:1;key={OP}")])
        self.assertIn(self.NEXT, texts(template.opportunity_page(page, "2026-10-24")))                # the line itself stays as history
        self.assertNotIn("Your next action", h.hub(ws.target)[1])


class Contract(unittest.TestCase):
    def test_the_line_formats_and_the_property_name_are_one_contract(self):
        self.assertEqual((view.HOLD_LINE.pattern, view.NEXT_LINE.pattern, view.HAND_SET), (M.HOLD_LINE.pattern, M.NEXT_LINE.pattern, D.HAND_SET))
        self.assertTrue(M.hold_line(M.PAUSED, SET_AT, OP).startswith(hiring_pipeline.HOLD_PREFIX))
        self.assertTrue(close_app.ACTION_LINE.fullmatch(f"v7-action:1;key={OP};text=x") and close_app.DONE_LINE.fullmatch(f"v7-done:1;key={OP};on=2026-10-06"))

    def test_hand_set_lines_are_carried_and_never_count_as_an_edit(self):
        para = lambda t: {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": t}]}}            # noqa: E731
        hold, nxt = M.hold_line(M.PAUSED, SET_AT, OP), OpportunityPage.NEXT
        self.assertEqual(create.hand_lines([para(notion.MARKERS["opportunity"]), para(hold), para("v7-hold:1;state=maybe"), para(nxt)]), (hold, nxt))
        nodes = [{"text": notion.FOLD, "children": [{"text": notion.MARKERS["opportunity"]}, {"text": hold}, {"text": nxt}]}]
        self.assertEqual(create._undone(nodes), [{"text": notion.FOLD, "children": [{"text": notion.MARKERS["opportunity"]}]}])

    def test_errors_and_logs_carry_no_text_jim_typed(self):
        h, fold = notes_page()
        review = h.fields(h.hand_review("next", nx="Secret plan for Pat")[1])
        h.world.ws.fail.add("append")
        status, body, _ = h.call("POST", "/manage", form={**review, "step": "confirm"})
        self.assertEqual(status, 500)
        detail = re.search(r'<div class="notice danger">(.*?)</div>', body).group(1)
        self.assertEqual(detail, "Status: DEGRADED · DECISION_WRITE_FAILED")


if __name__ == "__main__":
    unittest.main()
