"""Live synthetic validation of the corrected retrieval, on fabricated fixtures only. Standard library only; the two scoped tokens are held in memory by the caller and are never logged, printed or stored.

What it does, in phases (python3 tools/um_retrieval_live.py --phase seed|behavioral|structured|summary):
- `seed`: in the NEW synthetic workspace, create the workspace (reasoning, dream and summary off), its peers and one session, then at most 24 fabricated conclusions (facts first, then two tombstones that name the ids Honcho gave the facts). Free routes only: no message, no deriver, no dream.
- `behavioral`: five repetitions of every frozen query against that workspace, a raw read (what the old recall shows) beside the corrected read, per query and per repetition.
- `structured`: the retained workspace and the Aura fixtures: direct, graph, raw Honcho, the old combined union and the corrected combined mode on identical denominators.
- `summary`: the metrics, the floor sweep and the security counters, computed from the evidence file only.
Every HTTP call goes through an allowlist that refuses anything else before it leaves the process; the meter's spend, call count and audit rows are read around every repetition and the run stops on any change; each result is appended to the evidence file and flushed before the next call, so a failure cannot destroy earlier results."""

import argparse
import base64
import importlib.util
import json
import math
import os
import pathlib
import platform
import re
import statistics
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "unified_memory"


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rt = _load("um_retrieval")
client = _load("um_honcho_client")
um = rt.um
guard = client.guard

RELEASE = "RELEASED-LIFE-1064:um-t2-fixture-v1+um-t6-behavioral-v1"
HOST = "lifeos-edge-01"
HONCHO_URL = "http://127.0.0.1:8000"
METER_URL = "http://172.28.0.1:8081/health"
LEDGER = "/var/lib/lifeos-honcho-meter/ledger.json"
AUDIT = "/var/lib/lifeos-honcho-meter/audit.jsonl"
WS_STRUCTURED, WS_BEHAVIORAL = "um-t2-fixture-v1", "um-t6-behavioral-v1"
MAX_LIFE_S, MIN_LIFE_S = 2 * 3600 + 60, 300
MAX_ROWS = 24
ENV = "tests/fixtures/unified_memory"


class Denied(Exception):
    """A call outside the allowlist; it is raised before anything leaves the process."""


class Halt(Exception):
    """The run stops: the meter moved, a flag turned on or the evidence could not be kept."""


# -- the allowlist -----------------------------------------------------------------------------------------------------
class Allowlist:
    """Wraps client.Http. `read` mode lets only the free read routes of one workspace through; `seed` mode adds the creation of that workspace, its peers and session, and at most MAX_ROWS conclusions."""

    def __init__(self, http, ws, mode):
        self.http, self.ws, self.mode, self.rows, self.calls = http, ws, mode, 0, 0
        base = re.escape(f"/v3/workspaces/{ws}")
        self.reads = (
            ("POST", re.compile(base + r"/conclusions/query$")),
            ("POST", re.compile(base + r"/conclusions/list\?page=\d{1,3}&size=\d{1,3}$")),
            ("GET", re.compile(base + r"/queue/status$")),
            ("GET", re.compile(base + r"/sessions/s1/messages/[A-Za-z0-9_-]{1,64}$")),
        )
        self.setup = (
            ("POST", re.compile(base + r"/peers$")),
            ("POST", re.compile(base + r"/sessions$")),
            ("POST", re.compile(base + r"/conclusions$")),
        )

    def __repr__(self):
        return f"Allowlist({self.ws}, {self.mode})"

    def _allowed(self, method, path, body):
        for m, pattern in self.reads:
            if method == m and pattern.match(path):
                return True
        if method == "POST" and path == "/v3/workspaces" and isinstance(body, dict):
            if body.get("id") != self.ws:
                return False
            if set(body) == {"id"}:  # reading the flags of a workspace that exists
                return True
            conf = body.get("configuration") or {}
            off = all((conf.get(k) or {}).get("enabled") is False for k in ("reasoning", "dream", "summary"))
            return self.mode == "seed" and set(body) == {"id", "configuration"} and off
        if self.mode == "seed":
            for m, pattern in self.setup:
                if method == m and pattern.match(path):
                    return True
        return False

    def call(self, method, path, body=None):
        if not self._allowed(method, path, body):
            raise Denied(f"{method} {path.split('?')[0]}")
        if method == "POST" and path.endswith("/conclusions") and self.mode == "seed":
            rows = (body or {}).get("conclusions") or []
            if self.rows + len(rows) > MAX_ROWS:
                raise Denied("conclusion limit")
            self.rows += len(rows)
        self.calls += 1
        return self.http.call(method, path, body)


# -- gate, meter, evidence ---------------------------------------------------------------------------------------------
def claims(token):
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, AttributeError):
        return None


def _token_ok(token, ws, now):
    c = claims(token or "")
    if c is None:
        return "token_missing_or_not_jwt"
    if c.get("ad") or c.get("w") != ws or any(c.get(k) for k in ("p", "s", "sc")):
        return "token_not_workspace_scoped"
    if not (MIN_LIFE_S <= (c.get("exp") or 0) - now <= MAX_LIFE_S):
        return "token_lifetime"
    return ""


def gate(phase, env, host, now, url=HONCHO_URL):
    """-> (True, "open") or (False, a fixed reason word). Every check fails closed; each phase needs only its own token."""
    if env.get("UM_LIVE_RELEASE") != RELEASE:
        return False, "not_released"
    if host != HOST:
        return False, "wrong_host"
    if not url.startswith("http://127.0.0.1:") and not url.startswith("http://localhost:"):
        return False, "url_not_loopback"
    if env.get("HONCHO_ADMIN_JWT") or env.get("HONCHO_ADMIN_TOKEN"):
        return False, "admin_token_present"
    need = {"seed": ("UM_T6_TOKEN",), "behavioral": ("UM_T6_TOKEN",), "structured": ("UM_T2_TOKEN", "UM_T6_TOKEN")}
    ws = {"UM_T2_TOKEN": WS_STRUCTURED, "UM_T6_TOKEN": WS_BEHAVIORAL}
    for name in need.get(phase, ()):
        reason = _token_ok(env.get(name), ws[name], now)
        if reason:
            return False, reason
    if "UM_T2_TOKEN" in need.get(phase, ()) and env.get("UM_T2_TOKEN") == env.get("UM_T6_TOKEN"):
        return False, "tokens_not_distinct"
    return True, "open"


def meter_state(opener=urllib.request.urlopen, ledger=LEDGER, audit=AUDIT):
    """-> what the meter shows now: monthly spend, ledger call count and audit row count. Any unreadable part raises Halt: spend must be provable."""
    try:
        with opener(METER_URL, timeout=3) as answer:
            health = json.loads(answer.read())
        calls = json.loads(pathlib.Path(ledger).read_text()).get("calls")
        rows = sum(1 for _ in open(audit))
    except (OSError, ValueError) as error:
        raise Halt("meter_unreadable") from error
    return {"spent_usd": health.get("spent_usd"), "calls": calls, "audit_rows": rows, "scope": health.get("scope")}


class Evidence:
    """One JSON line per result, flushed and synced before the caller goes on."""

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, **row):
        row["t"] = round(time.time(), 3)
        line = json.dumps(row, sort_keys=True, default=str)
        try:
            with open(self.path, "a") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            raise Halt("evidence_unwritable") from error

    def rows(self):
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]


def assert_unchanged(before, after, evidence, where):
    if {k: before[k] for k in ("spent_usd", "calls", "audit_rows")} != {
        k: after[k] for k in ("spent_usd", "calls", "audit_rows")
    }:
        evidence.add(kind="halt", where=where, before=before, after=after)
        raise Halt("meter_moved")


# -- live readers ------------------------------------------------------------------------------------------------------
def parse_envelope(row):
    try:
        doc = json.loads(row.get("content") or "")
    except (ValueError, TypeError):
        return None
    return doc if isinstance(doc, dict) and doc.get("lifeos_memory") == 1 else None


class LiveRows:
    """Reads conclusions of the behavioral workspace: the ranked rows for a query and the tombstones."""

    def __init__(self, honcho):
        self.honcho = honcho
        self.calls = 0

    def ranked(self, tenant, query, top_k):
        self.calls += 1
        return self.honcho.conclusions(tenant, query, top_k)

    def tombstones(self):
        self.calls += 1
        dead, page = {}, 1
        while True:
            out = self.honcho.http.call("POST", self.honcho._p(f"/conclusions/list?page={page}&size=100"), {})
            for row in out.get("items", []):
                env = parse_envelope(row)
                if env and env.get("kind") == "tombstone" and isinstance(env.get("of"), str):
                    dead[env["of"]] = env.get("why") or "revoked"
            if page >= int(out.get("pages") or 1):
                return dead
            page += 1


def _item(row, dead=None):
    env = parse_envelope(row)
    if not env or env.get("kind") != "fact":
        return None
    item = {
        "source_ref": env.get("ref"),
        "text": env.get("text"),
        "revision": env.get("rev"),
        "live_id": row.get("id"),
        "refs": [r for r in (env.get("sources") or []) if isinstance(r, str)],
    }
    if dead is not None and row.get("id") in dead:
        item["live_state"] = dead[row["id"]]
    return item


class RawMemory:
    """What an unguarded consumer sees: the top ranked facts as stored, tombstones ignored."""

    def __init__(self, rows):
        self.rows = rows

    def recall(self, query, tenant, top_k):
        return [i for r in self.rows.ranked(tenant, query, top_k) if (i := _item(r))]


class GuardedMemory:
    """The corrected path's reader: the same ranked facts (a wider window, because revoked and stale ones take slots) with each item's live tombstone state attached."""

    def __init__(self, rows, window=3):
        self.rows, self.window = rows, window
        self.last = []

    def recall(self, query, tenant, top_k):
        dead = self.rows.tombstones()
        items = [i for r in self.rows.ranked(tenant, query, top_k * self.window) if (i := _item(r, dead))]
        self.last = items
        return items


# -- seeding -----------------------------------------------------------------------------------------------------------
def seed_rows(live, registry):
    """-> (fact rows, tombstone specs) from the frozen seed: each fact is an envelope carrying its registry reference, text, revision and provenance."""
    src = {s["id"]: s for s in registry["sources"]}
    facts = []
    for ref in live["seed"]["facts"]:
        rec = src[ref]
        env = {
            "lifeos_memory": 1,
            "kind": "fact",
            "ref": ref,
            "text": rec["text"],
            "rev": rec["revision"],
            "at": rec["at"],
            "subject": rec["subject"],
            "sources": rec["provenance"],
        }
        facts.append({"content": json.dumps(env, sort_keys=True), "tenant": rec["tenant"], "ref": ref})
    return facts, live["seed"]["tombstones"]


def seed(honcho, live, registry, evidence, meter=meter_state):
    """Creates the synthetic workspace and its conclusions through the allowlist. -> the id Honcho gave each fact reference."""
    before = meter()
    evidence.add(kind="seed_start", meter=before, workspace=honcho.ws)
    honcho.create_workspace()
    honcho.create_peers_and_session()
    flags = honcho.get_flags()
    if any(flags):
        raise Halt("flags_on")
    facts, tombs = seed_rows(live, registry)
    if len(facts) + len(tombs) > MAX_ROWS:
        raise Halt("seed_over_limit")
    honcho.create_conclusions([{"content": f["content"], "tenant": f["tenant"]} for f in facts])
    ids, page = {}, 1
    while True:
        out = honcho.http.call("POST", honcho._p(f"/conclusions/list?page={page}&size=100"), {})
        for row in out.get("items", []):
            env = parse_envelope(row)
            if env and env.get("kind") == "fact":
                ids[env["ref"]] = row["id"]
        if page >= int(out.get("pages") or 1):
            break
        page += 1
    if set(ids) != set(live["seed"]["facts"]):
        raise Halt("seed_ids_missing")
    tenant = {f["ref"]: f["tenant"] for f in facts}
    rows = []
    for t in tombs:
        env = {
            "lifeos_memory": 1,
            "kind": "tombstone",
            "of": ids[t["of"]],
            "of_ref": t["of"],
            "why": t["why"],
            "by": ids.get(t["by"]) if t["by"] else None,
        }
        rows.append({"content": json.dumps(env, sort_keys=True), "tenant": tenant[t["of"]]})
    honcho.create_conclusions(rows)
    after = meter()
    total = honcho.explicit_count()
    evidence.add(
        kind="seed_done",
        rows=total,
        facts=len(facts),
        tombstones=len(tombs),
        ids=ids,
        flags=list(honcho.get_flags()),
        meter=after,
    )
    assert_unchanged(before, after, evidence, "seed")
    return ids


# -- measurement -------------------------------------------------------------------------------------------------------
def _ms(start):
    return (time.perf_counter() - start) * 1000


def _refs(items):
    return [i.get("source_ref") for i in items if isinstance(i, dict) and i.get("source_ref")]


def behavioral(honcho, live, registry, evidence, reps=5, meter=meter_state, floor=rt.FLOOR):
    """Raw read beside the corrected read for every frozen query, `reps` times; one evidence line per query and repetition."""
    world = rt.build_world(registry)
    rows = LiveRows(honcho)
    guarded = GuardedMemory(rows)
    retriever = rt.Retriever(world, guarded, None, registry["grants"], registry["messages"], floor=floor)
    raw = RawMemory(rows)
    for rep in range(1, reps + 1):
        before = meter()
        for q in live["queries"]:
            t0 = time.perf_counter()
            raw_items = raw.recall(q["query"], q["tenant"], rt.TOP_K)
            raw_ms = _ms(t0)
            t1 = time.perf_counter()
            res = retriever.answer(q["tenant"], q["query"])
            cor_ms = _ms(t1)
            evidence.add(
                kind="behavioral",
                rep=rep,
                qid=q["id"],
                split=q["split"],
                tenant=q["tenant"],
                raw={"refs": _refs(raw_items), "live_ids": [i.get("live_id") for i in raw_items], "ms": raw_ms},
                corrected={
                    "answers": [a["ref"] for a in res["answers"]],
                    "sources": [a["sources"] for a in res["answers"]],
                    "authority": [a["authority"] for a in res["answers"]],
                    "abstain": res["abstain"],
                    "degraded": res["degraded"],
                    "intent": res["intent"],
                    "stats": dict(res["stats"]),
                    "trace": res["trace"],
                    "ms": cor_ms,
                },
                candidates=guarded.last if res["intent"] == "behavioral" else [],
            )
        after = meter()
        evidence.add(kind="rep_meter", phase="behavioral", rep=rep, before=before, after=after)
        assert_unchanged(before, after, evidence, f"behavioral rep {rep}")


STRUCT_ARMS = ("direct", "graph", "honcho_raw", "combined_old", "combined_v2")


def structured(honcho, graph, structured_fix, corpus, evidence, reps=5, meter=meter_state):
    """The retained workspace and the graph: every arm on the same questions, `reps` times."""
    questions = {q["id"]: q for q in corpus["questions"]}
    paraphrases = {p["id"]: p for p in structured_fix["paraphrases"]}
    everything = [*corpus["questions"], *structured_fix["paraphrases"]]
    world = um.World(corpus)
    memory = honcho
    retriever = rt.Retriever(world, memory, graph, rt.grants_for(world))
    for rep in range(1, reps + 1):
        before = meter()
        for q in everything:
            tenant, text = q["tenant"], q["text"]
            arm_out = {}
            t0 = time.perf_counter()
            arm_out["direct"] = (um.answer_direct(world, tenant, text)[0], _ms(t0))
            t0 = time.perf_counter()
            g_ids, _ = graph.answer(tenant, text)
            arm_out["graph"] = (list(g_ids), _ms(t0))
            t0 = time.perf_counter()
            h_items = memory.recall(text, tenant, rt.TOP_K)
            h_ids = _refs(h_items)
            arm_out["honcho_raw"] = (h_ids, _ms(t0))
            old = list(g_ids) + [
                i for i in h_ids if i in world.src and world.src[i]["tenant"] == tenant and i not in g_ids
            ]
            arm_out["combined_old"] = (old, arm_out["graph"][1] + arm_out["honcho_raw"][1])
            t0 = time.perf_counter()
            res = retriever.answer(tenant, text)
            arm_out["combined_v2"] = ([a["ref"] for a in res["answers"]], _ms(t0))
            evidence.add(
                kind="structured",
                rep=rep,
                qid=q["id"],
                paraphrase=q["id"] in paraphrases,
                arms={a: {"ids": ids, "ms": ms} for a, (ids, ms) in arm_out.items()},
                v2={
                    "degraded": res["degraded"],
                    "intent": res["intent"],
                    "trace": res["trace"],
                    "stats": dict(res["stats"]),
                },
            )
        for c in corpus["dependency_cases"]:
            out = {}
            t0 = time.perf_counter()
            out["direct"] = (um.deps_direct(world, c)[0], _ms(t0))
            t0 = time.perf_counter()
            out["graph"] = (list(graph.deps(c)[0]), _ms(t0))
            t0 = time.perf_counter()
            h = _refs(memory.recall(c["text"], c["tenant"], rt.TOP_K))
            out["honcho_raw"] = (h, _ms(t0))
            out["combined_old"] = (
                sorted(
                    set(out["graph"][0]) | {i for i in h if i in world.src and world.src[i]["tenant"] == c["tenant"]}
                ),
                0.0,
            )
            t0 = time.perf_counter()
            res = retriever.answer_case(c)
            out["combined_v2"] = ([a["ref"] for a in res["answers"]], _ms(t0))
            evidence.add(
                kind="structured",
                rep=rep,
                qid=c["id"],
                paraphrase=False,
                arms={a: {"ids": ids, "ms": ms} for a, (ids, ms) in out.items()},
                v2={
                    "degraded": res["degraded"],
                    "intent": res["intent"],
                    "trace": res["trace"],
                    "stats": dict(res["stats"]),
                },
            )
        after = meter()
        evidence.add(kind="rep_meter", phase="structured", rep=rep, before=before, after=after)
        assert_unchanged(before, after, evidence, f"structured rep {rep}")


def _pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, math.ceil(p * len(values)) - 1)] if values else 0.0


def structured_summary(rows, corpus, fix):
    """-> per arm, per denominator metrics via the unchanged scorer. Denominators: eligible originals, paraphrases, full originals, dependency cases."""
    rows = [r for r in rows if r.get("kind") == "structured"]
    world = um.World(corpus)
    elig = set(fix["eligible_question_ids"])
    para = {p["id"]: dict(p, expect_first=(p["expect_ids"] or [None])[0]) for p in fix["paraphrases"]}
    orig = {q["id"]: q for q in corpus["questions"]}
    sets = {
        "eligible17": [orig[i] for i in sorted(elig)],
        "paraphrase10": [para[i] for i in sorted(para)],
        "full20": [orig[i] for i in sorted(orig)],
    }
    out = {}
    reps = sorted({r["rep"] for r in rows})
    for arm in STRUCT_ARMS:
        for name, qs in sets.items():
            if name == "full20" and arm == "honcho_raw":
                continue  # the raw arm cannot answer sources the workspace never held: only the eligible denominator is fair
            doc = {**corpus, "questions": qs, "dependency_cases": corpus["dependency_cases"]}
            w = um.World(doc)
            passes = []
            for rep in reps:
                by = {r["qid"]: r for r in rows if r["rep"] == rep}
                p = {}
                for q in qs:
                    cell = by[q["id"]]["arms"][arm]
                    p[q["id"]] = {"ids": cell["ids"], "abstain": not cell["ids"], "calls": 1, "ms": cell["ms"]}
                for c in corpus["dependency_cases"]:
                    cell = by[c["id"]]["arms"][arm]
                    p[c["id"]] = {"ids": cell["ids"], "abstain": not cell["ids"], "calls": 1, "ms": cell["ms"]}
                passes.append(p)
            scored = um.score(w, passes)
            out[f"{arm}|{name}"] = {
                "metrics": scored["metrics"],
                "pass": scored["pass"],
                "deps": sum(scored["dependency_ok"].values()),
                "reps": scored["reps"],
            }
    return out


def behavioral_summary(rows, live, registry, floors=None):
    """-> raw and corrected metrics (all, dev, heldout), the per-kind table, safety counters and the floor sweep, from the evidence lines only."""
    rows = [r for r in rows if r.get("kind") == "behavioral"]
    if not rows:
        return {}
    queries = {q["id"]: q for q in live["queries"]}
    src = {s["id"]: s for s in registry["sources"]}
    first = min(r["rep"] for r in rows)

    def judge(sel):
        raw_p, raw_hit, raw_abs, raw_abs_n, cor_exact, cor_abs, cor_abs_n = [], 0, 0, 0, 0, 0, 0
        answerable = 0
        wrong_admitted = lost = leaks = fabricated = revoked = unsourced = raw_bad = 0
        for r in sel:
            q = queries[r["qid"]]
            want = [a["ref"] for a in q["expect"]["answers"]]
            ok_refs = set(want) | set(q.get("equiv") or [])
            top = r["raw"]["refs"][: rt.TOP_K]
            if want:
                answerable += 1
                raw_p.append(len(ok_refs & set(top)) / len(top) if top else 0.0)
                raw_hit += bool(ok_refs & set(top))
            else:
                raw_abs_n += 1
                raw_abs += not top
            raw_bad += sum(
                1
                for ref in top
                if ref not in src or src[ref]["status"] in ("revoked", "stale") or src[ref]["tenant"] != q["tenant"]
            )
            c = r["corrected"]
            if want:
                cor_exact += c["answers"] == want and c["sources"] == [a["sources"] for a in q["expect"]["answers"]]
                lost += not set(want) <= set(c["answers"])
                wrong_admitted += len(set(c["answers"]) - set(want))
            else:
                cor_abs_n += 1
                cor_abs += c["abstain"]
                wrong_admitted += len(c["answers"])
            for ref in c["answers"]:
                leaks += ref in src and src[ref]["tenant"] != q["tenant"]
                fabricated += ref not in src
                revoked += ref in src and src[ref]["status"] == "revoked"
            unsourced += sum(1 for s in c["sources"] if not s)
        n = len(sel)
        return {
            "queries": n,
            "answerable": answerable,
            "absent": cor_abs_n,
            "raw_precision_at_5": round(statistics.mean(raw_p), 3) if raw_p else None,
            "raw_recall_at_5": round(raw_hit / answerable, 3) if answerable else None,
            "raw_abstention": round(raw_abs / raw_abs_n, 3) if raw_abs_n else None,
            "raw_unsafe_outputs": raw_bad,
            "corrected_exact": round(cor_exact / answerable, 3) if answerable else None,
            "corrected_abstention": round(cor_abs / cor_abs_n, 3) if cor_abs_n else None,
            "corrected_lost_answers": lost,
            "corrected_wrong_admitted": wrong_admitted,
            "leaks": leaks,
            "fabricated": fabricated,
            "revoked_outputs": revoked,
            "answers_without_sources": unsourced,
            "raw_p50_ms": round(_pct([r["raw"]["ms"] for r in sel], 0.5), 2),
            "raw_p95_ms": round(_pct([r["raw"]["ms"] for r in sel], 0.95), 2),
            "corrected_p50_ms": round(_pct([r["corrected"]["ms"] for r in sel], 0.5), 2),
            "corrected_p95_ms": round(_pct([r["corrected"]["ms"] for r in sel], 0.95), 2),
        }

    one = [r for r in rows if r["rep"] == first]
    out = {
        "reps": len({r["rep"] for r in rows}),
        "all": judge(rows),
        "dev": judge([r for r in rows if r["split"] == "dev"]),
        "heldout": judge([r for r in rows if r["split"] == "heldout"]),
        "by_kind": {
            k: judge([r for r in rows if queries[r["qid"]]["kind"] == k])
            for k in sorted({q["kind"] for q in live["queries"]})
        },
        "stable_across_reps": all(
            {(r["qid"]): r["corrected"]["answers"] for r in rows if r["rep"] == rep}
            == {(r["qid"]): r["corrected"]["answers"] for r in one}
            for rep in {r["rep"] for r in rows}
        ),
    }
    out["floor_sweep"] = floor_sweep(one, live, registry, floors or live["floors"])
    return out


def floor_sweep(rows, live, registry, floors):
    """Replays the recorded candidates of one repetition through the corrected pipeline at each floor: no new live call. -> {floor: {split: {correct, lost, wrong_admitted}}}"""
    queries = {q["id"]: q for q in live["queries"]}
    world = rt.build_world(registry)
    out = {}
    for floor in floors:
        table = {"dev": [0, 0, 0, 0], "heldout": [0, 0, 0, 0]}
        for r in rows:
            q = queries[r["qid"]]
            if r["corrected"]["intent"] != "behavioral":
                items = []
            else:
                items = r["candidates"]
            retriever = rt.Retriever(
                world,
                rt.CaseClient({"mode": "returns", "items": items}),
                None,
                registry["grants"],
                registry["messages"],
                floor=floor,
            )
            res = retriever.answer(q["tenant"], q["query"])
            got = [a["ref"] for a in res["answers"]]
            want = [a["ref"] for a in q["expect"]["answers"]]
            t = table[q["split"]]
            t[0] += got == want
            t[1] += len(set(want) - set(got))
            t[2] += len(set(got) - set(want))
            t[3] += 1
        out[str(floor)] = {
            s: {"exact": v[0], "of": v[3], "lost_answers": v[1], "wrong_admitted": v[2]} for s, v in table.items()
        }
    return out


def summary(evidence, root=ROOT):
    live = json.loads((FIX / "behavioral_live.json").read_text())
    registry = json.loads((FIX / "behavioral.json").read_text())
    corpus = json.loads(um.CORPUS.read_text())
    fix = json.loads((FIX / "structured_live.json").read_text())
    rows = evidence.rows()
    meters = [r for r in rows if r["kind"] == "rep_meter"]
    return {
        "behavioral": behavioral_summary(rows, live, registry),
        "structured": structured_summary(rows, corpus, fix) if any(r["kind"] == "structured" for r in rows) else {},
        "meter_unchanged_every_rep": all(
            {k: m["before"][k] for k in ("spent_usd", "calls", "audit_rows")}
            == {k: m["after"][k] for k in ("spent_usd", "calls", "audit_rows")}
            for m in meters
        ),
        "meter_checks": len(meters),
        "halts": [r for r in rows if r["kind"] == "halt"],
        "evidence_lines": len(rows),
    }


# -- command line ------------------------------------------------------------------------------------------------------
def build_honcho(ws, token, mode):
    http = Allowlist(client.Http(HONCHO_URL, token), ws, mode)
    return client.Honcho(http, ws), http


def main(argv=None, env=None, host=None, now=None):
    ap = argparse.ArgumentParser(prog="um_retrieval_live")
    ap.add_argument("--phase", choices=("seed", "behavioral", "structured", "summary"), required=True)
    ap.add_argument("--evidence", default="/var/tmp/um-t6/evidence.jsonl")
    ap.add_argument("--reps", type=int, default=5)
    args = ap.parse_args(argv)
    env = os.environ if env is None else env
    if args.phase == "summary":
        print(json.dumps(summary(Evidence(args.evidence)), indent=1, sort_keys=True, default=str))
        return 0
    ok, reason = gate(args.phase, env, platform.node() if host is None else host, time.time() if now is None else now)
    if not ok:
        print(reason)
        return 2
    evidence = Evidence(args.evidence)
    live = json.loads((FIX / "behavioral_live.json").read_text())
    registry = json.loads((FIX / "behavioral.json").read_text())
    try:
        if args.phase == "seed":
            honcho, _ = build_honcho(WS_BEHAVIORAL, env["UM_T6_TOKEN"], "seed")
            seed(honcho, live, registry, evidence)
        elif args.phase == "behavioral":
            honcho, _ = build_honcho(WS_BEHAVIORAL, env["UM_T6_TOKEN"], "read")
            behavioral(honcho, live, registry, evidence, args.reps)
        else:
            aura = _load("um_aura_pilot")
            secrets = aura.load_secrets()
            driver = aura.connect(secrets)
            try:
                scoped = aura.Scoped(driver, secrets["uri"].split("://", 1)[1].split(".")[0])
                honcho, _ = build_honcho(WS_STRUCTURED, env["UM_T2_TOKEN"], "read")
                corpus = json.loads(um.CORPUS.read_text())
                fix = json.loads((FIX / "structured_live.json").read_text())
                structured(honcho, aura.AuraGraph(scoped), fix, corpus, evidence, args.reps)
            finally:
                getattr(driver, "close", lambda: None)()
    except (Halt, Denied, guard.HonchoError, guard.Unauthorized) as error:
        evidence.add(kind="halt", phase=args.phase, why=type(error).__name__ + ":" + str(error)[:80])
        print("halt", type(error).__name__)
        return 3
    print(json.dumps({"phase": args.phase, "evidence_lines": len(evidence.rows())}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
