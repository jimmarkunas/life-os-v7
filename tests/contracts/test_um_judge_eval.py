"""The model-judge test harness: candidates keep every safeguard, the judge sees exactly what memory_recall
returns, the request fits the meter, and scoring keeps wrong answers apart from wrong silences. No network."""

import importlib.util
import io
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


je = _load("um_judge_eval", ROOT / "tools" / "um_judge_eval.py")
meter = _load("meter_proxy_for_judge", ROOT / "lifeos" / "edge" / "honcho" / "meter_proxy.py")
from lifeos.platform.memory_honcho import ANSWER_RULE  # noqa: E402

DOC = json.loads((ROOT / "tests" / "fixtures" / "unified_memory" / "acceptance_judge.json").read_text())
REG = DOC["registry"]
QS = {q["id"]: q for q in DOC["sets"]["J1"]}


class WordEmbedder:
    """Bag of words: enough to rank rows by shared words, no network."""

    def embed(self, text, kind="doc"):
        words = sorted({w.strip("?.,'").lower() for w in text.split()})
        return [1 if w in words else 0 for w in VOCAB]


VOCAB = sorted({w.strip("?.,'").lower() for s in REG["sources"] for w in s["text"].split()})


class Candidates(unittest.TestCase):
    def window(self, qid):
        q = QS[qid]
        return je.proxy_window(q["query"], q["tenant"], REG, WordEmbedder())

    def test_the_proxy_window_is_one_tenant_and_flags_tombstoned_rows(self):
        w = self.window("J1-021")  # "Which city is the alpha offsite in?"
        tenants = {next(s for s in REG["sources"] if s["id"] == i["source_ref"])["tenant"] for i in w}
        self.assertEqual(tenants, {"alpha"})
        states = {i["source_ref"]: i.get("live_state") for i in w}
        self.assertEqual((states["FAKE-JDG-009"], states["FAKE-JDG-011"]), ("superseded", "revoked"))

    def test_validated_candidates_never_include_stale_revoked_or_foreign_rows_and_stop_at_three(self):
        for qid, q in QS.items():
            cands = je.validated(self.window(qid), q["query"], q["tenant"], REG)
            self.assertLessEqual(len(cands), je.SHOWN, qid)
            for c in cands:
                rec = next(s for s in REG["sources"] if s["id"] == c["ref"])
                self.assertEqual((rec["status"], rec["tenant"]), ("current", q["tenant"]), qid)
                self.assertTrue(c["sources"], qid)

    def test_a_question_the_word_router_calls_a_dependency_still_reaches_memory(self):
        q = QS["J1-009"]  # "When is Rowan's protected deep-work block?": "block" reads as a dependency word
        self.assertEqual(je.rt.route(q["query"]), "dependency")
        cands = je.validated(self.window("J1-009"), q["query"], q["tenant"], REG)
        self.assertTrue(cands)  # memory was asked; before the bypass this list was empty
        self.assertEqual(je.rt.route(q["query"]), "dependency")  # the bypass is undone afterwards

    def test_the_view_is_the_recall_shape_with_the_answer_rule(self):
        q = QS["J1-021"]
        view = je.recall_view(je.validated(self.window("J1-021"), q["query"], q["tenant"], REG))
        self.assertEqual(
            (view["label"], view["answer_rule"], view["count"]), ("memory", ANSWER_RULE, len(view["items"]))
        )
        self.assertEqual(set(view["items"][0]), {"id", "label", "text", "status", "source"})


class Judge(unittest.TestCase):
    def test_the_prompt_carries_the_shipped_description_and_rule(self):
        self.assertIn(ANSWER_RULE, je.SYSTEM)
        self.assertEqual(len(je.PROMPT_SHA256), 64)

    def test_the_request_passes_the_meters_own_admission(self):
        sent = {}

        def opener(request, timeout):
            sent["body"] = request.data
            return io.BytesIO(json.dumps({"content": [{"type": "text", "text": '{"use": ["FAKE-JDG-010"]}'}]}).encode())

        view = {"items": [{"id": "FAKE-JDG-010", "text": "x"}], "count": 1}
        ids, rec = je.HaikuJudge(opener=opener).choose("Which city?", view)
        self.assertEqual((ids, rec["parsed"]), (["FAKE-JDG-010"], True))
        reservation, why = meter.admission(sent["body"])
        self.assertEqual(why, "")
        self.assertGreater(reservation, 0)

    def test_the_output_limit_leaves_room_for_thinking_and_the_reply(self):
        self.assertGreaterEqual(je.MAX_TOKENS, 2000)

    def test_the_judge_only_goes_through_the_meter(self):
        with self.assertRaises(ValueError):
            je.HaikuJudge(base="https://api.anthropic.com")

    def test_a_reply_can_only_choose_shown_ids_and_an_unreadable_one_chooses_nothing(self):
        self.assertEqual(je.parse_use('Sure: {"use": ["A", "Z"]}', {"A", "B"}), (["A"], True))
        self.assertEqual(je.parse_use("no json", {"A"}), ([], False))
        self.assertEqual(je.parse_use('{"use": "A"}', {"A"}), ([], False))


class Scoring(unittest.TestCase):
    def test_wrong_answers_and_wrong_silences_are_counted_apart_and_bars_split(self):
        qs = [QS["J1-001"], QS["J1-021"], QS["J1-061"], QS["J1-028"]]  # paraphrase, correction, hard negative, revoked
        perfect = {q["id"]: [a["ref"] for a in q["expect"]["answers"]] for q in qs}
        self.assertTrue(je.score(qs, perfect)["meets_bars"])
        bad = dict(perfect, **{"J1-061": ["FAKE-JDG-001"], "J1-001": []})
        s = je.score(qs, bad)
        self.assertEqual((s["wrong_answers"], s["wrong_silences"]), (["J1-061"], ["J1-001"]))
        self.assertEqual((s["quiet_abstention"], s["safety"], s["meets_bars"]), (0.0, 1.0, False))

    def test_top1_with_no_judge_never_stays_quiet(self):
        """The free baseline answers every query that has a candidate: the failure the judge must fix."""
        rows = []
        q = QS["J1-061"]
        sets = {"J1": ([q], REG, {q["id"]: je.proxy_window(q["query"], q["tenant"], REG, WordEmbedder())})}
        rep = je.run(sets, je.Top1Judge(), 1, rows.append)
        self.assertEqual(rep["J1"]["reps"][0]["quiet_abstention"], 0.0)
        self.assertEqual(rows[0]["kind"], "judge")


if __name__ == "__main__":
    unittest.main()
