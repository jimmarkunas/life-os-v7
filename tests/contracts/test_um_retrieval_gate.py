"""The embedding relevance gate, native-rank selection and routing without a word list. Fakes only; no network."""

import importlib.util
import io
import json
import pathlib
import socket
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("um_retrieval", ROOT / "tools" / "um_retrieval.py")
rt = importlib.util.module_from_spec(spec)
sys.modules["um_retrieval"] = rt
spec.loader.exec_module(rt)
um = rt.um
BEH = json.loads(rt.BEHAVIORAL_FIXTURE.read_text())
QUERY = "What does Avery prefer for meeting times?"


def refuse(*args, **kwargs):
    raise AssertionError("network used")


class Vectors:
    """Texts map to fixed vectors; the query and each record text get the vector their words ask for."""

    def __init__(self, table, fail=False):
        self.table, self.fail, self.calls = table, fail, []

    def embed(self, text, kind="doc"):
        self.calls.append((kind, text))
        if self.fail:
            raise ConnectionError("down")
        return self.table[text]


def make(items, table, gate, route_all=False, fail=False, doc=BEH):
    world = rt.build_world(doc)
    emb = Vectors(table, fail)
    client = rt.CaseClient({"mode": "returns", "items": items})
    r = rt.Retriever(world, client, None, doc["grants"], doc["messages"], embedder=emb, gate=gate, route_all=route_all)
    return r, client, emb


T1 = "Avery prefers morning meeting times before ten."
T10 = "Avery usually eats lunch at noon."
TABLE = {QUERY: [1.0, 0.0], T1: [0.9, 0.1], T10: [0.3, 0.95]}


class Offline(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(socket, "socket", refuse)
        patcher.start()
        self.addCleanup(patcher.stop)


class Gate(Offline):
    def test_a_record_above_the_threshold_is_admitted_and_one_below_is_not(self):
        r, _, _ = make(
            [{"source_ref": "FAKE-MEM-010"}, {"source_ref": "FAKE-MEM-001"}],
            TABLE,
            {"kind": "cosine", "tau": 0.8, "select": "top1"},
        )
        out = r.answer("alpha", QUERY)
        self.assertEqual([a["ref"] for a in out["answers"]], ["FAKE-MEM-001"])
        sims = {t["ref"]: t.get("sim") for t in out["trace"]}
        self.assertGreater(sims["FAKE-MEM-001"], 0.8)
        self.assertLess(sims["FAKE-MEM-010"], 0.8)

    def test_nothing_above_the_threshold_is_an_abstention(self):
        r, _, _ = make([{"source_ref": "FAKE-MEM-010"}], TABLE, {"kind": "cosine", "tau": 0.8, "select": "top1"})
        out = r.answer("alpha", QUERY)
        self.assertEqual((out["abstain"], out["answers"]), (True, []))

    def test_the_gate_cannot_admit_what_the_safeguards_refuse(self):
        r, _, _ = make(
            [{"source_ref": "FAKE-MEM-011"}],
            {**TABLE, "Avery prefers phone calls for urgent pages.": [1.0, 0.0]},
            {"kind": "cosine", "tau": 0.1, "select": "top1"},
        )
        out = r.answer("alpha", "How does Avery prefer urgent pages?")
        self.assertTrue(out["abstain"])  # revoked stays refused however similar it is

    def test_an_embedder_that_is_down_admits_nothing_and_says_so(self):
        r, _, _ = make(
            [{"source_ref": "FAKE-MEM-001"}], TABLE, {"kind": "cosine", "tau": 0.5, "select": "top1"}, fail=True
        )
        out = r.answer("alpha", QUERY)
        self.assertEqual((out["abstain"], out["degraded"]), (True, "embedder_unavailable"))

    def test_record_vectors_are_computed_once(self):
        r, _, emb = make([{"source_ref": "FAKE-MEM-001"}], TABLE, {"kind": "cosine", "tau": 0.5, "select": "top1"})
        r.answer("alpha", QUERY)
        r.answer("alpha", QUERY)
        self.assertEqual(sum(1 for k, t in emb.calls if t == T1), 1)

    def test_the_none_gate_trusts_the_source_filter(self):
        r, _, emb = make([{"source_ref": "FAKE-MEM-010"}], TABLE, {"kind": "none", "select": "top1"})
        out = r.answer("alpha", QUERY)
        self.assertEqual(([a["ref"] for a in out["answers"]], emb.calls), (["FAKE-MEM-010"], []))


class Selection(Offline):
    def table(self):
        return {
            **TABLE,
            "Avery prefers shared rooms for workshops.": [0.88, 0.1],
            "Avery prefers quiet rooms for focus time.": [0.8, 0.3],
        }

    def test_top1_keeps_the_sources_own_first_choice_after_validation(self):
        items = [{"source_ref": "FAKE-MEM-011"}, {"source_ref": "FAKE-MEM-015"}, {"source_ref": "FAKE-MEM-014"}]
        r, _, _ = make(items, self.table(), {"kind": "cosine", "tau": 0.1, "select": "top1"})
        out = r.answer("alpha", QUERY)
        self.assertEqual([a["ref"] for a in out["answers"]], ["FAKE-MEM-015"])

    def test_within_margin_returns_every_answer_close_to_the_best(self):
        items = [{"source_ref": "FAKE-MEM-015"}, {"source_ref": "FAKE-MEM-014"}, {"source_ref": "FAKE-MEM-010"}]
        r, _, _ = make(items, self.table(), {"kind": "cosine", "tau": 0.1, "select": "within", "margin": 0.1})
        out = r.answer("alpha", QUERY)
        self.assertEqual([a["ref"] for a in out["answers"]], ["FAKE-MEM-015", "FAKE-MEM-014"])

    def test_a_conflict_inside_one_subject_still_goes_to_the_newest(self):
        t = {
            **TABLE,
            "The alpha team prefers plain text release notes.": [1.0, 0.0],
            "The alpha team prefers formatted release notes with headings.": [1.0, 0.0],
        }
        items = [{"source_ref": "FAKE-MEM-008"}, {"source_ref": "FAKE-MEM-009"}]
        r, _, _ = make(
            items,
            {**t, "Which release notes does the team prefer?": [1.0, 0.0]},
            {"kind": "cosine", "tau": 0.5, "select": "top1"},
        )
        out = r.answer("alpha", "Which release notes does the team prefer?")
        self.assertEqual([a["ref"] for a in out["answers"]], ["FAKE-MEM-009"])


class Routing(Offline):
    def test_without_the_word_list_a_plain_question_still_reaches_memory(self):
        q = "Where does Avery like to hold workshops?"
        t = {q: [1.0, 0.0], "Avery prefers shared rooms for workshops.": [0.95, 0.1]}
        legacy, c1, _ = make([{"source_ref": "FAKE-MEM-015"}], t, {"kind": "cosine", "tau": 0.5, "select": "top1"})
        every, c2, _ = make(
            [{"source_ref": "FAKE-MEM-015"}], t, {"kind": "cosine", "tau": 0.5, "select": "top1"}, route_all=True
        )
        plain = "Which room is used for workshops by Avery?"
        t[plain] = [1.0, 0.0]
        self.assertEqual(rt.route(plain), "canonical")
        a = legacy.answer("alpha", plain)
        b = every.answer("alpha", plain)
        self.assertEqual((a["abstain"], c1.calls), (True, 0))
        self.assertEqual(([x["ref"] for x in b["answers"]], c2.calls), (["FAKE-MEM-015"], 1))

    def test_dependency_and_unknown_questions_are_not_sent_to_memory(self):
        r, client, _ = make(
            [{"source_ref": "FAKE-MEM-001"}], TABLE, {"kind": "cosine", "tau": 0.5, "select": "top1"}, route_all=True
        )
        self.assertEqual(r.answer("alpha", "Hmm?")["intent"], "unknown")
        self.assertEqual(
            r.answer("alpha", "Which requirement is at risk if the upgrade slips?")["intent"], "dependency"
        )
        self.assertEqual(client.calls, 0)

    def test_canonical_still_beats_memory_when_memory_is_always_asked(self):
        q = "What is the approved release day?"
        t = {q: [1.0, 0.0], "Avery prefers releases on Fridays.": [1.0, 0.0]}
        r, _, _ = make(
            [{"source_ref": "FAKE-MEM-016"}], t, {"kind": "cosine", "tau": 0.5, "select": "top1"}, route_all=True
        )
        out = r.answer("alpha", q)
        self.assertEqual([a["ref"] for a in out["answers"]], ["FAKE-CAN-001"])


class Embedder(Offline):
    def test_the_local_embedder_posts_one_prompt_to_loopback(self):
        seen = {}

        def opener(request, timeout=0):
            seen["url"], seen["body"] = request.full_url, json.loads(request.data)
            return io.BytesIO(json.dumps({"embedding": [0.5, 0.5]}).encode())

        e = rt.OllamaEmbedder(opener=opener, query_prefix="search_query: ", doc_prefix="search_document: ")
        self.assertEqual(e.embed("hi", "query"), [0.5, 0.5])
        e.embed("doc text", "doc")
        self.assertEqual(seen["url"], "http://127.0.0.1:11434/api/embeddings")
        self.assertEqual(seen["body"], {"model": "nomic-embed-text", "prompt": "search_document: doc text"})

    def test_a_non_loopback_embedder_is_refused(self):
        with self.assertRaises(ValueError):
            rt.OllamaEmbedder(url="https://api.example.com/embeddings")

    def test_cosine_of_orthogonal_and_equal_vectors(self):
        self.assertAlmostEqual(rt.cosine([1, 0], [0, 1]), 0.0)
        self.assertAlmostEqual(rt.cosine([2, 0], [1, 0]), 1.0)
        self.assertEqual(rt.cosine([0, 0], [1, 0]), 0.0)


if __name__ == "__main__":
    unittest.main()
