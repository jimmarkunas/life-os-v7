"""Route one question to exactly one store: the graph index, Honcho memory or the canonical source.

A relationship question goes to the graph, a question about what Jim usually does or decided to Honcho, a plain fact or
anything unclear to the canonical source. Never a union of two stores.

`route` only classifies. `answer` runs the chosen provider behind an authorization check made before the provider is
called and again before its output is returned. A graph answer that cannot be served falls back to the canonical source
only when both the graph operation and the canonical source are authorized right now; a canonical answer that is
missing, incomplete or unauthorized is `DEGRADED` or a denial. A grant is never invented or bypassed. Fixed codes only."""

import re

GRAPH, HONCHO, CANONICAL = "graph", "honcho", "canonical"
_GRAPH = re.compile(
    r"\b(blocks?|blocked|blocking|blockers?|depends? on|dependenc(?:y|ies)|upstream|downstream|at risk|slips?|slipped"
    r"|comes? from|derived from|delivered by|missing an? (?:pull request|acceptance check))\b",
    re.IGNORECASE,
)
_BEHAVIOR = re.compile(
    r"\b(do i (?:usually|tend to|normally)|did i (?:say|decide|mention|prefer)|my (?:preference|habit|routine)"
    r"|i (?:usually|prefer|tend to)|last time i|what do i like|how do i usually|remind me what i said)\b",
    re.IGNORECASE,
)
FRESH = "fresh"


def route(question):
    """-> "graph", "honcho" or "canonical". A question that matches both kinds, or neither, is canonical."""
    text = question if isinstance(question, str) else ""
    graph, behavior = bool(_GRAPH.search(text)), bool(_BEHAVIOR.search(text))
    if graph and not behavior:
        return GRAPH
    if behavior and not graph:
        return HONCHO
    return CANONICAL


def _call(provider, question):
    try:
        return provider(question)
    except Exception:  # noqa: BLE001  any provider failure is "unavailable", never a guess
        return None


def _complete(result):
    return (
        isinstance(result, dict)
        and bool(result.get("content") or result.get("data"))
        and result.get("freshness") == FRESH
        and bool(result.get("revision") or result.get("provenance"))
    )


def _one(store, provider, question, authorize):
    """Authorize, call, authorize again. -> ("ok", result) | ("denied", None) | ("unavailable", None)."""
    if not authorize(store, "before"):
        return "denied", None
    result = _call(provider, question)
    if result is None:
        return "unavailable", None
    return ("ok", result) if authorize(store, "after") else ("denied", None)


def answer(question, providers, authorize):
    """-> {"route", "store", "ok": True, "result", ["fallback": True, "reason"]} or {"ok": False, "status": "DEGRADED" | "denied"}.

    `providers` maps "graph", "honcho" and "canonical" to functions of the question; a graph provider that cannot be
    served returns None. `authorize(store, phase)` is the gate (fresh each call)."""
    chosen = route(question)
    state, result = _one(chosen, providers[chosen], question, authorize)
    if state == "ok" and (chosen != CANONICAL or _complete(result)):
        return {"ok": True, "route": chosen, "store": chosen, "result": result}
    if state == "denied":
        return {"ok": False, "route": chosen, "status": "denied"}
    if chosen != GRAPH:
        return {"ok": False, "route": chosen, "status": "DEGRADED"}
    # the graph was authorized but could not be served: the canonical source answers, only if it is authorized and complete now
    if not authorize(CANONICAL, "before"):
        return {"ok": False, "route": chosen, "status": "denied"}
    fallback = _call(providers[CANONICAL], question)
    if not _complete(fallback):
        return {"ok": False, "route": chosen, "status": "DEGRADED"}
    if not authorize(CANONICAL, "after"):
        return {"ok": False, "route": chosen, "status": "denied"}
    return {
        "ok": True,
        "route": chosen,
        "store": CANONICAL,
        "result": fallback,
        "fallback": True,
        "reason": "graph_unavailable",
    }
