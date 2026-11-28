"""The graph index loader, proven on fabricated records with fake clients and a fake MySQL connection."""

from datetime import datetime, timedelta
import importlib.util
import io
from contextlib import redirect_stdout
import json
from pathlib import Path
import re
import unittest

from lifeos.platform import graph_index as gi

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("graph_index_sync", ROOT / "tools" / "graph_index_sync.py")
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

T0 = datetime(2026, 11, 28, 12, 0, 0)
NS = gi.NS_FIXTURE


def fixture():
    return gi.fixture()


def run_cli(argv, client=None, store=None):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = sync.main(argv, client=client, store=store, now=lambda: T0)
    return code, [json.loads(line) for line in buffer.getvalue().splitlines() if line]


class Apply(unittest.TestCase):
    def test_apply_twice_gives_identical_results(self):  # acceptance 1
        nodes, edges = fixture()
        client, store = gi.MemoryClient(), gi.MemoryStore()
        first = gi.apply(client, store, NS, nodes, edges, T0)
        second = gi.apply(client, store, NS, nodes, edges, T0 + timedelta(hours=1))
        self.assertEqual((first["written_nodes"], first["written_edges"]), (12, 11))
        self.assertEqual((second["written_nodes"], second["written_edges"]), (0, 0))
        same = ("nodes", "edges", "active_nodes", "active_edges", "checksum16", "membership16")
        self.assertEqual({k: first[k] for k in same}, {k: second[k] for k in same})
        self.assertEqual((first["generation"], second["generation"]), (1, 2))

    def test_the_command_line_does_the_same_and_defaults_to_a_dry_run(self):  # acceptance 1
        code, out = run_cli([])
        self.assertEqual((code, out[0]["mode"], out[0]["would_write_nodes"]), (0, "dry-run", 12))
        client, store = gi.MemoryClient(), gi.MemoryStore()
        code, out = run_cli(["--apply", "--times", "2"], client, store)
        self.assertEqual(code, 0)
        self.assertEqual(out[0]["checksum16"], out[1]["checksum16"])
        self.assertEqual(len(store.rows), 2)


class Allowlist(unittest.TestCase):
    def test_titles_text_notion_ids_and_hashes_fail_validation(self):  # acceptance 2
        nodes, edges = fixture()
        for bad in (
            {"title": "a title"},
            {"text": "body"},
            {"notion_id": "x"},
            {"hash": "abc"},
            {"url": "https://example.test"},
        ):
            with self.assertRaisesRegex(gi.Refused, "PROPERTY_NOT_ALLOWED"):
                gi.validate_all([{**nodes[3], **bad}], [])
        for value in ("ab" * 16, "-".join(("aaaaaaaa", "bbbb", "cccc", "dddd", "eeeeeeeeeeee"))):
            with self.assertRaisesRegex(gi.Refused, "VALUE_NOT_ALLOWED"):
                gi.validate_all([{**nodes[3], "status": value}], [])
            with self.assertRaisesRegex(gi.Refused, "VALUE_NOT_ALLOWED"):
                gi.validate_all([{"kind": "Ticket", "key": value, "status": "Done"}], [])
        for bad in ({"status": "Whatever"}, {"key": "LIFE-x"}, {"suppressed": "yes"}):
            with self.assertRaises(gi.Refused):
                gi.validate_all([{**nodes[3], **bad}], [])
        with self.assertRaisesRegex(gi.Refused, "EDGE_NOT_ALLOWED"):
            gi.validate_all(nodes, [{"type": "KNOWS", "src": edges[0]["src"], "dst": edges[0]["dst"]}])
        with self.assertRaisesRegex(gi.Refused, "EDGE_ENDPOINT_NOT_ALLOWED"):
            gi.validate_all(nodes, [{"type": "IMPLEMENTS", "src": edges[0]["src"], "dst": edges[0]["dst"]}])

    def test_no_stored_record_carries_a_property_outside_the_list(self):
        nodes, edges = fixture()
        client = gi.MemoryClient()
        gi.apply(client, gi.MemoryStore(), NS, nodes, edges, T0)
        for node in client.nodes.values():
            self.assertLessEqual(set(node) - {"kind"}, set(gi.NODE_PROPS[node["kind"]]))
        self.assertTrue(all(set(e) <= {"type", "src", "dst", "suppressed"} for e in client.edges.values()))


class Changes(unittest.TestCase):
    def setUp(self):
        self.nodes, self.edges = fixture()
        self.client, self.store = gi.MemoryClient(), gi.MemoryStore()
        gi.apply(self.client, self.store, NS, self.nodes, self.edges, T0)

    def test_add_change_removal_and_suppress_are_additive_and_the_revoked_are_not_served(self):  # acceptance 3
        nodes = [dict(n) for n in self.nodes if n.get("key") != "LIFE-9005"]  # a ticket drops out of the source
        nodes = [{**n, "status": "Done"} if n.get("key") == "LIFE-9002" else n for n in nodes]  # one changes
        nodes.append({"kind": "Ticket", "key": "LIFE-9007", "status": "To Do"})  # one is added
        edges = [e for e in self.edges if e["dst"] != "Requirement:REQ-9002"]  # an edge drops out
        edges = [e for e in edges if "LIFE-9005" not in e["src"] + e["dst"]]  # the edges of the dropped ticket
        edges.append({"type": "CHILD_OF", "src": "Ticket:LIFE-9007", "dst": "Ticket:LIFE-9001"})
        result = gi.apply(self.client, self.store, NS, nodes, edges, T0 + timedelta(hours=1))
        self.assertEqual(self.client.writes, 2)
        self.assertEqual(result["nodes"], 13)  # nothing deleted: 12 + 1 added
        self.assertEqual(result["active_nodes"], 12)
        self.assertTrue(self.client.nodes["Ticket:LIFE-9005"]["suppressed"])
        self.assertEqual(self.client.nodes["Ticket:LIFE-9002"]["status"], "Done")
        removed = self.client.edges[("IMPLEMENTS", "Ticket:LIFE-9003", "Requirement:REQ-9002")]
        self.assertTrue(removed["suppressed"])
        served_nodes, served_edges = gi.serve(self.client, self.store, NS, T0 + timedelta(hours=2))
        self.assertNotIn("Ticket:LIFE-9005", served_nodes)
        self.assertNotIn(("IMPLEMENTS", "Ticket:LIFE-9003", "Requirement:REQ-9002"), served_edges)
        self.assertIn(("CHILD_OF", "Ticket:LIFE-9007", "Ticket:LIFE-9001"), served_edges)

    def test_a_record_that_comes_back_is_unsuppressed(self):
        gi.apply(
            self.client,
            self.store,
            NS,
            self.nodes[:-1],
            [e for e in self.edges if "#9103" not in e["dst"]],
            T0 + timedelta(hours=1),
        )
        self.assertTrue(self.client.nodes["PullRequest:example-owner/example-repo#9103"]["suppressed"])
        gi.apply(self.client, self.store, NS, self.nodes, self.edges, T0 + timedelta(hours=2))
        self.assertFalse(self.client.nodes["PullRequest:example-owner/example-repo#9103"]["suppressed"])


class FailClosed(unittest.TestCase):
    def test_a_write_in_between_fails_the_refresh_and_nothing_is_published(self):  # acceptance 4
        nodes, edges = fixture()

        class Raced(gi.MemoryClient):
            def write(self, ns, node_rows, edge_rows):
                super().write(ns, node_rows, edge_rows)
                self.nodes["Ticket:LIFE-9999"] = {
                    "kind": "Ticket",
                    "key": "LIFE-9999",
                    "status": "Done",
                    "suppressed": False,
                }  # another writer

        client, store = Raced(), gi.MemoryStore()
        with self.assertRaisesRegex(gi.Failed, "READBACK_MISMATCH"):
            gi.apply(client, store, NS, nodes, edges, T0)
        self.assertEqual(store.latest(NS)["status"], "building")
        with self.assertRaisesRegex(gi.Unserved, "CHECKPOINT_BUILDING"):
            gi.serve(client, store, NS, T0)

    def test_a_failed_write_leaves_the_checkpoint_building_and_the_graph_unserved(self):
        nodes, edges = fixture()

        class Broken(gi.MemoryClient):
            def write(self, ns, node_rows, edge_rows):
                raise OSError("down")

        store = gi.MemoryStore()
        with self.assertRaisesRegex(gi.Failed, "APPLY_FAILED"):
            gi.apply(Broken(), store, NS, nodes, edges, T0)
        self.assertEqual(store.latest(NS)["status"], "building")

    def test_a_second_run_cannot_start_while_one_is_building_and_a_stale_building_row_is_passed(self):
        nodes, edges = fixture()
        store = gi.MemoryStore()
        store.begin(NS, T0)
        with self.assertRaisesRegex(gi.Failed, "CHECKPOINT_BUSY"):
            gi.apply(gi.MemoryClient(), store, NS, nodes, edges, T0 + timedelta(minutes=5))
        later = T0 + timedelta(minutes=gi.BUILDING_GRACE_MINUTES + 1)
        self.assertEqual(gi.apply(gi.MemoryClient(), store, NS, nodes, edges, later)["generation"], 2)

    def test_readers_refuse_when_missing_stale_or_mismatched_and_a_published_row_is_never_overwritten(self):
        nodes, edges = fixture()
        client, store = gi.MemoryClient(), gi.MemoryStore()
        with self.assertRaisesRegex(gi.Unserved, "CHECKPOINT_MISSING"):
            gi.serve(client, store, NS, T0)
        gi.apply(client, store, NS, nodes, edges, T0)
        self.assertEqual(len(gi.serve(client, store, NS, T0 + timedelta(hours=23))[0]), 12)
        with self.assertRaisesRegex(gi.Unserved, "CHECKPOINT_STALE"):
            gi.serve(client, store, NS, T0 + timedelta(hours=25))
        client.nodes["Ticket:LIFE-9001"] = {
            **client.nodes["Ticket:LIFE-9001"],
            "status": "Blocked",
        }  # changed behind the checkpoint
        with self.assertRaisesRegex(gi.Unserved, "CHECKPOINT_MISMATCH"):
            gi.serve(client, store, NS, T0 + timedelta(hours=1))
        with self.assertRaisesRegex(gi.Failed, "CHECKPOINT_NOT_BUILDING"):
            store.publish(NS, 1, 1, 1, "a" * 64, "b" * 64, T0)


class Wipe(unittest.TestCase):
    def test_wipe_and_rebuild_exist_only_on_the_memory_client(self):  # acceptance 5
        nodes, edges = fixture()
        client, store = gi.MemoryClient(), gi.MemoryStore()
        first = gi.apply(client, store, NS, nodes, edges, T0)
        code, out = run_cli(["--rebuild"], client, store)
        self.assertEqual(code, 0)
        self.assertEqual(out[0]["checksum16"], first["checksum16"])
        self.assertEqual(out[0]["written_nodes"], 12)  # empty first, then rebuilt from scratch
        self.assertFalse(hasattr(gi.AuraClient, "wipe") or hasattr(gi.AuraClient, "delete"))
        code, out = run_cli(["--live", "--rebuild"], client, store)
        self.assertEqual((code, out), (2, [{"refused": "LIVE_NEEDS_APPLY_OR_READBACK"}]))
        code, out = run_cli(["--live", "--apply", "--rebuild"], client, store)
        self.assertEqual((code, out), (2, [{"refused": "NO_LIVE_DELETE"}]))

    def test_no_statement_the_live_client_can_send_deletes_or_builds_text_from_input(self):
        statements = list(gi.NODE_WRITE.values()) + list(gi.EDGE_WRITE.values())
        for text in statements + [
            gi.READ_NODES,
            gi.READ_EDGES,
            gi.READ_TOTAL_NODES,
            gi.READ_TOTAL_RELS,
            gi.PILOT_NODES,
            gi.PILOT_EDGES,
        ]:
            self.assertIsNone(re.search(r"\b(DELETE|DETACH|REMOVE|DROP|CREATE|CALL|LOAD)\b", text), text)
        for text in statements:
            self.assertIn("$ns", text)
            self.assertNotIn("UmT4Fake", text)


class Refusals(unittest.TestCase):
    def check_refused(self, expected, nodes, edges, client=None, ns=NS):
        client, store = client or gi.MemoryClient(), gi.MemoryStore()
        with self.assertRaisesRegex(gi.Refused, expected):
            gi.apply(client, store, ns, nodes, edges, T0)
        self.assertEqual(client.writes, 0)
        self.assertEqual(store.rows, {})  # not even a checkpoint row

    def test_another_namespace_foreign_content_and_a_cap_breach_stop_before_any_write(self):  # acceptance 6
        nodes, edges = fixture()
        self.check_refused("NAMESPACE_NOT_ALLOWED", nodes, edges, ns="um-t4-fake-v1")
        self.check_refused("NAMESPACE_NOT_ALLOWED", nodes, edges, ns="someone-else")
        self.check_refused("NAMESPACE_OCCUPIED", nodes, edges, client=gi.MemoryClient(foreign=1))
        original = gi.NODE_CAP
        gi.NODE_CAP = 5
        try:
            self.check_refused("CAP_EXCEEDED", nodes, edges)
        finally:
            gi.NODE_CAP = original

    def test_a_live_run_into_a_namespace_that_holds_anything_else_is_refused_and_the_cli_exits_2(self):
        nodes, edges = fixture()
        other = gi.MemoryClient(
            {"Ticket:LIFE-9001": {"kind": "Ticket", "key": "LIFE-9001", "status": "Done", "suppressed": False}}
        )
        code, out = run_cli(["--apply", "--live"], other, gi.MemoryStore())
        self.assertEqual((code, out), (2, [{"refused": "NAMESPACE_OCCUPIED"}]))
        self.assertEqual(other.writes, 0)
        code, out = run_cli(["--live"], gi.MemoryClient(), gi.MemoryStore())
        self.assertEqual((code, out), (2, [{"refused": "LIVE_NEEDS_APPLY_OR_READBACK"}]))
        code, out = run_cli(["--readback"], gi.MemoryClient(), gi.MemoryStore())
        self.assertEqual(code, 2)

    def test_the_live_run_happens_once_and_a_second_one_writes_nothing(
        self,
    ):  # acceptance 7 (logic; the live proof is on the ticket)
        client, store = gi.MemoryClient(), gi.MemoryStore()
        code, first = run_cli(["--apply", "--live"], client, store)
        self.assertEqual((code, first[0]["written_nodes"], first[0]["written_edges"]), (0, 12, 11))
        code, again = run_cli(["--apply", "--live"], client, store)
        self.assertEqual((code, again[0]["written_nodes"], again[0]["written_edges"]), (0, 0, 0))
        code, back = run_cli(["--readback", "--live"], client, store)
        self.assertEqual(code, 0)
        self.assertTrue(back[0]["equals_fixture"])
        self.assertEqual(back[0]["served"], True)
        writes = client.writes
        run_cli(["--readback", "--live"], client, store)
        self.assertEqual(client.writes, writes)  # read-back writes nothing


class FakeDriver:
    """A tiny stand-in for Aura: it records what is sent, applies the loader's own write templates to a small model, and answers
    the loader's read templates from that model."""

    def __init__(self, reads=None):
        self.sent, self.reads = [], reads or {}
        self.model_nodes, self.model_edges = [], []

    def _write_nodes(self, kind, ns, rows):
        for row in rows:
            ident = {k: v for k, v in row.items() if k in ("id", "key", "repo", "number")}
            for node in self.model_nodes:
                if node["labels"] == [kind] and all(node["p"].get(k) == v for k, v in ident.items()):
                    node["p"].update(row)
                    break
            else:
                self.model_nodes.append({"labels": [kind], "p": {"ns": ns, **row}})

    def _find(self, kind, ident):
        for node in self.model_nodes:
            if node["labels"] == [kind] and all(node["p"].get(k) == v for k, v in ident.items()):
                return node
        raise AssertionError("an edge write named a node that is not there")

    def _write_edges(self, etype, ns, rows):
        ends = {
            "BLOCKS": ("Ticket", "Ticket"),
            "CHILD_OF": ("Ticket", "Ticket"),
            "IMPLEMENTS": ("Ticket", "Requirement"),
            "DELIVERED_BY": ("Ticket", "PullRequest"),
        }[etype]
        for row in rows:
            a = self._find(ends[0], {"key": row["src"]})
            b = self._find(
                ends[1],
                {"repo": row["repo"], "number": row["number"]}
                if etype == "DELIVERED_BY"
                else ({"key": row["dst"]} if ends[1] == "Ticket" else {"id": row["dst"]}),
            )
            for edge in self.model_edges:
                if edge["al"] == a["labels"] and edge["ap"] is a["p"] and edge["t"] == etype and edge["bp"] is b["p"]:
                    edge["rp"]["suppressed"] = row["suppressed"]
                    break
            else:
                self.model_edges.append(
                    {
                        "al": a["labels"],
                        "ap": a["p"],
                        "t": etype,
                        "rp": {"ns": ns, "suppressed": row["suppressed"]},
                        "bl": b["labels"],
                        "bp": b["p"],
                    }
                )

    def session(self, database=None, default_access_mode=None):
        driver = self

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def run(self, text, **params):
                driver.sent.append((text, default_access_mode, params))
                rows = driver.reads.get(text)
                for kind, template in gi.NODE_WRITE.items():
                    if text == template:
                        driver._write_nodes(kind, params["ns"], params["rows"])
                for etype, template in gi.EDGE_WRITE.items():
                    if text == template:
                        driver._write_edges(etype, params["ns"], params["rows"])
                if rows is None and text == gi.READ_NODES:
                    rows = [
                        {"labels": n["labels"], "p": dict(n["p"])}
                        for n in driver.model_nodes
                        if n["p"].get("ns") == params["ns"]
                    ]
                if rows is None and text == gi.READ_EDGES:
                    rows = [
                        {**e, "ap": dict(e["ap"]), "bp": dict(e["bp"]), "rp": dict(e["rp"])} for e in driver.model_edges
                    ]

                class Result:
                    def data(self_inner):
                        return rows or []

                return Result()

        return Session()


class Live(unittest.TestCase):
    def test_the_live_client_writes_only_its_namespace_templates_and_never_touches_the_pilot(self):  # acceptance 8
        nodes, edges = fixture()
        driver = FakeDriver()
        client = gi.AuraClient(driver)
        result = gi.apply(client, gi.MemoryStore(), NS, nodes, edges, T0)
        self.assertEqual((result["nodes"], result["edges"]), (12, 11))
        again = gi.apply(
            client, gi.MemoryStore(), NS, nodes, edges, T0, live=True
        )  # the same records again: nothing is rewritten
        self.assertEqual(
            (again["written_nodes"], again["written_edges"], again["checksum16"]), (0, 0, result["checksum16"])
        )
        writes = [(t, p) for t, mode, p in driver.sent if mode == "WRITE"]
        self.assertTrue(writes)
        for text, params in writes:
            self.assertIn(text, list(gi.NODE_WRITE.values()) + list(gi.EDGE_WRITE.values()))
            self.assertEqual(params["ns"], NS)
        self.assertEqual({m for _, m, _ in driver.sent}, {"READ", "WRITE"})
        self.assertTrue(
            all(
                m == "READ"
                for t, m, _ in driver.sent
                if t not in gi.NODE_WRITE.values() and t not in gi.EDGE_WRITE.values()
            )
        )
        with self.assertRaises(gi.Refused):
            client.read_namespace("um-t4-fake-v1")
        with self.assertRaises(gi.Refused):
            client.write("um-t4-fake-v1", {}, {})

    def test_the_pilot_state_is_read_only_and_compared_with_the_recorded_baseline(self):
        node_rows = [
            {
                "id": f"N{i}",
                "tenant": "t",
                "kind": "k",
                "title": "x",
                "text": "y",
                "revision": 1,
                "status": "current",
                "superseded_by": None,
            }
            for i in range(3)
        ]
        driver = FakeDriver({gi.PILOT_NODES: node_rows, gi.PILOT_EDGES: []})
        state = gi.AuraClient(driver).pilot_state()
        self.assertEqual(set(state), set(gi.PILOT_NAMESPACES))
        self.assertTrue(all(m == "READ" for _, m, _ in driver.sent))
        self.assertEqual(state["um-t4-fake-v1"]["nodes"], 3)
        self.assertNotEqual(state, gi.PILOT_BASELINE)
        self.assertEqual(
            gi.PILOT_BASELINE["um-t4-fake-v1"], {"nodes": 25, "edges": 17, "checksum16": "ac368c034feca277"}
        )

    def test_a_live_namespace_read_rejects_unknown_labels_and_properties_as_foreign(self):
        driver = FakeDriver(
            {
                gi.READ_NODES: [
                    {"labels": ["Ticket"], "p": {"ns": NS, "key": "LIFE-9001", "status": "Done", "suppressed": False}},
                    {"labels": ["Ticket"], "p": {"ns": NS, "key": "LIFE-9002", "status": "Done", "title": "no"}},
                    {"labels": ["Other"], "p": {"ns": NS}},
                ],
                gi.READ_EDGES: [],
            }
        )
        nodes, edges, foreign = gi.AuraClient(driver).read_namespace(NS)
        self.assertEqual((len(nodes), len(edges), foreign), (1, 0, 2))


class FakeConnection:
    def __init__(self, log, rows=None, fail_insert=False, rowcount=1):
        self.log, self.rows, self.fail_insert, self.rowcount_value = log, rows or [], fail_insert, rowcount

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, text, params=None):
        if self.fail_insert and text.startswith("INSERT"):
            raise RuntimeError("duplicate")
        self.log.append((text, params))
        self.rowcount = self.rowcount_value

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


class Checkpoint(unittest.TestCase):
    def test_the_store_names_only_its_own_table_and_never_deletes_or_replaces(self):  # acceptance 9
        log = []
        store = gi.CheckpointStore(lambda: FakeConnection(log))
        store.ensure_table()
        generation = store.begin(NS, T0)
        store.publish(NS, generation, 12, 11, "a" * 64, "b" * 64, T0)
        store.latest(NS)
        self.assertEqual(generation, 1)
        text = " ".join(t for t, _ in log)
        self.assertIn("v7_graph_index_checkpoint", text)
        self.assertEqual(set(re.findall(r"\bv7_[a-z_]+", text)), {"v7_graph_index_checkpoint"})
        self.assertIsNone(re.search(r"\b(DELETE|REPLACE|DROP|TRUNCATE|ALTER)\b", text))
        self.assertTrue(any("WHERE ns=%s AND generation=%s AND status='building'" in t for t, _ in log))

    def test_a_duplicate_generation_and_a_publish_that_changes_nothing_fail_closed(self):
        with self.assertRaisesRegex(gi.Failed, "CHECKPOINT_RACE"):
            gi.CheckpointStore(lambda: FakeConnection([], fail_insert=True)).begin(NS, T0)
        with self.assertRaisesRegex(gi.Failed, "CHECKPOINT_NOT_BUILDING"):
            gi.CheckpointStore(lambda: FakeConnection([], rowcount=0)).publish(NS, 1, 1, 1, "a" * 64, "b" * 64, T0)

    def test_the_latest_row_is_read_back_as_a_dict(self):
        row = (NS, 3, "published", 12, 11, "a" * 64, "b" * 64, T0, T0)
        got = gi.CheckpointStore(lambda: FakeConnection([], rows=[row])).latest(NS)
        self.assertEqual((got["generation"], got["status"], got["nodes"]), (3, "published", 12))

    def test_the_connection_uses_the_checkpoint_login_directly_with_no_tunnel(self):
        seen = {}

        class Driver:
            @staticmethod
            def connect(**kwargs):
                seen.update(kwargs)
                return "connection"

        connect = gi.connect_checkpoint({"host": "h", "name": "d", "user": "u", "password": "p"}, driver=Driver)
        self.assertEqual(connect(), "connection")
        self.assertEqual((seen["host"], seen["database"], seen["user"]), ("h", "d", "u"))
        self.assertNotIn("port", seen)
        self.assertEqual(seen["autocommit"], True)

    def test_no_code_path_touches_the_v7_store(self):
        for path in (ROOT / "lifeos/platform/graph_index.py", ROOT / "tools/graph_index_sync.py"):
            text = path.read_text()
            self.assertNotIn("lifeos.platform import db", text)
            self.assertNotIn("platform.db", text)
            self.assertNotIn("LIFEOS_ACQ", text)
            self.assertNotIn("ssh", text.lower().replace("pushing", ""))

    def test_the_driver_is_pinned_with_a_hash_in_the_lock_file(self):
        lock = (ROOT / "requirements.lock").read_text()
        self.assertRegex(lock, r"(?m)^PyMySQL==[0-9.]+ \\\n\s+--hash=sha256:[0-9a-f]{64}")
        self.assertIn("PyMySQL==", (ROOT / "requirements.txt").read_text())


if __name__ == "__main__":
    unittest.main()
