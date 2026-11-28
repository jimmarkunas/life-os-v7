"""The governed graph questions: they answer only from a published, fresh checkpoint, with the switch on and a plain key,
and refuse everything else before the graph is read. Fabricated records and fake clients only."""

import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from lifeos.platform import graph_index as gi, graph_tools as gt, mcp_access
from tests.platform.mcp_access_fixture import CATALOG, DOC

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 28, 12, 0, 0)
OPS = tuple(gt.OPS)


class Counting(gi.MemoryClient):
    """A fake Aura that counts every read."""

    reads = 0

    def read_namespace(self, ns):
        type(self).reads += 1
        return super().read_namespace(ns)


def loaded(now=T0, client_class=Counting):
    client_class.reads = 0
    client, store = client_class(), gi.MemoryStore()
    nodes, edges = gi.fixture()
    gi.apply(client, store, gi.NS_FIXTURE, nodes, edges, now)
    client_class.reads = 0
    return client, store


def state(client, store, when=T0 + timedelta(hours=1), on=True):
    return {"client": client, "store": store, "now": lambda: when, "switch": lambda: on}


def ask(op, args, client, store, **kw):
    return gt.run(op, args, state(client, store, **kw))


class Cases(unittest.TestCase):
    """The three dependency cases of the first baseline, put on the synthetic graph (its kinds are requirement, ticket and
    pull request): impact, gaps and chain."""

    def setUp(self):
        self.client, self.store = loaded()

    def test_impact_gaps_and_chain_match_with_provenance_three_of_three(self):
        impact = ask("graph_requirements_for_ticket", {"ticket": "LIFE-9002"}, self.client, self.store)
        gaps = []
        for requirement in ("REQ-9001", "REQ-9002", "REQ-9003"):
            got = ask("graph_tickets_for_requirement", {"requirement": requirement}, self.client, self.store)
            if got["tickets"] and not any(t["pull_requests"] for t in got["tickets"]):
                gaps.append(requirement)
        chain = ask("graph_dependency_chain", {"ticket": "LIFE-9002"}, self.client, self.store)
        passed = [
            [r["key"] for r in impact["at_risk"]]
            == ["REQ-9001"],  # if the first ticket slips, the requirement behind what it blocks is at risk
            gaps == ["REQ-9002"],  # the one requirement whose tickets have no pull request
            chain["chain"] == [{"key": "LIFE-9004", "depth": 1}],  # what must finish first
        ]
        self.assertEqual(passed, [True, True, True])
        for result in (impact, chain):
            provenance = result["provenance"]
            self.assertEqual(provenance["source"], "graph_index")
            self.assertTrue(
                provenance["key"] and provenance["fetched_at"].endswith("CT") and provenance["generation"] == 1
            )

    def test_the_other_questions_read_the_same_active_records(self):
        self.assertEqual(
            ask("graph_blockers_of", {"ticket": "LIFE-9002"}, self.client, self.store)["blockers"],
            [{"key": "LIFE-9004"}],
        )
        delivered = ask("graph_delivered_by", {"ticket": "LIFE-9001"}, self.client, self.store)
        self.assertEqual(delivered["pull_requests"], [{"key": "example-owner/example-repo#9101", "state": "merged"}])
        direct = ask("graph_requirements_for_ticket", {"ticket": "LIFE-9001"}, self.client, self.store)
        self.assertEqual(direct["implements"], [{"key": "REQ-9001", "status": "approved"}])
        self.assertEqual(direct["at_risk"], [])

    def test_an_unknown_key_is_not_found_and_a_suppressed_record_is_gone(self):
        with self.assertRaises(gt.GraphError) as caught:
            ask("graph_blockers_of", {"ticket": "LIFE-9999"}, self.client, self.store)
        self.assertEqual(caught.exception.reason, "not_found")
        nodes, edges = gi.fixture()
        nodes = [n for n in nodes if n.get("key") != "LIFE-9004"]
        edges = [e for e in edges if "LIFE-9004" not in e["src"] + e["dst"]]
        gi.apply(self.client, self.store, gi.NS_FIXTURE, nodes, edges, T0 + timedelta(minutes=30))
        self.assertEqual(ask("graph_dependency_chain", {"ticket": "LIFE-9002"}, self.client, self.store)["chain"], [])


class Refusals(unittest.TestCase):
    def setUp(self):
        self.client, self.store = loaded()

    def refused(self, op, args, reason, **kw):
        Counting.reads = 0
        with self.assertRaises(gt.GraphError) as caught:
            ask(op, args, self.client, self.store, **kw)
        self.assertEqual(caught.exception.reason, reason, (op, args))
        self.assertEqual(Counting.reads, 0, "the graph must not be read")

    def test_switch_off_malformed_cypher_and_wrong_workspace_are_refused_with_zero_reads(self):
        self.refused("graph_blockers_of", {"ticket": "LIFE-9002"}, "operation_disabled", on=False)
        for bad in (
            "",
            "life-9002",
            "LIFE-",
            "LIFE-9002 ",
            "LIFE-9002; MATCH (n) DETACH DELETE n",
            "REQ-9001",
            "' OR 1=1 //",
            9002,
            None,
            "LIFE-1234567",
        ):
            self.refused("graph_blockers_of", {"ticket": bad}, "invalid_argument")
        for bad in ("LIFE-9002", "req-1", "REQ-1) RETURN 1 //", ""):
            self.refused("graph_tickets_for_requirement", {"requirement": bad}, "invalid_argument")
        self.refused("graph_blockers_of", {"ticket": "LIFE-9002", "workspace": "Bytalos"}, "invalid_argument")
        self.refused("graph_anything_else", {"ticket": "LIFE-9002"}, "unknown_operation")

    def test_the_switch_ships_off_and_a_missing_or_broken_file_is_off(self):
        self.assertEqual(json.loads((ROOT / "lifeos/platform/graph_switch.json").read_text()), {"enabled": False})
        self.assertFalse(gt.enabled())
        with tempfile.TemporaryDirectory() as folder:
            on, broken = Path(folder, "on.json"), Path(folder, "broken.json")
            on.write_text('{"enabled": true}')
            broken.write_text("{")
            self.assertEqual(
                (gt.enabled(on), gt.enabled(broken), gt.enabled(Path(folder, "none.json"))), (True, False, False)
            )
            on.write_text('{"enabled": "yes"}')
            self.assertFalse(gt.enabled(on))

    def test_no_client_holds_a_grant_and_the_gate_refuses_the_operations(self):
        access = mcp_access.load()
        self.assertFalse(any(op in access.purpose for op in OPS))
        self.assertFalse(any(g.tool in OPS for g in access.grants))
        self.assertFalse(any("graph" in g["tool"] for g in DOC["grants"]))
        for op in OPS:
            tenant, action, account, service = gt.route(op, {}, "personal")
            with self.assertRaises(mcp_access.AccessError):
                mcp_access.call(
                    access,
                    access.clients[0],
                    tenant,
                    op,
                    action,
                    account,
                    service,
                    lambda: self.fail("ran"),
                    lambda r: None,
                )
        self.assertEqual(gt.route("graph_blockers_of", {"workspace": "Bytalos"}, "personal")[0], "unknown")

    def test_a_revoked_grant_and_a_wrong_tenant_are_denied_by_the_gate_without_running(self):
        doc, catalog = copy.deepcopy(DOC), copy.deepcopy(CATALOG)
        row = {
            "principal": "claude-chat",
            "tool": "graph_blockers_of",
            "action": "read",
            "provider": "buzz",
            "account_alias": None,
            "host": "oracle-edge",
            "source": "lifeos/platform/graph_tools.py",
            "as_of": "2026-11-22",
            "max_age_days": 90,
            "status": "synthetic",
        }
        catalog["capabilities"].append(row)
        doc["tool_purpose"]["graph_blockers_of"] = "buzz-read"
        doc["grants"].append(
            {
                "id": "t-graph",
                "principal": "claude-chat",
                "tenant": "personal",
                "account": "graph",
                "tool": "graph_blockers_of",
                "purpose": "buzz-read",
                "actions": ["read"],
                "credential_ref": "buzz-scoped-member-key",
                "expires": "2027-02-22",
                "revoked": True,
            }
        )
        access = mcp_access.load(json.dumps(doc), json.dumps(catalog))
        client = next(c for c in access.clients if c["id"] == "claude-chat")
        for tenant in ("personal", "bytalos"):
            receipts = []
            out = mcp_access.call(
                access,
                client,
                tenant,
                "graph_blockers_of",
                "read",
                "graph",
                "graph",
                lambda: self.fail("ran"),
                receipts.append,
            )
            self.assertFalse(out.executed, tenant)
            self.assertEqual(receipts[-1]["outcome"], "deny")


class Unserved(unittest.TestCase):
    def refused(self, client, store, reason, when=T0 + timedelta(hours=1)):
        with self.assertRaises(gt.GraphError) as caught:
            ask("graph_blockers_of", {"ticket": "LIFE-9002"}, client, store, when=when)
        self.assertEqual(caught.exception.reason, reason)

    def test_building_stale_mismatched_cross_tenant_and_unreadable_states_are_never_answered_from(self):
        client, store = loaded()
        self.refused(client, store, "stale_source", when=T0 + timedelta(hours=25))
        client, store = loaded()
        store.begin(gi.NS_FIXTURE, T0 + timedelta(hours=2))  # a newer run is still building
        self.refused(client, store, "source_unavailable", when=T0 + timedelta(hours=2, minutes=1))
        client, store = loaded()
        client.nodes["Ticket:LIFE-9004"] = {
            **client.nodes["Ticket:LIFE-9004"],
            "status": "Cancelled",
        }  # changed behind the checkpoint
        self.refused(client, store, "source_unavailable")
        client, store = loaded()
        client.foreign = 1  # a record that is not this namespace's
        self.refused(client, store, "source_unavailable")
        client, store = loaded()
        store.rows.clear()  # no checkpoint at all
        self.refused(client, store, "source_unavailable")

        class Broken:
            def latest(self, ns):
                raise OSError("down")

        self.refused(client, Broken(), "source_unavailable")
        with self.assertRaises(gt.GraphError):
            gt.run("graph_blockers_of", {"ticket": "LIFE-9002"}, {"switch": lambda: True})  # nothing wired: unavailable


class Wiring(unittest.TestCase):
    def test_the_store_is_wired_only_when_all_four_names_are_present_and_no_client_is(self):
        full = {"CKPT_DB_HOST": "h", "CKPT_DB_NAME": "n", "CKPT_DB_USER": "u", "CKPT_DB_PASSWORD": "p"}
        wired = {"client": None, "store": None}
        gt.wire(full, wired)
        self.assertIsInstance(wired["store"], gi.CheckpointStore)
        self.assertIsNone(wired["client"])
        for missing in full:
            partial = {k: v for k, v in full.items() if k != missing}
            state_ = {"client": None, "store": None}
            gt.wire(partial, state_)
            self.assertIsNone(state_["store"], missing)

    def test_the_vendored_driver_imports_alone_and_is_the_pinned_version(self):
        done = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys; sys.path.insert(0, 'deploy/buzz-mcp/vendor'); import pymysql; print('.'.join(map(str, pymysql.VERSION[:3])), pymysql.__file__)",
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        version, path = done.stdout.split(" ", 1)
        self.assertEqual(version, "1.1.1")
        self.assertIn("deploy/buzz-mcp/vendor/pymysql", path)
        pinned = (ROOT / "requirements.txt").read_text()
        self.assertIn("PyMySQL==1.1.1", pinned)


if __name__ == "__main__":
    unittest.main()
