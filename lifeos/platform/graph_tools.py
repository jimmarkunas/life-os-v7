"""Governed graph retrieval for the Oracle MCP: five fixed, read-only relationship questions over the derived graph index.

Ships OFF. A question is answered only when the switch file says enabled, the caller holds a live grant for the operation
(the gate decides, before and after this module runs), the input is a plain LIFE-n or REQ-n key, and the newest checkpoint
is published, fresh and equal to what is stored (`graph_index.serve`). There is no client Cypher: the module asks for the
active records and walks them in Python; anything else, an unreadable or stale checkpoint included, is a fixed refusal, and
the router then answers from the canonical source. Fixed codes only; a refusal never carries a value."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

from lifeos.platform import ct_stamp, graph_index

SWITCH = Path(__file__).with_name("graph_switch.json")
NS = graph_index.NS_FIXTURE  # the only namespace there is until real sources are loaded
TENANT = {"Personal": "personal"}
TICKET, REQUIREMENT = re.compile(r"^LIFE-[0-9]{1,6}$"), re.compile(r"^REQ-[0-9]{1,6}$")
_WORKSPACE = {"workspace": ("str", False)}
OPS = {
    "graph_dependency_chain": (
        "Every ticket that must finish before one ticket, with its depth (1 = directly blocks it), from the derived graph index; read only, with provenance.",
        {"ticket": ("str", True), **_WORKSPACE},
    ),
    "graph_blockers_of": (
        "The tickets that directly block one ticket, from the derived graph index; read only, with provenance.",
        {"ticket": ("str", True), **_WORKSPACE},
    ),
    "graph_tickets_for_requirement": (
        "The tickets that implement one requirement, each with its status and pull requests, from the derived graph index; read only, with provenance.",
        {"requirement": ("str", True), **_WORKSPACE},
    ),
    "graph_requirements_for_ticket": (
        "The requirements one ticket implements, and the requirements put at risk if it slips (those implemented by tickets it blocks); read only, with provenance.",
        {"ticket": ("str", True), **_WORKSPACE},
    ),
    "graph_delivered_by": (
        "The pull requests that deliver one ticket, with their state, from the derived graph index; read only, with provenance.",
        {"ticket": ("str", True), **_WORKSPACE},
    ),
}


class GraphError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def enabled(path=SWITCH):
    """True only when the switch file says `{"enabled": true}`; a missing or unreadable file is off."""
    try:
        return json.loads(path.read_text()).get("enabled") is True
    except Exception:  # noqa: BLE001
        return False


def route(op, args, tenant):
    """-> (tenant, action, account, service) for the gate: Personal only, read only."""
    return TENANT.get(args.get("workspace", "Personal"), "unknown"), "read", "graph", "graph"


def _start(op, args):
    key = args.get("requirement" if op == "graph_tickets_for_requirement" else "ticket")
    pattern = REQUIREMENT if op == "graph_tickets_for_requirement" else TICKET
    if not isinstance(key, str) or not pattern.match(key):
        raise GraphError("invalid_argument")
    return ("Requirement:" if pattern is REQUIREMENT else "Ticket:") + key


def _walk(edges, start, forward):
    """Breadth first over BLOCKS: forward follows what `start` blocks, backward what blocks `start`. -> [(node id, depth)]."""
    seen, order, level = {start}, [], [start]
    depth = 0
    while level:
        depth += 1
        nxt = []
        for here in level:
            for (kind, src, dst), _edge in sorted(edges.items()):
                if kind == "BLOCKS" and (src if forward else dst) == here:
                    there = dst if forward else src
                    if there not in seen:
                        seen.add(there)
                        nxt.append(there)
                        order.append((there, depth))
        level = nxt
    return order


def _key(node_id):
    return node_id.split(":", 1)[1]


def _targets(edges, ticket, kind):
    return sorted(dst for (k, src, dst) in edges if k == kind and src == ticket)


def answer(op, start, nodes, edges):
    """The data of one question over the served (active) records."""
    if start not in nodes:
        raise GraphError("not_found")
    if op == "graph_blockers_of":
        return {"blockers": [{"key": _key(n)} for n, depth in _walk(edges, start, False) if depth == 1]}
    if op == "graph_dependency_chain":
        return {"chain": [{"key": _key(n), "depth": d} for n, d in _walk(edges, start, False)]}
    if op == "graph_delivered_by":
        return {
            "pull_requests": [
                {"key": _key(p), "state": nodes[p]["state"]} for p in _targets(edges, start, "DELIVERED_BY")
            ]
        }
    if op == "graph_tickets_for_requirement":
        tickets = sorted(src for (k, src, dst) in edges if k == "IMPLEMENTS" and dst == start)
        return {
            "tickets": [
                {
                    "key": _key(t),
                    "status": nodes[t]["status"],
                    "pull_requests": [_key(p) for p in _targets(edges, t, "DELIVERED_BY")],
                }
                for t in tickets
            ]
        }
    direct = _targets(edges, start, "IMPLEMENTS")
    risk = {}
    for blocked, depth in _walk(edges, start, True):
        for requirement in _targets(edges, blocked, "IMPLEMENTS"):
            if requirement not in direct and requirement not in risk:
                risk[requirement] = {"key": _key(requirement), "through": _key(blocked), "depth": depth}
    return {
        "implements": [{"key": _key(r), "status": nodes[r]["status"]} for r in direct],
        "at_risk": sorted(risk.values(), key=lambda r: (r["depth"], r["key"])),
    }


def run(op, args, state):
    """-> the answer with its provenance, or raises GraphError(reason). Zero reads of the graph while the switch is off,
    the input is not a plain key, or the checkpoint cannot be served."""
    if op not in OPS:
        raise GraphError("unknown_operation")
    if not (state.get("switch") or enabled)():
        raise GraphError("operation_disabled")
    if args.get("workspace", "Personal") not in TENANT:
        raise GraphError("invalid_argument")
    start = _start(op, args)
    client, store = state.get("client"), state.get("store")
    if client is None or store is None:
        raise GraphError("source_unavailable")
    now = state["now"]() if state.get("now") else datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        nodes, edges = graph_index.serve(client, store, NS, now)
        row = store.latest(NS)
    except graph_index.Unserved as unserved:
        raise GraphError("stale_source" if str(unserved) == "CHECKPOINT_STALE" else "source_unavailable") from None
    except Exception:  # noqa: BLE001  an unreadable checkpoint or graph is never answered from
        raise GraphError("source_unavailable") from None
    data = answer(op, start, nodes, edges)
    return {
        **data,
        "provenance": {
            "source": "graph_index",
            "ns": NS,
            "key": start.split(":", 1)[1],
            "generation": row["generation"],
            "published_at": ct_stamp.stamp(row["published_at"].replace(tzinfo=timezone.utc)),
            "fetched_at": ct_stamp.stamp(now.replace(tzinfo=timezone.utc)),
        },
    }


def wire(environ, state):
    """Fill the state from the environment: the checkpoint store when all four CKPT_DB names are present. There is no
    Aura client here (no credential for it is placed), so with the switch on the graph still answers `source_unavailable`
    and callers fall back to the canonical source."""
    config = {
        "host": (environ.get("CKPT_DB_HOST") or "").strip(),
        "name": (environ.get("CKPT_DB_NAME") or "").strip(),
        "user": (environ.get("CKPT_DB_USER") or "").strip(),
        "password": (environ.get("CKPT_DB_PASSWORD") or "").strip(),
    }
    if all(config.values()):
        state["store"] = graph_index.CheckpointStore(_connect(config))


def _connect(config):
    def connect():
        vendor = str(Path(__file__).resolve().parents[2] / "vendor")
        if vendor not in sys.path and Path(vendor).is_dir():
            sys.path.append(vendor)
        return graph_index.connect_checkpoint(config)()

    return connect
