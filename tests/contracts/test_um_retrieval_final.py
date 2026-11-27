"""The final feasibility harness: dev capture, the rule that picks the gate, the acceptance runner and the protocol guard. In-memory Honcho; no network."""

import importlib.util
import json
import pathlib
import socket
import sys
import tempfile
import unittest
import zlib
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("um_retrieval_live", ROOT / "tools" / "um_retrieval_live.py")
lv = importlib.util.module_from_spec(spec)
sys.modules["um_retrieval_live"] = lv
spec.loader.exec_module(lv)
rt, um, client = lv.rt, lv.um, lv.client
LIVE = json.loads((lv.FIX / "behavioral_live.json").read_text())
REG = json.loads((lv.FIX / "behavioral.json").read_text())
HELD = json.loads((lv.FIX / "acceptance_heldout.json").read_text())


def refuse(*a, **k):
    raise AssertionError("network used")


class Bag:
    """A token-bag embedder: texts that share words are close."""

    def embed(self, text, kind="doc"):
        v = [0.0] * 256
        for tok in um.tokens(text):
            v[zlib.crc32(tok.encode()) % 256] += 1.0
        return v


def meter():
    return {"spent_usd": 0.7, "calls": 3, "audit_rows": 9, "scope": None}


class Fake(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(socket, "socket", refuse)
        p.start()
        self.addCleanup(p.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ev = lv.Evidence(pathlib.Path(self.tmp.name) / "e.jsonl")


class Pure(Fake):
    def test_plateau_is_the_midpoint_of_the_widest_run_at_the_best_score(self):
        scores = [(0.1, 1), (0.2, 3), (0.3, 3), (0.4, 2), (0.5, 3), (0.6, 3), (0.7, 3), (0.8, 1)]
        self.assertEqual(lv.plateau(scores), 0.6)

    def test_exact_needs_the_same_refs_and_sources_and_no_answer_when_none_is_wanted(self):
        q = {
            "expect": {"answers": [{"ref": "A", "sources": ["m1"]}, {"ref": "B", "sources": ["m2"]}], "unordered": True}
        }
        self.assertTrue(lv.exact(q, [{"ref": "B", "sources": ["m2"]}, {"ref": "A", "sources": ["m1"]}]))
        self.assertFalse(lv.exact(q, [{"ref": "A", "sources": ["m1"]}]))
        self.assertFalse(lv.exact(q, [{"ref": "A", "sources": ["x"]}, {"ref": "B", "sources": ["m2"]}]))
        ordered = {"expect": {"answers": [{"ref": "A", "sources": ["m1"]}]}}
        self.assertFalse(lv.exact(ordered, [{"ref": "A", "sources": ["m1"]}, {"ref": "B", "sources": ["m2"]}]))
        self.assertTrue(lv.exact({"expect": {"answers": []}}, []))
        self.assertFalse(lv.exact({"expect": {"answers": []}}, [{"ref": "A", "sources": ["m1"]}]))

    def test_the_gate_needs_only_its_own_phase_tokens_and_no_two_alike(self):
        import base64
        import time

        def jwt(ws):
            part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
            return ".".join([part({"a": 1}), part({"w": ws, "exp": int(time.time()) + 7000}), "s"])

        env = {"UM_LIVE_RELEASE": lv.RELEASE, "UM_T7_TOKEN": jwt(lv.WS_HELDOUT)}
        self.assertEqual(lv.gate("accept_H1", env, lv.HOST, time.time()), (True, "open"))
        self.assertEqual(lv.gate("seed7", env, lv.HOST, time.time()), (True, "open"))
        self.assertEqual(lv.gate("accept_dev", env, lv.HOST, time.time())[1], "token_missing_or_not_jwt")
        same = {**env, "UM_T6_TOKEN": env["UM_T7_TOKEN"]}
        self.assertEqual(lv.gate("accept_H1", same, lv.HOST, time.time())[1], "tokens_not_distinct")


class Live(Fake):
    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(ROOT / "tests" / "contracts"))
        from test_um_retrieval_live import FakeHonchoHttp  # noqa: PLC0415

        self.http = FakeHonchoHttp()
        self.h = client.Honcho(lv.Allowlist(self.http, lv.WS_BEHAVIORAL, "seed"), lv.WS_BEHAVIORAL)
        lv.seed(self.h, LIVE, REG, self.ev, meter=meter)
        self.read = client.Honcho(lv.Allowlist(self.http, lv.WS_BEHAVIORAL, "read"), lv.WS_BEHAVIORAL)

    def test_the_dev_capture_records_the_window_and_every_distance_cutoff_for_every_query(self):
        lv.dev_capture(self.read, LIVE, REG, self.ev, meter=meter)
        rows = [r for r in self.ev.rows() if r["kind"] == "devcand"]
        self.assertEqual(len(rows), len(LIVE["queries"]))
        self.assertEqual(set(rows[0]["dist"]), {str(d) for d in lv.DIST_GRID})
        self.assertTrue(all("live_id" in i for i in rows[0]["window"]))

    def test_the_rule_picks_the_simplest_candidate_that_meets_the_dev_bars_and_reads_no_held_out_set(self):
        lv.dev_capture(self.read, LIVE, REG, self.ev, meter=meter)
        rows = [r for r in self.ev.rows() if r["kind"] == "devcand"]
        res = lv.choose_gate(rows, LIVE["queries"], REG, {"C1": Bag(), "C2": Bag()})
        self.assertEqual(set(res["candidates"]), {"C1", "C2", "C3"})
        for v in res["candidates"].values():
            self.assertIn("exact_overall", v["dev"])
        if res["chosen"]:
            self.assertGreaterEqual(res["chosen"]["dev"]["exact_overall"], 0.9)
            self.assertEqual(res["chosen"]["dev"]["absent_abstention"], 1.0)

    def test_a_chosen_gate_runs_on_a_query_set_and_the_summary_judges_it_against_the_bars(self):
        config = {"candidate": "C1", "gate": {"kind": "cosine", "tau": 0.6, "select": "top1"}, "route_all": True}
        lv.acceptance(self.read, LIVE["queries"], REG, self.ev, config, "dev", reps=2, meter=meter, embedder=Bag())
        out = lv.acceptance_summary(self.ev.rows(), LIVE["queries"], REG, "dev")
        self.assertEqual((out["queries"], out["reps"]), (len(LIVE["queries"]), 2))
        self.assertTrue(out["stable_across_reps"])
        self.assertEqual(out["unsafe"], {"leaks": 0, "fabricated": 0, "revoked": 0, "unsupported": 0})
        self.assertIn("meets_bars", out)

    def test_a_moving_meter_halts_an_acceptance_run(self):
        states = iter([meter(), {**meter(), "calls": 4}])
        config = {"gate": {"kind": "none", "select": "top1"}, "route_all": True}
        with self.assertRaises(lv.Halt):
            lv.acceptance(
                self.read, LIVE["queries"][:2], REG, self.ev, config, "dev", reps=1, meter=lambda: next(states)
            )


class Protocol(Fake):
    def accept_row(self, tag, qid="H1-01", answers=()):
        self.ev.add(
            kind="accept",
            tag=tag,
            rep=1,
            qid=qid,
            tenant="alpha",
            answers=list(answers),
            sources=[],
            authority=[],
            abstain=not answers,
            degraded=None,
            intent="behavioral",
            trace=[],
            ms=1.0,
        )

    def test_a_set_is_run_once(self):
        self.accept_row("dev")
        self.assertEqual(lv.protocol_guard(self.ev, "accept_H1"), "")
        self.accept_row("H1")
        self.assertEqual(lv.protocol_guard(self.ev, "accept_H1"), "set_already_run")

    def test_the_held_out_sets_need_the_dev_acceptance_first(self):
        self.assertEqual(lv.protocol_guard(self.ev, "accept_H1"), "dev_acceptance_not_run")

    def test_the_second_set_is_closed_unless_the_first_was_run_and_failed(self):
        self.accept_row("dev")
        self.assertEqual(lv.protocol_guard(self.ev, "accept_H2"), "h2_not_needed")
        self.accept_row("H1")  # no answers: the answerable queries fail, so H1 misses the bars
        self.assertEqual(lv.protocol_guard(self.ev, "accept_H2"), "")


class Seed7(Fake):
    def test_the_fresh_workspace_seed_fits_the_limit_and_names_the_ids_honcho_gave(self):
        sys.path.insert(0, str(ROOT / "tests" / "contracts"))
        from test_um_retrieval_live import FakeHonchoHttp  # noqa: PLC0415

        http = FakeHonchoHttp()
        h = client.Honcho(lv.Allowlist(http, lv.WS_HELDOUT, "seed"), lv.WS_HELDOUT)
        ids = lv.seed(h, {"seed": HELD["seed"]}, HELD["registry"], self.ev, meter=meter)
        self.assertEqual((len(http.rows), len(ids)), (24, 20))
        tombs = [json.loads(r["content"]) for r in http.rows if json.loads(r["content"])["kind"] == "tombstone"]
        self.assertEqual({t["of_ref"] for t in tombs}, {"FAKE-HLD-004", "FAKE-HLD-014", "FAKE-HLD-008", "FAKE-HLD-019"})


if __name__ == "__main__":
    unittest.main()
