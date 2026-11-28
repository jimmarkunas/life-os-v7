"""One question goes to one store; a graph answer that cannot be served falls back to the canonical source only under the
fallback rules. Fabricated corpus and fake providers only."""

import json
from pathlib import Path
import unittest

from lifeos.platform import memory_router as r

ROOT = Path(__file__).resolve().parents[2]
CORPUS = json.loads((ROOT / "tests/fixtures/unified_memory/corpus.json").read_text())
BEHAVIOR = (
    "Do I usually answer mail before noon?",
    "Did I decide to keep the weekly review on Mondays?",
    "What do I like for the Friday summary?",
    "How do I usually name branches?",
    "Last time I picked the smaller card, remind me what I said.",
)
GOOD = {"content": "a fact", "freshness": "fresh", "revision": {"commit_sha": "abc"}}


class Routing(unittest.TestCase):
    def test_the_corpus_routes_dependency_to_graph_fact_to_canonical_and_behavior_to_honcho(self):
        self.assertEqual([r.route(c["text"]) for c in CORPUS["dependency_cases"]], ["graph"] * 3)
        self.assertEqual({r.route(q["text"]) for q in CORPUS["questions"]}, {"canonical"})
        self.assertEqual(len(CORPUS["questions"]), 20)
        self.assertEqual([r.route(text) for text in BEHAVIOR], ["honcho"] * len(BEHAVIOR))

    def test_ambiguous_empty_and_non_text_questions_are_canonical(self):
        for text in ("Do I usually ask what blocks this?", "", "hello", None, 7):
            self.assertEqual(r.route(text), "canonical", text)


def providers(graph=None, honcho=None, canonical=None):
    calls = []

    def make(name, result):
        def provider(question):
            calls.append(name)
            if isinstance(result, Exception):
                raise result
            return result

        return provider

    return {
        "graph": make("graph", graph),
        "honcho": make("honcho", honcho),
        "canonical": make("canonical", canonical),
    }, calls


def allow(*refused):
    seen = []

    def authorize(store, phase):
        seen.append((store, phase))
        return store not in refused

    authorize.seen = seen
    return authorize


DEP, FACT, HABIT = (
    "Which ticket blocks the export bundle?",
    "What does the bundle requirement say?",
    "How do I usually name branches?",
)


class Answering(unittest.TestCase):
    def test_each_question_is_answered_from_exactly_one_store_and_authorized_before_and_after(self):
        for question, store in ((DEP, "graph"), (FACT, "canonical"), (HABIT, "honcho")):
            p, calls = providers(
                graph={"data": {"k": 1}, "provenance": {"source": "graph_index"}},
                honcho={"data": {"m": 1}},
                canonical=GOOD,
            )
            gate = allow()
            out = r.answer(question, p, gate)
            self.assertEqual((out["ok"], out["store"], calls), (True, store, [store]), question)
            self.assertNotIn("fallback", out)
            self.assertEqual(gate.seen, [(store, "before"), (store, "after")])

    def test_an_unauthorized_store_is_denied_with_no_provider_call_and_no_fallback(self):
        for question, store in ((DEP, "graph"), (HABIT, "honcho"), (FACT, "canonical")):
            p, calls = providers(graph={"data": 1}, honcho={"data": 1}, canonical=GOOD)
            out = r.answer(question, p, allow(store))
            self.assertEqual((out["ok"], out["status"], calls), (False, "denied", []), question)

    def test_a_grant_revoked_while_the_source_answered_gives_no_output(self):
        p, calls = providers(graph={"data": 1, "provenance": {"s": 1}})
        phases = iter([True, False])
        out = r.answer(DEP, p, lambda store, phase: next(phases))
        self.assertEqual((out["ok"], out["status"], calls), (False, "denied", ["graph"]))


class Fallback(unittest.TestCase):
    def test_a_graph_that_cannot_be_served_falls_back_to_a_verified_canonical_answer_after_fresh_authorization(self):
        for broken in (None, OSError("down")):
            p, calls = providers(graph=broken, canonical=GOOD)
            gate = allow()
            out = r.answer(DEP, p, gate)
            self.assertEqual(
                (out["ok"], out["store"], out["fallback"], out["reason"]),
                (True, "canonical", True, "graph_unavailable"),
            )
            self.assertEqual(calls, ["graph", "canonical"])  # the graph failed, so it is not a second answering store
            self.assertEqual(gate.seen, [("graph", "before"), ("canonical", "before"), ("canonical", "after")])

    def test_canonical_unavailable_incomplete_or_unauthorized_is_degraded_or_a_denial_never_an_invented_answer(self):
        for bad in (
            None,
            OSError("down"),
            {"content": "x"},
            {"content": "x", "freshness": "stale", "revision": {"a": 1}},
            {"freshness": "fresh", "revision": {"a": 1}},
        ):
            p, _ = providers(graph=None, canonical=bad)
            out = r.answer(DEP, p, allow())
            self.assertEqual((out["ok"], out["status"]), (False, "DEGRADED"), bad)
        p, calls = providers(graph=None, canonical=GOOD)
        out = r.answer(DEP, p, allow("canonical"))
        self.assertEqual((out["ok"], out["status"], calls), (False, "denied", ["graph"]))
        p, calls = providers(graph=None, canonical=GOOD)
        phases = iter([True, True, False])
        out = r.answer(DEP, p, lambda store, phase: next(phases))
        self.assertEqual((out["ok"], out["status"]), (False, "denied"))

    def test_honcho_and_canonical_failures_are_degraded_without_a_second_store(self):
        p, calls = providers(honcho=None, canonical=GOOD)
        self.assertEqual(r.answer(HABIT, p, allow())["status"], "DEGRADED")
        self.assertEqual(calls, ["honcho"])
        p, calls = providers(canonical={"content": "x"})
        self.assertEqual(r.answer(FACT, p, allow())["status"], "DEGRADED")
        self.assertEqual(calls, ["canonical"])


if __name__ == "__main__":
    unittest.main()
