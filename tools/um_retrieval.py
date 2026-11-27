"""Authority-aware retrieval for the synthetic unified-memory pipeline. Pure, stdlib only, no network, no write, no spend.

The original runner (um_baseline.py) stays as it is, so old and new results compare. This module adds what it lacked:
- `route`: the intent of a query from its own words (canonical lookup, typed dependency, behavioral memory, unknown), never from a question id or an expected answer.
- `Retriever`: canonical records are the authority; typed graph dependencies are exact and memory never changes them; behavioral memory (Honcho) only supplements, after every item is validated and filtered.
- Validation at both boundaries: the tenant must be granted before any source is asked and each item is re-checked before it is used; an item is accepted only when its source ref is a known record of the same tenant that is not revoked, not stale (a stale record is replaced by the record that supersedes it), carries the claimed revision, clears a relevance floor and has provenance that resolves. A source ref written in the text is never proof.
- Truthful degradation: a source that is down, a malformed item or an unknown intent narrows the answer to the authorized canonical lookup, or to abstention; it never guesses.
Usage: python3 tools/um_retrieval.py [--reps 5] [--json OUT]. Fakes only: it makes no network call."""

import argparse
import datetime
import importlib.util
import json
import math
import pathlib
import socket
import statistics
import sys
import time
import urllib.request
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
BEHAVIORAL_FIXTURE = ROOT / "tests" / "fixtures" / "unified_memory" / "behavioral.json"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


um = sys.modules.get("um_baseline") or _load("um_baseline")
TOP_K = um.TOP_K
BEHAVIOR = frozenset(
    um.tokens(
        "prefer preference preferred like want usually typically habit style favorite rather choose chose chosen decide decided"
    )
)
DEPENDENCY = frozenset(
    um.tokens("risk blocked blocks depends dependency downstream upstream impact slips uncovered lacks missing")
)
NODE_NOUNS = frozenset(um.tokens("requirement task issue decision pull request uat acceptance"))
MEMORY, CANONICAL = "memory", "canonical"
MAX_HOPS = 5
FLOOR = (
    0.75  # share of a query's topical words a memory record must cover (the canonical lookup keeps the original half)
)
GUARDED = (KeyError, TypeError, ValueError, AttributeError, IndexError)  # a malformed record is dropped and counted


def route(query):
    """-> "unknown" | "dependency" | "behavioral" | "canonical", from the query's own words. A dependency term next to a record noun
    (requirement, task, issue...) is a dependency question even when a preference word is also present."""
    words = um.tokens(query or "")
    if len(words) < 2:
        return "unknown"
    if words & DEPENDENCY and words & NODE_NOUNS:
        return "dependency"
    if words & BEHAVIOR:
        return "behavioral"
    if words & DEPENDENCY:
        return "dependency"
    return "canonical"


def _norm(text):
    return " ".join(sorted(um.tokens(text)))


def build_world(doc):
    """um_baseline.World over a registry that may hold malformed records: a record whose title or text is not text is flagged and emptied, so building the index never raises and the retriever refuses the record."""
    clean = json.loads(json.dumps(doc))
    for rec in clean.get("sources", []):
        if not isinstance(rec.get("title"), str) or not isinstance(rec.get("text"), str):
            rec["title"], rec["text"], rec["malformed"] = "", "", True
    return um.World(clean)


def _at(rec):
    value = rec.get("at")
    return value if isinstance(value, str) else ""


def _subject(rec):
    value = rec.get("subject")
    return value if isinstance(value, str) and value else None


class OllamaEmbedder:
    """The host's local embedding model, the one Honcho itself uses. Loopback only: no spend, no other route, nothing stored. `kind` is "query" or "doc" so a model that wants prefixes can have them."""

    def __init__(
        self,
        url="http://127.0.0.1:11434/api/embeddings",
        model="nomic-embed-text",
        opener=urllib.request.urlopen,
        query_prefix="",
        doc_prefix="",
    ):
        if not url.startswith("http://127.0.0.1:") and not url.startswith("http://localhost:"):
            raise ValueError("embedder_not_loopback")
        self.url, self.model, self.opener = url, model, opener
        self.query_prefix, self.doc_prefix = query_prefix, doc_prefix
        self.calls = 0

    def embed(self, text, kind="doc"):
        self.calls += 1
        prompt = (self.query_prefix if kind == "query" else self.doc_prefix) + text
        request = urllib.request.Request(
            self.url,
            data=json.dumps({"model": self.model, "prompt": prompt}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.opener(request, timeout=30) as answer:
            return json.loads(answer.read())["embedding"]


class OllamaJudge:
    """A small local model asked one question per candidate: does this stored note state what the question asks for? Loopback only, no spend; the answer is YES or NO and anything else is NO."""

    def __init__(self, url="http://127.0.0.1:11434/api/generate", model="qwen3:0.6b", opener=urllib.request.urlopen):
        if not url.startswith("http://127.0.0.1:") and not url.startswith("http://localhost:"):
            raise ValueError("judge_not_loopback")
        self.url, self.model, self.opener, self.calls, self.cache = url, model, opener, 0, {}

    def answers(self, query, text):
        key = (query, text)
        if key not in self.cache:
            self.calls += 1
            prompt = (
                "You decide whether a stored note answers a question.\n"
                f"Question: {query}\nNote: {text}\n"
                "Does the note state the specific thing the question asks for? Answer with one word: YES or NO."
            )
            body = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 3},
            }
            request = urllib.request.Request(
                self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
            )
            with self.opener(request, timeout=60) as answer:
                reply = json.loads(answer.read()).get("response", "")
            self.cache[key] = reply.strip().upper().startswith("YES")
        return self.cache[key]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


LEGACY_GATE = {"kind": "lexical", "select": "all"}


def _epoch(value):
    """-> seconds since the epoch from a number or an ISO time (naive means UTC); ValueError when it is neither."""
    if isinstance(value, bool):
        raise ValueError("bad_time")
    if isinstance(value, (int, float)):
        return float(value)
    stamp = datetime.datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp.timestamp()


class Retriever:
    """world: um_baseline.World over the registry (records may carry authority, subject, at, provenance); client: .recall(query, tenant, top_k) or None;
    graph: .answer(tenant, text) and .deps(case) or None (the lexical graph then answers); grants: {tenant: [permissions]} or {tenant: {perms, expires, revoked}};
    no grants at all means nothing is granted; messages: [{id, tenant}] the provenance ids that exist; clock: seconds, for grant expiry; floor: share of topical words a memory record must cover."""

    def __init__(
        self,
        world,
        client=None,
        graph=None,
        grants=None,
        messages=None,
        clock=time.time,
        floor=FLOOR,
        embedder=None,
        gate=None,
        route_all=False,
        judge=None,
    ):
        self.world, self.client, self.graph, self.grants = world, client, graph, grants
        self.messages = {m["id"]: m["tenant"] for m in messages or []}
        self.tenants = {s["tenant"] for s in world.src.values()}
        self.client_calls = 0
        self.clock, self.floor = clock, floor
        self.embedder, self.gate, self.route_all = embedder, dict(gate or LEGACY_GATE), route_all
        self.judge, self._judged = judge, 0
        self._doc_vecs, self._qvec, self._sims = {}, None, {}
        self._trace = []
        self._clean = None

    # -- boundaries -------------------------------------------------------------------------------------------------
    def _grant(self, tenant):
        """-> "" when the tenant may be read now, else a fixed reason word. Missing, malformed, expired or revoked all deny."""
        if self.grants is None:
            return "grants_unavailable"
        try:
            grant = self.grants.get(tenant)
        except AttributeError:
            return "grants_unavailable"
        if grant is None:
            return "tenant_not_granted"
        perms, expires, revoked = (
            (grant, None, False)
            if isinstance(grant, (list, tuple))
            else (grant.get("perms"), grant.get("expires"), bool(grant.get("revoked")))
            if isinstance(grant, dict)
            else (None, None, False)
        )
        if not isinstance(perms, (list, tuple)):
            return "grant_malformed"
        if "read" not in perms:
            return "tenant_not_granted"
        if revoked:
            return "grant_revoked"
        if expires is not None:
            try:
                if self.clock() >= _epoch(expires):
                    return "grant_expired"
            except (TypeError, ValueError):
                return "grant_malformed"
        return ""

    def granted(self, tenant):
        return self._grant(tenant) == ""

    def _authority(self, rec):
        return rec.get("authority", CANONICAL)

    def _resolves(self, ref, tenant):
        if ref in self.messages:
            return self.messages[ref] == tenant
        return ref in self.world.src and self.world.src[ref]["tenant"] == tenant

    def _sources(self, rec, claimed, stats):
        """The validated refs of one record: its registered provenance (all must resolve, none may be the record itself) plus any claimed ref that resolves.
        None when its own provenance does not. A canonical record with no provenance is its own system of record; a memory record never is."""
        provenance = rec.get("provenance")
        if self._authority(rec) == CANONICAL and not provenance:
            own = [rec["id"]]
        elif isinstance(provenance, list) and provenance:
            own = list(provenance)
        else:
            return None
        if not all(isinstance(r, str) for r in own):
            return None
        if self._authority(rec) == MEMORY and rec["id"] in own:
            return None  # a record is not proof of itself
        if not all(self._resolves(r, rec["tenant"]) for r in own):
            return None
        out = list(own)
        for ref in claimed:
            if not isinstance(ref, str) or not self._resolves(ref, rec["tenant"]):
                stats["unvalidated_refs"] += 1
            elif ref not in out:
                out.append(ref)
        return out

    def _note(self, ref, decision):
        row = {"ref": ref if isinstance(ref, str) else None, "decision": decision}
        if self.gate["kind"] == "cosine" and row["ref"] in self._sims:
            row["sim"] = round(self._sims[row["ref"]], 4)
        self._trace.append(row)

    # -- canonical --------------------------------------------------------------------------------------------------
    def _canonical_ids(self, tenant, query, stats):
        """-> ids of current canonical records for this tenant. The graph answers first; if it is down the lexical lookup answers and says so."""
        ids = None
        if self.graph is not None:
            try:
                ids, _ = self.graph.answer(tenant, query)
            except Exception:  # noqa: BLE001  any failure of the graph source narrows, never guesses
                stats["degraded"] = "graph_unavailable"
        if ids is None:
            found, _ = self.world.search(tenant, query, len(self.world.src))
            found = [i for i in found if self._authority(self.world.src[i]) == CANONICAL][:TOP_K]
            ids, _ = self.world.current(found)
        ok = [
            i
            for i in ids
            if i in self.world.src
            and self.world.src[i]["tenant"] == tenant
            and self._authority(self.world.src[i]) == CANONICAL
            and self.world.src[i]["status"] == "current"
        ]
        return ok[:TOP_K]

    def _canonical_answer(self, rec_id, tenant, stats):
        try:
            rec = self.world.src[rec_id]
            sources = self._sources(rec, [], stats)
            if sources is None or rec.get("malformed") or not isinstance(rec["text"], str):
                return None
            return {"ref": rec_id, "text": rec["text"], "sources": sources, "authority": CANONICAL, "merged": []}
        except GUARDED:
            stats["malformed_record"] += 1
            return None

    # -- memory -----------------------------------------------------------------------------------------------------
    def _relevant(self, query, rec_id):
        """-> a positive relevance, or 0 when the record does not answer the query. The gate is lexical (the original floor), `none` (the source already filtered) or `cosine` (local embeddings)."""
        kind = self.gate["kind"]
        if kind == "none":
            return 1.0
        if kind == "judge":
            if self.gate.get("tau") is not None:  # an optional similarity pre-filter keeps the judge to plausible notes
                if self._qvec is None or self._qvec[0] != query:
                    self._qvec = (query, self.embedder.embed(query, "query"))
                if rec_id not in self._doc_vecs:
                    self._doc_vecs[rec_id] = self.embedder.embed(self.world.src[rec_id]["text"], "doc")
                if cosine(self._qvec[1], self._doc_vecs[rec_id]) < self.gate["tau"]:
                    return 0.0
            self._judged = getattr(self, "_judged", 0) + 1
            if self._judged > self.gate.get("max", 4):
                return 0.0
            return 1.0 if self.judge.answers(query, self.world.src[rec_id]["text"]) else 0.0
        if kind == "cosine":
            if self._qvec is None or self._qvec[0] != query:
                self._qvec = (query, self.embedder.embed(query, "query"))
            if rec_id not in self._doc_vecs:
                self._doc_vecs[rec_id] = self.embedder.embed(self.world.src[rec_id]["text"], "doc")
            sim = cosine(self._qvec[1], self._doc_vecs[rec_id])
            self._sims[rec_id] = sim
            return sim if sim >= self.gate["tau"] else 0.0
        rest = um.tokens(query) - BEHAVIOR
        need = max(2, math.ceil(self.floor * len(rest)))
        hit = len(rest & self.world.tok[rec_id])
        return hit if hit >= need else 0

    def _reject(self, stats, ref, reason):
        stats[reason] += 1
        self._note(ref, reason)
        return None

    def _admit(self, item, tenant, query, stats):
        """-> (record, relevance, claimed_refs) for one client item, or None with the reason counted and traced."""
        if not isinstance(item, dict):
            return self._reject(stats, None, "malformed")
        ref = item.get("source_ref")
        if not isinstance(ref, str) or not ref:
            return self._reject(stats, None, "malformed")
        if ref not in self.world.src:
            return self._reject(stats, ref, "fabricated")
        rec = self.world.src[ref]
        claimed_revision = item.get("revision")
        if claimed_revision is not None and claimed_revision != rec.get("revision"):
            return self._reject(stats, ref, "stale_revision")
        if item.get("live_state"):
            return self._reject(stats, ref, "tombstoned")  # the live source says this item was revoked or superseded
        hops = 0
        while rec["status"] == "stale" and rec.get("superseded_by") in self.world.src and hops < MAX_HOPS:
            nxt = self.world.src[rec["superseded_by"]]
            if nxt["tenant"] != rec["tenant"]:
                return self._reject(stats, ref, "cross_tenant_supersession")
            rec, hops = nxt, hops + 1
        if rec["tenant"] != tenant or self._authority(rec) != MEMORY:
            return self._reject(stats, ref, "foreign")
        if rec["status"] != "current":
            return self._reject(stats, ref, "revoked_or_stale")
        if rec.get("superseded_by"):
            return self._reject(
                stats, ref, "unverified_revision"
            )  # marked current yet superseded: the registry disagrees with itself
        if item.get("live_id") and item.get("text") != rec.get("text"):
            return self._reject(stats, ref, "text_mismatch")  # the live text is not what the registry holds
        if rec.get("malformed") or not isinstance(rec.get("text"), str):
            return self._reject(stats, ref, "malformed_record")
        try:
            relevance = self._relevant(query, rec["id"])
        except Exception:  # noqa: BLE001  a gate that cannot run admits nothing and says so
            stats["degraded"] = "embedder_unavailable"
            return self._reject(stats, ref, "embedder_unavailable")
        if not relevance:
            return self._reject(stats, ref, "irrelevant")
        claimed = item.get("refs")
        return rec, relevance, claimed if isinstance(claimed, list) else []

    def _memory_answers(self, tenant, query, stats):
        if self.client is None:
            return []
        self.client_calls += 1
        self._judged = 0
        try:
            items = self.client.recall(query, tenant, TOP_K)
        except Exception:  # noqa: BLE001  an outage is reported, not retried or guessed around
            stats["degraded"] = stats.get("degraded") or "memory_unavailable"
            return []
        if not isinstance(items, list):
            stats["malformed"] += 1
            return []
        admitted = []
        for position, item in enumerate(items):
            try:
                row = self._admit(item, tenant, query, stats)
                if row is None:
                    continue
                rec, relevance, claimed = row
                sources = self._sources(rec, claimed, stats)
                if sources is None:
                    self._reject(stats, rec["id"], "unsupported")
                    continue
                admitted.append((rec, relevance, sources, _at(rec), _subject(rec), position))
            except GUARDED:
                self._reject(stats, item.get("source_ref") if isinstance(item, dict) else None, "malformed_record")
        groups = {}
        for rec, relevance, sources, at, subject, position in sorted(admitted, key=lambda row: row[0]["id"]):
            key = (subject or rec["id"], _norm(rec["text"]))
            if (
                key in groups
            ):  # the same fact from another record: one answer under the lowest id, every validated source
                g = groups[key]
                if rec["id"] != g["ref"]:
                    g["merged"].append(rec["id"])
                g["sources"] += [s for s in sources if s not in g["sources"]]
                g["at"] = max(g["at"], at)
                g["rank"] = min(g["rank"], position)
                g["relevance"] = max(g["relevance"], relevance)
                continue
            groups[key] = {
                "ref": rec["id"],
                "text": rec["text"],
                "sources": list(sources),
                "authority": MEMORY,
                "merged": [],
                "at": at,
                "subject": subject,
                "relevance": relevance,
                "rank": position,
            }
        # records with the same text merge under the lowest id so the answer is stable
        out, by_subject = [], {}
        for g in groups.values():
            by_subject.setdefault(g["subject"] or g["ref"], []).append(g)
        for subject, rows in by_subject.items():
            rows.sort(key=lambda g: (g["at"], g["ref"]), reverse=True)
            if len(rows) > 1:
                stats["conflicts"] += len(rows) - 1  # the newest valid record of a subject wins
            out.append(rows[0])
        if self.gate.get("select", "all") == "all":
            out.sort(key=lambda g: (-g["relevance"], _neg(g["at"]), g["ref"]))
        else:  # the source's own ranking decides the order; one answer, or every answer close to the best
            out.sort(key=lambda g: (g["rank"], g["ref"]))
            if self.gate["select"] == "top1":
                out = out[:1]
            elif self.gate["select"] == "within" and out:
                best = max(g["relevance"] for g in out)
                out = [g for g in out if g["relevance"] >= best - self.gate["margin"]]
        for g in out:
            self._note(g["ref"], "admitted")
        return out

    # -- entry points -----------------------------------------------------------------------------------------------
    def _still_valid(self, answers, tenant):
        """The last check before output: the grant still stands and every ref is still a current record of this tenant."""
        if self._grant(tenant):
            return False
        return all(
            a["ref"] in self.world.src
            and self.world.src[a["ref"]]["tenant"] == tenant
            and self.world.src[a["ref"]]["status"] == "current"
            for a in answers
        )

    def answer(self, tenant, query):
        """-> {"intent", "answers": [...], "abstain", "degraded", "stats", "trace"}; abstains when nothing validated supports an answer."""
        stats = Counter()
        self._trace = []
        intent = route(query)
        if self.route_all and intent == "canonical":
            intent = "behavioral"  # memory is asked about every question that is not a typed dependency or unknown; canonical still wins
        result = {
            "intent": intent,
            "answers": [],
            "abstain": True,
            "degraded": None,
            "stats": stats,
            "trace": self._trace,
        }
        denied = self._grant(tenant)
        if denied:
            stats["degraded"] = denied
        elif intent != "unknown":
            canonical = [
                a for i in self._canonical_ids(tenant, query, stats) if (a := self._canonical_answer(i, tenant, stats))
            ]
            answers = list(canonical)
            if intent == "behavioral":
                taken = {_subject(self.world.src[a["ref"]]) for a in canonical} - {None}
                for m in self._memory_answers(tenant, query, stats):
                    if m["subject"] in taken:
                        stats["canonical_wins"] += 1  # memory never contradicts or displaces a canonical fact
                        self._note(m["ref"], "canonical_wins")
                    else:
                        answers.append(m)
            if not self._still_valid(answers, tenant):
                stats["degraded"] = self._grant(tenant) or "state_changed_before_output"
                answers = []
            result["answers"] = [
                {k: v for k, v in a.items() if k in ("ref", "text", "sources", "authority", "merged")} for a in answers
            ]
        result["abstain"] = not result["answers"]
        result["degraded"] = stats.pop("degraded", None) if isinstance(stats.get("degraded"), str) else None
        return result

    def _clean_world(self):
        """The registry with only valid lineage: nodes that are current and edges whose two ends are both kept and in one tenant. A path through anything else does not exist here."""
        if self._clean is None:
            doc = self.world.doc
            keep = {i for i, s in self.world.src.items() if s["status"] == "current"}
            edges = [
                e
                for e in doc["edges"]
                if e["from"] in keep
                and e["to"] in keep
                and self.world.src[e["from"]]["tenant"] == self.world.src[e["to"]]["tenant"]
            ]
            self._clean = um.World({**doc, "sources": [s for s in doc["sources"] if s["id"] in keep], "edges": edges})
        return self._clean

    def answer_case(self, case):
        """A typed dependency case. The graph's set must equal the set the registry's own valid edges give; any member that fails validation, any stale or foreign edge on a path, or any difference makes the whole answer an explicit abstention. Memory never joins it."""
        stats = Counter()
        tenant = case["tenant"]
        result = {"intent": "dependency", "answers": [], "abstain": True, "degraded": None, "stats": stats, "trace": []}
        denied = self._grant(tenant)
        if denied:
            result["degraded"] = denied
            return result
        try:
            ids, _ = self.graph.deps(case) if self.graph is not None else um.deps_graph(self.world, case)
        except Exception:  # noqa: BLE001  a dependency cannot be guessed from text: abstain
            result["degraded"] = "graph_unavailable"
            return result
        if not isinstance(ids, (list, tuple)) or not all(isinstance(i, str) for i in ids):
            result["degraded"] = "malformed_dependency_set"
            return result
        try:
            reference, _ = um.deps_graph(self._clean_world(), case)
        except GUARDED:
            result["degraded"] = "lineage_unverifiable"
            return result
        answers = [a for i in ids if i in self.world.src and (a := self._canonical_answer(i, tenant, stats))]
        valid = [
            a
            for a in answers
            if self.world.src[a["ref"]]["tenant"] == tenant and self.world.src[a["ref"]]["status"] == "current"
        ]
        if len(valid) != len(set(ids)):
            result["degraded"] = "incomplete_dependencies"  # a member failed validation: never shown as a complete set
            stats["dropped"] = len(set(ids)) - len(valid)
            return result
        if sorted(set(ids)) != sorted(reference):
            result["degraded"] = "lineage_mismatch"
            return result
        if self._grant(tenant):
            result["degraded"] = self._grant(tenant)
            return result
        result["answers"], result["abstain"] = valid, not valid
        return result


def _neg(text):
    """Sort key that puts the later date first inside an ascending tuple."""
    return tuple(-ord(c) for c in text)


def grants_for(world):
    """The synthetic harness states its grants explicitly: read for every tenant of this registry. A missing grants table denies; it is never the default."""
    return {t: ["read"] for t in sorted({s["tenant"] for s in world.src.values()})}


def run_mode_v2(world, retriever):
    """One pass over the original questions and dependency cases -> {key: {"ids", "abstain", "calls", "ms"}}, the shape um_baseline.score reads."""
    out = {}
    for q in world.doc["questions"]:
        t0, before = time.perf_counter(), retriever.client_calls
        res = retriever.answer(q["tenant"], q["text"])
        ids = [a["ref"] for a in res["answers"]]
        out[q["id"]] = {
            "ids": ids,
            "abstain": res["abstain"],
            "calls": 1 + retriever.client_calls - before,
            "ms": (time.perf_counter() - t0) * 1000,
        }
    for c in world.doc["dependency_cases"]:
        t0 = time.perf_counter()
        res = retriever.answer_case(c)
        ids = [a["ref"] for a in res["answers"]]
        out[c["id"]] = {"ids": ids, "abstain": res["abstain"], "calls": 1, "ms": (time.perf_counter() - t0) * 1000}
    return out


# -- synthetic fakes (no network) ---------------------------------------------------------------------------------------
class CleanHoncho:
    """A fake memory that returns only sources the lexical search finds: the best case for the old blind union."""

    cost_usd = 0.0

    def __init__(self, world):
        self.world = world

    def recall(self, query, tenant, top_k):
        ids, _ = self.world.search(tenant, query, top_k)
        return [{"source_ref": i, "text": self.world.src[i]["text"]} for i in ids]


class NoisyHoncho(CleanHoncho):
    """A fake that behaves like a vector store with no relevance floor: it always fills top_k, padding with the same tenant's closest remaining sources (stale ones included)."""

    def recall(self, query, tenant, top_k):
        items = super().recall(query, tenant, top_k)
        have = {x["source_ref"] for x in items}
        q = um.tokens(query)
        near = sorted(
            (-len(q & self.world.tok[i]), i)
            for i, s in self.world.src.items()
            if s["tenant"] == tenant and i not in have
        )
        items += [{"source_ref": i, "text": self.world.src[i]["text"]} for _, i in near[: max(0, top_k - len(items))]]
        return items


class CaseClient:
    """Returns exactly what a frozen case lists: its items, an outage, or nothing; counts its calls."""

    cost_usd = 0.0

    def __init__(self, spec):
        self.spec, self.calls = spec, 0

    def recall(self, query, tenant, top_k):
        self.calls += 1
        if self.spec["mode"] == "raises":
            raise ConnectionError("synthetic outage")
        return list(self.spec["items"])


class DownGraph:
    def answer(self, tenant, text):
        raise ConnectionError("synthetic outage")

    def deps(self, case):
        raise ConnectionError("synthetic outage")


# -- behavioral acceptance --------------------------------------------------------------------------------------------
def check_case(case, result, client_calls, world):
    """-> the list of failed expectations (empty = pass), plus the safety counts for the case."""
    want, fails = case["expect"], []
    got = [{"ref": a["ref"], "sources": a["sources"], "authority": a["authority"]} for a in result["answers"]]
    if got != want["answers"]:
        fails.append("answers")
    if want.get("abstain") != result["abstain"]:
        fails.append("abstain")
    for key in ("degraded", "intent"):
        if key in want and result[key] != want[key]:
            fails.append(key)
    if "client_calls" in want and client_calls != want["client_calls"]:
        fails.append("client_calls")
    for key in ("malformed", "unvalidated_refs"):
        if key in want and result["stats"].get(key, 0) != want[key]:
            fails.append(key)
    if "merged" in want and [m for a in result["answers"] for m in a["merged"]] != want["merged"]:
        fails.append("merged")
    refs = {a["ref"] for a in result["answers"]}
    if refs & set(want.get("forbidden", [])):
        fails.append("forbidden")
    safety = {
        "leaks": sum(1 for r in refs if r in world.src and world.src[r]["tenant"] != case["tenant"]),
        "fabricated": sum(1 for r in refs if r not in world.src),
        "revoked": sum(1 for r in refs if r in world.src and world.src[r]["status"] == "revoked"),
        "unsourced": sum(1 for a in result["answers"] if not a["sources"]),
    }
    return fails, safety


def evaluate_behavioral(doc):
    """Runs every frozen case through the real Retriever with its fake client. -> {"cases": {id: {...}}, "summary": {...}}."""
    world = um.World(doc)
    rows = {}
    for case in doc["cases"]:
        client = CaseClient(case["client"])
        graph = DownGraph() if case.get("graph_down") else None
        retriever = Retriever(world, client, graph, doc["grants"], doc["messages"])
        t0 = time.perf_counter()
        result = retriever.answer(case["tenant"], case["query"])
        ms = (time.perf_counter() - t0) * 1000
        fails, safety = check_case(case, result, client.calls, world)
        rows[case["id"]] = {
            "split": case["split"],
            "query": case["query"],
            "answers": [a["ref"] for a in result["answers"]],
            "sources": [a["sources"] for a in result["answers"]],
            "intent": result["intent"],
            "degraded": result["degraded"],
            "client_calls": client.calls,
            "stats": dict(result["stats"]),
            "fails": fails,
            "safety": safety,
            "ms": ms,
        }
    group = lambda prefix, split=None: [  # noqa: E731
        r for k, r in rows.items() if k.startswith(prefix) and (split is None or r["split"] == split)
    ]
    ok = lambda rs: bool(rs) and all(not r["fails"] for r in rs)  # noqa: E731
    total = lambda key: sum(r["safety"][key] for r in rows.values())  # noqa: E731
    summary = {
        "cases": len(rows),
        "passed": sum(not r["fails"] for r in rows.values()),
        "explicit_correct": ok(group("B")),
        "corrections_correct": ok(group("C")),
        "absent_abstained": ok(group("A")),
        "revoked_unauthorized_blocked": ok(group("R")),
        "multi_source_correct": ok(group("M")),
        "canonical_wins": ok(group("K")),
        "outages_and_malformed": ok(group("O")),
        "dev_pass": ok([r for r in rows.values() if r["split"] == "dev"]),
        "heldout_pass": ok([r for r in rows.values() if r["split"] == "heldout"]),
        "leaks": total("leaks"),
        "fabricated": total("fabricated"),
        "revoked_outputs": total("revoked"),
        "answers_without_sources": total("unsourced"),
        "provenance_complete": total("unsourced") == 0,
    }
    return {"cases": rows, "summary": summary}


# -- end-to-end synthetic report ----------------------------------------------------------------------------------------
class _NoNetwork:
    """Blocks every socket while the synthetic run is in progress; a connection attempt is a failure of the run."""

    def __enter__(self):
        self._saved = socket.socket

        def refuse(*args, **kwargs):
            raise RuntimeError("network_blocked")

        socket.socket = refuse
        return self

    def __exit__(self, *exc):
        socket.socket = self._saved


def _pass_ids(passes):
    return {k: v["ids"] for k, v in passes[0].items()}


def report(reps=5):
    doc = json.loads(um.CORPUS.read_text())
    world = um.World(doc)
    out = {"reps": reps, "network": "blocked", "cost_usd": 0.0, "modes": {}, "per_query": {}}
    with _NoNetwork():
        arms = {
            "direct": lambda: um.run_mode(world, "direct"),
            "graph": lambda: um.run_mode(world, "graph"),
            "honcho_noisy": lambda: um.run_mode(world, "honcho", NoisyHoncho(world)),
            "combined_old_noisy": lambda: um.run_mode(world, "combined", NoisyHoncho(world)),
            "combined_old_clean": lambda: um.run_mode(world, "combined", CleanHoncho(world)),
            "combined_v2_noisy": lambda: run_mode_v2(
                world, Retriever(world, NoisyHoncho(world), grants=grants_for(world))
            ),
            "combined_v2_clean": lambda: run_mode_v2(
                world, Retriever(world, CleanHoncho(world), grants=grants_for(world))
            ),
        }
        for name, fn in arms.items():
            passes = [fn() for _ in range(reps)]
            scored = um.score(world, passes)
            out["modes"][name] = scored
            out["per_query"][name] = _pass_ids(passes)
        behavioral = evaluate_behavioral(json.loads(BEHAVIORAL_FIXTURE.read_text()))
    out["behavioral"] = behavioral
    base = out["modes"]["graph"]["metrics"]
    out["delta_vs_graph"] = {
        n: {k: round(m["metrics"][k] - base[k], 4) for k in ("precision_at_5", "recall", "abstention_accuracy")}
        for n, m in out["modes"].items()
    }
    return out


def table(rep):
    cols = (
        "precision_at_5",
        "recall",
        "abstention_accuracy",
        "canonical_citation_rate",
        "wrong_tenant_leaks",
        "fabricated_answers",
        "p50_ms",
        "p95_ms",
        "tool_calls",
    )
    lines = ["mode".ljust(20) + "pass  deps  " + "  ".join(c for c in cols)]
    for name, m in rep["modes"].items():
        deps = sum(m["dependency_ok"].values())
        lines.append(
            name.ljust(20)
            + ("PASS  " if m["pass"] else "FAIL  ")
            + f"{deps}/{len(m['dependency_ok'])}   "
            + "  ".join(str(m["metrics"][c]) for c in cols)
        )
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="um_retrieval", description="synthetic retrieval acceptance (fakes only, no network)"
    )
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)
    rep = report(args.reps)
    print(table(rep))
    print(json.dumps({"behavioral": rep["behavioral"]["summary"], "delta_vs_graph": rep["delta_vs_graph"]}, indent=1))
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
