"""LIFE-47 S2 (Career): the Close Opportunity application, driven the way the Edge gate calls it, against the synthetic Ledger and Notes stand-ins already used for S1. Every name here is invented;
nothing is live. A `Both` client stands for the one Notion integration (Ledger rows and Notes pages); GET gets it behind the gate's real `ReadOnly` facade."""
import ast
import html
import importlib
import os
import re
import tempfile
import unittest
from unittest import mock

from lifeos.edge import close_gate
from lifeos.edge.oracle import close_release as rel
from lifeos.hiring import decision as D, models as M
from lifeos.sources.apps import close_app
from tests.edge.support import github
from tests.hiring import test_decision as TD, test_hiring as T
from tests.hiring.test_notes_decision import World, options

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
        """LIFE-973: the Close screen is retired; a decision is recorded the way the screen did (decision.confirm_rejected) so the Manage and Reopen tests keep their closed rows."""
        both = Both(self.world)
        rec = D.route(D.rows(D.ledger_pages(both)), {"action": D.ACTION, "id": pid})
        done = D.confirm_rejected(both, {"action": D.ACTION, "confirmed": True, "id": pid, "company": rec["company"], "role": rec["role"], "reason": notes or outcome, "outcome": outcome,
                                         "op": "test-" + D._page_id(pid)[:12] + outcome[:3].lower()}, True, self.clock)
        assert done["verified"] is True, done
        return 200, "", "text/html"


STAGED = "ee" * 16


def staged_harness(**kw):
    """An interview pursuit's row (LIFE-361): Stage set, Applied not ticked. LIFE-353: it closes on the row like any application."""
    h = Harness([(STAGED, COMPANY, ROLE)], **kw)
    h.world.source.pages[STAGED] = TD.Source.make(STAGED, COMPANY, ROLE, applied=False, stage="Interviewing")
    return h


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
        self.assertEqual(rel.APP, "lifeos/sources/apps/close_app.py")
        self.assertIn("lifeos/sources/__init__.py", rel.EDGE)
        unit = next(line for line in (ROOT / "lifeos/edge/oracle/install-close.sh").read_text().splitlines() if line.startswith("ExecStart="))
        self.assertIn("from lifeos.sources.apps.close_app import handle; sys.exit(close_gate.main(handle))", unit)
        self.assertNotIn("lifeos.sources.close_app", unit)                                              # LIFE-624 step 2: the unit imports the real module, not the shim
        self.assertNotIn("lifeos.hiring.close_app", unit)
        self.assertTrue(callable(close_app.handle) and close_app.handle.__module__ == "lifeos.sources.apps.close_app")
        self.assertFalse((ROOT / "lifeos/hiring/close_app.py").exists())                              # no wrapper is left in the hiring package

    def test_the_old_path_is_gone_and_the_release_closure_ships_only_the_real_module(self):
        """LIFE-624 step 3: the shim at lifeos/sources/close_app.py is removed (Oracle's unit and the release tool already name lifeos.sources.apps.close_app)."""
        self.assertFalse((ROOT / "lifeos/sources/close_app.py").exists())
        self.assertNotIn("close_app", importlib.import_module("lifeos.sources").__all__)
        self.assertNotIn("lifeos/sources/close_app.py", close_app.CLOSURE)
        self.assertIn("lifeos/sources/apps/close_app.py", close_app.CLOSURE)
        shipped = set(rel.closure((ROOT / rel.APP).read_bytes()))                                          # the release tool reads the literal CLOSURE from the real module
        self.assertLessEqual(set(close_app.CLOSURE), shipped)
        self.assertNotIn("lifeos/sources/close_app.py", shipped)

    def test_no_stage_workflow_or_schedule_reaches_the_application(self):
        for path in list((ROOT / ".github/workflows").glob("*.yml")) + [ROOT / "lifeos/run.py"]:
            text = path.read_text()
            for name in ("close_app", "close_screen"):
                self.assertNotIn(name, text, path.name)
        from lifeos import run as entry
        self.assertFalse([n for n, s in entry.STAGES.items() if "close_" in str(getattr(s, "target", ""))])

    def test_the_budget_and_the_files(self):
        lines = sum(1 for p in ("lifeos/sources/apps/close_app.py", "lifeos/hiring/close_screen.py") for line in (ROOT / p).read_text().splitlines() if line.strip())
        self.assertLessEqual(lines, 330)                               # E05 Mark Complete lives here too (Jim, Oct 23: the 300-line cap is suspended for the Jobs/Interview OS delivery)
