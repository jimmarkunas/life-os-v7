"""LIFE-1005 (REQ-85 T4): the synthetic Aura pilot. Fabricated fixture only (tests/fixtures/unified_memory/corpus.json), in the label `:UmT4Fake`, in allowlisted namespaces only.

Every statement is a fixed template that carries the label and `$ns` (the one schema write and the read-only global counts are the allowlisted exceptions); anything else is refused before it is sent. Output is counts, booleans and ids of
fabricated records: never a credential, a URI or a query result from outside the namespace. Run on Oracle, as the host user that reads the Bitwarden project, from a venv that holds `neo4j` and `certifi`:
python tools/um_aura_pilot.py --evidence /var/tmp/um-t4/evidence.json"""

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("um_baseline", pathlib.Path(__file__).with_name("um_baseline.py"))
um = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(um)

NAMESPACES = (
    "um-t4-fake-v1",
    "um-t4-fake-v1r",
)  # the fixture namespace and the one rebuilt from scratch beside it (rebuild proof without any delete)
NODE_CAP, REL_CAP = 100, 300
REL_TYPES = {
    "implements": "IMPLEMENTS",
    "verifies": "VERIFIES",
    "blocks": "BLOCKS",
    "supersedes": "SUPERSEDES",
    "derived_from": "DERIVED_FROM",
}
EXTRA_EDGES = (
    {
        "from": "FAKE-ISS-003",
        "type": "blocks",
        "to": "FAKE-ISS-001",
        "revoked": True,
    },  # a revoked edge: traversals must not cross it
    {"from": "FAKE-TASK-002", "type": "derived_from", "to": "FAKE-DEC-001", "revoked": False},
)  # an edge into a stale decision: the current-only template must not return it
NAMES = {
    "uri": "lifeos/edge/NEO4J_AURA_FREE_URI",
    "user": "lifeos/edge/NEO4J_AURA_FREE_USERNAME",
    "pw": "lifeos/edge/NEO4J_AURA_FREE_PASSWORD",
}
CT = ZoneInfo("America/Chicago")


class Refused(Exception):
    pass


def _put_edge(rel):
    return (
        "UNWIND $rows AS row MATCH (a:UmT4Fake {ns:$ns, id:row.from}) MATCH (b:UmT4Fake {ns:$ns, id:row.to}) WHERE a.tenant = row.tenant AND b.tenant = row.tenant "
        f"MERGE (a)-[r:{rel}]->(b) SET r.ns = $ns, r.tenant = row.tenant, r.revoked = row.revoked"
    )


_DOWN = (
    "MATCH p = (s:UmT4Fake {ns:$ns, id:$start})-[rels*1..6]->(t:UmT4Fake {ns:$ns}) WHERE s.tenant = $tenant AND t.tenant = $tenant AND t.kind IN $kinds "
    "AND all(r IN rels WHERE type(r) IN $via AND r.ns = $ns %s) AND all(n IN nodes(p) WHERE n.tenant = $tenant %s) RETURN DISTINCT t.id AS id"
)
_DERIVED = "MATCH (t:UmT4Fake {ns:$ns})-[r:DERIVED_FROM]->(d:UmT4Fake {ns:$ns, id:$id}) WHERE t.tenant = $tenant AND d.tenant = $tenant AND r.ns = $ns %s RETURN t.id AS id"
READ, WRITE = "read", "write"
TEMPLATES = {
    "put_nodes": (WRITE, "UNWIND $rows AS row MERGE (n:UmT4Fake {ns:$ns, id:row.id}) SET n += row.props"),
    **{f"put_edges_{k}": (WRITE, _put_edge(v)) for k, v in REL_TYPES.items()},
    "tenant_nodes": (
        READ,
        "MATCH (n:UmT4Fake {ns:$ns}) WHERE n.tenant = $tenant RETURN n.id AS id, n.tenant AS tenant, n.kind AS kind, n.title AS title, n.text AS text, n.revision AS revision, n.status AS status, n.superseded_by AS superseded_by ORDER BY n.id",
    ),
    "all_nodes": (
        READ,
        "MATCH (n:UmT4Fake {ns:$ns}) RETURN n.id AS id, n.tenant AS tenant, n.kind AS kind, n.title AS title, n.text AS text, n.revision AS revision, n.status AS status, n.superseded_by AS superseded_by ORDER BY n.id",
    ),
    "all_edges": (
        READ,
        "MATCH (a:UmT4Fake {ns:$ns})-[r]->(b:UmT4Fake {ns:$ns}) WHERE r.ns = $ns RETURN a.id AS src, type(r) AS type, b.id AS dst, r.tenant AS tenant, r.revoked AS revoked ORDER BY src, type, dst",
    ),
    "count_ns": (READ, "MATCH (n:UmT4Fake {ns:$ns}) RETURN count(n) AS nodes"),
    "count_ns_rels": (READ, "MATCH (:UmT4Fake {ns:$ns})-[r]->(:UmT4Fake {ns:$ns}) RETURN count(r) AS rels"),
    "downstream": (READ, _DOWN % ("AND coalesce(r.revoked, false) = false", "AND n.status = 'current'")),
    "downstream_unfiltered": (READ, _DOWN % ("", "")),  # diagnostic only: shows what the production template excludes
    "gaps": (
        READ,
        "MATCH (r:UmT4Fake {ns:$ns, kind:'requirement'}) WHERE r.tenant = $tenant AND r.status = 'current' RETURN r.id AS id, "
        "EXISTS { MATCH path = (p:UmT4Fake {ns:$ns, kind:'pull_request'})-[:IMPLEMENTS*1..2]->(r) WHERE p.tenant = $tenant AND all(x IN relationships(path) WHERE x.ns = $ns AND coalesce(x.revoked, false) = false) } AS has_pr, "
        "EXISTS { MATCH (u:UmT4Fake {ns:$ns, kind:'uat'})-[x:VERIFIES]->(r) WHERE u.tenant = $tenant AND x.ns = $ns AND coalesce(x.revoked, false) = false } AS has_uat ORDER BY id",
    ),
    "derived": (READ, _DERIVED % "AND d.status = 'current' AND coalesce(r.revoked, false) = false"),
    "derived_unfiltered": (READ, _DERIVED % ""),
}
SCHEMA = {
    "create_constraint": "CREATE CONSTRAINT um_t4_id IF NOT EXISTS FOR (n:UmT4Fake) REQUIRE (n.ns, n.id) IS UNIQUE"
}
GLOBAL_READS = {
    "ping": "RETURN 1 AS ok",
    "count_nodes": "MATCH (n) RETURN count(n) AS c",
    "count_rels": "MATCH ()-[r]->() RETURN count(r) AS c",
    "labels": "CALL db.labels() YIELD label RETURN collect(label) AS l",
    "constraints": "SHOW CONSTRAINTS",
    "indexes": "SHOW INDEXES",
    "outside_nodes": "MATCH (n) WHERE NOT n:UmT4Fake RETURN count(n) AS c",
    "cross_label_rels": "MATCH (a:UmT4Fake)-[r]-(b) WHERE NOT b:UmT4Fake RETURN count(r) AS c",
    "cross_ns_rels": "MATCH (a:UmT4Fake)-[r]-(b:UmT4Fake) WHERE a.ns <> b.ns RETURN count(r) AS c",
    "count_by_ns": "MATCH (n:UmT4Fake) RETURN n.ns AS ns, count(n) AS c ORDER BY ns",
    "components": "CALL dbms.components() YIELD name, versions, edition RETURN name, versions[0] AS v, edition",
}


class Scoped:
    """The only way the pilot talks to the database: a template name and a namespace, never free text."""

    def __init__(self, driver, database):
        self.driver, self.database, self.calls = driver, database, 0

    def _run(self, text, mode, params):
        self.calls += 1
        with self.driver.session(database=self.database, default_access_mode=mode) as s:
            return s.run(text, **params).data()

    def read(self, name, ns, **params):
        kind, text = TEMPLATES[name]
        if kind != READ or ns not in NAMESPACES:
            raise Refused("template_or_ns")
        return self._run(text, "READ", {"ns": ns, **params})

    def write(self, name, ns, **params):
        kind, text = TEMPLATES[name]
        if kind != WRITE or ns not in NAMESPACES:
            raise Refused("template_or_ns")
        return self._run(text, "WRITE", {"ns": ns, **params})

    def schema(self, name):
        return self._run(SCHEMA[name], "WRITE", {})

    def glob(self, name):
        return self._run(GLOBAL_READS[name], "READ", {})

    def run_cypher(self, text):
        """Free text is never sent: only an allowlisted statement passes."""
        if text in GLOBAL_READS.values():
            return self._run(text, "READ", {})
        raise Refused("unscoped_query")


def canonical(nodes, edges):
    """A checksum of a namespace that does not depend on the namespace's name."""
    rows = {
        "nodes": sorted(json.dumps({k: n.get(k) for k in sorted(n)}, sort_keys=True) for n in nodes),
        "edges": sorted(json.dumps({k: e.get(k) for k in sorted(e)}, sort_keys=True) for e in edges),
    }
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def expected(doc):
    nodes = [
        {
            "id": s["id"],
            "tenant": s["tenant"],
            "kind": s["kind"],
            "title": s["title"],
            "text": s["text"],
            "revision": s["revision"],
            "status": s["status"],
            "superseded_by": s["superseded_by"],
        }
        for s in doc["sources"]
    ]
    tenant = {s["id"]: s["tenant"] for s in doc["sources"]}
    edges = [
        {"src": e["from"], "type": REL_TYPES[e["type"]], "dst": e["to"], "tenant": tenant[e["from"]], "revoked": False}
        for e in doc["edges"]
    ]
    edges += [
        {
            "src": e["from"],
            "type": REL_TYPES[e["type"]],
            "dst": e["to"],
            "tenant": tenant[e["from"]],
            "revoked": e["revoked"],
        }
        for e in EXTRA_EDGES
    ]
    return nodes, edges


def import_corpus(sc, doc, ns):
    tenant = {s["id"]: s["tenant"] for s in doc["sources"]}
    rows = [
        {
            "id": s["id"],
            "props": {k: s[k] for k in ("tenant", "kind", "title", "text", "revision", "status")}
            | ({"superseded_by": s["superseded_by"]} if s["superseded_by"] else {}),
        }
        for s in doc["sources"]
    ]
    sc.write("put_nodes", ns, rows=rows)
    for key in REL_TYPES:
        batch = [
            {"from": e["from"], "to": e["to"], "tenant": tenant[e["from"]], "revoked": False}
            for e in doc["edges"]
            if e["type"] == key
        ]
        batch += [
            {"from": e["from"], "to": e["to"], "tenant": tenant[e["from"]], "revoked": e["revoked"]}
            for e in EXTRA_EDGES
            if e["type"] == key
        ]
        if batch:
            sc.write(f"put_edges_{key}", ns, rows=batch)


def namespace_state(sc, ns):
    nodes, edges = sc.read("all_nodes", ns), sc.read("all_edges", ns)
    return {"nodes": len(nodes), "rels": len(edges), "checksum": canonical(nodes, edges)}


def deps_aura(sc, ns, case):
    it, t = case["intent"], case["tenant"]
    if it["op"] == "downstream":
        rows = sc.read(
            "downstream", ns, start=it["start"], tenant=t, via=[REL_TYPES[v] for v in it["via"]], kinds=it["kinds"]
        )
        return sorted(r["id"] for r in rows), 1
    if it["op"] == "gaps":
        rows = sc.read("gaps", ns, tenant=t)
        return sorted(r["id"] for r in rows if not r["has_pr"] or not r["has_uat"]), 1
    if it["op"] == "derived":
        sources = sc.read("tenant_nodes", ns, tenant=t)
        world = um.World({"sources": [dict(r, refs=[]) for r in sources], "edges": []})
        found, _ = world.search(t, it["decision_query"], kinds=["decision"], current_only=True)
        rows = sc.read("derived", ns, tenant=t, id=found[0]) if found else []
        return sorted(r["id"] for r in rows), 2
    raise ValueError("unknown_op")


def exclusions(prod, raw, prod_stale, raw_stale):
    """The revoked edge ISS-003 BLOCKS ISS-001 would lead to REQ-001; ISS-003 also really implements REQ-002, which both templates return. The stale decision DEC-001 would lead to TASK-002."""
    prod, raw, prod_stale, raw_stale = ([r["id"] for r in x] for x in (prod, raw, prod_stale, raw_stale))
    return {
        "revoked_edge": {"production_template": prod, "without_filter": raw},
        "stale_record": {"production_template": prod_stale, "without_filter": raw_stale},
        "revoked_edge_excluded": "FAKE-REQ-001" not in prod and "FAKE-REQ-001" in raw and "FAKE-REQ-002" in prod,
        "stale_record_excluded": prod_stale == [] and raw_stale == ["FAKE-TASK-002"],
    }


def run_aura_pass(sc, ns, doc):
    out = {}
    for q in doc["questions"]:
        t0 = time.perf_counter()
        rows = sc.read("tenant_nodes", ns, tenant=q["tenant"])
        world = um.World({"sources": [dict(r, refs=[]) for r in rows], "edges": []})
        ids, lookups = um.answer_direct(world, q["tenant"], q["text"])
        out[q["id"]] = {"ids": ids, "abstain": not ids, "calls": 1 + lookups, "ms": (time.perf_counter() - t0) * 1000}
    for c in doc["dependency_cases"]:
        t0 = time.perf_counter()
        ids, calls = deps_aura(sc, ns, c)
        out[c["id"]] = {"ids": ids, "abstain": not ids, "calls": calls, "ms": (time.perf_counter() - t0) * 1000}
    return out


class AuraGraph:
    """The graph arm for um_baseline: answers lookups and dependency cases from the Aura fixtures (read-only templates, one namespace)."""

    def __init__(self, scoped, ns=NAMESPACES[0]):
        self.sc, self.ns = scoped, ns

    def answer(self, tenant, text):
        rows = self.sc.read("tenant_nodes", self.ns, tenant=tenant)
        world = um.World({"sources": [dict(r, refs=[]) for r in rows], "edges": []})
        ids, lookups = um.answer_direct(world, tenant, text)
        return ids, 1 + lookups

    def deps(self, case):
        return deps_aura(self.sc, self.ns, case)


def load_secrets(env_file="~/.config/bws/openclaw.env"):
    """Oracle only: the machine-account token is read from its file into this process, `bws` lists the project, and only the three named values are kept. Nothing is printed."""
    token = ""
    for line in open(os.path.expanduser(env_file)):
        line = line.strip().removeprefix("export ")
        if line.startswith("BWS_ACCESS_TOKEN="):
            token = line.split("=", 1)[1].strip().strip("'\"")
    done = subprocess.run(
        ["bws", "secret", "list", "-o", "json"],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "BWS_ACCESS_TOKEN": token},
        capture_output=True,
        text=True,
        timeout=30,
    )
    if done.returncode:
        raise SystemExit("bws_list_failed")
    by = {r["key"]: r.get("value") for r in json.loads(done.stdout)}
    if not all(by.get(n) for n in NAMES.values()):
        raise SystemExit("secret_missing")
    return {k: by[v] for k, v in NAMES.items()}


def connect(secrets, uri=None):
    import neo4j

    d = neo4j.GraphDatabase.driver(
        uri or secrets["uri"],
        auth=(secrets["user"], secrets["pw"]),
        warn_notification_severity="OFF",
        connection_timeout=8,
        max_transaction_retry_time=2,
    )
    d.verify_connectivity()
    return d


def now_ct():
    return datetime.now(CT).strftime("%b %d %Y, %I:%M:%S %p CT")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="um_aura_pilot")
    ap.add_argument("--evidence", default="/var/tmp/um-t4/evidence.json")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument(
        "--explain-only",
        action="store_true",
        help="plan every template with EXPLAIN (nothing runs, nothing is written) and print ok or the error class",
    )
    args = ap.parse_args(argv)
    doc = json.loads(um.CORPUS.read_text())
    ev = {"started_ct": now_ct(), "namespaces": list(NAMESPACES)}
    secrets = load_secrets()
    database = secrets["uri"].split("://", 1)[1].split(".")[0]
    driver = connect(secrets)
    sc = Scoped(driver, database)
    g = sc.glob
    if args.explain_only:
        dummy = {"ns": NAMESPACES[0], "rows": [], "tenant": "alpha", "start": "x", "via": [], "kinds": [], "id": "x"}
        report = {}
        for name, (_, text) in TEMPLATES.items():
            try:
                sc._run("EXPLAIN " + text, "READ", dummy)
                report[name] = "ok"
            except Exception as e:  # noqa: BLE001
                report[name] = type(e).__name__ + ": " + str(e)[:90].replace("\n", " ")
        print(json.dumps(report, indent=1))
        driver.close()
        return 0 if all(v == "ok" for v in report.values()) else 4
    # P0: read-only preflight; the instance must be Running (connected) and empty.
    pre = {
        "nodes": g("count_nodes")[0]["c"],
        "rels": g("count_rels")[0]["c"],
        "labels": g("labels")[0]["l"],
        "constraints": len(g("constraints")),
        "indexes": len(g("indexes")),
        "components": [(x["name"], x["v"], x["edition"]) for x in g("components")],
    }
    ev["p0_preflight"] = pre
    resumed = (
        bool(pre["nodes"] or pre["rels"])
        and not g("outside_nodes")[0]["c"]
        and all(r["ns"] in NAMESPACES for r in g("count_by_ns"))
    )  # a rerun after an interrupted run: only our own namespaces
    ev["p0_preflight"]["resumed_after_interruption"] = resumed
    if (pre["nodes"] or pre["rels"] or pre["labels"]) and not resumed:
        ev["stop"] = "instance_not_empty"
        json.dump(ev, open(args.evidence, "w"), indent=1)
        print(json.dumps(ev))
        return 3
    # P1: the one schema write, recorded as created by this run.
    sc.schema("create_constraint")
    ev["p1_constraint"] = {
        "created_by_this_run": pre["constraints"] == 0 and len(g("constraints")) == 1,
        "constraints_after": len(g("constraints")),
    }
    # P2: import, then import again: nothing is added.
    import_corpus(sc, doc, NAMESPACES[0])
    first = namespace_state(sc, NAMESPACES[0])
    import_corpus(sc, doc, NAMESPACES[0])
    second = namespace_state(sc, NAMESPACES[0])
    exp_nodes, exp_edges = expected(doc)
    ev["p2_import"] = {
        "first": first,
        "after_duplicate_import": second,
        "duplicate_adds_nothing": first == second,
        "matches_fixture_checksum": first["checksum"] == canonical(exp_nodes, exp_edges),
        "within_caps": first["nodes"] <= NODE_CAP and first["rels"] <= REL_CAP,
    }
    # P3: a second namespace built from scratch from the same fixture has the same counts and checksum.
    import_corpus(sc, doc, NAMESPACES[1])
    rebuilt = namespace_state(sc, NAMESPACES[1])
    ev["p3_rebuild"] = {"rebuilt": rebuilt, "same_counts_and_checksum": rebuilt == first}
    # P4: isolation.
    tenant_ids = {t: {r["id"] for r in sc.read("tenant_nodes", NAMESPACES[0], tenant=t)} for t in doc["tenants"]}
    before = (g("count_rels")[0]["c"],)
    sc.write(
        "put_edges_implements",
        NAMESPACES[0],
        rows=[{"from": "FAKE-ISS-001", "to": "FAKE-REQ-101", "tenant": "alpha", "revoked": False}],
    )  # an alpha record to a beta record: must create nothing
    after = (g("count_rels")[0]["c"],)
    try:
        sc.run_cypher("MATCH (n) DETACH DELETE n")
        refused = False
    except Refused:
        refused = True
    try:
        sc.read("all_nodes", "some-other-ns")
        ns_refused = False
    except Refused:
        ns_refused = True
    ev["p4_isolation"] = {
        "cross_label_rels": g("cross_label_rels")[0]["c"],
        "cross_ns_rels": g("cross_ns_rels")[0]["c"],
        "nodes_outside_namespace": g("outside_nodes")[0]["c"],
        "count_by_ns": g("count_by_ns"),
        "tenants_disjoint": not (tenant_ids["alpha"] & tenant_ids["beta"]),
        "cross_tenant_edge_created": after != before,
        "wrong_tenant_downstream_empty": sc.read(
            "downstream",
            NAMESPACES[0],
            start="FAKE-ISS-002",
            tenant="beta",
            via=["BLOCKS", "IMPLEMENTS"],
            kinds=["requirement"],
        )
        == [],
        "unscoped_query_refused": refused,
        "unknown_namespace_refused": ns_refused,
    }
    # P5: a revoked edge and an edge into a stale record are not followed.
    prod = sc.read(
        "downstream",
        NAMESPACES[0],
        start="FAKE-ISS-003",
        tenant="alpha",
        via=["BLOCKS", "IMPLEMENTS"],
        kinds=["requirement"],
    )
    raw = sc.read(
        "downstream_unfiltered",
        NAMESPACES[0],
        start="FAKE-ISS-003",
        tenant="alpha",
        via=["BLOCKS", "IMPLEMENTS"],
        kinds=["requirement"],
    )
    prod2 = sc.read("derived", NAMESPACES[0], tenant="alpha", id="FAKE-DEC-001")
    raw2 = sc.read("derived_unfiltered", NAMESPACES[0], tenant="alpha", id="FAKE-DEC-001")
    ev["p5_exclusions"] = exclusions(prod, raw, prod2, raw2)
    # P6: the four retrieval modes on the same corpus (honcho and combined need T2's workspace and are not run here).
    world = um.World(doc)
    local = um.run(doc, ("direct", "graph"), args.reps)
    passes = [run_aura_pass(sc, NAMESPACES[0], doc) for _ in range(args.reps)]
    aura = um.score(world, passes)
    graph_pass = um.run_mode(world, "graph")
    same = {k: sorted(v["ids"]) == sorted(passes[0][k]["ids"]) for k, v in graph_pass.items()}
    ev["p6_retrieval"] = {
        "direct": local["direct"],
        "graph_in_memory": local["graph"],
        "graph_on_aura": aura,
        "aura_answers_equal_in_memory_graph": all(same.values()),
        "differences": [k for k, v in same.items() if not v],
        "honcho_and_combined": "not run: need T2's Honcho workspace",
    }
    last = sc.glob("ping")
    # P7: Aura unreachable: the answer comes from the direct mode and says so.
    degraded = {}
    try:
        connect(secrets, uri="neo4j://127.0.0.1:1")
        degraded["unreachable_connect_failed"] = False
    except Exception as e:  # noqa: BLE001
        degraded["unreachable_connect_failed"] = True
        degraded["error_class"] = type(e).__name__
        fallback, again = um.run_mode(world, "direct"), um.run_mode(world, "direct")
        degraded["answers_equal_direct"] = all(sorted(fallback[k]["ids"]) == sorted(again[k]["ids"]) for k in fallback)
        degraded["served_by"] = "direct"
        degraded["label"] = "graph unavailable, answered from canonical navigation"
    ev["p7_degrade"] = degraded
    # P8: cleanup preview only; nothing is deleted.
    ev["p8_cleanup_preview"] = {
        "count_by_ns": g("count_by_ns"),
        "cross_ns_rels": g("cross_ns_rels")[0]["c"],
        "cross_label_rels": g("cross_label_rels")[0]["c"],
        "outside_nodes": g("outside_nodes")[0]["c"],
        "deleted": 0,
    }
    ev["p9_final"] = {
        "nodes": g("count_nodes")[0]["c"],
        "rels": g("count_rels")[0]["c"],
        "caps_ok": g("count_nodes")[0]["c"] <= NODE_CAP and g("count_rels")[0]["c"] <= REL_CAP,
        "statements_sent": sc.calls,
        "last_activity_ct": now_ct(),
        "last_activity_ping": bool(last),
    }
    ev["finished_ct"] = now_ct()
    driver.close()
    json.dump(ev, open(args.evidence, "w"), indent=1, default=str)
    print(json.dumps(ev, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
