"""LIFE-1002 (REQ-85 T1): the retrieval baseline runner. Local, stdlib only, fabricated corpus only.

It answers 20 fabricated questions (10 known, 5 absent, 5 stale or conflicting) and 3 dependency cases in four modes and reports how they differ: `direct` (canonical navigation: lexical search plus the references a record
carries, one hop), `graph` (the same search plus typed edges followed to any depth), `honcho` and `combined` (an injected client; T2 supplies the real one, here a fake is used in tests). It makes no network call, no write and no
metered call. Usage: python3 tools/um_baseline.py [--modes direct,graph] [--reps 5] [--corpus PATH] [--json OUT]."""
import argparse
import json
import math
import pathlib
import re
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "unified_memory" / "corpus.json"
MODES = ("direct", "graph", "honcho", "combined")
TOP_K = 5
STOP = frozenset("a an and are as at be by does for from how in is it of on or that the this to until was what when where which who whose why with".split())


def tokens(text):
    out = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in STOP:
            continue
        out.add(word[:-1] if len(word) > 3 and word.endswith("s") else word)
    return out


class World:
    """The corpus indexed once: sources by id, outgoing and incoming typed edges, token sets."""

    def __init__(self, doc):
        self.doc = doc
        self.src = {s["id"]: s for s in doc["sources"]}
        self.out, self.inc = {}, {}
        for e in doc["edges"]:
            self.out.setdefault(e["from"], []).append((e["type"], e["to"]))
            self.inc.setdefault(e["to"], []).append((e["type"], e["from"]))
        self.tok = {i: tokens(s["title"] + " " + s["text"]) for i, s in self.src.items()}
        self.title_tok = {i: tokens(s["title"]) for i, s in self.src.items()}

    def search(self, tenant, query, k=TOP_K, kinds=None, current_only=False):
        """-> (ids, lookups). A source answers when it covers at least half of the query's content words (and at least two)."""
        q = tokens(query)
        need = max(2, math.ceil(len(q) / 2))
        scored = []
        for i, s in self.src.items():
            if s["tenant"] != tenant or (kinds and s["kind"] not in kinds) or (current_only and s["status"] != "current"):
                continue
            hit = len(q & self.tok[i])
            if hit >= need:
                scored.append((-hit, -len(q & self.title_tok[i]), i))
        return [i for _, _, i in sorted(scored)][:k], 1

    def current(self, ids):
        """Replace each stale source by the current one that supersedes it (the canonical correction); order kept, no repeats."""
        out, lookups = [], 0
        for i in ids:
            s = self.src[i]
            if s["status"] == "stale" and s["superseded_by"] in self.src:
                i, lookups = s["superseded_by"], lookups + 1
            if i not in out:
                out.append(i)
        return out, lookups


def answer_direct(world, tenant, text):
    ids, n = world.search(tenant, text)
    ids, m = world.current(ids)
    return ids, n + m


answer_graph = answer_direct                                # lookups are the same search; the graph differs only on dependency traversal


def deps_direct(world, case):
    """One hop only: what a record names in `refs`, or what names it."""
    t, it = case["tenant"], case["intent"]
    kinds = lambda i, ks: world.src[i]["tenant"] == t and (not ks or world.src[i]["kind"] in ks)  # noqa: E731
    near = lambda i: {r for r in world.src[i]["refs"] if r in world.src} | {j for j, s in world.src.items() if i in s["refs"]}  # noqa: E731
    if it["op"] == "downstream":
        return sorted(j for j in near(it["start"]) if kinds(j, it.get("kinds"))), 1
    if it["op"] == "gaps":
        reqs = [i for i, s in world.src.items() if s["tenant"] == t and s["kind"] == it["kind"]]
        have = lambda r, need: any(world.src[j]["kind"] == need for j in near(r))  # noqa: E731
        miss = all if it.get("missing") == "all" else any
        return sorted(r for r in reqs if miss(not have(r, n) for n in it["needs"])), len(reqs)
    if it["op"] == "derived":
        found, n = world.search(t, it["decision_query"], kinds=["decision"], current_only=True)
        return sorted({j for d in found[:1] for j in near(d) if world.src[j]["kind"] == "task"}), n + 1
    raise ValueError("unknown_op")


def deps_graph(world, case):
    """Typed edges, any depth."""
    t, it = case["tenant"], case["intent"]
    if it["op"] == "downstream":
        seen, todo = set(), [it["start"]]
        while todo:
            for typ, to in world.out.get(todo.pop(), []):
                if typ in it["via"] and to not in seen:
                    seen.add(to)
                    todo.append(to)
        return sorted(i for i in seen if world.src[i]["tenant"] == t and world.src[i]["kind"] in it["kinds"]), len(seen) + 1
    if it["op"] == "gaps":
        reqs = [i for i, s in world.src.items() if s["tenant"] == t and s["kind"] == it["kind"]]

        def covered(req, need):
            for typ, frm in world.inc.get(req, []):
                if world.src[frm]["kind"] == need:
                    return True
                if typ == "implements" and any(world.src[g]["kind"] == need for _, g in world.inc.get(frm, [])):
                    return True
            return False
        miss = all if it.get("missing") == "all" else any
        return sorted(r for r in reqs if miss(not covered(r, n) for n in it["needs"])), len(reqs)
    if it["op"] == "derived":
        found, n = world.search(t, it["decision_query"], kinds=["decision"], current_only=True)
        return sorted({frm for d in found[:1] for typ, frm in world.inc.get(d, []) if typ in it["via"]}), n + 1
    raise ValueError("unknown_op")


def _ids_of(items):
    return [x["source_ref"] for x in items if x.get("source_ref")]


def honcho_answer(world, client, tenant, text):
    items = client.recall(text, tenant, TOP_K)
    return _ids_of(items), 1


def run_mode(world, mode, client=None):
    """One pass over every question and case -> {key: {"ids": [...], "abstain": bool, "calls": n, "ms": float}}."""
    out = {}
    for q in world.doc["questions"]:
        t0 = time.perf_counter()
        if mode in ("direct", "graph"):
            ids, calls = answer_direct(world, q["tenant"], q["text"])
        elif mode == "honcho":
            ids, calls = honcho_answer(world, client, q["tenant"], q["text"])
        else:
            g, c1 = answer_direct(world, q["tenant"], q["text"])
            h, c2 = honcho_answer(world, client, q["tenant"], q["text"])
            ids = g + [i for i in h if i in world.src and world.src[i]["tenant"] == q["tenant"] and i not in g]       # honcho items that are not canonical for this tenant are dropped
            calls = c1 + c2
        out[q["id"]] = {"ids": ids, "abstain": not ids, "calls": calls, "ms": (time.perf_counter() - t0) * 1000}
    for c in world.doc["dependency_cases"]:
        t0 = time.perf_counter()
        if mode == "direct":
            ids, calls = deps_direct(world, c)
        elif mode == "graph":
            ids, calls = deps_graph(world, c)
        elif mode == "honcho":
            ids, calls = honcho_answer(world, client, c["tenant"], c["text"])
        else:
            g, c1 = deps_graph(world, c)
            h, c2 = honcho_answer(world, client, c["tenant"], c["text"])
            ids, calls = sorted(set(g) | {i for i in h if i in world.src and world.src[i]["tenant"] == c["tenant"]}), c1 + c2
        out[c["id"]] = {"ids": list(ids), "abstain": not ids, "calls": calls, "ms": (time.perf_counter() - t0) * 1000}
    return out


def score(world, passes, client=None):
    """-> the nine metrics, the PASS rule and the per-item table for one mode, from its repeated passes."""
    first = passes[0]
    qs, cs = world.doc["questions"], world.doc["dependency_cases"]
    answerable = [q for q in qs if q["category"] != "absent"]
    prec, rec, cite, first_ok = [], [], 0, {}
    for q in answerable:
        ids = first[q["id"]]["ids"][:TOP_K]
        want = set(q["expect_ids"])
        prec.append(len(want & set(ids)) / len(ids) if ids else 0.0)
        rec.append(len(want & set(ids)) / len(want))
        first_ok[q["id"]] = bool(ids) and ids[0] == q["expect_first"]
        cite += first_ok[q["id"]]
    absent_ok = {q["id"]: first[q["id"]]["abstain"] for q in qs if q["category"] == "absent"}
    abstain_ok = sum(absent_ok.values()) + sum(not first[q["id"]]["abstain"] for q in answerable)
    leaks = sum(1 for q in qs for i in first[q["id"]]["ids"] if i in world.src and world.src[i]["tenant"] != q["tenant"])
    leaks += sum(1 for c in cs for i in first[c["id"]]["ids"] if i in world.src and world.src[i]["tenant"] != c["tenant"])
    fabricated = sum(1 for k in first for i in first[k]["ids"] if i not in world.src)
    dep_ok = {c["id"]: sorted(first[c["id"]]["ids"]) == sorted(c["expect_ids"]) for c in cs}
    ms = sorted(p[k]["ms"] for p in passes for k in p)
    pct = lambda x: ms[min(len(ms) - 1, math.ceil(x * len(ms)) - 1)]  # noqa: E731
    stale = [q for q in qs if q["category"] == "stale"]                                      # PASS wording: cite the canonical correction, never serve the stale record (rank of the correction is reported apart)
    stale_ok = all(set(q["expect_ids"]) <= set(first[q["id"]]["ids"][:TOP_K]) and not any(world.src[i]["status"] == "stale" for i in first[q["id"]]["ids"] if i in world.src) for q in stale)
    metrics = {"precision_at_5": round(statistics.mean(prec), 3), "recall": round(statistics.mean(rec), 3), "abstention_accuracy": round(abstain_ok / len(qs), 3),
               "canonical_citation_rate": round(cite / len(answerable), 3), "wrong_tenant_leaks": leaks, "fabricated_answers": fabricated,
               "p50_ms": round(pct(0.5), 4), "p95_ms": round(pct(0.95), 4), "tool_calls": sum(v["calls"] for v in first.values()), "cost_usd": round(float(getattr(client, "cost_usd", 0.0)), 6)}
    passing = leaks == 0 and fabricated == 0 and all(absent_ok.values()) and stale_ok and all(dep_ok.values())
    return {"metrics": metrics, "pass": passing, "identical_across_reps": all({k: v["ids"] for k, v in p.items()} == {k: v["ids"] for k, v in first.items()} for p in passes),
            "reps": len(passes), "absent_abstained": absent_ok, "stale_first_ok": {k: v for k, v in first_ok.items() if k in {q["id"] for q in qs if q["category"] == "stale"}},
            "dependency_ok": dep_ok, "known_first_ok": {k: v for k, v in first_ok.items() if k in {q["id"] for q in qs if q["category"] == "known"}}}


def run(corpus=None, modes=("direct", "graph"), reps=5, clients=None):
    world = World(corpus or json.loads(CORPUS.read_text()))
    report = {}
    for mode in modes:
        client = (clients or {}).get("honcho")
        if mode in ("honcho", "combined") and client is None:
            raise ValueError("honcho_client_missing")
        report[mode] = score(world, [run_mode(world, mode, client) for _ in range(reps)], client)
    return report


def table(report):
    cols = ("precision_at_5", "recall", "abstention_accuracy", "canonical_citation_rate", "wrong_tenant_leaks", "fabricated_answers", "p50_ms", "p95_ms", "tool_calls", "cost_usd")
    lines = ["mode      pass  " + "  ".join(c for c in cols) + "  deps"]
    for mode, r in report.items():
        lines.append(f"{mode:<9} {'PASS' if r['pass'] else 'FAIL'}  " + "  ".join(str(r["metrics"][c]) for c in cols) + f"  {sum(r['dependency_ok'].values())}/{len(r['dependency_ok'])}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="um_baseline", description="LIFE-1002 retrieval baseline (local, fabricated corpus only)")
    ap.add_argument("--modes", default="direct,graph")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    try:
        report = run(json.loads(pathlib.Path(a.corpus).read_text()), tuple(m for m in a.modes.split(",") if m), a.reps)
    except ValueError as error:
        print(str(error))
        return 2
    print(table(report))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
