"""LIFE-868: governed shared memory on the retained Honcho (store, recall, correct, revoke), four operations of the Oracle LIFE OS MCP; memory is never a canonical fact.

Each operation is its own gate tool (memory_store, memory_recall, memory_correct, memory_revoke) granted per client and per workspace; the client in every record is the verified one (injected as `_client` by the MCP
dispatch after argument checking, never an argument). Items are Honcho conclusions (the retained service's own store; embeddings only, no model call, so no spend) holding one JSON envelope: text, source, client, workspace,
stable id (the conclusion id), CT time, and `supersedes` for a correction. Nothing is ever edited: a correction writes a new item and a `superseded` tombstone for the old one, a revoke writes a `revoked` tombstone,
and recall hides every tombstoned item at once (`include_history` shows them marked). Recall labels everything `memory`, says canonical Notion, Jira or GitHub facts win, and echoes a caller-named `canonical_ref`.
Honcho down or refusing is the fixed code memory_unavailable, never a guess. Counts, ids and fixed codes only in errors. Stdlib only."""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
WORKSPACES = {"Personal": "personal"}                      # Bytalos is a separate tenant and ungranted; TGP stays disabled (an unknown name is refused by the gate before this runs)
TENANT = {"Personal": "personal", "Bytalos": "bytalos"}             # the gate decides which is granted (only Personal is); TGP and any other name are refused before anything runs
WRITE = ("memory_store", "memory_correct", "memory_revoke")         # gate action write; recall is read
PEER = "lifeos-memory"
TEXT_MAX = 2000
NOTICE = "This is memory, not a canonical fact. If Notion, Jira or GitHub holds the same fact, the canonical source wins."
OPS = {"memory_store": ("Store one synthetic memory in a granted workspace with its source; returns its stable id and provenance. Memory is never a canonical fact.", {"workspace": ("str", True), "text": ("str", True), "source": ("str", True)}),
       "memory_recall": ("Recall memories from a granted workspace by meaning, labelled as memory with provenance; revoked and superseded items are hidden; canonical sources win.", {"workspace": ("str", True), "query": ("str", True), "top_k": ("int", False), "include_history": ("str", False), "canonical_ref": ("str", False)}),
       "memory_correct": ("Replace one stored memory by id with corrected text; the old one is kept and marked superseded.", {"workspace": ("str", True), "id": ("str", True), "text": ("str", True), "source": ("str", True)}),
       "memory_revoke": ("Revoke one stored memory by id: it is unavailable to recall at once; the record is kept.", {"workspace": ("str", True), "id": ("str", True)})}


class MemoryError_(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Honcho:
    """The few Honcho calls memory needs. `token` is a workspace-scoped Honcho JWT; `opener` is urlopen (tests pass a fake)."""
    def __init__(self, base, token, opener=urllib.request.urlopen):
        self.base, self.token, self.opener = base.rstrip("/"), token, opener

    def call(self, path, body=None, method="POST"):
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(), method=method, headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=20) as reply:
                raw = reply.read()
        except urllib.error.HTTPError as error:
            raise MemoryError_("memory_not_found" if error.code == 404 else "memory_unavailable") from None
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            raise MemoryError_("memory_unavailable") from None
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise MemoryError_("memory_unavailable") from None

    def ensure_peer(self, workspace):
        self.call(f"/v3/workspaces/{workspace}/peers", {"id": PEER})

    def create(self, workspace, envelope):
        out = self.call(f"/v3/workspaces/{workspace}/conclusions", {"conclusions": [{"content": json.dumps(envelope, sort_keys=True), "observer_id": PEER, "observed_id": PEER}]})
        rows = out if isinstance(out, list) else out.get("items") or out.get("conclusions") or []
        if not rows or not isinstance(rows[0], dict) or not rows[0].get("id"):
            raise MemoryError_("memory_unavailable")
        return rows[0]["id"]

    def query(self, workspace, text, top_k):
        out = self.call(f"/v3/workspaces/{workspace}/conclusions/query", {"query": text, "top_k": top_k, "filters": {"observer_id": PEER, "observed_id": PEER}})
        return out if isinstance(out, list) else out.get("items") or []

    def listing(self, workspace):
        rows, page = [], 1
        while page <= 20:
            out = self.call(f"/v3/workspaces/{workspace}/conclusions/list?page={page}&size=100", {"filters": {"observer_id": PEER}})
            items = out.get("items") or []
            rows += items
            if page >= int(out.get("pages") or 1) or not items:
                return rows
            page += 1
        return rows

    def get(self, workspace, cid):
        return self.call(f"/v3/workspaces/{workspace}/conclusions/{cid}", None, "GET")


def _now(state):
    return (state["now"]() if state.get("now") else datetime.now(CENTRAL)).astimezone(CENTRAL).isoformat(timespec="seconds")


def _envelope(row):
    try:
        doc = json.loads(row.get("content") or "")
    except (ValueError, TypeError):
        return None
    return doc if isinstance(doc, dict) and doc.get("lifeos_memory") == 1 else None


def _workspace(args):
    ws = WORKSPACES.get(args.get("workspace"))
    if ws is None:
        raise MemoryError_("unknown_workspace")
    return ws


def _text(args, key="text"):
    text = args.get(key)
    if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
        raise MemoryError_("invalid_argument")
    return text.strip()


def _item(row, env, dead, include_history):
    status = dead.get(row["id"], {}).get("why", "active")
    if status != "active" and not include_history:
        return None
    return {"id": row["id"], "label": "memory", "text": env["text"], "status": status, "source": env.get("source"), "client": env.get("client"), "workspace": env.get("workspace"), "stored_at_ct": env.get("ct"),
            "supersedes": env.get("supersedes"), "superseded_by": dead.get(row["id"], {}).get("by")}


def _tombstones(honcho, ws):
    dead = {}
    for row in honcho.listing(ws):
        env = _envelope(row)
        if env and env.get("kind") == "tombstone" and isinstance(env.get("of"), str):
            dead[env["of"]] = {"why": env.get("why"), "by": env.get("by")}
    return dead


def run(op, args, state):
    honcho = state.get("client")
    if honcho is None:
        raise MemoryError_("memory_not_configured")
    ws, client, ct = _workspace(args), str(args.get("_client") or "unknown"), _now(state)
    if op == "memory_store":
        text, source = _text(args), _text(args, "source")
        honcho.ensure_peer(ws)
        cid = honcho.create(ws, {"lifeos_memory": 1, "kind": "fact", "text": text, "source": source, "client": client, "workspace": args["workspace"], "ct": ct})
        return {"id": cid, "label": "memory", "text": text, "source": source, "client": client, "workspace": args["workspace"], "stored_at_ct": ct, "notice": NOTICE}
    if op == "memory_recall":
        top_k = args.get("top_k") if isinstance(args.get("top_k"), int) and 1 <= args["top_k"] <= 20 else 5
        history = args.get("include_history") == "true"
        dead = _tombstones(honcho, ws)
        items = []
        for row in honcho.query(ws, _text(args, "query"), top_k * 3):
            env = _envelope(row)
            shown = _item(row, env, dead, history) if env and env.get("kind") == "fact" else None
            if shown:
                items.append(shown)
        out = {"items": items[:top_k], "count": len(items[:top_k]), "label": "memory", "notice": NOTICE}
        if isinstance(args.get("canonical_ref"), str) and args["canonical_ref"]:
            out["canonical"] = {"ref": args["canonical_ref"], "rule": "canonical wins: if this canonical source says otherwise, ignore the memory"}
        return out
    old = args.get("id")
    if not isinstance(old, str) or not old:
        raise MemoryError_("invalid_argument")
    row = honcho.get(ws, old)
    env = _envelope(row)
    if not env or env.get("kind") != "fact":
        raise MemoryError_("memory_not_found")
    if old in _tombstones(honcho, ws):
        raise MemoryError_("memory_not_found")                                       # already revoked or superseded: nothing to change
    if op == "memory_revoke":
        honcho.create(ws, {"lifeos_memory": 1, "kind": "tombstone", "of": old, "why": "revoked", "client": client, "ct": ct})
        return {"id": old, "status": "revoked", "revoked_at_ct": ct, "label": "memory"}
    text, source = _text(args), _text(args, "source")
    new = honcho.create(ws, {"lifeos_memory": 1, "kind": "fact", "text": text, "source": source, "client": client, "workspace": args["workspace"], "ct": ct, "supersedes": old})
    honcho.create(ws, {"lifeos_memory": 1, "kind": "tombstone", "of": old, "why": "superseded", "by": new, "client": client, "ct": ct})
    return {"id": new, "supersedes": old, "label": "memory", "text": text, "source": source, "client": client, "stored_at_ct": ct, "notice": NOTICE}


def client_from_env(env):
    """-> a Honcho client from HONCHO_URL (default loopback) and HONCHO_MEMORY_TOKEN, or None when the token is not set (every memory operation then answers memory_not_configured)."""
    token = (env.get("HONCHO_MEMORY_TOKEN") or "").strip()
    return Honcho(env.get("HONCHO_URL") or "http://127.0.0.1:8000", token) if token else None


def register(read, lift, state):
    """Add the four operations to the MCP's read registry (`lift` turns a module operation into a registry row) and wire the Honcho client from the environment (None until HONCHO_MEMORY_TOKEN is set)."""
    read.update({op: (text, params, lift(sys.modules[__name__], op, state)) for op, (text, params) in OPS.items()})
    state["client"] = client_from_env(os.environ)
