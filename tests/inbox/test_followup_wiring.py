"""(synthetic): the production wiring of the `followup` stage."""

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from tests import wftext  # noqa: E402

from lifeos.inbox import followup_stage as S, followup_wiring as W
from lifeos.outlook import stage as outlook_stage
from lifeos.inbox.followup_store import FollowupStore
from lifeos.inbox.ledger import Ledger
from lifeos.platform import followup_fetchers as F, followup_readers as R
from tests.kit.mysqlite import MySQLite
import sys as _sys

_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "tools"))
import wfsets  # noqa: E402


UTC = timezone.utc
T0 = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
REFS = F.GRANT_REFS  # what a grant names, from the fetchers' own table
EXPIRES = {key: "2027-11-15" for key in REFS}
ON = {"FOLLOWUP_WIRING": "on", "FOLLOWUP_ACCOUNT_GMAIL_PERSONAL": "gm"}
OUTLOOK_SLOTS = range(1, F.SLOTS[("outlook", "personal")] + 1)
OUTLOOKS = {F.ACCOUNT_NAMES[("outlook", "personal", n)]: f"ol{n}" for n in OUTLOOK_SLOTS}
JIRA = ("jira", "personal", 1), ("jira", "bytalos", 1)


def shim(ensure=True):
    shared = MySQLite()

    @contextmanager
    def connect():
        yield shared

    return connect


def fake_build(environ):
    return {"jira": lambda ref, since: []}


class Switch(unittest.TestCase):
    def test_the_kill_switch_keeps_the_stage_a_no_op_whatever_the_file_says(self):
        off = {"FOLLOWUP_WIRING": "off"}
        self.assertIsNone(W.build(off))
        self.assertIsNone(S.production_wiring(off))
        with mock.patch.dict("os.environ", off):
            self.assertEqual(S.run(0, False, None)["unwired"], 1)

    def test_on_with_only_the_fixed_jira_scopes_wires_just_those_and_off_still_wins(self):
        with mock.patch.object(F, "build_fetchers", fake_build), mock.patch.object(W.db, "connect", shim()):
            wiring = W.build({"FOLLOWUP_WIRING": "on"})
            self.assertEqual(
                {(s.workspace, s.account) for s in wiring.scopes},
                {("personal", "jira-personal"), ("bytalos", "jira-bytalos")},
            )
            self.assertIsNone(W.build({**ON, "FOLLOWUP_WIRING": "off"}))  # the kill switch wins

    def test_without_the_fetchers_module_nothing_is_configured_and_nothing_crashes(self):
        with mock.patch.object(W, "_fetchers", lambda: None), mock.patch.object(W.db, "connect", shim()):
            self.assertEqual(W.credential_names(), {})
            wiring = W.build(ON)
        self.assertEqual((wiring.fetchers, wiring.grants), ({}, ()))  # every source degraded, nothing read


class Build(unittest.TestCase):
    def test_builds_from_a_fake_environment(self):
        env = {**ON, "FOLLOWUP_ACCOUNT_CALENDAR_PERSONAL": "cp"}
        with (
            mock.patch.object(F, "build_fetchers", fake_build),
            mock.patch.object(W, "EXPIRES", EXPIRES),
            mock.patch.object(W.db, "connect", shim()),
        ):
            dry = W.build(env, live=False, clock=lambda: T0)
            live = W.build(env, live=True, clock=lambda: T0)
        self.assertEqual(
            {(s.workspace, s.account) for s in dry.scopes},
            {("personal", "jira-personal"), ("personal", "gm"), ("personal", "cp"), ("bytalos", "jira-bytalos")},
        )
        self.assertTrue(all("asana" not in s.sources for s in dry.scopes))
        self.assertEqual(
            {(g.source, g.tenant, g.account, g.credential_ref) for g in dry.grants},
            {
                ("jira", "personal", "jira-personal", REFS[("jira", "personal", 1)]),
                ("jira", "bytalos", "jira-bytalos", REFS[("jira", "bytalos", 1)]),
                ("gmail", "personal", "gm", REFS[("gmail", "personal", 1)]),
                ("calendar", "personal", "cp", REFS[("calendar", "personal", 1)]),
            },
        )
        self.assertIsInstance(dry.store, W.DryStore)
        self.assertIsInstance(live.store, FollowupStore)
        self.assertEqual(dry.jira_base, W.JIRA_SITE)

    def test_a_credential_without_an_expiry_has_no_grant(self):
        grants = W.grants_for(ON, REFS, {("jira", "personal", 1): "2027-11-15"})
        self.assertEqual([(g.source, g.tenant) for g in grants], [("jira", "personal")])
        env = {**ON, "FOLLOWUP_ACCOUNT_ASANA_PERSONAL": "x", "FOLLOWUP_ACCOUNT_JIRA_TGP": "x"}
        self.assertEqual(
            W.grants_for(
                env,
                {("asana", "personal", 1): "X", ("jira", "tgp", 1): "X"},
                {("asana", "personal", 1): "2027-11-15", ("jira", "tgp", 1): "2027-11-15"},
            ),
            (),
        )

    def test_jira_is_two_scopes_of_one_account_with_one_credential_item_and_separate_grants(self):
        scopes = W.configured({"FOLLOWUP_WIRING": "on"})
        self.assertEqual(
            {k for k, members in scopes.items() if ("jira", 1) in members},
            {("personal", "jira-personal"), ("bytalos", "jira-bytalos")},
        )
        grants = [g for g in W.grants_for(ON, REFS, EXPIRES) if g.source == "jira"]
        self.assertEqual(len(grants), 2)  # two Grant objects...
        self.assertEqual({F.CREDENTIAL_NAMES[JIRA[0]], F.CREDENTIAL_NAMES[JIRA[1]]}, {"JIRA_API_TOKEN"})  # ...
        self.assertNotEqual(grants[0].credential_ref, grants[1].credential_ref)
        self.assertEqual(W.fetcher_environment({})["JIRA_BASE_URL"], W.JIRA_SITE)
        self.assertEqual(F.JIRA_PROJECTS["personal"], ("JFM", "LIFE"))
        self.assertEqual(F.JIRA_PROJECTS["bytalos"], ("BAD", "BPP"))

    def test_a_bytalos_scope_can_never_be_given_personal_projects(self):
        self.assertFalse(set(F.JIRA_PROJECTS["personal"]) & set(F.JIRA_PROJECTS["bytalos"]))
        grants = {g.tenant: g for g in W.grants_for(ON, REFS, EXPIRES) if g.source == "jira"}
        seen = []
        fetch = F.build_fetchers(
            {"JIRA_API_TOKEN": "x", "JIRA_EMAIL": "a@example.com"},
            factories={"jira": lambda env, ws, secret, projects=None: seen.append((ws, projects)) or None},
        )["jira"]
        self.assertEqual(grants["bytalos"].credential_ref, F.GRANT_REFS[("jira", "bytalos", 1)])
        with self.assertRaises(F.FetchError):
            fetch("JIRA_API_TOKEN:calendar.bytalos", None)

    def test_each_jira_scope_reads_with_its_own_grant_and_receipts_and_cannot_borrow_the_other(self):
        grants = W.grants_for(ON, REFS, EXPIRES)
        receipts, seen = [], []
        for tenant in ("personal", "bytalos"):

            def fetcher(ref, since, tenant=tenant):
                seen.append((tenant, ref))
                return []

            R.read_delta("jira", tenant, f"jira-{tenant}", None, grants, fetcher, receipts.append, T0, "batch-0")
        self.assertEqual(seen, [("personal", REFS[("jira", "personal", 1)]), ("bytalos", REFS[("jira", "bytalos", 1)])])
        self.assertEqual({r["tenant"] for r in receipts}, {"personal", "bytalos"})  # separate receipts per scope
        wrong = R.read_delta(
            "jira",
            "bytalos",
            "jira-bytalos",
            None,
            [g for g in grants if g.tenant == "personal"],
            lambda ref, since: [],
            lambda r: None,
            T0,
            "x",
        )
        self.assertEqual((wrong.status, wrong.reason), ("refused", "no_grant"))
        stranger = R.read_delta(
            "jira", "personal", "stranger", None, grants, lambda ref, since: [], lambda r: None, T0, "y"
        )
        self.assertEqual((stranger.status, stranger.reason), ("refused", "no_grant"))

    def test_existing_credentials_are_reused_and_only_two_secret_names_are_new(self):
        raw = F.CREDENTIAL_NAMES
        self.assertEqual({raw[("notion", "personal", 1)], raw[("interview_os", "personal", 1)]}, {"NOTION_API_TOKEN"})
        self.assertEqual(
            {raw[("calendar", "personal", 1)], raw[("calendar", "bytalos", 1)]}, {"GCAL_SERVICE_ACCOUNT_JSON"}
        )
        self.assertEqual(raw[("gmail", "personal", 1)], "GMAIL_OAUTH_REFRESH_TOKEN")
        used = {
            raw[k]
            for k in raw
            if k[1] in ("personal", "bytalos") and k[0] in W.SOURCE_WORKSPACES and k[1] in W.SOURCE_WORKSPACES[k[0]]
        }
        self.assertEqual(
            {v for v in used if v.startswith("FOLLOWUP_")},
            {"FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH", "FOLLOWUP_TODOIST_PERSONAL_TOKEN"},
        )
        table = W.expiry_table()
        for key, name in raw.items():
            self.assertEqual(key in table, key[0] in F.STORED_TOKENS or name in W.INVENTORY, key)
        env = {
            **ON,
            "FOLLOWUP_ACCOUNT_CALENDAR_PERSONAL": "cp",
            "FOLLOWUP_ACCOUNT_CALENDAR_BYTALOS": "cb",
            "FOLLOWUP_ACCOUNT_NOTION_PERSONAL": "np",
            "FOLLOWUP_ACCOUNT_GMAIL_BYTALOS": "gb",
            "FOLLOWUP_ACCOUNT_TODOIST_PERSONAL": "tp",
        }
        grants = W.grants_for(env, W.credential_names())
        calendars = {g.tenant: g for g in grants if g.source == "calendar"}
        self.assertEqual(
            {t: g.account for t, g in calendars.items()}, {"personal": "cp", "bytalos": "cb"}
        )  # one item, two Grants, two scopes
        self.assertNotEqual(calendars["personal"].credential_ref, calendars["bytalos"].credential_ref)
        self.assertEqual(
            {
                (g.source, g.tenant)
                for g in grants
                if g.source == "todoist" or (g.source == "gmail" and g.tenant == "bytalos")
            },
            {("todoist", "personal"), ("gmail", "bytalos")},
        )  # both have a loader line and an inventory row now

    def test_outlook_reuses_the_existing_sign_in_labels_and_never_bytalos(self):
        extra = len(OUTLOOKS) + 1  # one slot past the connected accounts is ignored
        env = {
            **ON,
            **OUTLOOKS,
            f"FOLLOWUP_ACCOUNT_OUTLOOK_PERSONAL_{extra}": "more",
            "FOLLOWUP_ACCOUNT_OUTLOOK_BYTALOS": "no",
        }
        self.assertEqual(W.accounts(env, "outlook", "personal"), [(n, f"ol{n}") for n in OUTLOOK_SLOTS])
        self.assertEqual(W.accounts(env, "outlook", "bytalos"), [])
        grants = [g for g in W.grants_for(env, W.credential_names()) if g.source == "outlook"]
        self.assertEqual(
            sorted((g.account, g.credential_ref) for g in grants),
            sorted((f"ol{n}", F.OUTLOOK_LABELS[n]) for n in OUTLOOK_SLOTS),
        )
        self.assertTrue({g.credential_ref for g in grants} <= set(outlook_stage.LABELS))

    def test_the_school_mailbox_is_tracked_not_connected(self):
        self.assertEqual([key for key in W.NOT_CONNECTED], [("outlook", "personal", "school mailbox")])
        self.assertTrue(all(reason for reason in W.NOT_CONNECTED.values()))
        env = {**ON, "OUTLOOK_CLIENT_ID": "x"}
        labels = [label for _, label in W.accounts(env, "outlook", "personal")]
        self.assertFalse([label for label in labels if "school" in label])  # no scope, so no grant and no read
        self.assertFalse([g for g in W.grants_for(env, W.credential_names()) if "school" in str(g.account).lower()])

    def test_todoist_notion_and_interview_os_are_personal_only(self):
        env = {"FOLLOWUP_WIRING": "on"}
        for source in ("todoist", "notion", "interview_os"):
            for workspace in ("personal", "bytalos"):
                for key in F.ACCOUNT_NAMES:
                    if key[:2] == (source, workspace):
                        env[F.ACCOUNT_NAMES[key]] = "acct"
            self.assertEqual(W.accounts(env, source, "personal"), [(1, "acct")])
            self.assertEqual(W.accounts(env, source, "bytalos"), [])

    def test_tgp_is_refused_as_a_workspace_and_as_an_account_label(self):
        label = "someone@tgp" + "international.com"
        env = {
            **ON,
            "FOLLOWUP_ACCOUNT_GMAIL_TGP": "x",
            "FOLLOWUP_ACCOUNT_GMAIL_BYTALOS": label,
            "FOLLOWUP_ACCOUNT_OUTLOOK_PERSONAL_2": label,
        }
        self.assertEqual(W.accounts(env, "gmail", "tgp"), [])
        self.assertEqual(W.accounts(env, "gmail", "bytalos"), [])
        self.assertEqual(W.accounts(env, "outlook", "personal"), [])
        self.assertTrue(all(label not in key for key in W.configured(env)))
        self.assertTrue(S.tgp_account(label))
        self.assertFalse(S.tgp_account("main"))
        calls = []
        wiring = S.Wiring(
            FollowupStore(shim()),
            (S.Scope("personal", label, ("jira",)),),
            W.grants_for(ON, REFS, EXPIRES),
            {"jira": lambda ref, since: calls.append(ref) or []},
            lambda r: None,
            lambda p: None,
            clock=lambda: T0,
        )
        counts = S.run(0, False, wiring)
        self.assertEqual(calls, [])
        self.assertEqual(counts.get("refused"), 1)

    def test_an_unconfigured_source_a_workspace_serves_shows_up_as_degraded_not_missing(self):
        scopes = W.configured({"FOLLOWUP_WIRING": "on"})
        members = {s for s, _ in scopes[("personal", "jira-personal")]}
        self.assertEqual(members, {"jira", "calendar", "gmail", "outlook", "todoist", "notion", "interview_os"})
        self.assertEqual([g.source for g in W.grants_for({"FOLLOWUP_WIRING": "on"}, REFS, EXPIRES)], ["jira", "jira"])


class Degraded(unittest.TestCase):
    def test_a_missing_credential_is_degraded_not_a_crash(self):
        connect = shim()
        store = FollowupStore(connect)
        store.ensure_schema()
        ledger = Ledger(connect)
        ledger.ensure_schema()
        calls = []
        wiring = S.Wiring(
            store,
            (S.Scope("personal", "jira-personal", ("jira", "gmail")),),
            (),
            {"jira": lambda ref, since: calls.append(ref) or []},
            W.make_audit(ledger),
            lambda payload: None,
            clock=lambda: T0,
        )
        counts = S.run(0, False, wiring)
        self.assertEqual(calls, [])  # no grant, no read
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
        grants = W.grants_for(ON, REFS, EXPIRES)
        audit = W.make_audit(Ledger(broken))
        with self.assertRaises(W.WiringError) as caught:
            audit(
                {
                    "t": T0.isoformat(),
                    "source": "jira",
                    "tenant": "personal",
                    "account": "jira-personal",
                    "request_id": "batch-0",
                    "outcome": "allow",
                    "reason": "ok",
                }
            )
        self.assertEqual(str(caught.exception), "FOLLOWUP_AUDIT_UNAVAILABLE")
        wiring = S.Wiring(
            store,
            (S.Scope("personal", "jira-personal", ("jira",)),),
            grants,
            {"jira": lambda ref, since: calls.append(ref) or []},
            audit,
            lambda payload: None,
            clock=lambda: T0,
        )
        counts = S.run(0, False, wiring)
        self.assertEqual(calls, [])  # no receipt, no read
        self.assertEqual(counts.get("item_failed"), 1)
        self.assertIn("FOLLOWUP_AUDIT_UNAVAILABLE", str(counts.get("item_codes")))


class Receipts(unittest.TestCase):
    def test_a_receipt_opens_and_settles_and_repeated_batch_ids_stay_unique(self):
        connect = shim()
        ledger = Ledger(connect)
        ledger.ensure_schema()
        audit = W.make_audit(ledger)
        grants = W.grants_for(ON, REFS, EXPIRES)
        fetch = lambda ref, since: []  # noqa: E731
        R.read_delta("jira", "personal", "jira-personal", None, grants, fetch, audit, T0, "batch-0")
        R.read_delta(
            "jira", "personal", "jira-personal", None, grants, fetch, audit, T0 + timedelta(hours=1), "batch-0"
        )  # the same reader id one hour later: a new receipt
        R.read_delta(
            "jira", "bytalos", "jira-bytalos", None, (), fetch, audit, T0, "batch-1"
        )  # a refusal still leaves a deny receipt
        rows = ledger.access_receipts(W.PRINCIPAL)
        self.assertEqual(len(rows), 3)
        self.assertEqual(sorted(r["decision"] for r in rows), ["allow", "allow", "deny"])
        self.assertEqual(sorted(r["outcome"] for r in rows if r["decision"] == "allow"), ["ok", "ok"])
        self.assertTrue(all(r["principal_id"] == "followup-stage" for r in rows))


class Dry(unittest.TestCase):
    def test_a_dry_run_store_reads_empty_when_the_tables_are_missing_and_never_writes(self):
        dry = W.DryStore(FollowupStore(shim()))  # no ensure_schema: the Followup tables do not exist
        self.assertIsNone(dry.cursor_for("personal", "jira-personal", "jira"))
        self.assertEqual(dry.threads("personal", "jira-personal"), [])
        with self.assertRaises(W.WiringError) as caught:
            dry.accept("personal", "jira-personal", "jira", [], "", T0)
        self.assertEqual(str(caught.exception), "FOLLOWUP_DRY_RUN_NO_WRITE")


class Payload(unittest.TestCase):
    def test_the_sink_refuses_a_store_without_the_payload_table(self):
        sink = W.make_sink(object(), lambda: T0)
        with self.assertRaises(W.WiringError) as caught:
            sink({"workspace": "personal", "account": "jira-personal"})
        self.assertEqual(str(caught.exception), "FOLLOWUP_PAYLOAD_STORE_MISSING")

    def test_the_sink_and_loader_round_trip_through_a_store_with_the_payload_table(self):
        class Fake:
            saved = {}

            def save_payload(self, workspace, account, payload):
                self.saved[(workspace, account)] = payload

            def load_payload(self, workspace, account):
                return self.saved.get((workspace, account))

        store = Fake()
        payload = {"component": "followup", "workspace": "personal", "account": "jira-personal"}
        W.make_sink(store, lambda: T0)(payload)
        self.assertEqual(W.payload_loader(store, "personal", "jira-personal")("ignored"), payload)
        self.assertIsNone(W.payload_loader(store, "bytalos", "jira-bytalos")("ignored"))
        self.assertIsNone(W.payload_loader(object(), "personal", "jira-personal")("ignored"))

    def test_the_sink_calls_the_real_store_with_arguments_that_bind_to_save_payload(self):
        """the first live run failed every scope because the sink passed one argument more than
        FollowupStore.save_payload takes
        """
        import inspect

        seen = []

        class Spy:
            def save_payload(self, *args, **kwargs):
                seen.append((args, kwargs))

        W.make_sink(Spy(), lambda: T0)(
            {
                "workspace": "personal",
                "account": "jira-personal",
                "state": "PASS",
                "generated_at": "2026-11-17T01:00:00+00:00",
            }
        )
        args, kwargs = seen[0]
        inspect.signature(FollowupStore.save_payload).bind(object(), *args, **kwargs)


class Job(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[2]

    def test_the_followup_job_goes_live_only_through_the_live_guard_and_loads_only_committed_names(self):
        import json

        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        text = wfsets.expand_text(wftext.logical("domains"))
        doc = yaml.safe_load(text)
        job = doc["jobs"]["followup"]
        self.assertEqual(doc["permissions"], {"contents": "read"})  # unchanged
        self.assertEqual(job["needs"], ["gate"])
        self.assertNotIn("vars.", job["if"])
        self.assertIn("needs.gate.outputs.due_followup == 'true'", job["if"])
        runs = [step.get("run", "") for step in job["steps"]]
        stage = [r for r in runs if "python -m lifeos.run followup" in r]
        self.assertEqual(len(stage), 1)
        self.assertIn('$([ "$LIVE" = "true" ] && echo --live)', stage[0])
        self.assertNotIn("--live", stage[0].replace('echo --live)', ""))
        loaded = next(
            step
            for step in job["steps"]
            if str(step.get("uses", "")).endswith("bws-load") and step.get("id") == "bws_followup"
        )
        self.assertTrue(
            {"JIRA_API_TOKEN", "JIRA_EMAIL"}
            <= {line.split()[-1] for line in str(loaded["with"].get("secrets", "")).splitlines() if line.strip()}
        )
        self.assertNotIn("FOLLOWUP_JIRA_", str(loaded["with"]))
        names = {line.split()[-1] for line in str(loaded["with"].get("secrets", "")).splitlines() if line.strip()}
        for name in names:
            self.assertRegex(name, r"^[A-Z0-9_]+$")
        self.assertFalse(any(marker in text for marker in W.stage.TGP_DOMAINS))
        self.assertEqual(
            json.loads((self.ROOT / "lifeos/platform/due_table.json").read_text())["jobs"]["followup"],
            {"every_hours": 1},
        )
        switch = json.loads((self.ROOT / "lifeos/platform/switches.json").read_text())["FOLLOWUP_WIRING"]
        self.assertIn(switch["state"], ("off", "on"))
        if switch["state"] == "on":
            self.assertTrue(switch["turned_on"].strip())
            self.assertIn("Go Live", switch["bounds"])

    def test_every_new_followup_credential_is_loaded_passed_and_has_an_inventory_row(self):
        """Derived from the code: each FOLLOWUP_ secret name the fetchers use needs a bws-load line, a step env entry and
        a credential inventory row, so a.
        """
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        from lifeos.platform.credential_list import CREDENTIALS

        job = yaml.safe_load(wfsets.expand_text(wftext.logical("domains")))["jobs"]["followup"]
        loaded = next(step for step in job["steps"] if step.get("id") == "bws_followup")
        lines = [line.split() for line in str(loaded["with"].get("secrets", "")).splitlines() if line.strip()]
        loaded_names = {parts[-1] for parts in lines}
        ids = [parts[0] for parts in lines]
        self.assertEqual(len(ids), len(set(ids)))  # one Bitwarden item per line
        env = next(step for step in job["steps"] if step.get("id") == "followup")["env"]
        rows = {row["key"] for row in CREDENTIALS}
        self.assertTrue(F.NEW_NAMES)
        for name in F.NEW_NAMES:
            if name.startswith("FOLLOWUP_GCAL_") or name.startswith("FOLLOWUP_ACCOUNT_"):
                continue
            self.assertIn(name, loaded_names)
            self.assertIn(name, env)
            self.assertIn(W.INVENTORY.get(name), rows)


class DefaultLabels(unittest.TestCase):
    """Jim creates no label item: each account has a committed label, used only where its credential is there to read."""

    def test_every_default_names_a_real_scope_and_is_never_an_address_or_tgp(self):
        self.assertEqual(set(W.DEFAULT_LABELS) - set(F.CREDENTIAL_NAMES), set())
        for key, label in W.DEFAULT_LABELS.items():
            self.assertTrue(label and len(label) <= 80 and "@" not in label and not S.tgp_account(label), key)

    def test_a_credential_in_the_environment_brings_its_committed_label(self):
        gmail, name = ("gmail", "bytalos", 1), F.CREDENTIAL_NAMES[("gmail", "bytalos", 1)]
        self.assertEqual(W.accounts({}, "gmail", "bytalos"), [])  # no credential, no scope
        self.assertEqual(W.accounts({name: "x"}, "gmail", "bytalos"), [(1, W.DEFAULT_LABELS[gmail])])
        self.assertEqual(
            W.accounts({name: "x", F.ACCOUNT_NAMES[gmail]: "mine"}, "gmail", "bytalos"), [(1, "mine")]
        )  # an environment label still wins
        self.assertEqual(
            W.accounts({name: "x", F.ACCOUNT_NAMES[gmail]: "a@" + "tgp" + "international.com"}, "gmail", "bytalos"), []
        )

    def test_outlook_is_judged_by_the_reader_so_its_client_id_is_enough(self):
        self.assertEqual(W.accounts({}, "outlook", "personal"), [])
        self.assertEqual(
            [slot for slot, _ in W.accounts({"OUTLOOK_CLIENT_ID": "x"}, "outlook", "personal")], list(OUTLOOK_SLOTS)
        )
        self.assertEqual(W.accounts({"OUTLOOK_CLIENT_ID": "x"}, "outlook", "bytalos"), [])


if __name__ == "__main__":
    unittest.main()
