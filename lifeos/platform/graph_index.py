"""GI-1: the deterministic loader for the derived AuraDB relationship index.

Aura holds only ids, repo-scoped PR numbers, typed edges and an allowlisted status, in one namespace per load. It is derived and rebuildable: Jira, Notion and
GitHub stay canonical. Removal is additive (a `suppressed` flag); this module never deletes anything remotely. The checkpoint lives only in its own MySQL
database (table v7_graph_index_checkpoint), never in the V7 store. Every error is a fixed code, never a value."""

from datetime import timedelta
import hashlib
import json
import re

NS_FIXTURE = "gi1-fixture-v1"
ALLOWED_NS = (NS_FIXTURE,)
NODE_CAP, REL_CAP = 20000, 40000
FRESH_HOURS = 24
BUILDING_GRACE_MINUTES = 15
TABLE = "v7_graph_index_checkpoint"
CREATE = (
    f"CREATE TABLE IF NOT EXISTS {TABLE} (ns VARCHAR(64) NOT NULL, generation INT NOT NULL,"
    " status ENUM('building','published') NOT NULL, nodes INT NOT NULL, edges INT NOT NULL, checksum CHAR(64) NOT NULL,"
    " membership_digest CHAR(64) NOT NULL, created_at DATETIME NOT NULL, published_at DATETIME NULL,"
    " PRIMARY KEY (ns, generation)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
)

# node kind -> properties a node may carry (besides its kind); anything else fails validation.
NODE_PROPS = {
    "Requirement": ("id", "status", "suppressed"),
    "Ticket": ("key", "status", "suppressed"),
    "PullRequest": ("repo", "number", "state", "suppressed"),
}
REQUIRED = {"Requirement": ("id", "status"), "Ticket": ("key", "status"), "PullRequest": ("repo", "number", "state")}
STATUS = {
    "Requirement": ("draft", "approved", "delivered", "retired"),
    "Ticket": ("To Do", "Agent Go", "In Progress", "Human UAT", "Blocked", "Done", "Cancelled"),
    "PullRequest": ("open", "merged", "closed"),
}
STATUS_KEY = {"Requirement": "status", "Ticket": "status", "PullRequest": "state"}
EDGE_TYPES = {
    "BLOCKS": ("Ticket", "Ticket"),
    "CHILD_OF": ("Ticket", "Ticket"),
    "IMPLEMENTS": ("Ticket", "Requirement"),
    "DELIVERED_BY": ("Ticket", "PullRequest"),
}
_PATTERN = {
    "id": re.compile(r"^REQ-[0-9]{1,6}$"),
    "key": re.compile(r"^LIFE-[0-9]{1,6}$"),
    "repo": re.compile(r"^[a-z0-9][a-z0-9-]*/[A-Za-z0-9._-]+$"),
}
_NOTION_ID = re.compile(
    r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|notion\.(so|com)"
)


class Refused(Exception):
    """The input or the target is not allowed; nothing was written. The message is a fixed code."""


class Failed(Exception):
    """A step after the checkpoint was opened failed; the checkpoint stays `building`, so the graph is unserved."""


class Unserved(Exception):
    """A reader must not serve the graph; the message says why (a fixed code)."""


# ---------------------------------------------------------------------------------------------- records


def node_id(node):
    kind = node["kind"]
    if kind == "Requirement":
        return f"Requirement:{node['id']}"
    if kind == "Ticket":
        return f"Ticket:{node['key']}"
    return f"PullRequest:{node['repo']}#{node['number']}"


def validate_node(node):
    kind = node.get("kind")
    if kind not in NODE_PROPS:
        raise Refused("NODE_KIND_NOT_ALLOWED")
    for name in node:
        if name != "kind" and name not in NODE_PROPS[kind]:
            raise Refused(f"PROPERTY_NOT_ALLOWED:{name}")
    for name in REQUIRED[kind]:
        if name not in node:
            raise Refused(f"PROPERTY_MISSING:{name}")
    for name, value in node.items():
        if name == "kind":
            continue
        if name == "suppressed":
            if not isinstance(value, bool):
                raise Refused("VALUE_NOT_ALLOWED:suppressed")
        elif name == "number":
            if not isinstance(value, int) or isinstance(value, bool) or not 0 < value < 10**7:
                raise Refused("VALUE_NOT_ALLOWED:number")
        elif not isinstance(value, str) or _NOTION_ID.search(value):
            raise Refused(f"VALUE_NOT_ALLOWED:{name}")
        elif name in _PATTERN and not _PATTERN[name].match(value):
            raise Refused(f"VALUE_NOT_ALLOWED:{name}")
    if node[STATUS_KEY[kind]] not in STATUS[kind]:
        raise Refused(f"STATUS_NOT_ALLOWED:{kind}")
    return node


def validate_edge(edge, ids):
    if set(edge) - {"type", "src", "dst", "suppressed"} or edge.get("type") not in EDGE_TYPES:
        raise Refused("EDGE_NOT_ALLOWED")
    want_src, want_dst = EDGE_TYPES[edge["type"]]
    for end, want in (("src", want_src), ("dst", want_dst)):
        if not isinstance(edge.get(end), str) or not edge[end].startswith(want + ":") or edge[end] not in ids:
            raise Refused("EDGE_ENDPOINT_NOT_ALLOWED")
    if "suppressed" in edge and not isinstance(edge["suppressed"], bool):
        raise Refused("VALUE_NOT_ALLOWED:suppressed")
    return edge


def edge_key(edge):
    return (edge["type"], edge["src"], edge["dst"])


def validate_all(nodes, edges):
    """-> ({node id: node}, {edge key: edge}) when every record is allowed, else Refused. No namespace or cap rule here."""
    by_id = {}
    for node in nodes:
        by_id[node_id(validate_node(dict(node)))] = {**node, "suppressed": bool(node.get("suppressed", False))}
    by_edge = {}
    for edge in edges:
        validate_edge(edge, by_id)
        by_edge[edge_key(edge)] = {**edge, "suppressed": bool(edge.get("suppressed", False))}
    return by_id, by_edge


def check_target(ns, nodes, edges):
    if ns not in ALLOWED_NS:
        raise Refused("NAMESPACE_NOT_ALLOWED")
    if len(nodes) > NODE_CAP or len(edges) > REL_CAP:
        raise Refused("CAP_EXCEEDED")


# ---------------------------------------------------------------------------------------------- checksums


def _rows(nodes, edges, active_only):
    n = sorted(
        json.dumps({k: v for k, v in node.items()}, sort_keys=True)
        for node in nodes.values()
        if not (active_only and node["suppressed"])
    )
    e = sorted(
        json.dumps({k: v for k, v in edge.items()}, sort_keys=True)
        for edge in edges.values()
        if not (active_only and edge["suppressed"])
    )
    return {"nodes": n, "edges": e}


def checksum(nodes, edges):
    """sha256 of every stored record, suppressed ones included (the graph as written)."""
    return hashlib.sha256(json.dumps(_rows(nodes, edges, False), sort_keys=True).encode()).hexdigest()


def membership_digest(nodes, edges):
    """sha256 of the ACTIVE records only: the set a reader may serve. A suppressed record is outside it."""
    return hashlib.sha256(json.dumps(_rows(nodes, edges, True), sort_keys=True).encode()).hexdigest()


def counts(nodes, edges):
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "active_nodes": sum(1 for n in nodes.values() if not n["suppressed"]),
        "active_edges": sum(1 for e in edges.values() if not e["suppressed"]),
    }


# ---------------------------------------------------------------------------------------------- plan


def plan(existing_nodes, existing_edges, desired_nodes, desired_edges):
    """What an apply writes (additive only). Records absent from `desired` are suppressed, never deleted."""
    node_rows, edge_rows = {}, {}
    for key, node in desired_nodes.items():
        old = existing_nodes.get(key)
        if old is None or old != node:
            node_rows[key] = node
    for key, old in existing_nodes.items():
        if key not in desired_nodes and not old["suppressed"]:
            node_rows[key] = {**old, "suppressed": True}
    for key, edge in desired_edges.items():
        old = existing_edges.get(key)
        if old is None or old != edge:
            edge_rows[key] = edge
    for key, old in existing_edges.items():
        if key not in desired_edges and not old["suppressed"]:
            edge_rows[key] = {**old, "suppressed": True}
    return node_rows, edge_rows


# ---------------------------------------------------------------------------------------------- clients


class MemoryClient:
    """An in-memory stand-in for Aura. It is the only client that can wipe (the live client has no delete)."""

    def __init__(self, nodes=None, edges=None, foreign=0):
        self.nodes, self.edges, self.foreign = dict(nodes or {}), dict(edges or {}), foreign
        self.writes = 0

    def read_namespace(self, ns):
        return dict(self.nodes), dict(self.edges), self.foreign

    def write(self, ns, node_rows, edge_rows):
        self.writes += 1
        self.nodes.update({k: dict(v) for k, v in node_rows.items()})
        self.edges.update({k: dict(v) for k, v in edge_rows.items()})

    def wipe(self, ns):
        self.nodes, self.edges = {}, {}


NODE_WRITE = {
    "Requirement": "UNWIND $rows AS row MERGE (n:Requirement {ns:$ns, id:row.id}) SET n.status = row.status, n.suppressed = row.suppressed",
    "Ticket": "UNWIND $rows AS row MERGE (n:Ticket {ns:$ns, key:row.key}) SET n.status = row.status, n.suppressed = row.suppressed",
    "PullRequest": "UNWIND $rows AS row MERGE (n:PullRequest {ns:$ns, repo:row.repo, number:row.number}) SET n.state = row.state, n.suppressed = row.suppressed",
}
_TICKET_END = "(a:Ticket {ns:$ns, key:row.src})"
EDGE_WRITE = {
    "BLOCKS": f"UNWIND $rows AS row MATCH {_TICKET_END} MATCH (b:Ticket {{ns:$ns, key:row.dst}}) MERGE (a)-[r:BLOCKS {{ns:$ns}}]->(b) SET r.suppressed = row.suppressed",
    "CHILD_OF": f"UNWIND $rows AS row MATCH {_TICKET_END} MATCH (b:Ticket {{ns:$ns, key:row.dst}}) MERGE (a)-[r:CHILD_OF {{ns:$ns}}]->(b) SET r.suppressed = row.suppressed",
    "IMPLEMENTS": f"UNWIND $rows AS row MATCH {_TICKET_END} MATCH (b:Requirement {{ns:$ns, id:row.dst}}) MERGE (a)-[r:IMPLEMENTS {{ns:$ns}}]->(b) SET r.suppressed = row.suppressed",
    "DELIVERED_BY": f"UNWIND $rows AS row MATCH {_TICKET_END} MATCH (b:PullRequest {{ns:$ns, repo:row.repo, number:row.number}}) MERGE (a)-[r:DELIVERED_BY {{ns:$ns}}]->(b) SET r.suppressed = row.suppressed",
}
READ_NODES = "MATCH (n) WHERE n.ns = $ns RETURN labels(n) AS labels, properties(n) AS p"
READ_EDGES = (
    "MATCH (a)-[r]->(b) WHERE a.ns = $ns OR b.ns = $ns OR r.ns = $ns "
    "RETURN labels(a) AS al, properties(a) AS ap, type(r) AS t, properties(r) AS rp, labels(b) AS bl, properties(b) AS bp"
)
READ_TOTAL_NODES = "MATCH (n) RETURN count(n) AS c"
READ_TOTAL_RELS = "MATCH ()-[r]->() RETURN count(r) AS c"
# The pilot's own read-only statements (tools/um_aura_pilot.py), used only to prove the pilot data did not change.
PILOT_NODES = "MATCH (n:UmT4Fake {ns:$ns}) RETURN n.id AS id, n.tenant AS tenant, n.kind AS kind, n.title AS title, n.text AS text, n.revision AS revision, n.status AS status, n.superseded_by AS superseded_by ORDER BY n.id"
PILOT_EDGES = "MATCH (a:UmT4Fake {ns:$ns})-[r]->(b:UmT4Fake {ns:$ns}) WHERE r.ns = $ns RETURN a.id AS src, type(r) AS type, b.id AS dst, r.tenant AS tenant, r.revoked AS revoked ORDER BY src, type, dst"
PILOT_NAMESPACES = ("um-t4-fake-v1", "um-t4-fake-v1r")
PILOT_BASELINE = {ns: {"nodes": 25, "edges": 17, "checksum16": "ac368c034feca277"} for ns in PILOT_NAMESPACES}


def _node_from_row(labels, p):
    kind = labels[0] if len(labels) == 1 else None
    if kind not in NODE_PROPS or set(p) - set(NODE_PROPS[kind]) - {"ns"}:
        return None
    node = {"kind": kind, **{k: v for k, v in p.items() if k != "ns"}}
    node["suppressed"] = bool(node.get("suppressed", False))
    return node


class AuraClient:
    """The live client: only the statements above, nothing built from input, no delete. `driver` is a neo4j driver."""

    def __init__(self, driver, database=None):
        self.driver, self.database = driver, database

    def _run(self, text, mode, **params):
        with self.driver.session(database=self.database, default_access_mode=mode) as session:
            return session.run(text, **params).data()

    def read_namespace(self, ns):
        if ns not in ALLOWED_NS:
            raise Refused("NAMESPACE_NOT_ALLOWED")
        nodes, edges, foreign = {}, {}, 0
        for row in self._run(READ_NODES, "READ", ns=ns):
            node = _node_from_row(row["labels"], row["p"])
            if node is None:
                foreign += 1
            else:
                nodes[node_id(node)] = node
        for row in self._run(READ_EDGES, "READ", ns=ns):
            a, b = _node_from_row(row["al"], row["ap"]), _node_from_row(row["bl"], row["bp"])
            if (
                a is None
                or b is None
                or row["t"] not in EDGE_TYPES
                or row["rp"].get("ns") != ns
                or row["ap"].get("ns") != ns
                or row["bp"].get("ns") != ns
            ):
                foreign += 1
                continue
            edge = {
                "type": row["t"],
                "src": node_id(a),
                "dst": node_id(b),
                "suppressed": bool(row["rp"].get("suppressed", False)),
            }
            edges[edge_key(edge)] = edge
        return nodes, edges, foreign

    def write(self, ns, node_rows, edge_rows):
        if ns not in ALLOWED_NS:
            raise Refused("NAMESPACE_NOT_ALLOWED")
        for kind in ("Requirement", "Ticket", "PullRequest"):
            rows = [{k: v for k, v in n.items() if k != "kind"} for n in node_rows.values() if n["kind"] == kind]
            if rows:
                self._run(NODE_WRITE[kind], "WRITE", ns=ns, rows=rows)
        for etype in EDGE_TYPES:
            rows = []
            for e in edge_rows.values():
                if e["type"] != etype:
                    continue
                row = {"src": e["src"].split(":", 1)[1], "suppressed": e["suppressed"]}
                end = e["dst"].split(":", 1)[1]
                if etype == "DELIVERED_BY":
                    repo, number = end.rsplit("#", 1)
                    row.update(repo=repo, dst=end, number=int(number))
                else:
                    row["dst"] = end
                rows.append(row)
            if rows:
                self._run(EDGE_WRITE[etype], "WRITE", ns=ns, rows=rows)

    def totals(self):
        return self._run(READ_TOTAL_NODES, "READ")[0]["c"], self._run(READ_TOTAL_RELS, "READ")[0]["c"]

    def pilot_state(self):
        """Read-only: node and relationship counts and the pilot's own content checksum for each pilot namespace."""
        out = {}
        for ns in PILOT_NAMESPACES:
            nodes, edges = self._run(PILOT_NODES, "READ", ns=ns), self._run(PILOT_EDGES, "READ", ns=ns)
            rows = {
                "nodes": sorted(json.dumps({k: n.get(k) for k in sorted(n)}, sort_keys=True) for n in nodes),
                "edges": sorted(json.dumps({k: e.get(k) for k in sorted(e)}, sort_keys=True) for e in edges),
            }
            out[ns] = {
                "nodes": len(nodes),
                "edges": len(edges),
                "checksum16": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16],
            }
        return out


# ---------------------------------------------------------------------------------------------- checkpoint


class MemoryStore:
    """An in-memory stand-in for the checkpoint table, with the same rules (insert once, publish once, never overwrite)."""

    def __init__(self):
        self.rows = {}

    def latest(self, ns):
        gens = [g for (n, g) in self.rows if n == ns]
        return dict(self.rows[(ns, max(gens))]) if gens else None

    def begin(self, ns, now):
        last = self.latest(ns)
        if (
            last
            and last["status"] == "building"
            and now - last["created_at"] < timedelta(minutes=BUILDING_GRACE_MINUTES)
        ):
            raise Failed("CHECKPOINT_BUSY")
        generation = (last["generation"] if last else 0) + 1
        if (ns, generation) in self.rows:
            raise Failed("CHECKPOINT_RACE")
        self.rows[(ns, generation)] = {
            "ns": ns,
            "generation": generation,
            "status": "building",
            "nodes": 0,
            "edges": 0,
            "checksum": "0" * 64,
            "membership_digest": "0" * 64,
            "created_at": now,
            "published_at": None,
        }
        return generation

    def publish(self, ns, generation, nodes, edges, check, digest, now):
        row = self.rows.get((ns, generation))
        if not row or row["status"] != "building":
            raise Failed("CHECKPOINT_NOT_BUILDING")
        row.update(
            status="published", nodes=nodes, edges=edges, checksum=check, membership_digest=digest, published_at=now
        )


class CheckpointStore:
    """The checkpoint table in its own MySQL database. `connect` returns a DB-API connection (pymysql). Only this table is
    named; there is no DELETE, REPLACE, DROP or TRUNCATE, and the V7 store module is never imported."""

    def __init__(self, connect):
        self._connect = connect

    def _cursor_run(self, work):
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                return work(cursor)
        finally:
            connection.close()

    def ensure_table(self):
        self._cursor_run(lambda c: c.execute(CREATE))

    def latest(self, ns):
        def work(cursor):
            cursor.execute(
                f"SELECT ns, generation, status, nodes, edges, checksum, membership_digest, created_at, published_at FROM {TABLE} WHERE ns=%s ORDER BY generation DESC LIMIT 1",
                (ns,),
            )
            row = cursor.fetchone()
            keys = (
                "ns",
                "generation",
                "status",
                "nodes",
                "edges",
                "checksum",
                "membership_digest",
                "created_at",
                "published_at",
            )
            return dict(zip(keys, row, strict=True)) if row else None

        return self._cursor_run(work)

    def begin(self, ns, now):
        last = self.latest(ns)
        if (
            last
            and last["status"] == "building"
            and now - last["created_at"] < timedelta(minutes=BUILDING_GRACE_MINUTES)
        ):
            raise Failed("CHECKPOINT_BUSY")
        generation = (last["generation"] if last else 0) + 1

        def work(cursor):
            cursor.execute(
                f"INSERT INTO {TABLE} (ns, generation, status, nodes, edges, checksum, membership_digest, created_at) VALUES (%s, %s, 'building', 0, 0, %s, %s, %s)",
                (ns, generation, "0" * 64, "0" * 64, now),
            )

        try:
            self._cursor_run(work)
        except Exception:
            raise Failed("CHECKPOINT_RACE") from None  # a duplicate generation: another run opened it first
        return generation

    def publish(self, ns, generation, nodes, edges, check, digest, now):
        def work(cursor):
            cursor.execute(
                f"UPDATE {TABLE} SET status='published', nodes=%s, edges=%s, checksum=%s, membership_digest=%s, published_at=%s WHERE ns=%s AND generation=%s AND status='building'",
                (nodes, edges, check, digest, now, ns, generation),
            )
            return cursor.rowcount

        if self._cursor_run(work) != 1:
            raise Failed("CHECKPOINT_NOT_BUILDING")


def connect_checkpoint(config, driver=None):
    """-> a connect() for CheckpointStore. `config` has host, name, user, password (from Bitwarden), `driver` is pymysql."""
    if driver is None:
        import pymysql as driver

    def connect():
        return driver.connect(
            host=config["host"],
            user=config["user"],
            password=config["password"],
            database=config["name"],
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=10,
            read_timeout=20,
            write_timeout=20,
        )

    return connect


# ---------------------------------------------------------------------------------------------- the run


def preflight(client, ns, desired_nodes, desired_edges, live):
    """Refuse (before any write) an unlisted namespace, foreign content in it, or a cap breach. -> the namespace as it is."""
    check_target(ns, desired_nodes, desired_edges)
    nodes, edges, foreign = client.read_namespace(ns)
    if foreign:
        raise Refused("NAMESPACE_OCCUPIED")
    if live and (nodes or edges) and (nodes != desired_nodes or edges != desired_edges):
        raise Refused("NAMESPACE_OCCUPIED")
    if len(set(nodes) | set(desired_nodes)) > NODE_CAP or len(set(edges) | set(desired_edges)) > REL_CAP:
        raise Refused("CAP_EXCEEDED")
    return nodes, edges


def apply(client, store, ns, nodes, edges, now, live=False):
    """Load `nodes` and `edges` into `ns`, then publish the checkpoint. Raises Refused before any write, Failed after the
    checkpoint opened (it then stays `building`). -> the result of this run."""
    desired_nodes, desired_edges = validate_all(nodes, edges)
    existing_nodes, existing_edges = preflight(client, ns, desired_nodes, desired_edges, live)
    generation = store.begin(ns, now)
    try:
        node_rows, edge_rows = plan(existing_nodes, existing_edges, desired_nodes, desired_edges)
        if node_rows or edge_rows:
            client.write(ns, node_rows, edge_rows)
        after_nodes, after_edges, foreign = client.read_namespace(ns)
        if foreign or membership_digest(after_nodes, after_edges) != membership_digest(desired_nodes, desired_edges):
            raise Failed("READBACK_MISMATCH")
        check, digest = checksum(after_nodes, after_edges), membership_digest(after_nodes, after_edges)
        store.publish(ns, generation, len(after_nodes), len(after_edges), check, digest, now)
    except (Failed, Refused):
        raise
    except Exception:
        raise Failed("APPLY_FAILED") from None
    return {
        "ns": ns,
        "generation": generation,
        "written_nodes": len(node_rows),
        "written_edges": len(edge_rows),
        **counts(after_nodes, after_edges),
        "checksum16": check[:16],
        "membership16": digest[:16],
    }


def serve(client, store, ns, now, fresh_hours=FRESH_HOURS):
    """What a reader may serve: the active records of `ns`, only while the newest checkpoint is published, fresh and equal to
    what is stored. Anything else raises Unserved with the reason (the graph is then not served)."""
    row = store.latest(ns)
    if row is None:
        raise Unserved("CHECKPOINT_MISSING")
    if row["status"] != "published":
        raise Unserved("CHECKPOINT_BUILDING")
    if now - row["published_at"] > timedelta(hours=fresh_hours):
        raise Unserved("CHECKPOINT_STALE")
    nodes, edges, foreign = client.read_namespace(ns)
    if (
        foreign
        or membership_digest(nodes, edges) != row["membership_digest"]
        or checksum(nodes, edges) != row["checksum"]
    ):
        raise Unserved("CHECKPOINT_MISMATCH")
    return (
        {k: v for k, v in nodes.items() if not v["suppressed"]},
        {
            k: v
            for k, v in edges.items()
            if not v["suppressed"] and not nodes[v["src"]]["suppressed"] and not nodes[v["dst"]]["suppressed"]
        },
    )


# ---------------------------------------------------------------------------------------------- the synthetic fixture


def fixture():
    """Fabricated records only (ids that do not exist in Jira or Notion): 3 requirements, 6 tickets, 3 pull requests."""
    nodes = [
        {"kind": "Requirement", "id": "REQ-9001", "status": "approved"},
        {"kind": "Requirement", "id": "REQ-9002", "status": "draft"},
        {"kind": "Requirement", "id": "REQ-9003", "status": "delivered"},
    ]
    nodes += [
        {"kind": "Ticket", "key": f"LIFE-90{i:02d}", "status": s}
        for i, s in enumerate(("Done", "In Progress", "Agent Go", "Blocked", "To Do", "Cancelled"), 1)
    ]
    nodes += [
        {"kind": "PullRequest", "repo": "example-owner/example-repo", "number": n, "state": s}
        for n, s in ((9101, "merged"), (9102, "open"), (9103, "closed"))
    ]

    def t(i):
        return f"Ticket:LIFE-90{i:02d}"

    def pr(n):
        return f"PullRequest:example-owner/example-repo#{n}"

    edges = [
        {"type": "BLOCKS", "src": t(2), "dst": t(1)},
        {"type": "BLOCKS", "src": t(4), "dst": t(2)},
        {"type": "CHILD_OF", "src": t(2), "dst": t(1)},
        {"type": "CHILD_OF", "src": t(3), "dst": t(1)},
        {"type": "CHILD_OF", "src": t(5), "dst": t(1)},
        {"type": "IMPLEMENTS", "src": t(1), "dst": "Requirement:REQ-9001"},
        {"type": "IMPLEMENTS", "src": t(3), "dst": "Requirement:REQ-9002"},
        {"type": "IMPLEMENTS", "src": t(6), "dst": "Requirement:REQ-9003"},
        {"type": "DELIVERED_BY", "src": t(1), "dst": pr(9101)},
        {"type": "DELIVERED_BY", "src": t(2), "dst": pr(9102)},
        {"type": "DELIVERED_BY", "src": t(6), "dst": pr(9103)},
    ]
    return nodes, edges
