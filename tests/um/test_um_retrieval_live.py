"""The live harness against an in-memory Honcho: allowlist, gate, incremental evidence, seeding, raw versus corrected reads and the summary. No network."""

import contextlib
import importlib.util
import io
import json
import pathlib
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("um_retrieval_live", ROOT / "tools" / "um_retrieval_live.py")
lv = importlib.util.module_from_spec(spec)
sys.modules["um_retrieval_live"] = lv
spec.loader.exec_module(lv)
rt, um, client = lv.rt, lv.um, lv.client
LIVE = json.loads((lv.FIX / "behavioral_live.json").read_text())
REGISTRY = json.loads((lv.FIX / "behavioral.json").read_text())
CORPUS = json.loads(um.CORPUS.read_text())
STRUCT = json.loads((lv.FIX / "structured_live.json").read_text())


def refuse(*args, **kwargs):
    raise AssertionError("network used")


class FakeHonchoHttp:
    """Just enough of the Honcho routes: workspaces, peers, sessions, conclusions (create, list, query), flags and queue status."""

    def __init__(self):
        self.workspaces, self.rows, self.log, self.fail_after = {}, [], [], None

    def call(self, method, path, body=None):
        self.log.append((method, path.split("?")[0]))
        if self.fail_after is not None and len(self.log) > self.fail_after:
            raise client.guard.HonchoError("synthetic_failure")
        if path == "/v3/workspaces":
            if set(body) == {"id"}:
                return self.workspaces[body["id"]]
            self.workspaces[body["id"]] = {"id": body["id"], "configuration": body["configuration"]}
            return self.workspaces[body["id"]]
        if path.endswith("/peers") or path.endswith("/sessions"):
            return {}
        if path.endswith("/queue/status"):
            return {"pending_work_units": 0, "in_progress_work_units": 0, "completed_work_units": 0}
        if path.endswith("/conclusions"):
            for c in body["conclusions"]:
                self.rows.append(
                    {"id": f"c{len(self.rows) + 1}", "content": c["content"], "observer_id": c["observer_id"]}
                )
            return {}
        if "/conclusions/list" in path:
            return {"items": list(self.rows), "total": len(self.rows), "pages": 1}
        if path.endswith("/conclusions/query"):
            peer = body["filters"]["observer"]
            q = um.tokens(body["query"])
            scored = []
            for r in self.rows:
                if r["observer_id"] != peer:
                    continue
                env = json.loads(r["content"])
                text = env.get("text", "")
                scored.append((-len(q & um.tokens(text)), r["id"], r))
            return [r for _, _, r in sorted(scored, key=lambda t: (t[0], int(t[1][1:])))[: body["top_k"]]]
        raise AssertionError("unexpected " + path)


def meter_const():
    return {"spent_usd": 0.71262, "calls": 315, "audit_rows": 318, "scope": None}


def honcho(ws=lv.WS_BEHAVIORAL, mode="seed", http=None):
    http = http or FakeHonchoHttp()
    return client.Honcho(lv.Allowlist(http, ws, mode), ws), http


class Offline(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(socket, "socket", refuse)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ev = lv.Evidence(pathlib.Path(self.tmp.name) / "evidence.jsonl")


class Allow(Offline):
    def test_only_the_listed_routes_pass_and_everything_else_is_denied_before_a_call(self):
        h, http = honcho(mode="read")
        w = lv.WS_BEHAVIORAL
        bad = [
            ("PUT", f"/v3/workspaces/{w}", {"configuration": {}}),
            ("DELETE", f"/v3/workspaces/{w}/conclusions/c1", None),
            ("POST", f"/v3/workspaces/{w}/sessions/s1/messages", {"messages": []}),
            ("POST", f"/v3/workspaces/{w}/schedule_dream", {}),
            ("POST", f"/v3/workspaces/{w}/dreams/list", {}),
            ("POST", "/v3/workspaces/personal/conclusions/query", {}),
            ("GET", "/v3/workspaces/personal/queue/status", None),
            ("POST", f"/v3/workspaces/{w}/conclusions", {"conclusions": []}),
            ("POST", f"/v3/workspaces/{w}/peers", {"id": "x"}),
            (
                "POST",
                "/v3/workspaces",
                {
                    "id": w,
                    "configuration": {
                        "reasoning": {"enabled": False},
                        "dream": {"enabled": False},
                        "summary": {"enabled": False},
                    },
                },
            ),
            ("POST", "/v3/workspaces", {"id": "personal"}),
            ("POST", "/v3/workspaces", {"id": w, "x": 1}),
        ]
        for method, path, body in bad:
            with self.assertRaises(lv.Denied, msg=f"{method} {path}"):
                h.http.call(method, path, body)
        self.assertEqual(http.log, [])

    def test_the_free_read_routes_pass(self):
        h, http = honcho(mode="read")
        w = lv.WS_BEHAVIORAL
        http.workspaces[w] = {"configuration": {}}
        h.http.call(
            "POST",
            f"/v3/workspaces/{w}/conclusions/query",
            {"query": "x", "top_k": 1, "filters": {"observer": "t-alpha"}},
        )
        h.http.call("POST", f"/v3/workspaces/{w}/conclusions/list?page=1&size=100", {})
        h.http.call("GET", f"/v3/workspaces/{w}/queue/status")
        h.http.call("POST", "/v3/workspaces", {"id": w})
        self.assertEqual(len(http.log), 4)

    def test_seed_mode_creates_only_the_one_workspace_with_everything_off_and_at_most_24_rows(self):
        h, http = honcho(mode="seed")
        bad_conf = {
            "id": lv.WS_BEHAVIORAL,
            "configuration": {
                "reasoning": {"enabled": True},
                "dream": {"enabled": False},
                "summary": {"enabled": False},
            },
        }
        with self.assertRaises(lv.Denied):
            h.http.call("POST", "/v3/workspaces", bad_conf)
        with self.assertRaises(lv.Denied):
            h.http.call("POST", "/v3/workspaces", {**bad_conf, "id": "personal"})
        h.create_workspace()
        h.create_peers_and_session()
        h.http.call(
            "POST",
            f"/v3/workspaces/{lv.WS_BEHAVIORAL}/conclusions",
            {"conclusions": [{"content": "c", "observer_id": "t-alpha"}] * 24},
        )
        with self.assertRaises(lv.Denied):
            h.http.call(
                "POST",
                f"/v3/workspaces/{lv.WS_BEHAVIORAL}/conclusions",
                {"conclusions": [{"content": "c", "observer_id": "t-alpha"}]},
            )
        self.assertEqual(len(http.rows), 24)


class Gate(unittest.TestCase):
    def jwt(self, ws, life=7200, **extra):
        import base64

        part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
        return ".".join([part({"alg": "x"}), part({"w": ws, "exp": int(time.time()) + life, **extra}), "sig"])

    def env(self, **change):
        env = {
            "UM_LIVE_RELEASE": lv.RELEASE,
            "UM_T6_TOKEN": self.jwt(lv.WS_BEHAVIORAL),
            "UM_T2_TOKEN": self.jwt(lv.WS_STRUCTURED),
        }
        env.update(change)
        return {k: v for k, v in env.items() if v is not None}

    def test_the_gate_opens_only_with_every_condition(self):
        now = time.time()
        for phase in ("seed", "behavioral", "structured"):
            self.assertEqual(lv.gate(phase, self.env(), lv.HOST, now), (True, "open"), phase)
        cases = {
            "not_released": self.env(UM_LIVE_RELEASE="x"),
            "admin_token_present": self.env(HONCHO_ADMIN_TOKEN="a"),
            "token_missing_or_not_jwt": self.env(UM_T6_TOKEN="nope"),
            "token_not_workspace_scoped": self.env(UM_T6_TOKEN=self.jwt("personal")),
            "token_lifetime": self.env(UM_T6_TOKEN=self.jwt(lv.WS_BEHAVIORAL, life=99999)),
        }
        for reason, env in cases.items():
            self.assertEqual(lv.gate("seed", env, lv.HOST, now), (False, reason), reason)
        self.assertEqual(lv.gate("seed", self.env(), "laptop", now), (False, "wrong_host"))
        self.assertEqual(
            lv.gate("seed", self.env(), lv.HOST, now, url="https://x.example"), (False, "url_not_loopback")
        )
        self.assertEqual(
            lv.gate("seed", self.env(UM_T6_TOKEN=self.jwt(lv.WS_BEHAVIORAL, ad=True)), lv.HOST, now)[1],
            "token_not_workspace_scoped",
        )
        same = self.jwt(lv.WS_BEHAVIORAL)
        self.assertEqual(lv.gate("structured", self.env(UM_T2_TOKEN=same, UM_T6_TOKEN=same), lv.HOST, now)[0], False)

    def test_main_refuses_without_the_release_marker_and_makes_no_call(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = lv.main(["--phase", "seed", "--evidence", "/nonexistent-dir-xyz/e.jsonl"], env={}, host=lv.HOST)
        self.assertEqual((code, out.getvalue().strip()), (2, "not_released"))


class EvidenceKept(Offline):
    def test_each_line_is_on_disk_before_the_next_call_so_a_failure_keeps_earlier_results(self):
        h, http = honcho()
        lv.seed(h, LIVE, REGISTRY, self.ev, meter=meter_const)
        before = len(self.ev.rows())
        reader, http2 = honcho(mode="read", http=http)
        http.fail_after = len(http.log) + 40
        with self.assertRaises(client.guard.HonchoError):
            lv.behavioral(reader, LIVE, REGISTRY, self.ev, reps=1, meter=meter_const)
        lines = self.ev.rows()
        self.assertGreater(len(lines), before)
        self.assertTrue(all(r["kind"] in ("seed_start", "seed_done", "behavioral") for r in lines))

    def test_an_unwritable_evidence_file_halts(self):
        ev = lv.Evidence("/dev/null/x/e.jsonl") if False else lv.Evidence(pathlib.Path(self.tmp.name) / "a.jsonl")
        with mock.patch("builtins.open", side_effect=OSError("disk")):
            with self.assertRaises(lv.Halt):
                ev.add(kind="x")


class Seed(Offline):
    def test_the_seed_writes_facts_then_tombstones_that_name_the_ids_honcho_gave(self):
        h, http = honcho()
        ids = lv.seed(h, LIVE, REGISTRY, self.ev, meter=meter_const)
        self.assertEqual((len(http.rows)), 19)
        self.assertEqual(set(ids), set(LIVE["seed"]["facts"]))
        tombs = [json.loads(r["content"]) for r in http.rows if json.loads(r["content"])["kind"] == "tombstone"]
        self.assertEqual(
            {(t["of_ref"], t["why"]) for t in tombs}, {("FAKE-MEM-006", "superseded"), ("FAKE-MEM-011", "revoked")}
        )
        self.assertEqual({t["of"] for t in tombs}, {ids["FAKE-MEM-006"], ids["FAKE-MEM-011"]})
        self.assertEqual(http.workspaces[lv.WS_BEHAVIORAL]["configuration"]["dream"], {"enabled": False})
        done = [r for r in self.ev.rows() if r["kind"] == "seed_done"][0]
        self.assertEqual((done["rows"], done["flags"]), (19, [False, False]))

    def test_a_moving_meter_halts_the_seed(self):
        states = iter([meter_const(), {**meter_const(), "audit_rows": 319}])
        h, _ = honcho()
        with self.assertRaises(lv.Halt):
            lv.seed(h, LIVE, REGISTRY, self.ev, meter=lambda: next(states))
        self.assertTrue(any(r["kind"] == "halt" for r in self.ev.rows()))

    def test_flags_that_are_on_halt_before_any_row_is_written(self):
        h, http = honcho()
        h.get_flags = lambda: (True, False)
        with self.assertRaises(lv.Halt):
            lv.seed(h, LIVE, REGISTRY, self.ev, meter=meter_const)
        self.assertEqual(http.rows, [])


class Compare(Offline):
    def setUp(self):
        super().setUp()
        self.h, self.http = honcho()
        lv.seed(self.h, LIVE, REGISTRY, self.ev, meter=meter_const)
        self.reader, _ = honcho(mode="read", http=self.http)
        lv.behavioral(self.reader, LIVE, REGISTRY, self.ev, reps=2, meter=meter_const)
        self.rows = [r for r in self.ev.rows() if r["kind"] == "behavioral"]

    def row(self, qid, rep=1):
        return next(r for r in self.rows if r["qid"] == qid and r["rep"] == rep)

    def test_every_query_is_recorded_for_every_repetition_with_raw_and_corrected(self):
        self.assertEqual(len(self.rows), len(LIVE["queries"]) * 2)
        r = self.row("L01")
        self.assertEqual(set(r), {"kind", "rep", "qid", "split", "tenant", "raw", "corrected", "candidates", "t"})
        self.assertIn("trace", r["corrected"])

    def test_raw_shows_the_revoked_item_and_the_corrected_read_hides_it(self):
        raw = self.row("L23")["raw"]["refs"]
        self.assertIn("FAKE-MEM-011", raw)
        c = self.row("L23")["corrected"]
        self.assertEqual((c["answers"], c["abstain"]), ([], True))
        self.assertIn({"ref": "FAKE-MEM-011", "decision": "tombstoned"}, c["trace"])

    def test_a_superseded_fact_is_replaced_by_its_correction(self):
        c = self.row("L11")["corrected"]
        self.assertEqual(c["answers"], ["FAKE-MEM-007"])

    def test_canonical_beats_memory_and_the_other_tenant_is_never_shown(self):
        self.assertEqual(self.row("L19")["corrected"]["answers"], ["FAKE-CAN-001"])
        self.assertEqual(self.row("L31")["corrected"]["answers"], [])
        self.assertNotIn("FAKE-MEM-020", self.row("L31")["raw"]["refs"])

    def test_the_summary_reports_both_reads_and_the_floor_sweep(self):
        s = lv.summary(self.ev)
        b = s["behavioral"]
        self.assertEqual(b["reps"], 2)
        for part in ("all", "dev", "heldout"):
            self.assertIn("raw_abstention", b[part])
        self.assertEqual(b["all"]["raw_abstention"], 0.0)  # an unguarded read never abstains
        self.assertEqual(b["all"]["leaks"], 0)
        self.assertEqual(b["all"]["revoked_outputs"], 0)
        self.assertEqual(set(b["floor_sweep"]), {"0.5", "0.6", "0.75", "0.9"})
        self.assertTrue(s["meter_unchanged_every_rep"])
        self.assertEqual(s["meter_checks"], 2)

    def test_a_meter_that_moves_stops_the_run_and_says_so(self):
        states = iter([meter_const(), {**meter_const(), "spent_usd": 0.8}])
        with self.assertRaises(lv.Halt):
            lv.behavioral(self.reader, LIVE, REGISTRY, self.ev, reps=1, meter=lambda: next(states))
        self.assertTrue(any(r["kind"] == "halt" for r in self.ev.rows()))


class Structured(Offline):
    def test_every_arm_is_recorded_on_the_same_questions_and_scored_on_each_denominator(self):
        world = um.World(CORPUS)

        class Graph:
            def answer(self, tenant, text):
                return um.answer_direct(world, tenant, text)

            def deps(self, case):
                return um.deps_graph(world, case)

        memory = rt.NoisyHoncho(world)
        lv.structured(memory, Graph(), STRUCT, CORPUS, self.ev, reps=2, meter=meter_const)
        rows = [r for r in self.ev.rows() if r["kind"] == "structured"]
        self.assertEqual(len(rows), (20 + 10 + 3) * 2)
        out = lv.structured_summary(self.ev.rows(), CORPUS, STRUCT)
        for arm in lv.STRUCT_ARMS:
            self.assertIn(f"{arm}|eligible17", out)
            self.assertIn(f"{arm}|paraphrase10", out)
        self.assertNotIn("honcho_raw|full20", out)
        self.assertEqual(out["combined_v2|eligible17"]["deps"], 3)
        self.assertEqual(out["combined_old|eligible17"]["deps"], 0)
        self.assertEqual(out["combined_v2|eligible17"]["metrics"]["abstention_accuracy"], 1.0)


class Summary(Offline):
    def test_the_summary_phase_reads_only_the_evidence_file(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = lv.main(["--phase", "summary", "--evidence", str(self.ev.path)], env={})
        self.assertEqual(code, 0)
        self.assertIn("evidence_lines", out.getvalue())


if __name__ == "__main__":
    unittest.main()
