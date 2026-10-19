"""LIFE-60: the dormant V7 side of Oracle's synthetic-only fit1 verb, against a fake endpoint that follows the Oracle operator's wire contract and one real loopback socket. No live call, no model, no cutover."""
import json
import re
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

from lifeos.jobs.fit import edge, judge

REQUEST = {"title": "Technical Program Manager", "description": "Run delivery for a synthetic commerce platform.", "profile": {"years": 15}}
BASE, CAPPED = SimpleNamespace(capped=False, exclusion=None), SimpleNamespace(capped=True, exclusion=None)
RECEIPT = {"provider": "openai", "model": "gpt-6-luna", "transport": "gateway", "agent": "fit", "run_ids": ["run-1234567890"], "run_finished": True, "started": "t0", "ended": "t1", "fallback": False,
           "usage": "unavailable", "cost": "unavailable", "deadline_s": 60}
JUDGMENT = json.dumps({"score": 90, "rationale": "Aligned on delivery ownership.", "confidence": "high"})
HOST = "oracle.example"


def reply(sent, **override):
    try:
        body = json.loads(sent.split(b"\n", 1)[1])
    except (IndexError, ValueError):
        body = {"request": {}, "nonce": ""}                                                              # a malformed frame still gets a refusal line from the fake
    out = {"schema": "lifeos.fit.v1", "status": "OK", "request_hash": edge.digest(body["request"]), "nonce": body["nonce"], "fixture": "A", "judgment_raw": JUDGMENT, "receipt": dict(RECEIPT),
           "runs_today": 6, "limit": 20}
    out.update(override)
    return json.dumps(out).encode()


def refusal(sent, reason="NOT_ALLOWLISTED", status="REFUSED"):
    return reply(sent, status=status, reason=reason, judgment_raw=None, receipt=None)


class Endpoint:
    """A fake Oracle: records every frame; the default answer is the OK reply the first time a nonce is seen and a NONCE_REPLAYED refusal after that. b"" = closed with no output."""
    def __init__(self, answer=None):
        self.answer, self.sent, self.nonces = answer, [], set()

    def __call__(self, host, data, **kw):
        self.sent.append(data)
        if self.answer:
            return self.answer(data)
        try:
            nonce = json.loads(data.split(b"\n", 1)[1])["nonce"]
        except (IndexError, ValueError, KeyError):
            return b""
        replayed, _ = nonce in self.nonces, self.nonces.add(nonce)
        return refusal(data, "NONCE_REPLAYED") if replayed else reply(data)


def manifest(request=REQUEST, sha=None):
    path = Path(tempfile.mkdtemp()) / "fixtures.json"
    path.write_text(json.dumps({"fixtures": [{"id": "A", "request": request, "request_sha256": sha or edge.digest(request)}]}))
    return path


ENV = {"IMESSAGE_ALERTS_HOST": HOST, "FIT_EDGE_MODE": "A"}
NEGATIVE = {**ENV, "FIT_EDGE_MODE": "negative"}


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

    def test_the_reader_never_holds_more_than_one_byte_over_the_reply_limit(self):
        server = socket.create_server(("127.0.0.1", 0))

        def serve():
            conn, _ = server.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(b"x" * 20000)
        thread = threading.Thread(target=serve)
        thread.start()
        got = edge.exchange("127.0.0.1", b"fit1 2\n{}", port=server.getsockname()[1], wait=5)
        thread.join()
        server.close()
        self.assertEqual(len(got), edge.MAX_REPLY + 1)

    def test_a_hung_server_is_a_fixed_timeout_code(self):
        server = socket.create_server(("127.0.0.1", 0))
        with self.assertRaises(edge.EdgeError) as ctx:
            edge.exchange("127.0.0.1", b"fit1 2\n{}", port=server.getsockname()[1], wait=0.3)
        server.close()
        self.assertEqual(str(ctx.exception), "FIT_EDGE_TIMEOUT")


class Invoke(unittest.TestCase):
    def test_a_correlated_ok_reply_is_accepted_by_the_unchanged_envelope(self):
        endpoint = Endpoint()
        got = edge.invoke(HOST, REQUEST, BASE, REQUEST["title"], send=endpoint)
        self.assertEqual((got["judgment"], got["request_hash"], len(endpoint.sent)), (judge.Judgment(90, "Go", "Aligned on delivery ownership.", "high"), edge.digest(REQUEST), 1))
        self.assertEqual((got["receipt"]["model"], got["runs_today"], got["limit"]), ("gpt-6-luna", 6, 20))

    def test_the_provider_prefixed_model_name_is_the_same_pinned_model(self):
        got = edge.invoke(HOST, REQUEST, BASE, "t", send=Endpoint(lambda s: reply(s, receipt={**RECEIPT, "model": "openai/gpt-6-luna"})))
        self.assertEqual(got["judgment"].score, 90)

    def test_the_code_owned_caps_still_decide(self):
        capped = edge.invoke(HOST, REQUEST, CAPPED, REQUEST["title"], send=Endpoint())
        self.assertEqual((capped["judgment"].score, capped["judgment"].decision), (40, "No-Go"))        # the family cap is V7's, not the model's

    def test_every_doubt_is_a_fixed_code_and_the_model_is_never_called_again(self):
        cases = {
            "FIT_EDGE_NO_REPLY": lambda s: b"",
            "FIT_EDGE_OFF_CONTRACT": lambda s: b"not json",
            "FIT_EDGE_HASH_MISMATCH": lambda s: reply(s, request_hash="0" * 64),
            "FIT_EDGE_NONCE_MISMATCH": lambda s: reply(s, nonce="replayed"),
            "FIT_EDGE_JUDGMENT_REJECTED": lambda s: reply(s, judgment_raw='{"ok": true}'),
            "FIT_EDGE_REFUSED_NOT_ALLOWLISTED": refusal,
            "FIT_EDGE_REFUSED_NONCE_REPLAYED": lambda s: refusal(s, "NONCE_REPLAYED"),
            "FIT_EDGE_REFUSED_DAILY_LIMIT": lambda s: refusal(s, "DAILY_LIMIT"),
            "FIT_EDGE_REFUSED_COUNTER_UNAVAILABLE": lambda s: refusal(s, "COUNTER_UNAVAILABLE"),
            "FIT_EDGE_REFUSED": lambda s: refusal(s, "free text about Secret Co"),
            "FIT_EDGE_BUSY": lambda s: refusal(s, None, "BUSY"),
            "FIT_EDGE_TIMEOUT": lambda s: reply(s, status="TIMEOUT", judgment_raw=None),
            "FIT_EDGE_TIMEOUT_ORPHAN": lambda s: reply(s, status="TIMEOUT_ORPHAN", judgment_raw=None),
            "FIT_EDGE_MODEL_DRIFT": lambda s: reply(s, status="MODEL_DRIFT", judgment_raw=None),
            "FIT_EDGE_ERROR": lambda s: reply(s, status="ERROR", judgment_raw=None),
            "FIT_EDGE_OVERSIZE": lambda s: refusal(s, None, "OVERSIZE"),
            "FIT_EDGE_OFF_CONTRACT/unknown status": lambda s: reply(s, status="SOMETHING_NEW"),
            "FIT_EDGE_OFF_CONTRACT/no status": lambda s: json.dumps({k: v for k, v in json.loads(reply(s)).items() if k != "status"}).encode(),
            "FIT_EDGE_OFF_CONTRACT/schema": lambda s: reply(s, schema="other"),
            "FIT_EDGE_RECEIPT_REJECTED/fallback": lambda s: reply(s, receipt={**RECEIPT, "fallback": True}),
            "FIT_EDGE_RECEIPT_REJECTED/model": lambda s: reply(s, receipt={**RECEIPT, "model": "gpt-5"}),
            "FIT_EDGE_RECEIPT_REJECTED/provider": lambda s: reply(s, receipt={**RECEIPT, "provider": "anthropic"}),
            "FIT_EDGE_RECEIPT_REJECTED/no receipt": lambda s: reply(s, receipt=None),
            "FIT_EDGE_RECEIPT_REJECTED/no judgment": lambda s: reply(s, judgment_raw=None),
        }
        for label, answer in cases.items():
            endpoint = Endpoint(answer)
            with self.assertRaises(edge.EdgeError) as ctx:
                edge.invoke(HOST, REQUEST, BASE, REQUEST["title"], send=endpoint)
            self.assertEqual(str(ctx.exception), label.split("/")[0], label)
            self.assertEqual(len(endpoint.sent), 1, label)                                              # one request, never a retry
        self.assertNotIn("Secret", str(ctx.exception))

    def test_a_reply_that_is_not_a_json_object_is_off_contract(self):
        for raw in (b"[]", b'"text"', b"1", b"null", b"true", b'[{"schema": "lifeos.fit.v1", "status": "OK"}]'):
            endpoint = Endpoint(lambda s, raw=raw: raw)
            with self.assertRaises(edge.EdgeError) as ctx:
                edge.invoke(HOST, REQUEST, BASE, REQUEST["title"], send=endpoint)
            self.assertEqual((str(ctx.exception), len(endpoint.sent)), ("FIT_EDGE_OFF_CONTRACT", 1), raw)

    def test_an_oversized_reply_is_rejected_before_it_is_parsed(self):
        padded = json.loads(reply(edge.frame(REQUEST, "n")))
        padded["judgment_raw"] = "x" * 5000
        for raw in (b"x" * 4097, json.dumps(padded).encode()):                                         # not JSON, and valid JSON, both over 4 KiB
            with mock.patch.object(edge.json, "loads", side_effect=AssertionError("parsed")):
                with self.assertRaises(edge.EdgeError) as ctx:
                    edge.invoke(HOST, REQUEST, BASE, REQUEST["title"], nonce="n", send=Endpoint(lambda s, raw=raw: raw))
            self.assertEqual(str(ctx.exception), "FIT_EDGE_REPLY_TOO_LARGE")

    def test_a_receipt_must_be_the_complete_oracle_run_record_before_any_judgment_is_accepted(self):
        bad = {"receipt": "x", "provider": {"provider": "anthropic"}, "model": {"model": ["gpt-6-luna"]}, "transport": {"transport": "http"}, "agent": {"agent": "main"},
               "fallback": {"fallback": "false"}, "run_ids": {"run_ids": []}, "run_finished": {"run_finished": False}, "started": {"started": ""}, "ended": {"ended": 5}}
        bad.update({"fallback ": {"fallback": 0}, "fallback  ": {"fallback": None}, "run_ids ": {"run_ids": "run-1"}, "run_ids  ": {"run_ids": [1]}, "run_ids   ": {"run_ids": [""]},
                    "run_ids    ": {"run_ids": ["r"] * 9}, "run_ids     ": {"run_ids": ["r" * 65]}, "run_finished ": {"run_finished": "true"}, "model ": {"model": "gpt-6-luna-mini"}})
        for field, change in bad.items():
            receipt = "x" if field == "receipt" else {**RECEIPT, **change}
            endpoint = Endpoint(lambda s, receipt=receipt: reply(s, receipt=receipt))
            with self.assertRaises(edge.EdgeError) as ctx:
                edge.invoke(HOST, REQUEST, BASE, REQUEST["title"], send=endpoint)
            self.assertEqual((str(ctx.exception), ctx.exception.detail, len(endpoint.sent)), ("FIT_EDGE_RECEIPT_REJECTED", field.strip(), 1), field)
        for missing in ("provider", "model", "transport", "agent", "fallback", "run_ids", "run_finished", "started", "ended"):
            with self.assertRaises(edge.EdgeError) as ctx:
                edge.invoke(HOST, REQUEST, BASE, "t", send=Endpoint(lambda s, missing=missing: reply(s, receipt={k: v for k, v in RECEIPT.items() if k != missing})))
            self.assertEqual((str(ctx.exception), ctx.exception.detail), ("FIT_EDGE_RECEIPT_REJECTED", missing))
        with self.assertRaises(edge.EdgeError) as ctx:
            edge.invoke(HOST, REQUEST, BASE, "t", send=Endpoint(lambda s: reply(s, judgment_raw=None)))
        self.assertEqual(ctx.exception.detail, "judgment_raw")
        extra = {**RECEIPT, "usage": "unavailable", "cost": "unavailable", "deadline_s": 60, "something_new": 1}
        self.assertEqual(edge.invoke(HOST, REQUEST, BASE, "t", send=Endpoint(lambda s: reply(s, receipt=extra)))["judgment"].score, 90)     # extra contract fields do not break a complete record

    def test_an_unhashable_refusal_reason_is_a_plain_refusal_not_a_crash(self):
        with self.assertRaises(edge.EdgeError) as ctx:
            edge.invoke(HOST, REQUEST, BASE, "t", send=Endpoint(lambda s: reply(s, status="REFUSED", reason=["NOT_ALLOWLISTED"], judgment_raw=None, receipt=None)))
        self.assertEqual(str(ctx.exception), "FIT_EDGE_REFUSED")

    def test_no_host_sends_nothing(self):
        endpoint = Endpoint()
        with self.assertRaises(edge.EdgeError):
            edge.invoke("", REQUEST, BASE, "t", send=endpoint)
        self.assertEqual(endpoint.sent, [])


class Stage(unittest.TestCase):
    def test_the_negative_probes_expect_no_model_run_whether_the_server_closes_or_names_a_refusal(self):
        for answer in (lambda s: b"", refusal, lambda s: refusal(s, None, "OVERSIZE")):
            endpoint = Endpoint(answer)
            out = edge.run(0, False, NEGATIVE, endpoint, manifest())
            self.assertEqual((out["status"], out["refused"], out["probes"], out["model_calls_expected"], out["failed"]), ("OK", 5, 5, 0, 0))
        sent = dict(zip(edge.probes(), endpoint.sent))
        self.assertEqual(sent["bad_length"], b"fit1 abc\n")
        self.assertIn(b'"extra":1', sent["unknown_key"])
        self.assertIn(b"(modified)", sent["modified_or_unlisted"])                                      # the real fixture with its title changed, so it cannot be on the allowlist
        self.assertNotIn(b"(modified)", edge.frame(REQUEST, "n"))

    def test_an_ok_reply_or_a_hang_to_a_probe_is_a_failure(self):
        out = edge.run(0, False, NEGATIVE, Endpoint(reply), manifest())
        self.assertEqual((out["status"], out["unexpected_reply"], out["failed"]), ("FIT_EDGE_PROBE_FAILED", 5, 1))
        out = edge.run(0, False, NEGATIVE, Endpoint(lambda s: b"something"), manifest())
        self.assertEqual((out["status"], out["unexpected_reply"], out["failed"]), ("FIT_EDGE_PROBE_FAILED", 5, 1))

        def hang(s):
            raise edge.EdgeError("FIT_EDGE_TIMEOUT")
        out = edge.run(0, False, NEGATIVE, Endpoint(hang), manifest())
        self.assertEqual((out["status"], out["hung"], out["failed"]), ("FIT_EDGE_PROBE_FAILED", 5, 1))

    def test_a_probe_or_replay_answer_that_is_not_an_object_or_is_oversized_is_not_a_refusal(self):
        for raw in (b"[]", b'"REFUSED"', b"null", b'{"status":"REFUSED","pad":"' + b"x" * 5000 + b'"}'):
            self.assertFalse(edge.refused(raw), raw[:20])
        self.assertTrue(edge.refused(b"") and edge.refused(b'{"status":"REFUSED"}'))
        out = edge.run(0, False, NEGATIVE, Endpoint(lambda s: b"[]"), manifest())
        self.assertEqual((out["status"], out["unexpected_reply"]), ("FIT_EDGE_PROBE_FAILED", 5))

    def test_the_stage_names_the_receipt_field_that_failed(self):
        out = edge.run(0, False, ENV, Endpoint(lambda s: reply(s, receipt={**RECEIPT, "run_finished": False})), manifest())
        self.assertEqual((out["status"], out["detail"], out["failed"]), ("FIT_EDGE_RECEIPT_REJECTED", "run_finished", 1))

    def test_a_fixture_run_is_one_invocation_then_a_replay_that_must_be_refused(self):
        endpoint = Endpoint()
        out = edge.run(0, False, ENV, endpoint, manifest())
        self.assertEqual((out["status"], out["failed"], len(endpoint.sent)), ("OK", 0, 2))
        self.assertEqual(endpoint.sent[0], endpoint.sent[1])                                              # the replay is the same frame, same nonce
        self.assertEqual((out["provider"], out["model"], out["fallback"], out["agent"], out["usage"], out["cost"], out["score"], out["decision"], out["confidence"], out["nonce_correlated"],
                          out["replay_refused"], out["runs_today"], out["limit"], out["run_ids"]),
                         ("openai", "gpt-6-luna", False, "fit", "unavailable", "unavailable", 90, "Go", "high", True, True, 6, 20, ["run-12345678"]))
        self.assertEqual(out["request_hash"], edge.digest(REQUEST)[:8])

    def test_a_replay_the_server_accepts_is_a_failure_that_names_it(self):
        out = edge.run(0, False, ENV, Endpoint(reply), manifest())
        self.assertEqual((out["status"], out["failed"]), ("FIT_EDGE_REPLAY_ACCEPTED", 1))

    def test_a_failed_fixture_run_is_red_and_is_not_retried_or_replayed(self):
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


class CommittedFixture(unittest.TestCase):
    def test_the_oracle_operators_export_is_committed_unchanged_synthetic_and_hash_checked(self):
        data = json.loads(edge.MANIFEST.read_text())
        self.assertEqual((data["schema"], [f["id"] for f in data["fixtures"]]), ("lifeos.fit.edge_fixtures.v1", ["A"]))
        fixture = data["fixtures"][0]
        self.assertEqual(edge.digest(fixture["request"]), fixture["request_sha256"])                      # the canonicalisation rule matches Oracle's
        self.assertEqual(fixture["request_sha256"], "26045e94d1d3a825e8fa2835f038175826df5280fd5f09801d68f7ea2bf84069")
        self.assertIn("SYNTHETIC", fixture["request"]["profile"]["notice"])
        self.assertEqual(set(fixture["request"]), {"title", "description", "profile"})                    # exactly what judge.request() produces
        self.assertNotRegex(edge.MANIFEST.read_text(), r"@|https?://|\b\d{3}[-.]\d{3}[-.]\d{4}\b")        # no address, link or number


class Dormant(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[3]

    def test_only_the_manual_workflow_runs_it_and_no_tick_scheduler_or_live_path_reaches_it(self):
        mentions = [p.name for p in (self.ROOT / ".github/workflows").glob("*.y*ml") if "fit-edge-test" in p.read_text()]
        self.assertEqual(mentions, ["fit-edge-test.yml"])
        text = (self.ROOT / ".github/workflows/fit-edge-test.yml").read_text()
        self.assertNotRegex(text, r"(?m)^\s*(schedule|workflow_run|push|pull_request\w*|repository_dispatch):")
        self.assertNotIn("--live", text)
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", text))), ["LIFEOS_BWS_RUNTIME_TOKEN"])
        self.assertIn("inputs.confirm == 'SYNTHETIC'", text)
        importers = [str(p.relative_to(self.ROOT)) for p in (self.ROOT / "lifeos").rglob("*.py") if p.name != "edge.py" and ("fit.edge" in p.read_text() or "fit import edge" in p.read_text())]
        self.assertEqual(sorted(importers), ["lifeos/jobs/fit/cloud.py", "lifeos/run.py"])               # the stage table, and the D214 shadow (off unless V7_CLOUD_FIT is set)
        workflows = [p.name for p in (self.ROOT / ".github/workflows").glob("*.y*ml") if "V7_CLOUD_FIT" in p.read_text()]
        self.assertEqual(workflows, ["hourly.yml"])                                                        # D214: only the tick reads the switch, from the repository variable
        hourly = (self.ROOT / ".github/workflows/hourly.yml").read_text()
        self.assertEqual(re.findall(r"V7_CLOUD_FIT: (\S.*?\}\})", hourly), ["${{ vars.V7_CLOUD_FIT || 'off' }}"])     # off unless Jim sets the variable (G6); never a dispatch input or a literal
        self.assertEqual(len(re.findall(r"vars\.V7_CLOUD_FIT == 'shadow'", hourly)), 1)                    # the tailnet join runs only in shadow


if __name__ == "__main__":
    unittest.main()
