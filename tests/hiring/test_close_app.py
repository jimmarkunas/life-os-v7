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
from lifeos.hiring import close_app, close_screen, decision as D, models as M
from tests.edge.test_close_release import github
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

    def writes(self):
        return (len([e for e in self.world.source.log if e[0] == "PATCH"]), len(self.world.ws.writes()))

    def call(self, method, path="/", query=None, form=None, who=WHO, write=True, now=None):
        req = {"method": method, "path": path, "query": query or {}, "headers": {}, "body": b"", "form": form or {}}
        secrets = {"link": SECRET, "config": self.cfg}
        both = Both(self.world)
        if method == "GET":
            secrets["reader"] = close_gate.ReadOnly(both)
        else:
            secrets["notion"] = TOKEN
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


class Get(unittest.TestCase):
    def test_get_shows_the_exact_record_makes_no_write_and_leaks_nothing(self):
        for pid, rows in ((TD.TARGET, [(TD.TARGET, COMPANY, ROLE)]), (None, [])):
            h = Harness(rows)
            pid = pid or h.world.ws.target.replace("-", "")
            before = h.writes()
            status, body, fields = h.open(pid)
            self.assertEqual((status, h.writes()), (200, before))
            self.assertTrue(all(r in body for r in M.OUTCOMES) and COMPANY in body and "Requisition not recorded" in body and "Unavailable on this screen" in body)
            self.assertEqual(set(fields), {"o", "exp", "st", "t"})
            self.assertFalse({SECRET.decode(), TOKEN.decode()} & set(re.findall(r"[\w-]{20,}", body)) or SECRET.decode() in body or TOKEN.decode() in body)
            self.assertNotIn("Interviewing", body)                                              # no stage is invented

    def test_the_stylesheet_is_served_and_pages_obey_the_gates_csp(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        status, css, ctype = h.call("GET", "/close.css")
        self.assertEqual((status, ctype), (200, "text/css; charset=utf-8"))
        self.assertTrue("prefers-color-scheme:dark" in css and "max-width:690px" in css and "@import" not in css and "url(" not in css)
        pages = [h.open(TD.TARGET)[1], h.review(TD.TARGET, "Hired")[1], h.close(TD.TARGET, "Hired")[1]]
        for body in pages:
            self.assertTrue('href="/close.css"' in body and "<script" not in body.lower() and "<style" not in body.lower() and not re.search(r"\sstyle=|\son\w+=|javascript:", body.lower()))

    def test_only_get_post_and_the_known_paths_are_answered(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        self.assertEqual(h.call("GET", "/other")[0], 404)
        self.assertEqual(h.call("PUT", "/")[0], 404)
        self.assertEqual(h.call("GET", "/", query={})[0], 400)
        self.assertEqual(h.call("GET", "/", query={"o": "not-an-id"})[0], 400)

    def test_an_unknown_ambiguous_cross_or_closed_record_is_refused_and_nothing_is_written(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        notes_id = h.world.ws.target.replace("-", "")                                           # the Notes page of a pursuit that closes on its Ledger row
        for pid, code in (("ab" * 16, "NOT_FOUND"), (notes_id, "STALE_IDENTITY")):
            status, body, _ = h.open(pid)
            self.assertEqual(status, 409)
            self.assertIn("DECISION_" + code, body)
        dup = Harness([(TD.TARGET, COMPANY, ROLE), (TD.SIBLING, COMPANY, ROLE)])
        self.assertIn("DECISION_AMBIGUOUS", dup.open(TD.TARGET)[1])
        twin = Harness()
        twin.world.ws.opportunity(f"{COMPANY} — {ROLE}", "Pat Twin")
        self.assertIn("DECISION_AMBIGUOUS", twin.open(twin.world.ws.target.replace("-", ""))[1])
        closed = Harness([(TD.TARGET, COMPANY, ROLE)])
        closed.world.source.pages[TD.TARGET]["properties"]["Hiring Decision"] = {"select": {"name": "Passed"}}
        self.assertEqual(closed.open(TD.TARGET)[0], 409)
        retired = Harness()
        retired.world.decide(retired.world.request())
        self.assertEqual(retired.open(retired.world.ws.target.replace("-", ""))[0], 409)
        for each in (h, dup, twin, closed):
            self.assertEqual(each.writes()[0], 0)

    def test_an_unapplied_ledger_row_for_the_same_role_blocks_a_notes_only_closure(self):
        h = Harness([("ee" * 16, COMPANY, ROLE)])
        h.world.source.pages["ee" * 16]["properties"]["Applied"] = {"checkbox": False}
        self.assertIn("DECISION_LEDGER_UNAPPLIED", h.open(h.world.ws.target.replace("-", ""))[1])


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

    def test_every_outcome_is_recorded_on_the_ledger_path_and_the_notes_path_only_after_read_back(self):
        for outcome in M.OUTCOMES:
            notes = "short explanation" if outcome == "Other" else ""
            led = Harness([(TD.TARGET, COMPANY, ROLE)])
            status, body, _ = led.close(TD.TARGET, outcome, notes)
            self.assertEqual((status, led.world.source.decision(), "Opportunity closed" in body), (200, {"name": outcome}, True), outcome)
            self.assertEqual([e["outcome"] for e in D.entries(led.world.source.history())], [outcome])
            nts = Harness()
            status, body, _ = nts.close(nts.world.ws.target.replace("-", ""), outcome, notes)
            ws = nts.world.ws
            self.assertEqual((status, ws.where(ws.target), "Opportunity closed" in body), (200, ws.retired[0], True), outcome)
            self.assertIn(f"Hiring decision (V7) · {outcome} · ", ws.digest(ws.target)[-1][2])

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
        notes = Harness()
        pid = notes.world.ws.target.replace("-", "")
        _, _, n = notes.review(pid, "Withdrew")
        self.assertEqual(notes.call("POST", form={**n, "step": "confirm"})[0], 200)
        before = len(notes.world.ws.writes())
        self.assertIn("already recorded", notes.call("POST", form={**n, "step": "confirm"})[1])
        self.assertEqual(len(notes.world.ws.writes()), before)

    def test_a_backend_failure_or_a_read_back_mismatch_never_looks_like_success(self):
        for attr in ("fail_write", "drop_write"):
            h = Harness([(TD.TARGET, COMPANY, ROLE)])
            setattr(h.world.source, attr, True)
            status, body, _ = h.close(TD.TARGET, "Rejected")
            self.assertEqual((status, "Opportunity closed" in body, "Could not verify closure" in body), (500, False, True), attr)
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        h.world.source.tamper = True
        self.assertNotIn("Opportunity closed", h.close(TD.TARGET, "Rejected")[1])
        n = Harness()
        n.world.ws.fail = {"move"}
        status, body, _ = n.close(n.world.ws.target.replace("-", ""), "Rejected")
        self.assertEqual((status, "Opportunity closed" in body), (500, False))

    def test_success_needs_the_decision_layers_verified_read_back_and_each_form_has_its_own_stable_operation(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        _, _, a = h.review(TD.TARGET, "Rejected")
        _, _, b = h.review(TD.TARGET, "Hired")
        seen = []
        with mock.patch.object(close_app.D, "confirm_rejected", lambda client, req, live, now: seen.append(req) or {"status": "recorded", "verified": False}):
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
        h = Harness()
        pid = h.world.ws.target.replace("-", "")
        rows, parents = close_app._state(Both(h.world), h.cfg["pipeline"])
        with mock.patch.object(close_app, "_state", lambda client, root: (rows, parents + [dict(p) for p in parents if p["id"].replace("-", "") == pid])):
            with self.assertRaises(D.DecisionError) as error:
                close_app.resolve(Both(h.world), h.cfg, pid)
        self.assertEqual(str(error.exception), "DECISION_AMBIGUOUS")

    def test_reopen_and_manage_are_not_reachable(self):
        h = Harness([(TD.TARGET, COMPANY, ROLE)])
        self.assertEqual(h.close(TD.TARGET, "Hired")[0], 200)
        _, _, f = h.review(TD.TARGET, "Hired")                                                # a closed record offers no new form
        self.assertEqual(h.open(TD.TARGET)[0], 409)
        smuggled = {"o": TD.TARGET, "step": "confirm", "action": "REOPEN", "reopen_hired": "true", "outcome": "Rejected"}
        self.assertEqual(h.call("POST", form=smuggled)[0], 403)
        self.assertEqual(h.world.source.decision(), {"name": "Hired"})
        text = (ROOT / "lifeos/hiring/close_app.py").read_text() + (ROOT / "lifeos/hiring/close_screen.py").read_text()
        self.assertFalse(re.search(r"D\.REOPEN|reopen_hired|\"REOPEN\"", text))
        self.assertEqual(h.call("GET", "/manage")[0], 404)

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
                mods = [node.module] if isinstance(node, ast.ImportFrom) and not node.level and node.module else [a.name for a in node.names] if isinstance(node, ast.Import) else []
                if isinstance(node, ast.ImportFrom) and node.level:
                    mods = ["lifeos.hiring" + ("." + node.module if node.module else "")] + [f"lifeos.hiring.{a.name}" for a in node.names]
                for mod in (m for m in mods if m.startswith("lifeos")):
                    for cand in (mod.replace(".", "/") + ".py", mod.replace(".", "/") + "/__init__.py"):
                        if (ROOT / cand).is_file():
                            todo.append(cand)
        self.assertEqual(seen - shipped, set(), "an import the release would not ship")
        files = {p: (ROOT / p).read_bytes() for p in shipped}
        dest = os.path.join(tempfile.mkdtemp(), "tree")
        rel.stage("a" * 40, dest, github(files))                                                  # the real verifier: regular blobs, hashes, and an isolated import of the app and the gate

    def test_no_stage_workflow_or_schedule_reaches_the_application(self):
        for path in list((ROOT / ".github/workflows").glob("*.yml")) + [ROOT / "lifeos/run.py"]:
            text = path.read_text()
            for name in ("close_app", "close_screen"):
                self.assertNotIn(name, text, path.name)
        from lifeos import run as entry
        self.assertFalse([n for n, s in entry.STAGES.items() if "close_" in str(getattr(s, "target", ""))])

    def test_the_budget_and_the_files(self):
        lines = sum(1 for p in ("close_app.py", "close_screen.py") for line in (ROOT / "lifeos/hiring" / p).read_text().splitlines() if line.strip())
        self.assertLessEqual(lines, 260)


if __name__ == "__main__":
    unittest.main()
