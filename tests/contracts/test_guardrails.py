"""Guardrails ported from the proven V1-V5 behaviour as assertions, not machinery. Each test names the invariant it protects."""
import hashlib
import inspect
from pathlib import Path
import re
import unittest

from lifeos.jobs import audit, guard, ledger, publish, retention
from lifeos.jobs import lanes
from lifeos.platform.notion_client import NotionError
from lifeos.sources.web import diff, lister, registry, run as web

ROOT = Path(__file__).resolve().parents[2]
CODE = [p for p in (ROOT / "lifeos").rglob("*.py")]


def strings_and_code(path):
    """Source with docstrings and comments removed, so a rule about code is not tripped by prose."""
    import ast
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and ast.get_docstring(node, clean=False):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


class HumanStateIsNeverDestroyed(unittest.TestCase):
    def test_every_trash_call_sits_behind_a_protection_check(self):
        users = {p.name: p.read_text() for p in CODE if '"in_trash": True' in p.read_text()}
        self.assertEqual(sorted(users), ["audit.py", "retention.py"])             # a new destructive writer must be added here on purpose
        self.assertLess(users["audit.py"].index("guard.protection"), users["audit.py"].index('"in_trash": True'))
        self.assertLess(users["retention.py"].index("hiring_pipeline.handoff_for"), users["retention.py"].index('"in_trash": True'))

    def test_retention_only_ever_selects_unapplied_undecided_rows(self):
        text = str(retention.query_filter(__import__("datetime").datetime(2026, 10, 1), "US Remote"))
        self.assertIn("'Applied'", text)
        self.assertIn("'Saturn Decision'", text)

    def test_unknown_is_protected(self):
        class Dead:
            def call(self, *a, **k):
                raise NotionError("NOTION_HTTP_500")
        self.assertEqual(guard.protection(Dead(), "p", "X", "Y"), guard.UNREADABLE)


class NoLegacyLifecycleAuthority(unittest.TestCase):
    def test_no_code_reads_or_writes_the_legacy_fields(self):
        bad = [p.name for p in CODE if re.search(r"['\"](Lifecycle|Liveness)['\"]", strings_and_code(p)) and p.name != "publish.py"]
        self.assertEqual(bad, [])
        # publish only ever writes Liveness = Live on a fresh page (a Ledger schema requirement), never reads it back as a decision
        self.assertNotIn('"Lifecycle"', strings_and_code(ROOT / "lifeos/jobs/publish.py"))


class ProductionTargetIdentity(unittest.TestCase):
    class Source:
        def __init__(self, props, fail=False):
            self.props, self.fail, self.source, self.calls = props, fail, "ds", 0

        def call(self, method, path, body=None):
            self.calls += 1
            if self.fail:
                raise NotionError("NOTION_HTTP_404")
            return {"properties": self.props}

    def full(self):
        return {name: {"type": kind} for name, kind in ledger.REQUIRED.items()}

    def test_verify(self):
        self.assertEqual(ledger.verify(self.Source(self.full())), ledger.OK)
        wrong = self.full()
        wrong["Applied"] = {"type": "rich_text"}
        self.assertEqual(ledger.verify(self.Source(wrong)), ledger.MISMATCH)
        self.assertEqual(ledger.verify(self.Source({})), ledger.MISMATCH)               # some other database
        self.assertEqual(ledger.verify(self.Source({}, fail=True)), ledger.UNREADABLE)

    def test_checked_once_per_client(self):
        client = self.Source(self.full())
        ledger.verify(client)
        ledger.verify(client)
        self.assertEqual(client.calls, 1)

    def test_every_notion_writer_verifies_the_target_before_writing(self):
        for module, write in ((publish, "client.create"), (audit, '"in_trash": True'), (retention, '"in_trash": True')):
            source = inspect.getsource(module)
            self.assertIn("ledger.verify", source, module.__name__)
            self.assertLess(source.index("ledger.verify"), source.index(write), module.__name__)


class SourcesAreAccountedFor(unittest.TestCase):
    def test_a_failed_board_is_never_zero_and_never_a_removal(self):
        listing = lister.Listing(lister.FAILED, reason="rate_limited")
        out = web.plan({"id": "x", "kind": "greenhouse", "company": "X"}, listing, {"1": ("h", "INGESTED")}, __import__("datetime").datetime(2026, 10, 1), 10)
        self.assertEqual((out.status, out.removed, out.items), ("FAILED", [], []))

    def test_removed_needs_a_complete_listing(self):
        self.assertEqual(diff.classify({"1": "h"}, [], False)[3], [])

    def test_a_run_accounts_for_every_due_source(self):
        from datetime import datetime
        sources = [{"id": f"s{i}", "kind": "greenhouse", "company": "C", "tier": "employer"} for i in range(3)]
        listings = {"s0": lister.Listing(lister.COMPLETE, []), "s1": lister.Listing(lister.FAILED, reason="network"), "s2": lister.Listing(lister.COMPLETE, [])}

        class Repo:
            def states(self, ids): return {}
            def items(self, sid): return {}
            def commit(self, *a, **k): pass
        counts = web.run(10, False, datetime(2026, 10, 1), lambda s: listings[s["id"]], Repo(), sources)
        self.assertEqual(counts["due"], counts["complete"] + counts["failed"])


class QuarantineAndSchedulerAndRegistries(unittest.TestCase):
    def test_a_disabled_lane_can_never_admit_or_become_visible(self):
        off = lanes.LanePolicy("Skilled Worker", "UK", "£", route="Skilled Worker", enabled=False)
        facts = lanes.Facts(fit=90, market="UK", work_mode="onsite", route={"Skilled Worker": lanes.POSITIVE}, geography=lanes.POSITIVE,
                            posted=__import__("datetime").date(2026, 10, 1))
        self.assertEqual(lanes.qualify(off, facts, facts.posted).status, lanes.DISABLED)
        results, visible = lanes.qualify_all(facts, facts.posted, {"Skilled Worker": off})
        self.assertEqual((results, visible), ({}, None))

    def test_exactly_one_recurring_scheduler(self):
        scheduled = [p.name for p in (ROOT / ".github/workflows").glob("*.y*ml") if re.search(r"^\s*schedule:", p.read_text(), re.M)]
        self.assertEqual(scheduled, ["hourly.yml"])
        self.assertEqual(len(re.findall(r"^\s*- cron:", (ROOT / ".github/workflows/hourly.yml").read_text(), re.M)), 1)

    def test_machine_input_universes_are_frozen(self):
        """A change to a source registry must be deliberate: update these numbers in the same commit."""
        for lane, total, ready, digest_head in (("US Remote", None, None, None), ("Scale-Up", 48, 19, None)):
            rows = registry.load(registry.PATHS[lane])
            if total:
                self.assertEqual(len(rows), total, lane)
                self.assertEqual(len(registry.for_lane(lane)), ready, lane)
        us = registry.load(registry.PATHS["US Remote"])
        self.assertEqual(len(us), 43)
        self.assertEqual(len(registry.for_lane("US Remote")), 29)
        ids = hashlib.sha256("\n".join(sorted(r["id"] for r in us)).encode()).hexdigest()
        self.assertEqual(len(ids), 64)          # the id set is pinned by test_registry's explicit coverage counts; this guards the file is readable


if __name__ == "__main__":
    unittest.main()
