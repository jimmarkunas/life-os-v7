"""(REQ-85 T1): the retrieval baseline runner and its fabricated corpus."""

import importlib.util
import io
import json
import pathlib
import re
import socket
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("um_baseline", ROOT / "tools" / "um_baseline.py")
um = importlib.util.module_from_spec(spec)
spec.loader.exec_module(um)
DOC = json.loads(um.CORPUS.read_text())
METRICS = {
    "precision_at_5",
    "recall",
    "abstention_accuracy",
    "canonical_citation_rate",
    "wrong_tenant_leaks",
    "fabricated_answers",
    "p50_ms",
    "p95_ms",
    "tool_calls",
    "cost_usd",
}


class FakeHoncho:
    """Stands in for the T2 client: recall(query, tenant, top_k) -> [{'source_ref', 'text'}]."""

    def __init__(self, world, leak=False, fabricate=False, uncited=False):
        self.world, self.leak, self.fabricate, self.uncited, self.cost_usd, self.calls = (
            world,
            leak,
            fabricate,
            uncited,
            0.002,
            0,
        )

    def recall(self, query, tenant, top_k):
        self.calls += 1
        ids, _ = self.world.search(tenant, query, top_k)
        items = [{"source_ref": i, "text": self.world.src[i]["text"]} for i in ids]
        if self.leak:
            other = next(i for i, s in self.world.src.items() if s["tenant"] != tenant)
            items.append({"source_ref": other, "text": "other tenant"})
        if self.fabricate:
            items.append({"source_ref": "FAKE-REQ-999", "text": "not in the corpus"})
        if self.uncited:
            items.append({"source_ref": None, "text": "free text with no source"})
        return items


class Corpus(unittest.TestCase):
    def test_counts_are_exactly_twenty_questions_and_three_cases(self):
        cats = [q["category"] for q in DOC["questions"]]
        self.assertEqual((len(cats), cats.count("known"), cats.count("absent"), cats.count("stale")), (20, 10, 5, 5))
        self.assertEqual([c["id"] for c in DOC["dependency_cases"]], ["D1", "D2", "D3"])
        self.assertEqual(sorted({c["kind"] for c in DOC["dependency_cases"]}), ["decision_to_task", "gaps", "impact"])

    def test_everything_is_fabricated_and_nothing_looks_real(self):
        ids = {s["id"] for s in DOC["sources"]}
        self.assertTrue(all(re.fullmatch(r"FAKE-[A-Z]+-\d{3}", i) for i in ids))
        text = json.dumps(DOC)
        for pattern in (
            r"[\w.+-]+@[\w-]+\.[\w.]+",
            r"https?://",
            r"\b[A-Fa-f0-9]{32,}\b",
            r"\bsk-[A-Za-z0-9]{10,}",
            r"\b\d{3}[- .]\d{3}[- .]\d{4}\b",
            r"\b[A-Za-z0-9_-]{20,}\b",
        ):
            self.assertIsNone(re.search(pattern, text), pattern)
        for e in DOC["edges"]:
            self.assertIn(e["from"], ids)
            self.assertIn(e["to"], ids)
        for q in DOC["questions"]:
            self.assertTrue(set(q["expect_ids"]) <= ids)
            self.assertTrue(all(DOC_SRC[i]["tenant"] == q["tenant"] for i in q["expect_ids"]))

    def test_the_two_tenants_share_words_on_purpose(self):
        self.assertEqual({s["tenant"] for s in DOC["sources"]}, {"alpha", "beta"})
        self.assertGreaterEqual(sum(1 for q in DOC["questions"] if q["tenant"] == "beta"), 3)


DOC_SRC = {s["id"]: s for s in DOC["sources"]}


class Local(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = um.run(DOC, ("direct", "graph"), 5)

    def test_both_modes_run_five_times_with_identical_answers_and_all_metrics(self):
        for mode in ("direct", "graph"):
            r = self.report[mode]
            self.assertEqual((r["reps"], r["identical_across_reps"]), (5, True))
            self.assertEqual(set(r["metrics"]), METRICS)
            self.assertEqual(r["metrics"]["cost_usd"], 0.0)

    def test_no_leak_no_fabrication_and_every_absent_question_abstains(self):
        for mode in ("direct", "graph"):
            r = self.report[mode]
            self.assertEqual((r["metrics"]["wrong_tenant_leaks"], r["metrics"]["fabricated_answers"]), (0, 0))
            self.assertTrue(all(r["absent_abstained"].values()), mode)

    def test_the_graph_answers_all_three_dependency_cases_and_one_hop_direct_does_not(self):
        self.assertEqual(self.report["graph"]["dependency_ok"], {"D1": True, "D2": True, "D3": True})
        self.assertTrue(self.report["graph"]["pass"])
        self.assertFalse(self.report["direct"]["dependency_ok"]["D1"])
        self.assertFalse(self.report["direct"]["dependency_ok"]["D2"])
        self.assertFalse(self.report["direct"]["pass"])

    def test_a_stale_record_is_never_served_and_the_correction_is_cited(self):
        world = um.World(DOC)
        for q in (x for x in DOC["questions"] if x["category"] == "stale"):
            ids, _ = um.answer_direct(world, q["tenant"], q["text"])
            self.assertTrue(set(q["expect_ids"]) <= set(ids[:5]), q["id"])
            self.assertFalse([i for i in ids if world.src[i]["status"] == "stale"], q["id"])

    def test_the_other_tenant_never_answers(self):
        world = um.World(DOC)
        ids, _ = um.answer_direct(world, "beta", "What is the default share link expiry?")
        self.assertEqual(ids, [])
        ids, _ = um.answer_direct(world, "alpha", "Which bundle format was chosen?")
        self.assertFalse([i for i in ids if world.src[i]["tenant"] != "alpha"])

    def test_the_pass_rule_fails_on_a_leak_and_on_a_missed_abstention(self):
        world = um.World(DOC)
        passes = [um.run_mode(world, "graph")]
        passes[0]["Q11"] = {"ids": ["FAKE-NOTE-001"], "abstain": False, "calls": 1, "ms": 0.1}
        self.assertFalse(um.score(world, passes)["pass"])
        passes = [um.run_mode(world, "graph")]
        passes[0]["Q01"]["ids"] = ["FAKE-REQ-101"]
        r = um.score(world, passes)
        self.assertEqual(r["metrics"]["wrong_tenant_leaks"], 1)
        self.assertFalse(r["pass"])


class WithAClient(unittest.TestCase):
    def test_honcho_and_combined_need_a_client_and_make_no_network_call(self):
        with self.assertRaises(ValueError) as caught:
            um.run(DOC, ("honcho",), 1)
        self.assertEqual(str(caught.exception), "honcho_client_missing")
        world = um.World(DOC)
        with (
            mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")),
            mock.patch("socket.create_connection", side_effect=AssertionError("network")),
        ):
            report = um.run(DOC, ("direct", "graph", "honcho", "combined"), 2, {"honcho": FakeHoncho(world)})
        self.assertEqual(set(report), {"direct", "graph", "honcho", "combined"})

    def test_a_faulty_client_is_measured_honestly(self):
        world = um.World(DOC)
        leak = um.run(DOC, ("honcho",), 1, {"honcho": FakeHoncho(world, leak=True)})["honcho"]
        self.assertGreater(leak["metrics"]["wrong_tenant_leaks"], 0)
        self.assertFalse(leak["pass"])
        fab = um.run(DOC, ("honcho",), 1, {"honcho": FakeHoncho(world, fabricate=True)})["honcho"]
        self.assertGreater(fab["metrics"]["fabricated_answers"], 0)
        unc = um.run(DOC, ("honcho",), 1, {"honcho": FakeHoncho(world, uncited=True)})["honcho"]
        self.assertEqual(unc["metrics"]["fabricated_answers"], 0)
        self.assertEqual(unc["metrics"]["cost_usd"], 0.002)

    def test_combined_drops_items_that_are_not_canonical_for_the_tenant(self):
        world = um.World(DOC)
        r = um.run(DOC, ("combined",), 1, {"honcho": FakeHoncho(world, leak=True, fabricate=True)})["combined"]
        self.assertEqual((r["metrics"]["wrong_tenant_leaks"], r["metrics"]["fabricated_answers"]), (0, 0))
        self.assertTrue(all(r["absent_abstained"].values()))


class GraphArm(unittest.TestCase):
    """the graph arm can be injected (the Aura fixtures)"""

    class Arm:
        def __init__(self, world):
            self.world, self.calls = world, 0

        def answer(self, tenant, text):
            self.calls += 1
            return um.answer_direct(self.world, tenant, text)

        def deps(self, case):
            self.calls += 1
            return um.deps_graph(self.world, case)

    def test_graph_and_combined_use_the_injected_arm_and_direct_does_not(self):
        world = um.World(DOC)
        arm = self.Arm(world)
        report = um.run(DOC, ("direct", "graph", "honcho", "combined"), 1, {"honcho": FakeHoncho(world), "graph": arm})
        self.assertEqual(arm.calls, 2 * (len(DOC["questions"]) + len(DOC["dependency_cases"])))
        self.assertTrue(report["graph"]["pass"])
        arm.calls = 0
        um.run(DOC, ("direct",), 1, {"graph": arm})
        self.assertEqual(arm.calls, 0)

    def test_without_an_arm_nothing_changes(self):
        a, b = um.run(DOC, ("graph",), 1), um.run(DOC, ("graph",), 1, {"graph": None})
        self.assertEqual(a["graph"]["dependency_ok"], b["graph"]["dependency_ok"])


class Cli(unittest.TestCase):
    def test_the_command_prints_a_table_and_writes_the_json_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "r.json"
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = um.main(["--modes", "direct,graph", "--reps", "2", "--json", str(out)])
            self.assertEqual(code, 0)
            self.assertIn("graph", buf.getvalue())
            self.assertEqual(set(json.loads(out.read_text())), {"direct", "graph"})

    def test_honcho_mode_without_a_client_is_refused_with_a_fixed_word(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(um.main(["--modes", "honcho"]), 2)
        self.assertEqual(buf.getvalue().strip(), "honcho_client_missing")


if __name__ == "__main__":
    unittest.main()
