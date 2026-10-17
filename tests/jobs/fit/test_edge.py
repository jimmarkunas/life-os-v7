"""LIFE-60: the dormant V7 side of Oracle's synthetic-only fit1 verb, against a fake endpoint and one real loopback socket. No live call, no model, no cutover."""
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from lifeos.jobs.fit import edge, judge

REQUEST = {"title": "Technical Program Manager", "description": "Run delivery for a synthetic commerce platform.", "profile": {"years": 15}}
BASE, CAPPED = SimpleNamespace(capped=False, exclusion=None), SimpleNamespace(capped=True, exclusion=None)
RECEIPT = {"provider": "openai", "model": "gpt-6-luna", "transport": "gateway", "run_id": "run-1234567890", "started": "t0", "ended": "t1", "status": "ok", "fallback": False, "usage": "unavailable"}
JUDGMENT = json.dumps({"score": 90, "rationale": "Aligned on delivery ownership.", "confidence": "high"})
HOST = "oracle.example"


def reply(sent, **override):
    body = json.loads(sent.split(b"\n", 1)[1])
    out = {"schema": "lifeos.fit.v1", "request_hash": edge.digest(body["request"]), "nonce": body["nonce"], "judgment_raw": JUDGMENT, "receipt": dict(RECEIPT)}
    out.update(override)
    return json.dumps(out).encode()


class Endpoint:
    """A fake Oracle: records every frame and answers with `answer(sent)` (b"" = closed with no output)."""
    def __init__(self, answer=reply):
        self.answer, self.sent = answer, []

    def __call__(self, host, data, **kw):
        self.sent.append(data)
        return self.answer(data)


def manifest(request=REQUEST, sha=None, **extra):
    path = Path(tempfile.mkdtemp()) / "fixtures.json"
    path.write_text(json.dumps({"fixtures": {"A": {"title": request["title"], "request": request, "sha256": sha or edge.digest(request), **extra}}}))
    return path


ENV = {"IMESSAGE_ALERTS_HOST": HOST, "FIT_EDGE_MODE": "A"}


class Transport(unittest.TestCase):
    def test_the_frame_is_fit1_then_exactly_the_request_and_nonce_and_never_more_than_16_kib(self):
        data = edge.frame(REQUEST, "n" * 32)
        head, body = data.split(b"\n", 1)
        self.assertEqual((head, json.loads(body)), (b"fit1 %d" % len(body), {"request": REQUEST, "nonce": "n" * 32}))
        with self.assertRaises(edge.EdgeError) as ctx:
            edge.frame({**REQUEST, "description": "x" * 17000}, "n")
        self.assertEqual(str(ctx.exception), "FIT_EDGE_REQUEST_TOO_LARGE")

    def test_a_real_socket_round_trip_sends_the_frame_and_reads_to_the_close(self):
        server = socket.create_server(("127.0.0.1", 0))
        got = []

        def serve():
            conn, _ = server.accept()
            with conn:
                data = b""
                while not data.endswith(b"}"):
                    data += conn.recv(4096)
                got.append(data)
                conn.sendall(b'{"ok":1}\n')
        thread = threading.Thread(target=serve)
        thread.start()
        data = edge.frame(REQUEST, "n" * 32)
        self.assertEqual(edge.exchange("127.0.0.1", data, port=server.getsockname()[1], wait=5), b'{"ok":1}\n')
        thread.join()
        server.close()
        self.assertEqual(got, [data])
        self.assertEqual(edge.exchange("127.0.0.1", data, port=1, wait=1), b"")                       # nothing listening: closed, no output

    def test_a_hung_server_is_a_fixed_timeout_code(self):
        server = socket.create_server(("127.0.0.1", 0))
        with self.assertRaises(edge.EdgeError) as ctx:
            edge.exchange("127.0.0.1", b"fit1 2\n{}", port=server.getsockname()[1], wait=0.3)
        server.close()
        self.assertEqual(str(ctx.exception), "FIT_EDGE_TIMEOUT")


class Invoke(unittest.TestCase):
    def test_a_correlated_reply_is_accepted_by_the_unchanged_envelope(self):
        endpoint = Endpoint()
        got = edge.invoke(HOST, REQUEST, BASE, REQUEST["title"], send=endpoint)
        self.assertEqual((got["judgment"], got["request_hash"], len(endpoint.sent)), (judge.Judgment(90, "Go", "Aligned on delivery ownership.", "high"), edge.digest(REQUEST), 1))
        self.assertEqual(got["receipt"]["model"], "gpt-6-luna")

    def test_the_code_owned_caps_still_decide(self):
        capped = edge.invoke(HOST, REQUEST, CAPPED, REQUEST["title"], send=Endpoint())
        self.assertEqual((capped["judgment"].score, capped["judgment"].decision), (40, "No-Go"))        # the family cap is V7's, not the model's

    def test_every_doubt_is_a_fixed_code_and_the_model_is_never_called_again(self):
        bad_receipts = {"fallback": {"fallback": True}, "model": {"model": "gpt-5"}, "provider": {"provider": "anthropic"}, "status": {"status": "failed"}}
        cases = {
            "FIT_EDGE_NO_REPLY": lambda s: b"",
            "FIT_EDGE_OFF_CONTRACT": lambda s: b"not json",
            "FIT_EDGE_HASH_MISMATCH": lambda s: reply(s, request_hash="0" * 64),
            "FIT_EDGE_NONCE_MISMATCH": lambda s: reply(s, nonce="replayed"),
            "FIT_EDGE_JUDGMENT_REJECTED": lambda s: reply(s, judgment_raw='{"ok": true}'),
        }
        cases.update({f"FIT_EDGE_RECEIPT_REJECTED/{k}": (lambda s, v=v: reply(s, receipt={**RECEIPT, **v})) for k, v in bad_receipts.items()})
        cases["FIT_EDGE_OFF_CONTRACT/schema"] = lambda s: reply(s, schema="other")
        cases["FIT_EDGE_OFF_CONTRACT/receipt"] = lambda s: reply(s, receipt="x")
        for label, answer in cases.items():
            endpoint = Endpoint(answer)
            with self.assertRaises(edge.EdgeError) as ctx:
                edge.invoke(HOST, REQUEST, BASE, REQUEST["title"], send=endpoint)
            self.assertEqual(str(ctx.exception), label.split("/")[0], label)
            self.assertEqual(len(endpoint.sent), 1, label)                                              # one request, never a retry

    def test_no_host_sends_nothing(self):
        endpoint = Endpoint()
        with self.assertRaises(edge.EdgeError):
            edge.invoke("", REQUEST, BASE, "t", send=endpoint)
        self.assertEqual(endpoint.sent, [])


class Stage(unittest.TestCase):
    def test_the_negative_probes_expect_no_output_and_no_model_call(self):
        endpoint = Endpoint(lambda s: b"")
        out = edge.run(0, False, {**ENV, "FIT_EDGE_MODE": "negative"}, endpoint)
        self.assertEqual((out["status"], out["refused"], out["probes"], out["model_calls_expected"], out["failed"]), ("OK", 5, 5, 0, 0))
        sent = {k: v for k, v in zip(edge.probes(), endpoint.sent)}
        self.assertEqual(sent["bad_length"], b"fit1 abc\n")
        self.assertIn(b'"extra":1', sent["unknown_key"])
        for data in endpoint.sent:
            self.assertNotIn(b"profile\":{\"years\":15", data)                                           # nothing but the fixed synthetic probes

    def test_an_unexpected_reply_or_a_hang_to_a_probe_is_a_failure(self):
        out = edge.run(0, False, {**ENV, "FIT_EDGE_MODE": "negative"}, Endpoint(lambda s: b"something"))
        self.assertEqual((out["status"], out["unexpected_reply"], out["failed"]), ("FIT_EDGE_PROBE_FAILED", 5, 1))

        def hang(s):
            raise edge.EdgeError("FIT_EDGE_TIMEOUT")
        out = edge.run(0, False, {**ENV, "FIT_EDGE_MODE": "negative"}, Endpoint(hang))
        self.assertEqual((out["status"], out["hung"], out["failed"]), ("FIT_EDGE_PROBE_FAILED", 5, 1))

    def test_a_fixture_run_is_one_invocation_with_the_receipt_and_the_judgment_reported(self):
        endpoint = Endpoint()
        out = edge.run(0, False, ENV, endpoint, manifest())
        self.assertEqual((out["status"], out["failed"], len(endpoint.sent)), ("OK", 0, 1))
        self.assertEqual((out["provider"], out["model"], out["fallback"], out["usage"], out["score"], out["decision"], out["confidence"], out["nonce_correlated"]),
                         ("openai", "gpt-6-luna", False, "unavailable", 90, "Go", "high", True))
        self.assertEqual((out["request_hash"], out["run_id"]), (edge.digest(REQUEST)[:8], "run-12345678"))

    def test_a_failed_fixture_run_is_red_and_is_not_retried(self):
        endpoint = Endpoint(lambda s: b"")
        out = edge.run(0, False, ENV, endpoint, manifest())
        self.assertEqual((out["status"], out["failed"], len(endpoint.sent)), ("FIT_EDGE_NO_REPLY", 1, 1))

    def test_nothing_leaves_v7_without_a_matching_manifest_entry(self):
        for label, path, status in (("missing file", Path("/nonexistent/fixtures.json"), "FIT_EDGE_FIXTURE_MISSING"), ("unknown label", manifest(), "FIT_EDGE_FIXTURE_MISSING"),
                                    ("modified fixture", manifest(sha="0" * 64), "FIT_EDGE_FIXTURE_HASH")):
            endpoint = Endpoint()
            env = {**ENV, "FIT_EDGE_MODE": "Z" if label == "unknown label" else "A"}
            self.assertEqual(edge.run(0, False, env, endpoint, path)["status"], status, label)
            self.assertEqual(endpoint.sent, [], label)
        self.assertEqual(edge.run(0, False, {"FIT_EDGE_MODE": "A"}, Endpoint(), manifest())["status"], "FIT_EDGE_NOT_CONFIGURED")           # no host
        self.assertEqual(edge.run(0, False, {"IMESSAGE_ALERTS_HOST": HOST}, Endpoint(), manifest())["status"], "FIT_EDGE_NOT_CONFIGURED")   # no mode

    def test_the_committed_manifest_holds_only_hash_checked_fixtures_when_it_exists(self):
        if edge.MANIFEST.exists():
            for label, fixture in json.loads(edge.MANIFEST.read_text())["fixtures"].items():
                self.assertEqual(edge.digest(fixture["request"]), fixture["sha256"], label)


class Dormant(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[3]

    def test_only_the_manual_workflow_runs_it_and_no_tick_scheduler_or_live_path_reaches_it(self):
        import re
        mentions = [p.name for p in (self.ROOT / ".github/workflows").glob("*.y*ml") if "fit-edge-test" in p.read_text()]
        self.assertEqual(mentions, ["fit-edge-test.yml"])
        text = (self.ROOT / ".github/workflows/fit-edge-test.yml").read_text()
        self.assertNotRegex(text, r"(?m)^\s*(schedule|workflow_run|push|pull_request\w*|repository_dispatch):")
        self.assertNotIn("--live", text)
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", text))), ["LIFEOS_BWS_RUNTIME_TOKEN"])
        self.assertIn("inputs.confirm == 'SYNTHETIC'", text)
        importers = [str(p.relative_to(self.ROOT)) for p in (self.ROOT / "lifeos").rglob("*.py") if p.name != "edge.py" and ("fit.edge" in p.read_text() or "fit import edge" in p.read_text())]
        self.assertEqual(importers, ["lifeos/run.py"])                                                    # the stage table only: the hourly pipeline and the Fit stage never import the transport


if __name__ == "__main__":
    unittest.main()
