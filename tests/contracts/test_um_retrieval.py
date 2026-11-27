"""Authority-aware retrieval: routing, validation at both boundaries, replay of the old blind union, and the frozen behavioral cases. Fakes only; every test runs with the network blocked."""

import importlib.util
import json
import pathlib
import re
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
CORPUS = json.loads(um.CORPUS.read_text())
BEHAVIORAL = json.loads(rt.BEHAVIORAL_FIXTURE.read_text())


def refuse(*args, **kwargs):
    raise AssertionError("network used")


class Offline(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(socket, "socket", refuse)
        patcher.start()
        self.addCleanup(patcher.stop)


class Routing(Offline):
    def test_intent_comes_from_the_words_of_the_query(self):
        cases = {
            "What does Avery prefer for lunch?": "behavioral",
            "Which tool did the team choose?": "behavioral",
            "If the upgrade slips, which requirement is at risk?": "dependency",
            "What does the offline export bundle requirement say?": "canonical",
            "Hmm?": "unknown",
            "": "unknown",
        }
        for query, want in cases.items():
            self.assertEqual(rt.route(query), want, query)

    def test_no_original_question_is_unknown_and_routing_never_reads_ids(self):
        self.assertNotIn("unknown", {rt.route(q["text"]) for q in CORPUS["questions"]})
        source = (ROOT / "tools" / "um_retrieval.py").read_text()
        self.assertIsNone(re.search(r"""["'][QD]\d{1,2}["']""", source))


class Replay(Offline):
    """The old blind union reproduced with fakes, and the new path against the same fakes."""

    def setUp(self):
        super().setUp()
        self.world = um.World(CORPUS)

    def score(self, passes):
        return um.score(self.world, passes)

    def test_the_old_combined_mode_fails_with_a_noisy_memory(self):
        old = self.score([um.run_mode(self.world, "combined", rt.NoisyHoncho(self.world))])
        self.assertFalse(old["pass"])
        self.assertEqual(sum(old["dependency_ok"].values()), 0)  # every extra memory id breaks the exact set
        self.assertLess(old["metrics"]["abstention_accuracy"], 1.0)  # absent questions got memory items
        self.assertLess(old["metrics"]["precision_at_5"], 0.647)

    def test_even_a_clean_memory_breaks_a_dependency_set_in_the_old_mode(self):
        old = self.score([um.run_mode(self.world, "combined", rt.CleanHoncho(self.world))])
        self.assertLess(sum(old["dependency_ok"].values()), 3)

    def test_the_original_reference_numbers_are_unchanged(self):
        graph = self.score([um.run_mode(self.world, "graph")])
        direct = self.score([um.run_mode(self.world, "direct")])
        self.assertEqual((graph["metrics"]["precision_at_5"], direct["metrics"]["precision_at_5"]), (0.647, 0.647))
        self.assertEqual((sum(graph["dependency_ok"].values()), sum(direct["dependency_ok"].values())), (3, 1))

    def test_the_new_combined_mode_keeps_every_graph_result_under_any_fake(self):
        graph = self.score([um.run_mode(self.world, "graph")])
        for fake in (rt.NoisyHoncho(self.world), rt.CleanHoncho(self.world), None):
            new = self.score([rt.run_mode_v2(self.world, rt.Retriever(self.world, fake))])
            self.assertTrue(new["pass"])
            self.assertEqual(sum(new["dependency_ok"].values()), 3)
            self.assertEqual(new["metrics"]["abstention_accuracy"], 1.0)
            self.assertGreaterEqual(new["metrics"]["precision_at_5"], graph["metrics"]["precision_at_5"])
            self.assertEqual(new["metrics"]["canonical_citation_rate"], graph["metrics"]["canonical_citation_rate"])
            self.assertEqual((new["metrics"]["wrong_tenant_leaks"], new["metrics"]["fabricated_answers"]), (0, 0))

    def test_five_repetitions_agree(self):
        passes = [rt.run_mode_v2(self.world, rt.Retriever(self.world, rt.NoisyHoncho(self.world))) for _ in range(5)]
        self.assertTrue(self.score(passes)["identical_across_reps"])


class Boundaries(Offline):
    def setUp(self):
        super().setUp()
        self.world = um.World(BEHAVIORAL)

    def retriever(self, items, **kwargs):
        client = rt.CaseClient({"mode": "returns", "items": items})
        return rt.Retriever(self.world, client, None, BEHAVIORAL["grants"], BEHAVIORAL["messages"], **kwargs), client

    def test_an_ungranted_tenant_asks_no_source_at_all(self):
        r, client = self.retriever([{"source_ref": "FAKE-MEM-001"}])
        out = r.answer("gamma", "What does Avery prefer for meeting times?")
        self.assertEqual((out["abstain"], out["degraded"], client.calls), (True, "tenant_not_granted", 0))

    def test_an_id_written_in_the_text_is_not_proof(self):
        r, _ = self.retriever([{"source_ref": "FAKE-MEM-777", "text": "Avery prefers meeting times FAKE-MEM-001"}])
        out = r.answer("alpha", "What does Avery prefer for meeting times?")
        self.assertTrue(out["abstain"])
        self.assertEqual(out["stats"]["fabricated"], 1)

    def test_the_answer_text_comes_from_the_registry_not_the_client(self):
        r, _ = self.retriever([{"source_ref": "FAKE-MEM-001", "text": "client says something else entirely"}])
        out = r.answer("alpha", "What does Avery prefer for meeting times?")
        self.assertEqual(out["answers"][0]["text"], "Avery prefers morning meeting times before ten.")

    def test_a_record_whose_own_provenance_does_not_resolve_is_unsupported(self):
        doc = json.loads(json.dumps(BEHAVIORAL))
        next(s for s in doc["sources"] if s["id"] == "FAKE-MEM-001")["provenance"] = ["FAKE-MSG-404"]
        r = rt.Retriever(
            um.World(doc),
            rt.CaseClient({"mode": "returns", "items": [{"source_ref": "FAKE-MEM-001"}]}),
            None,
            doc["grants"],
            doc["messages"],
        )
        out = r.answer("alpha", "What does Avery prefer for meeting times?")
        self.assertEqual((out["abstain"], out["stats"]["unsupported"]), (True, 1))

    def test_a_non_list_reply_is_malformed_not_an_answer(self):
        r, _ = self.retriever(None)
        r.client.spec["items"] = None
        r.client.recall = lambda *a: {"source_ref": "FAKE-MEM-001"}
        out = r.answer("alpha", "What does Avery prefer for meeting times?")
        self.assertEqual((out["abstain"], out["stats"]["malformed"]), (True, 1))


class Dependencies(Offline):
    def setUp(self):
        super().setUp()
        self.world = um.World(CORPUS)

    def test_memory_never_joins_a_dependency_set(self):
        for fake in (rt.NoisyHoncho(self.world), rt.CleanHoncho(self.world)):
            r = rt.Retriever(self.world, fake)
            for case in CORPUS["dependency_cases"]:
                got = sorted(a["ref"] for a in r.answer_case(case)["answers"])
                self.assertEqual(got, sorted(case["expect_ids"]), case["id"])
        self.assertEqual(fake.__class__, rt.CleanHoncho)

    def test_a_dependency_cannot_be_guessed_when_the_graph_is_down(self):
        r = rt.Retriever(self.world, rt.NoisyHoncho(self.world), rt.DownGraph())
        out = r.answer_case(CORPUS["dependency_cases"][0])
        self.assertEqual((out["abstain"], out["degraded"]), (True, "graph_unavailable"))


class Behavioral(Offline):
    def test_every_frozen_case_passes_through_the_real_selection_and_filtering(self):
        ev = rt.evaluate_behavioral(BEHAVIORAL)
        failed = {k: v["fails"] for k, v in ev["cases"].items() if v["fails"]}
        self.assertEqual(failed, {})
        s = ev["summary"]
        for key in (
            "explicit_correct",
            "corrections_correct",
            "absent_abstained",
            "revoked_unauthorized_blocked",
            "multi_source_correct",
            "canonical_wins",
            "outages_and_malformed",
            "dev_pass",
            "heldout_pass",
            "provenance_complete",
        ):
            self.assertTrue(s[key], key)
        self.assertEqual(
            (s["leaks"], s["fabricated"], s["revoked_outputs"], s["answers_without_sources"]), (0, 0, 0, 0)
        )

    def test_the_acceptance_floor_of_case_types_is_present_in_the_run(self):
        ids = list(rt.evaluate_behavioral(BEHAVIORAL)["cases"])
        n = lambda p: sum(i.startswith(p) for i in ids)  # noqa: E731
        self.assertGreaterEqual(n("B"), 5)
        self.assertEqual((n("C"), n("A"), n("R"), n("M")), (3, 3, 3, 2))
        self.assertGreaterEqual(n("O"), 3)


class Report(Offline):
    def test_the_synthetic_report_is_offline_and_free_and_lists_every_mode(self):
        rep = rt.report(reps=2)
        self.assertEqual((rep["network"], rep["cost_usd"]), ("blocked", 0.0))
        self.assertEqual(
            set(rep["modes"]),
            {
                "direct",
                "graph",
                "honcho_noisy",
                "combined_old_noisy",
                "combined_old_clean",
                "combined_v2_noisy",
                "combined_v2_clean",
            },
        )
        self.assertEqual(
            set(rep["per_query"]["combined_v2_noisy"]), {q["id"] for q in CORPUS["questions"]} | {"D1", "D2", "D3"}
        )
        self.assertEqual(rep["delta_vs_graph"]["combined_v2_noisy"]["precision_at_5"], 0.0)
        self.assertIn("combined_v2_noisy", rt.table(rep))

    def test_the_command_line_runs(self):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rt.main(["--reps", "1"]), 0)
        self.assertIn("combined_v2_noisy", out.getvalue())


if __name__ == "__main__":
    unittest.main()
