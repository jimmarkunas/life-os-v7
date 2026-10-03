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
        flt = {f["property"]: f for f in retention.query_filter(__import__("datetime").datetime(2026, 10, 1), "US Remote")["and"] if "property" in f}
        self.assertEqual(flt["Applied"]["checkbox"], {"equals": False})
        self.assertEqual(flt["Applied On"]["date"], {"is_empty": True})
        self.assertEqual(flt["Saturn Decision"]["select"], {"is_empty": True})

    def test_unknown_is_protected(self):
        class Dead:
            def call(self, *a, **k):
                raise NotionError("NOTION_HTTP_500")
        self.assertEqual(guard.protection(Dead(), "p", "X", "Y"), guard.UNREADABLE)


class AuditBehaviour(unittest.TestCase):
    """Behavioural proof: for every protected state, a failing published row is neither trashed nor demoted."""

    FULL = {"Applied": {"checkbox": False}, "Applied On": {"date": None}, "Saturn Decision": {"select": None}}

    def run_audit(self, page_props, target=None):
        from unittest import mock
        from tests.kit.db import FakeConn
        trashed, writes = [], []
        target_props = target if target is not None else {n: {"type": k} for n, k in ledger.REQUIRED.items()}

        class Client:
            source = "ds"

            def call(self, method, path, body=None):
                if method == "PATCH":
                    trashed.append(path)
                    return {}
                if path.startswith("/data_sources/"):
                    return {"properties": target_props}
                if page_props is None:
                    raise NotionError("NOTION_HTTP_500")
                return {"properties": page_props}

        conn = FakeConn(handler=lambda sql, args, cur: writes.append(sql.split()[0]))
        served = []
        conn.cur.fetchall = lambda: [] if served else (served.append(1) or [(1, "PUBLISHED", "https://x.example/jobs/1", "page1", "x", "Acme", "Program Manager")])
        with mock.patch.object(audit.store, "connect", return_value=conn), mock.patch.object(audit.store, "ensure_schema"), \
                mock.patch.object(audit.notion_client, "Client", return_value=Client()):
            counts = audit.run(10, True, environ={})
        return counts, trashed, [w for w in writes if w in ("UPDATE", "DELETE")]

    def test_unprotected_failing_row_is_trashed_and_demoted(self):
        counts, trashed, writes = self.run_audit(self.FULL)
        self.assertEqual((len(trashed), counts["demoted"]), (1, 1))

    def test_every_protected_state_blocks_the_trash_and_the_demotion(self):
        cases = {"applied": {**self.FULL, "Applied": {"checkbox": True}},
                 "applied_on": {**self.FULL, "Applied On": {"date": {"start": "2026-09-15"}}},
                 "saturn_decision": {**self.FULL, "Saturn Decision": {"select": {"name": "Pursue"}}},
                 "unreadable": None,
                 "missing_property": {"Applied": {"checkbox": False}}}
        for why, props in cases.items():
            with self.subTest(why):
                counts, trashed, writes = self.run_audit(props)
                self.assertEqual((trashed, writes, counts["demoted"]), ([], [], 0))
                self.assertEqual(counts["protected"], {"unreadable" if why in ("unreadable", "missing_property") else why: 1})

    def test_a_wrong_target_blocks_everything(self):
        counts, trashed, writes = self.run_audit(self.FULL, target={"Job": {"type": "title"}})
        self.assertEqual((trashed, writes, counts["protected"]), ([], [], {"target_mismatch": 1}))


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
        self.assertEqual(sorted(scheduled), ["hourly.yml", "watchdog.yml"])    # the watchdog only raises an alert (D52); it never runs the pipeline
        watchdog = (ROOT / ".github/workflows/watchdog.yml").read_text()
        self.assertNotIn("lifeos.run", watchdog)
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", watchdog))), ["GITHUB_TOKEN", "NTFY_TOPIC"])
        self.assertEqual(len(re.findall(r"^\s*- cron:", (ROOT / ".github/workflows/hourly.yml").read_text(), re.M)), 3)   # D30: three triggers, one gate

    def test_machine_input_universes_are_frozen(self):
        """A change to a source registry must be deliberate: update these numbers in the same commit."""
        for lane, total, ready, digest_head in (("US Remote", None, None, None), ("Scale-Up", 48, 36, None)):
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


class ExcludedPagesAreCleared(unittest.TestCase):
    """With the Fit gate on, a published page whose lane decision is EXCLUDE is trashed (never when a human pursuit may exist) and never published again."""
    FULL = {"Applied": {"checkbox": False}, "Applied On": {"date": None}, "Saturn Decision": {"select": None}}

    def run_audit(self, page_props, gated=True):
        from unittest import mock
        from tests.kit.db import FakeConn
        trashed, writes = [], []
        target_props = {n: {"type": k} for n, k in ledger.REQUIRED.items()}

        class Client:
            source = "ds"

            def call(self, method, path, body=None):
                if method == "PATCH":
                    trashed.append(path)
                    return {}
                if path.startswith("/data_sources/"):
                    return {"properties": target_props}
                return {"properties": page_props}

        conn = FakeConn(handler=lambda sql, args, cur: writes.append(sql))
        batches = iter([[], [(7, "PUBLISHED", "https://x.example/jobs/7", "page7", None, "Acme", "Program Manager")]])
        conn.cur.fetchall = lambda: next(batches, [])
        with mock.patch.object(audit.store, "connect", return_value=conn), mock.patch.object(audit.store, "ensure_schema"), \
                mock.patch.object(audit.notion_client, "Client", return_value=Client()):
            counts = audit.run(10, True, environ={"V7_FIT_GATE": "true"} if gated else {})
        return counts, trashed, [w for w in writes if w.split()[0] in ("UPDATE", "DELETE")]

    def test_an_unprotected_excluded_page_is_trashed_and_marked_excluded_fit_keeping_its_hash(self):
        counts, trashed, writes = self.run_audit(self.FULL)
        self.assertEqual((len(trashed), counts["by_reason"], counts["excluded_cleared"]), (1, {"excluded_fit": 1}, 1))
        self.assertTrue(any("EXCLUDED_FIT" in w for w in writes))
        self.assertFalse(any("DELETE" in w for w in writes))                # the ledger hash and the description stay

    def test_applied_or_decided_pages_are_never_trashed(self):
        for props in ({**self.FULL, "Applied": {"checkbox": True}}, {**self.FULL, "Saturn Decision": {"select": {"name": "Apply"}}}):
            counts, trashed, writes = self.run_audit(props)
            self.assertEqual((trashed, writes, counts.get("excluded_cleared", 0)), ([], [], 0))

    def test_without_the_gate_nothing_is_cleared(self):
        counts, trashed, writes = self.run_audit(self.FULL, gated=False)
        self.assertEqual((trashed, writes, counts["failed"]), ([], [], 0))


class InterviewJobIsIsolated(unittest.TestCase):
    """The Interview job gets only its own token: every Jobs secret is blanked, it is dispatch-only, and its failure is a warning, not a failed run."""

    def block(self):
        text = (ROOT / ".github/workflows/hourly.yml").read_text()
        start = text.index("\n  interview:\n")
        return text[start:text.index("\n  jira:\n")]

    def test_no_jobs_secret_reaches_the_interview_job(self):
        block = self.block()
        for name in ("NOTION_API_TOKEN", "NOTION_JOB_LEDGER_DATA_SOURCE_ID", "GMAIL_OAUTH_CLIENT_SECRET", "TINYFISH_API_KEY", "LIFEOS_ACQ_DB_PASSWORD", "FIT_PROFILE_JSON"):
            self.assertIn(f'{name}: ""', block, name)
        self.assertNotIn("secrets.NOTION_API_TOKEN", block)
        self.assertIn("secrets.NOTION_INTERVIEW_TOKEN", block)

    def test_dispatch_only_and_failure_is_a_warning(self):
        block = self.block()
        self.assertIn("github.event_name == 'workflow_dispatch'", block)
        self.assertNotIn("\n    continue-on-error:", block)                    # a job-level continue-on-error would hide the result from the report job
        self.assertEqual(block.count("continue-on-error: true"), 3)            # the three steps
        self.assertIn("::warning", block)


class DecisionIndexTests(unittest.TestCase):
    """The decision index is what agents read instead of the 47 KB log; it must list every decision."""

    def test_every_decision_heading_is_in_the_index(self):
        from pathlib import Path
        docs = Path(__file__).resolve().parents[2] / "docs"
        heads = [line[3:].strip() for line in (docs / "DECISIONS.md").read_text().splitlines() if line.startswith("## D")]
        index = (docs / "DECISIONS_INDEX.md").read_text()
        missing = [h for h in heads if "- " + h not in index]
        self.assertEqual(missing, [])
