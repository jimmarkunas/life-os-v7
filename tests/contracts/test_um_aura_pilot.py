"""(REQ-85 T4): the Aura pilot's guard rails, with no database: every statement is a fixed template that carries the
label and the namespace, anything.
"""

import importlib.util
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("um_aura_pilot", ROOT / "tools" / "um_aura_pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
DOC = json.loads(pilot.um.CORPUS.read_text())


class FakeSession:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, text, **params):
        self.log.append((text, params))
        return type("R", (), {"data": lambda _: self.rows})()


class FakeDriver:
    def __init__(self, rows=()):
        self.log, self.modes, self.rows = [], [], list(rows)

    def session(self, database, default_access_mode):
        self.modes.append((database, default_access_mode))
        return FakeSession(self.log, self.rows)


class Templates(unittest.TestCase):
    def test_every_template_carries_the_label_and_the_namespace(self):
        for name, (kind, text) in pilot.TEMPLATES.items():
            self.assertIn(":UmT4Fake", text, name)
            self.assertIn("$ns", text, name)
            self.assertIn(kind, (pilot.READ, pilot.WRITE))
        self.assertNotRegex(" ".join(t for _, t in pilot.TEMPLATES.values()), r"DETACH DELETE|DROP |REMOVE ")

    def test_only_one_schema_write_exists_and_it_is_the_ticket_constraint(self):
        self.assertEqual(list(pilot.SCHEMA), ["create_constraint"])
        self.assertIn("um_t4_id", pilot.SCHEMA["create_constraint"])
        self.assertIn("(n.ns, n.id) IS UNIQUE", pilot.SCHEMA["create_constraint"])

    def test_the_global_reads_are_read_only(self):
        for name, text in pilot.GLOBAL_READS.items():
            self.assertNotRegex(text, r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP)\b", name)

    def test_relationship_types_are_a_closed_list(self):
        self.assertEqual(
            set(pilot.REL_TYPES.values()), {"IMPLEMENTS", "VERIFIES", "BLOCKS", "SUPERSEDES", "DERIVED_FROM"}
        )
        self.assertTrue({e["type"] for e in DOC["edges"]} <= set(pilot.REL_TYPES))


class Scope(unittest.TestCase):
    def test_a_read_goes_out_in_read_mode_with_the_database_and_the_namespace(self):
        d = FakeDriver([{"id": "x"}])
        sc = pilot.Scoped(d, "db1")
        sc.read("tenant_nodes", "um-t4-fake-v1", tenant="alpha")
        self.assertEqual(d.modes, [("db1", "READ")])
        self.assertEqual(d.log[0][1]["ns"], "um-t4-fake-v1")

    def test_a_write_template_cannot_be_used_as_a_read_and_the_reverse(self):
        sc = pilot.Scoped(FakeDriver(), "db1")
        with self.assertRaises(pilot.Refused):
            sc.read("put_nodes", "um-t4-fake-v1", rows=[])
        with self.assertRaises(pilot.Refused):
            sc.write("tenant_nodes", "um-t4-fake-v1", tenant="alpha")

    def test_an_unknown_namespace_and_free_text_are_refused_before_anything_is_sent(self):
        d = FakeDriver()
        sc = pilot.Scoped(d, "db1")
        for bad in (
            lambda: sc.read("all_nodes", "another-ns"),
            lambda: sc.run_cypher("MATCH (n) DETACH DELETE n"),
            lambda: sc.run_cypher("MATCH (n) RETURN n"),
        ):
            with self.assertRaises(pilot.Refused):
                bad()
        self.assertEqual(d.log, [])

    def test_an_unknown_template_name_fails_closed(self):
        with self.assertRaises(KeyError):
            pilot.Scoped(FakeDriver(), "db1").read("anything", "um-t4-fake-v1")


class Checksum(unittest.TestCase):
    def test_the_fixture_state_is_deterministic_and_independent_of_the_namespace(self):
        nodes, edges = pilot.expected(DOC)
        self.assertEqual(pilot.canonical(nodes, edges), pilot.canonical(list(reversed(nodes)), list(reversed(edges))))
        self.assertNotEqual(pilot.canonical(nodes, edges), pilot.canonical(nodes[:-1], edges))

    def test_the_caps_hold_for_both_namespaces(self):
        nodes, edges = pilot.expected(DOC)
        self.assertLessEqual(len(nodes) * len(pilot.NAMESPACES), pilot.NODE_CAP)
        self.assertLessEqual(len(edges) * len(pilot.NAMESPACES), pilot.REL_CAP)

    def test_the_two_extra_edges_exist_in_the_corpus_ids(self):
        ids = {s["id"] for s in DOC["sources"]}
        for e in pilot.EXTRA_EDGES:
            self.assertTrue({e["from"], e["to"]} <= ids)
        self.assertEqual([e["revoked"] for e in pilot.EXTRA_EDGES], [True, False])


class Exclusions(unittest.TestCase):
    rows = staticmethod(lambda *ids: [{"id": i} for i in ids])

    def test_a_revoked_edge_and_a_stale_record_are_reported_excluded_only_when_the_filter_removed_them(self):
        r = pilot.exclusions(
            self.rows("FAKE-REQ-002"),
            self.rows("FAKE-REQ-002", "FAKE-REQ-001"),
            self.rows(),
            self.rows("FAKE-TASK-002"),
        )
        self.assertEqual((r["revoked_edge_excluded"], r["stale_record_excluded"]), (True, True))

    def test_it_says_false_when_the_filter_let_them_through(self):
        r = pilot.exclusions(
            self.rows("FAKE-REQ-002", "FAKE-REQ-001"),
            self.rows("FAKE-REQ-002", "FAKE-REQ-001"),
            self.rows("FAKE-TASK-002"),
            self.rows("FAKE-TASK-002"),
        )
        self.assertEqual((r["revoked_edge_excluded"], r["stale_record_excluded"]), (False, False))


class Arm(unittest.TestCase):
    def test_the_aura_graph_arm_answers_a_lookup_and_a_dependency_case_through_read_templates_only(self):
        rows = [
            dict(
                id=s["id"],
                tenant=s["tenant"],
                kind=s["kind"],
                title=s["title"],
                text=s["text"],
                revision=s["revision"],
                status=s["status"],
                superseded_by=s["superseded_by"],
            )
            for s in DOC["sources"]
            if s["tenant"] == "alpha"
        ]

        class Sc:
            def __init__(self):
                self.log = []

            def read(self, name, ns, **p):
                self.log.append(name)
                return rows if name == "tenant_nodes" else [{"id": "FAKE-REQ-001"}]

        sc = Sc()
        arm = pilot.AuraGraph(sc)
        ids, calls = arm.answer("alpha", "What does the offline export bundle requirement say?")
        self.assertEqual((ids[0], calls), ("FAKE-REQ-001", 2))  # one fetch plus one lookup
        ids, calls = arm.deps(DOC["dependency_cases"][0])
        self.assertEqual(ids, ["FAKE-REQ-001"])
        self.assertTrue(set(sc.log) <= {"tenant_nodes", "downstream"})


class Hygiene(unittest.TestCase):
    def test_the_script_holds_no_credential_and_names_secrets_only_by_their_item_names(self):
        text = (ROOT / "tools" / "um_aura_pilot.py").read_text()
        self.assertEqual(
            set(pilot.NAMES.values()),
            {
                "lifeos/edge/NEO4J_AURA_FREE_URI",
                "lifeos/edge/NEO4J_AURA_FREE_USERNAME",
                "lifeos/edge/NEO4J_AURA_FREE_PASSWORD",
            },
        )
        self.assertIsNone(re.search(r"neo4j\+s://\S+|databases\.neo4j\.io|BWS_ACCESS_TOKEN\s*=\s*['\"]\w{8}", text))

    def test_import_writes_nodes_once_and_every_edge_batch_carries_its_tenant(self):
        d = FakeDriver()
        pilot.import_corpus(pilot.Scoped(d, "db1"), DOC, "um-t4-fake-v1")
        self.assertEqual(sum(1 for t, _ in d.log if "MERGE (n:UmT4Fake" in t), 1)
        rows = [r for _, p in d.log for r in p["rows"] if "from" in r]
        self.assertEqual(len(rows), len(DOC["edges"]) + len(pilot.EXTRA_EDGES))
        self.assertTrue(all(r["tenant"] in ("alpha", "beta") for r in rows))


if __name__ == "__main__":
    unittest.main()
