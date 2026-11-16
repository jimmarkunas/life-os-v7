"""LIFE-757 (synthetic): the production wiring of the `followup` stage. A sqlite shim stands in for MySQL; no provider, credential, table, workflow or schedule is touched."""
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest import mock

from lifeos.inbox import followup_stage as S, followup_wiring as W
from lifeos.inbox.followup_store import FollowupStore
from lifeos.inbox.ledger import Ledger
from lifeos.platform import followup_readers as R
from tests.kit.mysqlite import MySQLite

UTC = timezone.utc
T0 = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
NAMES = {("jira", "personal"): "FOLLOWUP_JIRA_PERSONAL_TOKEN", ("jira", "bytalos"): "FOLLOWUP_JIRA_BYTALOS_TOKEN", ("gmail", "personal"): "FOLLOWUP_GMAIL_PERSONAL_TOKEN",
         ("outlook", "personal", 1): "FOLLOWUP_OUTLOOK_1", ("outlook", "personal", 2): "FOLLOWUP_OUTLOOK_2", ("outlook", "personal", 3): "FOLLOWUP_OUTLOOK_3"}
EXPIRES = {key: "2027-11-15" for key in NAMES}
ON = {"FOLLOWUP_WIRING": "on", "FOLLOWUP_ACCOUNT_JIRA_PERSONAL": "main", "FOLLOWUP_ACCOUNT_JIRA_BYTALOS": "main", "FOLLOWUP_ACCOUNT_GMAIL_PERSONAL": "gm"}
OUTLOOKS = {"FOLLOWUP_ACCOUNT_OUTLOOK_PERSONAL_1": "ol1", "FOLLOWUP_ACCOUNT_OUTLOOK_PERSONAL_2": "ol2", "FOLLOWUP_ACCOUNT_OUTLOOK_PERSONAL_3": "ol3"}


def shim(ensure=True):
    shared = MySQLite()

    @contextmanager
    def connect():
        yield shared
    return connect


def fake_fetchers(names=NAMES, build=None):
    module = types.ModuleType("lifeos.platform.followup_fetchers")
    module.CREDENTIAL_NAMES = names
    module.build_fetchers = build or (lambda environ: {"jira": lambda ref, since: []})
    return mock.patch.dict(sys.modules, {"lifeos.platform.followup_fetchers": module})


class Switch(unittest.TestCase):
    def test_off_by_default_and_the_stage_stays_a_no_op(self):
        with fake_fetchers():
            self.assertIsNone(W.build({}))
            self.assertIsNone(S.production_wiring({}))
            self.assertEqual(S.run(0, False, None)["unwired"], 1)

    def test_on_without_a_configured_account_is_still_a_no_op(self):
        with fake_fetchers():
            self.assertIsNone(W.build({"FOLLOWUP_WIRING": "on"}))
            self.assertIsNone(W.build({**ON, "FOLLOWUP_WIRING": "off"}))               # the kill switch wins

    def test_without_the_fetchers_module_nothing_is_configured(self):
        with mock.patch.dict(sys.modules, {"lifeos.platform.followup_fetchers": None}):
            self.assertEqual(W.credential_names(), {})
            with mock.patch.object(W.db, "connect", shim()):
                wiring = W.build(ON)
        self.assertEqual((wiring.grants, wiring.fetchers), ((), {}))                    # every source degraded (no grant), nothing read, nothing crashed


class Build(unittest.TestCase):
    def test_builds_from_a_fake_environment(self):
        with fake_fetchers(), mock.patch.object(W, "EXPIRES", EXPIRES), mock.patch.object(W.db, "connect", shim()):
            dry = W.build(ON, live=False, clock=lambda: T0)
            live = W.build(ON, live=True, clock=lambda: T0)
        self.assertEqual({(s.workspace, s.account) for s in dry.scopes}, {("personal", "main"), ("personal", "gm"), ("bytalos", "main")})
        self.assertTrue(all("asana" not in s.sources for s in dry.scopes))
        self.assertEqual({(g.source, g.tenant, g.account) for g in dry.grants}, {("jira", "personal", "main"), ("jira", "bytalos", "main"), ("gmail", "personal", "gm")})
        self.assertTrue(all(g.credential_ref in NAMES.values() and not g.revoked for g in dry.grants))
        self.assertIsInstance(dry.store, W.DryStore)
        self.assertIsInstance(live.store, FollowupStore)
        self.assertEqual(set(dry.fetchers), {"jira"})

    def test_a_credential_without_an_expiry_row_has_no_grant(self):
        grants = W.grants_for(ON, NAMES, {("jira", "personal"): "2027-11-15"})
        self.assertEqual([(g.source, g.tenant) for g in grants], [("jira", "personal")])
        env = {**ON, "FOLLOWUP_ACCOUNT_ASANA_PERSONAL": "x", "FOLLOWUP_ACCOUNT_JIRA_TGP": "x"}
        grants = W.grants_for(env, {("asana", "personal"): "X", ("jira", "tgp"): "X"}, {("asana", "personal"): "2027-11-15", ("jira", "tgp"): "2027-11-15"})
        self.assertEqual(grants, ())                                                      # Asana never, TGP never

    def test_jira_is_two_scopes_of_one_account_with_separate_credentials_and_projects(self):
        scopes = W.configured({"FOLLOWUP_WIRING": "on", "FOLLOWUP_ACCOUNT_JIRA_PERSONAL": "a", "FOLLOWUP_ACCOUNT_JIRA_BYTALOS": "a"})
        self.assertEqual({k for k, members in scopes.items() if ("jira", None) in members}, {("personal", "a"), ("bytalos", "a")})
        grants = W.grants_for(ON, NAMES, EXPIRES)
        self.assertEqual({(g.tenant, g.credential_ref) for g in grants if g.source == "jira"}, {("personal", "FOLLOWUP_JIRA_PERSONAL_TOKEN"), ("bytalos", "FOLLOWUP_JIRA_BYTALOS_TOKEN")})
        env = W.fetcher_environment({})
        self.assertEqual(env["FOLLOWUP_JIRA_PROJECTS_PERSONAL"], "JFM,LIFE,LOS")
        self.assertEqual(env["FOLLOWUP_JIRA_PROJECTS_BYTALOS"], "BAD,BPP")
        self.assertEqual(W.fetcher_environment({"FOLLOWUP_JIRA_PROJECTS_PERSONAL": "X"})["FOLLOWUP_JIRA_PROJECTS_PERSONAL"], "X")

    def test_outlook_is_up_to_three_personal_accounts_and_never_bytalos(self):
        env = {**ON, **OUTLOOKS, "FOLLOWUP_ACCOUNT_OUTLOOK_PERSONAL_4": "ol4", "FOLLOWUP_ACCOUNT_OUTLOOK_BYTALOS": "no"}
        self.assertEqual(W.accounts(env, "outlook", "personal"), [(1, "ol1"), (2, "ol2"), (3, "ol3")])
        self.assertEqual(W.accounts(env, "outlook", "bytalos"), [])
        grants = [g for g in W.grants_for(env, NAMES, EXPIRES) if g.source == "outlook"]
        self.assertEqual(sorted((g.account, g.credential_ref) for g in grants), [("ol1", "FOLLOWUP_OUTLOOK_1"), ("ol2", "FOLLOWUP_OUTLOOK_2"), ("ol3", "FOLLOWUP_OUTLOOK_3")])

    def test_todoist_notion_and_interview_os_are_personal_only(self):
        env = {"FOLLOWUP_WIRING": "on"}
        for source in ("todoist", "notion", "interview_os"):
            env[W.account_env(source, "personal")] = "acct"
            env[W.account_env(source, "bytalos")] = "other"
            self.assertEqual(W.accounts(env, source, "personal"), [(None, "acct")])
            self.assertEqual(W.accounts(env, source, "bytalos"), [])

    def test_tgp_is_refused_as_a_workspace_and_as_an_account_label(self):
        label = "someone@tgp" + "international.com"
        env = {**ON, "FOLLOWUP_ACCOUNT_GMAIL_TGP": "x", "FOLLOWUP_ACCOUNT_GMAIL_BYTALOS": label, "FOLLOWUP_ACCOUNT_OUTLOOK_PERSONAL_2": label}
        self.assertEqual(W.accounts(env, "gmail", "tgp"), [])
        self.assertEqual(W.accounts(env, "gmail", "bytalos"), [])
        self.assertEqual(W.accounts(env, "outlook", "personal"), [])
        self.assertTrue(all(label not in key for key in W.configured(env)))
        self.assertTrue(S.tgp_account(label))
        self.assertFalse(S.tgp_account("main"))
        # a scope that names TGP is counted refused by the stage and never read
        calls = []
        wiring = S.Wiring(FollowupStore(shim()), (S.Scope("personal", label, ("jira",)),), W.grants_for(ON, NAMES, EXPIRES), {"jira": lambda ref, since: calls.append(ref) or []}, lambda r: None, lambda p: None, clock=lambda: T0)
        counts = S.run(0, False, wiring)
        self.assertEqual(calls, [])
        self.assertEqual(counts.get("refused"), 1)

    def test_an_unconfigured_source_a_workspace_serves_shows_up_as_degraded_not_missing(self):
        scopes = W.configured({"FOLLOWUP_WIRING": "on", "FOLLOWUP_ACCOUNT_JIRA_PERSONAL": "a"})
        members = {s for s, _ in scopes[("personal", "a")]}
        self.assertEqual(members, {"jira", "calendar", "gmail", "outlook", "todoist", "notion", "interview_os"})
        self.assertIn("todoist", members)
        self.assertEqual(W.grants_for({"FOLLOWUP_WIRING": "on", "FOLLOWUP_ACCOUNT_JIRA_PERSONAL": "a"}, {}, EXPIRES), ())

    def test_two_scopes_never_share_a_grant(self):
        seen = []
        grants = W.grants_for(ON, NAMES, EXPIRES)
        fetchers = {"jira": lambda ref, since: seen.append(ref) or []}
        audit = lambda receipt: None                                                      # noqa: E731
        R.read_delta("jira", "personal", "main", None, grants, fetchers["jira"], audit, T0, "a")
        R.read_delta("jira", "bytalos", "main", None, grants, fetchers["jira"], audit, T0, "b")
        self.assertEqual(seen, ["FOLLOWUP_JIRA_PERSONAL_TOKEN", "FOLLOWUP_JIRA_BYTALOS_TOKEN"])
        wrong = R.read_delta("jira", "personal", "stranger", None, grants, fetchers["jira"], audit, T0, "c")                # another workspace's account borrows nothing
        self.assertEqual((wrong.status, wrong.reason), ("refused", "no_grant"))


class Degraded(unittest.TestCase):
    def test_a_missing_credential_is_degraded_not_a_crash(self):
        connect = shim()
        store = FollowupStore(connect)
        store.ensure_schema()
        ledger = Ledger(connect)
        ledger.ensure_schema()
        calls = []
        wiring = S.Wiring(store, (S.Scope("personal", "main", ("jira", "gmail")),), (), {"jira": lambda ref, since: calls.append(ref) or []}, W.make_audit(ledger), lambda payload: None,
                          clock=lambda: T0)
        counts = S.run(0, False, wiring)
        self.assertEqual(calls, [])                                                       # no grant, no read
        self.assertEqual(counts["sources_degraded"], 2)
        self.assertEqual(counts["sources_ok"], 0)
        self.assertNotIn("item_failed", counts)

    def test_an_audit_failure_refuses_the_read(self):
        @contextmanager
        def broken():
            raise RuntimeError("driver text that must never leave")
            yield
        store_connect = shim()
        store = FollowupStore(store_connect)
        store.ensure_schema()
        calls = []
        grants = W.grants_for(ON, NAMES, EXPIRES)
        audit = W.make_audit(Ledger(broken))
        with self.assertRaises(W.WiringError) as caught:
            audit({"t": T0.isoformat(), "source": "jira", "tenant": "personal", "account": "main", "request_id": "batch-0", "outcome": "allow", "reason": "ok"})
        self.assertEqual(str(caught.exception), "FOLLOWUP_AUDIT_UNAVAILABLE")
        wiring = S.Wiring(store, (S.Scope("personal", "main", ("jira",)),), grants, {"jira": lambda ref, since: calls.append(ref) or []}, audit, lambda payload: None, clock=lambda: T0)
        counts = S.run(0, False, wiring)
        self.assertEqual(calls, [])                                                       # no receipt, no read
        self.assertEqual(counts.get("item_failed"), 1)
        self.assertIn("FOLLOWUP_AUDIT_UNAVAILABLE", str(counts.get("item_codes")))


class Receipts(unittest.TestCase):
    def test_a_receipt_opens_and_settles_and_repeated_batch_ids_stay_unique(self):
        connect = shim()
        ledger = Ledger(connect)
        ledger.ensure_schema()
        audit = W.make_audit(ledger)
        grants = W.grants_for(ON, NAMES, EXPIRES)
        fetch = lambda ref, since: []                                                     # noqa: E731
        R.read_delta("jira", "personal", "main", None, grants, fetch, audit, T0, "batch-0")
        R.read_delta("jira", "personal", "main", None, grants, fetch, audit, T0 + timedelta(hours=1), "batch-0")      # the same reader id one hour later: a new receipt
        R.read_delta("jira", "bytalos", "main", None, (), fetch, audit, T0, "batch-1")                                 # a refusal still leaves a deny receipt
        rows = ledger.access_receipts(W.PRINCIPAL)
        self.assertEqual(len(rows), 3)
        self.assertEqual(sorted(r["decision"] for r in rows), ["allow", "allow", "deny"])
        self.assertEqual(sorted(r["outcome"] for r in rows if r["decision"] == "allow"), ["ok", "ok"])
        self.assertTrue(all(r["principal_id"] == "followup-stage" for r in rows))


class Dry(unittest.TestCase):
    def test_a_dry_run_store_reads_empty_when_the_tables_are_missing_and_never_writes(self):
        dry = W.DryStore(FollowupStore(shim()))                                         # no ensure_schema: the Followup tables do not exist
        self.assertIsNone(dry.cursor_for("personal", "main", "jira"))
        self.assertEqual(dry.threads("personal", "main"), [])
        with self.assertRaises(W.WiringError) as caught:
            dry.accept("personal", "main", "jira", [], "", T0)
        self.assertEqual(str(caught.exception), "FOLLOWUP_DRY_RUN_NO_WRITE")


class Payload(unittest.TestCase):
    def test_the_sink_refuses_a_store_without_the_payload_table(self):
        sink = W.make_sink(object(), lambda: T0)
        with self.assertRaises(W.WiringError) as caught:
            sink({"workspace": "personal", "account": "main"})
        self.assertEqual(str(caught.exception), "FOLLOWUP_PAYLOAD_STORE_MISSING")

    def test_the_sink_and_loader_round_trip_through_a_store_with_the_payload_table(self):
        class Fake:
            saved = {}

            def save_payload(self, workspace, account, payload, now):
                self.saved[(workspace, account)] = payload

            def load_payload(self, workspace, account):
                return self.saved.get((workspace, account))
        store = Fake()
        payload = {"component": "followup", "workspace": "personal", "account": "main"}
        W.make_sink(store, lambda: T0)(payload)
        self.assertEqual(W.payload_loader(store, "personal", "main")("ignored"), payload)
        self.assertIsNone(W.payload_loader(store, "bytalos", "main")("ignored"))
        self.assertIsNone(W.payload_loader(object(), "personal", "main")("ignored"))


class Job(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[2]

    def test_the_followup_job_is_dry_run_only_default_off_and_loads_only_committed_names(self):
        import json
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        text = (self.ROOT / ".github/workflows/domains.yml").read_text()
        doc = yaml.safe_load(text)
        job = doc["jobs"]["followup"]
        self.assertEqual(doc["permissions"], {"contents": "read"})                       # unchanged
        self.assertEqual(job["needs"], ["gate"])
        self.assertNotIn("vars.", job["if"])                                              # no new GitHub value: the switch is in switches.json (off), so the stage is a counts-only no-op until it is turned on
        self.assertIn("needs.gate.outputs.due_followup == 'true'", job["if"])
        runs = [step.get("run", "") for step in job["steps"]]
        self.assertTrue(any("python -m lifeos.run followup" in r for r in runs))
        self.assertFalse(any("--live" in r for r in runs))
        loaded = next(step for step in job["steps"] if str(step.get("uses", "")).endswith("bws-load") and step.get("id") == "bws_followup")
        names = {line.split()[-1] for line in str(loaded["with"].get("secrets", "")).splitlines() if line.strip()}
        for name in names:
            self.assertRegex(name, r"^[A-Z0-9_]+$")
        self.assertFalse(any(marker in text for marker in W.stage.TGP_DOMAINS))
        self.assertEqual(json.loads((self.ROOT / "lifeos/platform/due_table.json").read_text())["jobs"]["followup"], {"every_hours": 1})
        self.assertEqual(json.loads((self.ROOT / "lifeos/platform/switches.json").read_text())["FOLLOWUP_WIRING"]["state"], "off")


if __name__ == "__main__":
    unittest.main()
