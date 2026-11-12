"""LIFE-47 S2 (Career): the Close Opportunity application, driven the way the Edge gate calls it, against the synthetic Ledger and Notes stand-ins already used for S1. Every name here is invented;
nothing is live. A `Both` client stands for the one Notion integration (Ledger rows and Notes pages); GET gets it behind the gate's real `ReadOnly` facade."""
import ast
import html
import os
import re
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

from lifeos.edge import close_gate
from lifeos.edge.oracle import close_release as rel
from lifeos.hiring import close_screen, decision as D, models as M
from lifeos.sources import close_app
from lifeos.sources.pipeline import hiring as hiring_source
from tests.edge.support import github
from tests.hiring import test_decision as TD, test_hiring as T
from tests.hiring.test_notes_decision import COMPANY, ROLE, World, options

ROOT = T.ROOT
SECRET, TOKEN = b"link-signing-key-0123456789abcdef", b"notion-token-secret"
LOGIN, NOW = "jim@example.com", T.NOW
WHO = {"login": LOGIN, "node": "node-1"}
NEW = [o for o in M.OUTCOMES if o != "Rejected"]
WRITERS = ("PATCH", "APPEND", "DELETE", "POST")


class Both:
    """One client for Ledger rows and Notes pages, like the real integration."""

    def __init__(self, world):
        self.w = world
        self.source = world.source.source

    def call(self, method, path, body=None):
        if path.startswith("/blocks/") and path.split("/")[2].split("?")[0].replace("-", "") in self.w.source.pages:
            return self.w.source.call(method, path, body)                                    # a Ledger row's own page body (LIFE-361 rows are the opportunity page)
        return (self.w.source if path.startswith("/data_sources") else self.w.ws).call(method, path, body)

    def call_once(self, *args, **kw):
        return self.w.ws.call_once(*args, **kw)

    def get_page(self, page_id):
        return self.w.source.get_page(page_id)

    def query_data_source(self, source_id=None, body=None):
        return self.w.source.query_data_source(source_id, body)

    def update_page_properties(self, page_id, properties):
        return self.w.source.update_page_properties(page_id, properties)


class Harness:
    def __init__(self, ledger_rows=(), full=True, **kw):
        self.world = World(ledger_rows=list(ledger_rows), **kw)
        if full:
            options(self.world.source, NEW)
        self.cfg = {"ledger": TD.LEDGER, "pipeline": self.world.ws.root}
        self.clock = NOW
        self.after = []

    def writes(self):
        return (len([e for e in self.world.source.log if e[0] == "PATCH"]), len(self.world.ws.writes()))

    def call(self, method, path="/", query=None, form=None, who=WHO, write=True, now=None):
        req = {"method": method, "path": path, "query": query or {}, "headers": {}, "body": b"", "form": form or {}}
        secrets = {"link": SECRET, "config": self.cfg}
        both = Both(self.world)
        if method == "GET":
            secrets["reader"] = close_gate.ReadOnly(both)
        else:
            secrets["notion"], secrets["after"] = TOKEN, self.after             # the gate's refresh requests (close_gate.REFRESH), as close_gate.main passes them
        def factory(cfg):
            assert method != "GET", "GET must not build a writing client"
            return both

        with mock.patch.object(close_app, "_now", lambda: now or self.clock), mock.patch.object(close_app, "Client", factory):
            status, ctype, body, extra = close_app.handle(req, who, secrets)
        return status, body.decode(), ctype

    def fields(self, text):
        return {k: html.unescape(v) for k, v in re.findall(r'type="hidden" name="(\w+)" value="([^"]*)"', text)}

    def open(self, pid):
        status, body, _ = self.call("GET", query={"o": pid})
        return status, body, self.fields(body)

    def review(self, pid, outcome, notes="", **kw):
        _, _, f = self.open(pid)
        status, body, _ = self.call("POST", form={**f, "step": "review", "outcome": outcome, "notes": notes}, **kw)
        return status, body, self.fields(body)

    def close(self, pid, outcome, notes="", **kw):
        status, body, f = self.review(pid, outcome, notes)
        assert status == 200, body
        return self.call("POST", form={**f, "step": "confirm"}, **kw)


STAGED = "ee" * 16


def staged_harness(**kw):
    """An interview pursuit's row (LIFE-361): Stage set, Applied not ticked. LIFE-353: it closes on the row like any application."""
    h = Harness([(STAGED, COMPANY, ROLE)], **kw)
    h.world.source.pages[STAGED] = TD.Source.make(STAGED, COMPANY, ROLE, applied=False, stage="Interviewing")
    return h


class Get(unittest.TestCase):
    def test_get_shows_the_exact_record_makes_no_write_and_leaks_nothing(self):
        for pid, h in ((TD.TARGET, Harness([(TD.TARGET, COMPANY, ROLE)])), (STAGED, staged_harness())):
            before = h.writes()
            status, body, fields = h.open(pid)
            self.assertEqual((status, h.writes()), (200, before))
            self.assertTrue(all(r in body for r in M.OUTCOMES) and COMPANY in body and "Requisition not recorded" in body and "Unavailable on this screen" in body)
            self.assertEqual(set(fields), {"o", "k", "co", "ro", "exp", "st", "t"})
            self.assertFalse({SECRET.decode(), TOKEN.decode()} & set(re.findall(r"[\w-]{20,}", body)) or SECRET.decode() in body or TOKEN.decode() in body)
            self.assertNotIn("Interviewing", body)                                              # no stage is invented

    def test_review_and_edit_render_from_the_signed_fingerprint_without_reading_notion(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        _, _, f = h.open(TD.TARGET)
        before = h.writes()
        with mock.patch.object(close_app, "resolve", side_effect=AssertionError("Review and Edit must not read Notion")):
            status, body, rf = (lambda r: (r[0], r[1], h.fields(r[1])))(h.call("POST", form={**f, "step": "review", "outcome": "Rejected"}))
            self.assertEqual(status, 200)
            self.assertTrue(COMPANY in body and ROLE in body and "Confirm closure" in body)
            status, body, _ = h.call("POST", form={**rf, "step": "edit"})
            self.assertEqual(status, 200)
        self.assertEqual(h.writes(), before)
        self.assertEqual(h.call("POST", form={**rf, "step": "confirm"})[0], 200)                 # Confirm resolves afresh and records

    def test_shown_fields_that_do_not_match_the_signed_fingerprint_are_refused(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        _, _, f = h.open(TD.TARGET)
        before = h.writes()
        for key, value in (("co", "Another Company"), ("ro", "Another Role"), ("k", "notes"), ("k", "other")):
            status, body, _ = h.call("POST", form={**f, key: value, "step": "review", "outcome": "Rejected"})
            self.assertEqual(status, 403, key)
            self.assertIn("no longer valid", body)
        self.assertEqual(h.writes(), before)

    def test_the_stylesheet_is_served_and_pages_obey_the_gates_csp(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        status, css, ctype = h.call("GET", "/close.css")
        self.assertEqual((status, ctype), (200, "text/css; charset=utf-8"))
        self.assertTrue("prefers-color-scheme:dark" in css and "max-width:690px" in css and "@import" not in css and "url(" not in css)
        self.assertTrue(".btn:not(:disabled):hover" in css and ".btn.spin::before" in css and "prefers-reduced-motion" in css)   # Jim, Oct 24: hover state and a spinner
        status, js, ctype = h.call("GET", "/close.js")
        self.assertEqual((status, ctype), (200, "text/javascript; charset=utf-8"))
        self.assertTrue("busy" in js and "spin" in js and not re.search(r"fetch|XMLHttpRequest|innerHTML|eval|cookie|localStorage|http", js))   # only toggles classes
        self.assertTrue("a.target" in js and '"download"' in js and "setTimeout(idle,15000)" in js)   # a new-tab link never sticks busy; a stopped load clears itself
        self.assertTrue('hasAttribute("data-wait")' in js and "textContent" in js and "setInterval(tick,1000)" in js)   # a long step (data-wait) counts up until its reply arrives, text only
        pages = [h.open(TD.TARGET)[1], h.review(TD.TARGET, "Hired")[1], h.close(TD.TARGET, "Hired")[1]]
        for body in pages:
            self.assertEqual(re.findall(r"<script[^>]*>", body.lower()), ['<script src="/close.js" defer>'])   # the one same-origin script, never inline
            self.assertTrue('href="/close.css"' in body and "</script><" in body and "<style" not in body.lower() and not re.search(r"\sstyle=|\son\w+=|javascript:", body.lower()))

    def test_only_get_post_and_the_known_paths_are_answered(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        self.assertEqual(h.call("GET", "/other")[0], 404)
        self.assertEqual(h.call("PUT", "/")[0], 404)
        self.assertEqual(h.call("GET", "/", query={})[0], 400)
        self.assertEqual(h.call("GET", "/", query={"o": "not-an-id"})[0], 400)

    def test_an_unknown_record_a_notes_page_or_a_closed_row_is_refused_and_nothing_is_written(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        notes_id = h.world.ws.target.replace("-", "")                                           # LIFE-353: an opportunity page carries no decision; Close takes a Ledger row id
        for pid in ("ab" * 16, notes_id):
            status, body, _ = h.open(pid)
            self.assertEqual(status, 409)
            self.assertIn("DECISION_NO_MATCH", body)
        unapplied = Harness([(STAGED, COMPANY, ROLE)])
        unapplied.world.source.pages[STAGED]["properties"]["Applied"] = {"checkbox": False}     # neither Applied nor a Stage: not a pursuit
        self.assertIn("DECISION_NO_MATCH", unapplied.open(STAGED)[1])
        closed = Harness([(TD.TARGET, COMPANY, ROLE)])
        closed.world.source.pages[TD.TARGET]["properties"]["Hiring Decision"] = {"select": {"name": "Passed"}}
        self.assertEqual(closed.open(TD.TARGET)[0], 409)
        for each in (h, unapplied, closed):
            self.assertEqual(each.writes(), (0, 0))

    def test_two_rows_for_one_company_and_role_each_open_their_own_record(self):
        dup = Harness([(TD.TARGET, COMPANY, ROLE), (TD.SIBLING, COMPANY, ROLE)])
        for pid in (TD.TARGET, TD.SIBLING):
            status, _, f = dup.open(pid)
            self.assertEqual((status, f["o"]), (200, pid))
        self.assertEqual(dup.close(TD.SIBLING, "Rejected")[0], 200)
        self.assertEqual((dup.world.source.decision(TD.TARGET), dup.world.source.decision(TD.SIBLING)), (None, {"name": "Rejected"}))


class Post(unittest.TestCase):
    def test_review_writes_nothing_shows_the_exact_choice_and_validates_the_form(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        status, body, f = h.review(TD.TARGET, "Withdrew", "Took another job")
        self.assertEqual((status, h.writes()), (200, (0, 0)))
        self.assertTrue(COMPANY in body and ROLE in body and "Withdrew" in body and "Took another job" in body and "c" in f)
        for outcome, notes in (("", ""), ("Maybe", ""), ("Other", ""), ("Hired", "x" * 201)):
            status, body, _ = h.review(TD.TARGET, outcome, notes)
            self.assertEqual((status, h.writes()), (400, (0, 0)))
            self.assertIn("Choose a closure reason", body)
        _, _, f = h.open(TD.TARGET)
        self.assertEqual(h.call("POST", form={**f, "step": "cancel"})[0], 200)
        self.assertEqual(h.writes(), (0, 0))
        self.assertIn("Withdrew", h.call("POST", form={**f, "step": "edit", "outcome": "Withdrew", "notes": ""})[1])

    def test_every_outcome_is_recorded_on_the_ledger_row_only_after_read_back_and_no_page_moves(self):
        for outcome in M.OUTCOMES:
            notes = "short explanation" if outcome == "Other" else ""
            for pid, h in ((TD.TARGET, Harness([(TD.TARGET, COMPANY, ROLE)])), (STAGED, staged_harness())):
                status, body, _ = h.close(pid, outcome, notes)
                self.assertEqual((status, h.world.source.decision(pid), "Opportunity closed" in body), (200, {"name": outcome}, True), outcome)
                self.assertEqual([e["outcome"] for e in D.entries(h.world.source.history(pid))], [outcome])
                self.assertEqual(h.world.ws.writes(), [])

    def test_a_lead_is_closed_with_one_of_three_reasons_recorded_as_an_existing_outcome_and_a_fixed_note(self):
        for label, outcome, fixed in (("I'm passing", "Withdrew", "Passed: not interested"), ("Not a real job", "Other", "Not a real job"), ("Went nowhere", "Other", "Went nowhere")):
            h = staged_harness()
            self.assertIn(label, html.unescape(h.open(STAGED)[1]))
            status, body, _ = h.close(STAGED, label, "")
            self.assertEqual((status, h.world.source.decision(STAGED), "Opportunity closed" in body), (200, {"name": outcome}, True), label)
            self.assertIn(fixed, D.entries(h.world.source.history(STAGED))[-1]["reason"])

    def test_a_missing_ledger_option_or_a_changed_record_writes_nothing(self):
        bare = Harness([(TD.TARGET, COMPANY, ROLE)], full=False)
        status, body, _ = bare.close(TD.TARGET, "Hired")
        self.assertEqual((status, bare.writes()[0], "DECISION_SCHEMA_MISSING" in body, "Opportunity closed" in body), (409, 0, True, False))
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        _, _, f = h.review(TD.TARGET, "Rejected")
        h.world.source.pages[TD.TARGET]["properties"]["Company"] = {"rich_text": TD.rich(COMPANY + " Renamed")}
        status, body, _ = h.call("POST", form={**f, "step": "confirm"})
        self.assertEqual((status, h.writes()[0], "Opportunity changed" in body or "STALE" in body), (409, 0, True))

    def test_a_missing_expired_forged_or_foreign_confirmation_is_refused_with_no_write(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE), (TD.SIBLING, COMPANY, "Director, Delivery")])
        _, _, f = h.open(TD.TARGET)
        review = {**f, "step": "review", "outcome": "Hired", "notes": ""}
        cases = {"no token": {k: v for k, v in review.items() if k != "t"}, "forged token": {**review, "t": "0" * 64}, "other record": {**review, "o": TD.SIBLING},
                 "other state": {**review, "st": "0" * 16}, "bad expiry": {**review, "exp": "x"}}
        for name, form in cases.items():
            self.assertEqual(h.call("POST", form=form)[0], 403, name)
        self.assertEqual(h.call("POST", form=review, who={**WHO, "login": "someone@example.com"})[0], 403)         # a token is bound to the login
        self.assertEqual(h.call("POST", form=review, now=NOW + timedelta(minutes=16))[0], 403)                      # and expires
        _, _, c = h.review(TD.TARGET, "Hired")
        for name, form in {"no confirmation": {**c, "step": "review"}, "other outcome": {**c, "step": "confirm", "outcome": "Rejected"}, "other note": {**c, "step": "confirm", "notes": "changed"},
                           "no signature": {k: v for k, v in {**c, "step": "confirm"}.items() if k != "c"}}.items():
            status, body, _ = h.call("POST", form=form)
            self.assertNotIn("Opportunity closed", body, name)
        self.assertEqual(h.writes(), (0, 0))

    def test_a_replay_is_idempotent_and_a_conflicting_decision_fails_closed(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        _, _, a = h.review(TD.TARGET, "Rejected")
        _, _, b = h.review(TD.TARGET, "Hired")
        self.assertEqual(h.call("POST", form={**a, "step": "confirm"})[0], 200)
        replay = h.call("POST", form={**a, "step": "confirm"})
        self.assertEqual((replay[0], "already recorded" in replay[1], h.writes()[0], len(D.entries(h.world.source.history()))), (200, True, 1, 1))
        status, body, _ = h.call("POST", form={**b, "step": "confirm"})                     # a second form, issued while the job was still open
        self.assertEqual((status, h.world.source.decision(), "Opportunity closed" in body), (409, {"name": "Rejected"}, False))

    def test_a_backend_failure_or_a_read_back_mismatch_never_looks_like_success(self):
        for attr in ("fail_write", "drop_write"):
            h = Harness([(TD.TARGET, COMPANY, ROLE)])
            setattr(h.world.source, attr, True)
            status, body, _ = h.close(TD.TARGET, "Rejected")
            self.assertEqual((status, "Opportunity closed" in body, "Could not verify closure" in body), (500, False, True), attr)
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        h.world.source.tamper = True
        self.assertNotIn("Opportunity closed", h.close(TD.TARGET, "Rejected")[1])

    def test_success_needs_the_decision_layers_verified_read_back_and_each_form_has_its_own_stable_operation(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        _, _, a = h.review(TD.TARGET, "Rejected")
        _, _, b = h.review(TD.TARGET, "Hired")
        seen = []
        with mock.patch.object(close_app.D, "confirm_rejected", lambda client, req, live, now, *rest: seen.append(req) or {"status": "recorded", "verified": False}):
            status, body, _ = h.call("POST", form={**a, "step": "confirm"})
            self.assertEqual((status, "Opportunity closed" in body), (500, False))
            h.call("POST", form={**a, "step": "confirm"})
            h.call("POST", form={**b, "step": "confirm"})
        self.assertEqual((seen[0]["op"] == seen[1]["op"], seen[0]["op"] == seen[2]["op"], seen[0]["action"], seen[0]["outcome"]), (True, False, "CONFIRM_REJECTED", "Rejected"))

    def test_the_review_page_has_one_confirm_button_and_edit_never_confirms(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        buttons = re.findall(r'<button[^>]*name="step" value="(\w+)"[^>]*>([^<]*)</button>', h.review(TD.TARGET, "Hired")[1])
        self.assertEqual([(v, t) for v, t in buttons if v == "confirm"], [("confirm", "Confirm closure")])
        self.assertEqual([t for v, t in buttons if v == "edit"], ["← Edit reason"])

    def test_the_same_page_id_listed_twice_is_ambiguous(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        pages = D.ledger_pages(Both(h.world))
        with mock.patch.object(close_app.D, "ledger_pages", lambda client: pages + pages):
            with self.assertRaises(D.DecisionError) as error:
                close_app.resolve(Both(h.world), h.cfg, TD.TARGET)
        self.assertEqual(str(error.exception), "DECISION_AMBIGUOUS")

    def test_a_closure_the_screen_records_leaves_the_compiled_hiring_rows_for_every_outcome(self):
        for outcome in M.OUTCOMES:
            notes = "short explanation" if outcome == "Other" else ""
            for rows in ([(TD.TARGET, COMPANY, ROLE), (TD.SIBLING, COMPANY, "Director, Delivery")],):
                h = Harness(rows)
                self.assertEqual(h.close(TD.TARGET, outcome, notes)[0], 200, outcome)
                ledger_rows, _ = close_app._state(Both(h.world), h.cfg["pipeline"])
                parents = hiring_source.read_parents(Both(h.world), h.cfg["pipeline"])                # the hourly job's own reader, interview counts included
                compiled = T.compile_([dict(r, applied_on="2026-10-02") for r in ledger_rows], parents)
                listed = [(r["company"], r["role"]) for r in compiled["rows"]]
                self.assertNotIn((COMPANY, ROLE), listed, outcome)                                    # the closed pursuit is no longer an active row
                self.assertEqual(compiled["reasons"], [], outcome)                                     # not flagged as ambiguous either
                self.assertIn((COMPANY, "Director, Delivery"), listed)                                # and nothing else left with it

    def test_every_result_page_has_the_same_neutral_subtitle(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        pages = [h.close(TD.TARGET, "Hired")[1], h.open(TD.TARGET)[1], h.call("GET", query={})[1],
                 h.call("POST", form={"step": "review"})[1], close_screen.result("a", "b", "c", "d").decode()]
        for body in pages:
            self.assertIn("A closure counts as done only after the canonical record is read back.", body)
            self.assertNotIn("Nothing else was changed", body)

    def test_the_close_route_never_reopens_and_manage_is_its_own_route(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        self.assertEqual(h.close(TD.TARGET, "Hired")[0], 200)
        _, _, f = h.review(TD.TARGET, "Hired")                                                # a closed record offers no new form
        self.assertEqual(h.open(TD.TARGET)[0], 409)
        smuggled = {"o": TD.TARGET, "step": "confirm", "action": "REOPEN", "reopen_hired": "true", "outcome": "Rejected"}
        self.assertEqual(h.call("POST", form=smuggled)[0], 403)
        self.assertEqual(h.world.source.decision(), {"name": "Hired"})
        text = (ROOT / "lifeos/sources/close_app.py").read_text() + (ROOT / "lifeos/hiring/close_screen.py").read_text()
        self.assertFalse(re.search(r"D\.REOPEN|reopen_hired|\"REOPEN\"", text))
        self.assertEqual(h.call("GET", "/manage")[0], 400)                                    # E14 (Jim, Oct 24): Manage and Reopen live in manage_app, with their own signed forms

    def test_hostile_text_is_escaped_in_every_page(self):
        evil = "<script>alert(1)</script> & Co"
        h = Harness([(TD.TARGET, evil, ROLE)])
        pages = [h.open(TD.TARGET)[1], h.review(TD.TARGET, "Other", "<b>n</b>")[1], h.close(TD.TARGET, "Other", "<b>n</b>")[1]]
        for body in pages:
            self.assertTrue("<script>alert" not in body and "<b>n</b>" not in body)
        self.assertIn("&lt;script&gt;", pages[0])


class Contract(unittest.TestCase):
    def test_the_closure_literal_covers_every_import_and_the_release_verifier_stages_and_imports_it(self):
        app = (ROOT / rel.APP).read_bytes()
        shipped = set(rel.closure(app))
        self.assertEqual(set(close_app.CLOSURE) - shipped, set())
        seen, todo = set(), [rel.APP]
        while todo:
            path = todo.pop()
            if path in seen:
                continue
            seen.add(path)
            for node in ast.walk(ast.parse((ROOT / path).read_text())):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] + [f"{node.module}.{a.name}" for a in node.names] if isinstance(node, ast.ImportFrom) and node.module else []
                for mod in (m for m in mods if m.startswith("lifeos")):
                    parts = mod.split(".")
                    for n in range(1, len(parts) + 1):                                                  # a module needs every package initializer above it too
                        for cand in ("/".join(parts[:n]) + ".py", "/".join(parts[:n]) + "/__init__.py"):
                            if (ROOT / cand).is_file():
                                todo.append(cand)
        self.assertEqual(seen - shipped, set(), "an import the release would not ship")
        files = {p: (ROOT / p).read_bytes() for p in shipped}
        dest = os.path.join(tempfile.mkdtemp(), "tree")
        rel.stage("a" * 40, dest, github(files))                                                  # the real verifier: regular blobs, hashes, and an isolated import of the app and the gate

    def test_the_release_verifier_and_the_installed_service_use_the_relocated_callable_handler(self):
        self.assertEqual(rel.APP, "lifeos/sources/close_app.py")
        self.assertIn("lifeos/sources/__init__.py", rel.EDGE)
        unit = next(line for line in (ROOT / "lifeos/edge/oracle/install-close.sh").read_text().splitlines() if line.startswith("ExecStart="))
        self.assertIn("from lifeos.sources.close_app import handle; sys.exit(close_gate.main(handle))", unit)
        self.assertNotIn("lifeos.hiring.close_app", unit)
        self.assertTrue(callable(close_app.handle) and close_app.handle.__module__ == "lifeos.sources.close_app")
        self.assertFalse((ROOT / "lifeos/hiring/close_app.py").exists())                              # no wrapper is left in the hiring package

    def test_no_stage_workflow_or_schedule_reaches_the_application(self):
        for path in list((ROOT / ".github/workflows").glob("*.yml")) + [ROOT / "lifeos/run.py"]:
            text = path.read_text()
            for name in ("close_app", "close_screen"):
                self.assertNotIn(name, text, path.name)
        from lifeos import run as entry
        self.assertFalse([n for n, s in entry.STAGES.items() if "close_" in str(getattr(s, "target", ""))])

    def test_the_budget_and_the_files(self):
        lines = sum(1 for p in ("lifeos/sources/close_app.py", "lifeos/hiring/close_screen.py") for line in (ROOT / p).read_text().splitlines() if line.strip())
        self.assertLessEqual(lines, 330)                               # E05 Mark Complete lives here too (Jim, Oct 23: the 300-line cap is suspended for the Jobs/Interview OS delivery)


class Refresh(unittest.TestCase):
    """Jim, Oct 24 ("approve the refresh trigger"): after a verified close the app asks the gate to redraw the Hiring Pipeline region now; nothing else asks."""

    def test_a_verified_close_asks_for_the_hiring_redraw_once(self):
        for pid, h in ((TD.TARGET, Harness([(TD.TARGET, COMPANY, ROLE)])), (STAGED, staged_harness())):
            self.assertEqual(h.close(pid, "Rejected")[0], 200)
            self.assertEqual(h.after, ["hiring"])

    def test_review_cancel_and_a_refused_or_unverified_close_ask_nothing(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        status, _, f = h.review(TD.TARGET, "Rejected")
        h.call("POST", form={**f, "step": "cancel"})
        h.call("POST", form={**f, "step": "confirm", "c": "forged"})
        with mock.patch.object(close_app.D, "confirm_rejected", lambda *a: {"status": "recorded", "verified": False}):
            self.assertEqual(h.call("POST", form={**f, "step": "confirm"})[0], 500)
        self.assertEqual((status, h.after), (200, []))

    def test_the_refresh_run_queues_with_the_after_tick_writers_and_runs_only_the_hiring_job(self):
        text = (ROOT / ".github/workflows/domains.yml").read_text()
        self.assertIn('options: ["none", "hiring", "recruiters"]', text)
        self.assertIn("group: ${{ (github.event_name == 'workflow_dispatch' && (inputs.refresh == 'hiring' || inputs.refresh == 'recruiters')) && 'life-os-v7-domains' ||", text)
        self.assertEqual(text.count("inputs.refresh == 'hiring'"), 2)                             # the group and the hiring job's `if`, nothing else
        self.assertEqual(text.count("if: ${{ inputs.refresh != 'recruiters' }}"), 4)              # recruiters refresh: mail stage, outcome scan, Interview OS and job-page outreach skipped
        self.assertIn("inputs.recruiters || inputs.refresh == 'recruiters') }}", text)               # the Recruiters job runs, so its Recruiter Outreach step does
        self.assertIn("RECRUITERS_STAGE_OUTCOME: ${{ steps.recruiters.outcome }}", text)              # 'skipped' on a refresh: the card redraws and says Redrawn
        self.assertEqual(close_gate.REFRESH["hiring"], {"refresh": "hiring", "live": "true"})          # the name the app appends is the gate's, and its input is this workflow's


class Speed(unittest.TestCase):
    """Jim, Oct 24: "the confirm closure button takes a bit too long". LIFE-353: a confirm reads the Ledger's pursuit rows once and never walks the opportunity pages."""

    def test_a_confirm_scans_the_ledger_once_and_never_walks_the_pages(self):
        for pid, h in ((TD.TARGET, Harness([(TD.TARGET, COMPANY, ROLE), (TD.SIBLING, COMPANY, "Director, Delivery")])), (STAGED, staged_harness())):
            _, _, f = h.review(pid, "Rejected")
            src, ws = len(h.world.source.log), len(h.world.ws.log)
            status, body, _ = h.call("POST", form={**f, "step": "confirm"})
            self.assertEqual(status, 200, body)
            self.assertEqual((len([e for e in h.world.source.log[src:] if e[0] == "QUERY"]), h.world.ws.log[ws:]), (1, []))


if __name__ == "__main__":
    unittest.main()
