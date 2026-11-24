"""LIFE-889: the spend guard in front of every Anthropic call. A fake Anthropic and a fake Ollama run on loopback; nothing here touches the network or a real key."""
import http.client
import http.server
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from lifeos.edge.honcho import meter_proxy as mp

REAL_KEY = "sk-ant-TEST-real-key-0123456789"


class Upstream(http.server.BaseHTTPRequestHandler):
    seen = []
    mode = "json"

    def log_message(self, *a):
        return

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Upstream.seen.append({"path": self.path, "key": self.headers.get("x-api-key"), "auth": self.headers.get("Authorization"), "version": self.headers.get("anthropic-version"), "body": body})
        if self.path == "/v1/embeddings":
            data = json.dumps({"data": [{"embedding": [0.1] * 768}], "usage": {"prompt_tokens": 3}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if Upstream.mode == "error":
            data = json.dumps({"type": "error", "error": {"type": "overloaded_error"}}).encode()
            self.send_response(529)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if Upstream.mode == "stream":
            lines = [{"type": "message_start", "message": {"usage": {"input_tokens": 1000, "cache_read_input_tokens": 500, "output_tokens": 1}}},
                     {"type": "message_delta", "usage": {"output_tokens": 200}}, {"type": "message_stop"}]
            data = "".join("event: x\ndata: " + json.dumps(e) + "\n\n" for e in lines).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(data)
            return
        if Upstream.mode == "broken":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", "100000")
            self.end_headers()
            self.wfile.write(b"data: {")
            self.wfile.flush()
            self.close_connection = True
            return
        data = json.dumps({"id": "m", "content": [{"type": "text", "text": "hi"}], "usage": {"input_tokens": 2000, "output_tokens": 100, "cache_creation_input_tokens": 0}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        Upstream.seen, Upstream.mode = [], "json"
        self.up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=lambda: self.up.serve_forever(poll_interval=0.01), daemon=True).start()
        self.addCleanup(self.up.shutdown)
        port = self.up.server_address[1]
        real = http.client.HTTPSConnection
        self.patches = [mock.patch.object(mp.http.client, "HTTPSConnection", lambda host, timeout=None: http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)),
                        mock.patch.object(mp, "OLLAMA", ("127.0.0.1", port))]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        del real
        self.start(0.90)

    def start(self, cap):
        self.ledger = mp.Ledger(state_dir=self.tmp, cap=cap)

        class H(mp.Handler):
            pass
        H.ledger, H.key = self.ledger, REAL_KEY
        self.proxy = mp.Server(("127.0.0.1", 0), H)
        threading.Thread(target=lambda: self.proxy.serve_forever(poll_interval=0.01), daemon=True).start()
        self.addCleanup(self.proxy.shutdown)
        self.port = self.proxy.server_address[1]

    def post(self, path, doc, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("POST", path, json.dumps(doc), {"Content-Type": "application/json", "x-api-key": "dummy-local", **(headers or {})})
        answer = conn.getresponse()
        return answer.status, answer.read()

    def audit(self):
        with open(self.ledger.audit_path) as handle:
            return [json.loads(line) for line in handle if line.strip()]


class Pricing(unittest.TestCase):
    def test_cost_uses_the_real_classes_and_missing_classes_count_zero(self):
        self.assertAlmostEqual(mp.usage_cost({"input_tokens": 90_000}), 0.009)
        self.assertAlmostEqual(mp.usage_cost({"output_tokens": 1_000_000}), 0.50)
        self.assertAlmostEqual(mp.usage_cost({"cache_creation_input_tokens": 80_000, "cache_read_input_tokens": 10_000}), 0.01 + 0.0001)
        self.assertEqual(mp.usage_cost(None), 0.0)

    def test_a_request_over_100k_input_tokens_is_priced_at_the_long_tier(self):
        self.assertAlmostEqual(mp.usage_cost({"input_tokens": 100_000}), 0.01)
        self.assertAlmostEqual(mp.usage_cost({"input_tokens": 100_001}), 100_001 * 0.50 / 1e6)
        self.assertAlmostEqual(mp.usage_cost({"input_tokens": 50_000, "cache_read_input_tokens": 60_000, "output_tokens": 1000}), 50_000 * 0.50 / 1e6 + 60_000 * 0.05 / 1e6 + 1000 * 2.50 / 1e6)
        big = json.dumps({"max_tokens": 1000, "messages": [{"role": "user", "content": "x" * 400_000}]}).encode()
        worst, tokens_in, _ = mp.estimate(big)
        self.assertGreater(tokens_in, 100_000)
        self.assertAlmostEqual(worst, tokens_in * 0.50 / 1e6 + 1000 * 2.50 / 1e6)

    def test_the_measured_live_run_costs_about_six_percent_of_the_old_dearest_rate_figure(self):
        new, written, read, out = 157_338, 72_421, 396_850, 27_797                                                          # the live meter's 248 calls on Nov 24 2026 (token counts only; every call is under 100,000 tokens)
        real = mp.usd(new, "input") + mp.usd(written, "cache_write") + mp.usd(read, "cache_read") + mp.usd(out, "output")
        self.assertAlmostEqual(real, 0.0427, delta=0.0427 * 0.2)
        dearest = (157_338 + 72_421 + 396_850) * 1.00 / 1e6 + 27_797 * 2.50 / 1e6
        self.assertAlmostEqual(real / dearest, 0.06, delta=0.012)

    def test_worst_case_is_input_bytes_over_three_plus_max_tokens_at_the_output_price(self):
        body = json.dumps({"max_tokens": 1000, "messages": [{"role": "user", "content": "x" * 3000}]}).encode()
        worst, tokens_in, max_tokens = mp.estimate(body)
        self.assertEqual(max_tokens, 1000)
        self.assertAlmostEqual(worst, tokens_in * 0.10 / 1e6 + 1000 * 0.50 / 1e6)
        self.assertGreater(tokens_in, 1000)
        self.assertEqual(mp.estimate(b"not json")[2], mp.DEFAULT_MAX_TOKENS)

    def test_stream_usage_takes_message_start_then_the_final_output_count(self):
        raw = ("data: " + json.dumps({"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 1}}}) + "\n\n"
               + "data: " + json.dumps({"type": "message_delta", "usage": {"output_tokens": 77}}) + "\n\n").encode()
        self.assertEqual(mp.parse_usage(raw, True), {"input_tokens": 10, "output_tokens": 77})
        self.assertEqual(mp.parse_usage(b"garbage", False), {})


class Metering(Case):
    def test_a_message_is_forwarded_with_the_real_key_only_and_settled_at_actual_usage(self):
        status, body = self.post("/v1/messages", {"model": "claude-haiku-5-5", "max_tokens": 200, "messages": [{"role": "user", "content": "hi"}]}, {"Authorization": "Bearer dummy"})
        self.assertEqual(status, 200)
        sent = Upstream.seen[0]
        self.assertEqual((sent["path"], sent["key"], sent["auth"], sent["version"]), ("/v1/messages", REAL_KEY, None, "2023-06-01"))
        self.assertAlmostEqual(self.ledger.spent, 2000 * 0.10 / 1e6 + 100 * 0.50 / 1e6)
        self.assertEqual((self.ledger.reserved, self.ledger.calls), (0.0, 1))
        row = self.audit()[-1]
        self.assertEqual((row["route"], row["status"], row["input_tokens"], row["output_tokens"]), ("/v1/messages", 200, 2000, 100))
        self.assertNotIn("hi", json.dumps(row))
        self.assertNotIn(REAL_KEY, json.dumps(row) + open(self.ledger.path).read())

    def test_a_stream_is_passed_through_and_counted_from_its_events(self):
        Upstream.mode = "stream"
        status, body = self.post("/v1/messages", {"max_tokens": 300, "stream": True, "messages": []})
        self.assertEqual(status, 200)
        self.assertIn(b"message_delta", body)
        self.assertAlmostEqual(self.ledger.spent, 1000 * 0.10 / 1e6 + 500 * 0.01 / 1e6 + 200 * 0.50 / 1e6)

    def test_a_request_that_would_pass_the_cap_is_refused_and_nothing_is_forwarded(self):
        self.start(0.001)
        status, body = self.post("/v1/messages", {"max_tokens": 4096, "messages": []})
        self.assertEqual(status, 400)
        self.assertIn(b"cap reached", body)
        self.assertEqual(Upstream.seen, [])
        self.assertEqual(self.ledger.spent, 0.0)
        self.assertEqual(self.audit()[-1]["refused"], "cap reached or stopped")

    def test_spend_stops_the_next_request_when_the_cap_is_reached_and_survives_a_restart(self):
        self.start(0.0010)
        big = {"max_tokens": 100, "messages": [{"role": "user", "content": "x" * 6100}]}          # the fake replies with 2000 input tokens: the estimate covers them
        for _ in range(10):
            self.post("/v1/messages", big)
        self.assertLessEqual(self.ledger.spent, 0.0010)
        calls = len(Upstream.seen)
        self.assertLess(calls, 10)
        again = mp.Ledger(state_dir=self.tmp, cap=0.0010)
        self.assertAlmostEqual(again.spent, self.ledger.spent)
        self.assertFalse(again.reserve(0.01))

    def test_complete_means_a_whole_json_body_or_a_stream_with_its_stop_event(self):
        self.assertTrue(mp.complete(b'{"a": 1}', False))
        self.assertFalse(mp.complete(b'{"a": ', False))
        self.assertTrue(mp.complete(b'data: {"type": "message_stop"}', True))
        self.assertFalse(mp.complete(b'data: {"type": "message_delta"}', True))

    def test_a_reply_that_breaks_off_keeps_the_whole_reservation(self):
        Upstream.mode = "broken"
        body = {"max_tokens": 1000, "messages": []}
        worst = mp.estimate(json.dumps(body).encode())[0]
        try:
            self.post("/v1/messages", body)
        except (http.client.HTTPException, OSError):
            pass
        for _ in range(50):
            if self.ledger.calls:
                break
            threading.Event().wait(0.05)
        self.assertEqual(self.ledger.calls, 1)
        self.assertTrue(self.audit()[-1]["broke"])
        self.assertGreaterEqual(self.ledger.spent, worst * 0.9)

    def test_an_upstream_error_is_passed_on_and_not_charged(self):
        Upstream.mode = "error"
        status, _ = self.post("/v1/messages", {"max_tokens": 50, "messages": []})
        self.assertEqual(status, 529)
        self.assertEqual(self.ledger.spent, 0.0)
        self.assertEqual(self.ledger.reserved, 0.0)

    def test_a_stop_file_refuses_everything_even_a_free_count(self):
        open(self.ledger.stop_path, "w").close()
        self.assertEqual(self.post("/v1/messages", {"max_tokens": 1, "messages": []})[0], 400)
        self.assertEqual(self.post("/v1/messages/count_tokens", {"messages": []})[0], 400)
        self.assertEqual(Upstream.seen, [])


class FailClosed(Case):
    """LIFE-867: a budget that cannot be verified, or credits that have ended, stop every Anthropic request; embeddings stay free; memory is never touched."""
    MSG = {"max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]}

    def test_an_unreadable_ledger_refuses_every_request_and_nothing_is_forwarded(self):
        with open(self.ledger.path, "w") as handle:
            handle.write("not json")
        self.start(0.90)
        status, body = self.post("/v1/messages", self.MSG)
        self.assertEqual(status, 400)
        self.assertIn(b"budget cannot be verified", body)
        self.assertEqual(self.post("/v1/messages/count_tokens", {"messages": []})[0], 400)
        self.assertEqual([s for s in Upstream.seen if s["path"] != "/v1/embeddings"], [])
        self.assertEqual(self.post("/v1/embeddings", {"input": "x"})[0], 200)                    # the local model is free and still answers

    def test_a_missing_ledger_beside_an_audit_file_with_spend_is_unverified_but_a_fresh_start_is_fine(self):
        self.post("/v1/messages", self.MSG)
        os.remove(self.ledger.path)
        self.assertEqual(mp.Ledger(state_dir=self.tmp, cap=0.90).unverified, "ledger missing while the audit file shows past spend")
        with tempfile.TemporaryDirectory() as fresh:
            self.assertEqual(mp.Ledger(state_dir=fresh, cap=0.90).unverified, "")

    def test_a_ledger_that_cannot_be_written_stops_the_next_request(self):
        with mock.patch.object(mp.Ledger, "save", side_effect=OSError("disk full")):
            self.assertEqual(self.post("/v1/messages", self.MSG)[0], 200)                         # this reply was already on its way
        self.assertEqual(self.ledger.unverified, "ledger unwritable")
        self.assertEqual(self.post("/v1/messages", self.MSG)[0], 400)

    def test_jims_optional_hard_stop_refuses_everything_and_health_says_why(self):
        with mock.patch.object(mp, "STOP_AFTER", self.ledger.clock() + 10):
            self.ledger.clock = lambda: mp.STOP_AFTER + 1
            status, body = self.post("/v1/messages", self.MSG)
            self.assertEqual((status, b"promotional credits ended" in body), (400, True))
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            conn.request("GET", "/health")
            self.assertEqual(json.loads(conn.getresponse().read())["blocked"], "promotional credits ended: spend would be paid")
            self.assertEqual([s for s in Upstream.seen if s["path"] != "/v1/embeddings"], [])

    def test_the_first_budget_refusal_leaves_one_exhausted_file_and_never_overwrites_it(self):
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "EXHAUSTED")))
        open(self.ledger.stop_path, "w").close()
        self.post("/v1/messages", self.MSG)
        path = os.path.join(self.tmp, "EXHAUSTED")
        first = open(path).read()
        self.assertEqual(sorted(json.loads(first)), ["epoch", "reason", "window"])
        self.post("/v1/messages", self.MSG)
        self.assertEqual(open(path).read(), first)

    def test_a_healthy_ledger_is_unaffected(self):
        self.assertEqual(self.post("/v1/messages", self.MSG)[0], 200)
        self.assertEqual(self.ledger.why_blocked(), "")


class Routes(Case):
    def test_embeddings_go_to_the_local_model_with_no_key_and_no_charge(self):
        status, body = self.post("/v1/embeddings", {"model": "nomic-embed-text", "input": "x"})
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["data"][0]["embedding"]), 768)
        self.assertEqual((Upstream.seen[0]["path"], Upstream.seen[0]["key"]), ("/v1/embeddings", None))
        self.assertEqual(self.ledger.spent, 0.0)

    def test_count_tokens_is_forwarded_unmetered_and_other_paths_are_404(self):
        self.assertEqual(self.post("/v1/messages/count_tokens", {"messages": []})[0], 200)
        self.assertEqual(Upstream.seen[0]["key"], REAL_KEY)
        self.assertEqual(self.ledger.spent, 0.0)
        for path in ("/v1/models", "/v1/complete", "/anything"):
            self.assertEqual(self.post(path, {})[0], 404, path)

    def test_health_reports_spend_and_cap_and_an_oversize_body_is_refused(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/health")
        out = json.loads(conn.getresponse().read())
        self.assertEqual((out["ok"], out["cap_usd"], out["spent_usd"]), (True, 0.9, 0.0))
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/v1/messages", b"", {"Content-Length": str(mp.MAX_BODY + 1)})
        self.assertEqual(conn.getresponse().status, 400)


class Source(unittest.TestCase):
    def test_the_program_has_no_shell_and_logs_no_bodies(self):
        with open(mp.__file__) as handle:
            source = handle.read()
        for banned in ("shell=True", "os.system", "subprocess", "print("):
            self.assertNotIn(banned, source, banned)
        self.assertEqual(os.path.basename(mp.__file__), "meter_proxy.py")


if __name__ == "__main__":
    unittest.main()


class MonthlyWindow(Case):
    """LIFE-950: the cap is per window (5th, 6:00 PM CT to the next 5th, 6:00 PM CT); the next window starts by itself; a window never rescues a budget that cannot be verified."""
    MSG = {"max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]}

    def at(self, year, month, day, hour, minute=0):
        from datetime import datetime as D
        return int(D(year, month, day, hour, minute, tzinfo=mp.CENTRAL).timestamp())

    def test_the_window_runs_from_the_5th_at_6pm_ct_to_the_next_5th_at_6pm_ct(self):
        for moment, start, end in ((self.at(2026, 11, 23, 12), self.at(2026, 11, 5, 18), self.at(2026, 12, 5, 18)),
                                   (self.at(2026, 12, 5, 17, 59), self.at(2026, 11, 5, 18), self.at(2026, 12, 5, 18)),
                                   (self.at(2026, 12, 5, 18), self.at(2026, 12, 5, 18), self.at(2027, 1, 5, 18)),
                                   (self.at(2027, 1, 2, 9), self.at(2026, 12, 5, 18), self.at(2027, 1, 5, 18)),
                                   (self.at(2027, 3, 5, 17, 59), self.at(2027, 2, 5, 18), self.at(2027, 3, 5, 18)),
                                   (self.at(2027, 3, 5, 18), self.at(2027, 3, 5, 18), self.at(2027, 4, 5, 18))):          # across the March 2027 daylight-time change
            self.assertEqual(mp.window(moment), (start, end))
        self.assertEqual(self.at(2026, 12, 5, 18), 1796515200)                                          # Dec 6 2026 00:00 UTC: the boundary the old stop date named

    def test_the_new_window_starts_at_zero_by_itself_and_the_old_one_is_archived(self):
        self.start(25)
        self.ledger.clock = lambda: self.at(2026, 11, 23, 12)
        self.ledger.start, self.ledger.end = mp.window(self.ledger.clock())
        self.ledger.spent, self.ledger.calls = 24.99, 40
        self.ledger.save()
        self.assertEqual(self.post("/v1/messages", self.MSG)[0], 200)                                     # inside the old window: spend is tiny but the cap is not
        self.ledger.clock = lambda: self.at(2026, 12, 5, 18, 1)
        self.assertEqual(self.ledger.why_blocked(), "")
        self.assertEqual((self.ledger.spent, self.ledger.calls, self.ledger.start), (0.0, 0, self.at(2026, 12, 5, 18)))
        saved = json.load(open(self.ledger.path))
        self.assertEqual((saved["window_start"], saved["window_end"], saved["spent_usd"]), (self.at(2026, 12, 5, 18), self.at(2027, 1, 5, 18), 0.0))
        old = [json.loads(line) for line in open(self.ledger.windows_path)]
        self.assertEqual((len(old), old[0]["start"], old[0]["end"], old[0]["cap_usd"]), (1, self.at(2026, 11, 5, 18), self.at(2026, 12, 5, 18), 25))
        self.assertGreater(old[0]["spent_usd"], 24.99)

    def test_over_the_cap_stops_inference_until_the_next_window_then_resumes(self):
        self.start(25)
        self.ledger.clock = lambda: self.at(2026, 11, 23, 12)
        self.ledger.start, self.ledger.end = mp.window(self.ledger.clock())
        self.ledger.spent = 24.999
        self.assertEqual(self.post("/v1/messages", {"max_tokens": 4000, "messages": [{"role": "user", "content": "hi"}]})[0], 400)
        self.assertEqual([s for s in Upstream.seen if s["path"] != "/v1/embeddings"], [])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "EXHAUSTED")))
        self.ledger.clock = lambda: self.at(2026, 12, 5, 18, 0)
        self.assertEqual(self.post("/v1/messages", self.MSG)[0], 200)

    def test_the_alert_file_is_written_once_per_window_not_once_forever(self):
        self.ledger.clock = lambda: self.at(2026, 11, 23, 12)
        self.ledger.start, self.ledger.end = mp.window(self.ledger.clock())
        open(self.ledger.stop_path, "w").close()
        self.post("/v1/messages", self.MSG)
        path = os.path.join(self.tmp, "EXHAUSTED")
        first = open(path).read()
        self.post("/v1/messages", self.MSG)
        self.assertEqual(open(path).read(), first)
        self.ledger.clock = lambda: self.at(2026, 12, 5, 18, 5)
        self.post("/v1/messages", self.MSG)                                                                # STOP still stands: a refusal in the NEW window writes a new alert
        self.assertEqual(json.loads(open(path).read())["window"], self.at(2026, 12, 5, 18))

    def test_the_stop_file_survives_a_new_window(self):
        self.ledger.clock = lambda: self.at(2026, 11, 23, 12)
        self.ledger.start, self.ledger.end = mp.window(self.ledger.clock())
        open(self.ledger.stop_path, "w").close()
        self.ledger.clock = lambda: self.at(2026, 12, 6, 12)
        self.assertEqual(self.ledger.why_blocked(), "cap reached or stopped")

    def test_an_unverifiable_ledger_is_not_reset_by_a_new_window(self):
        with open(self.ledger.path, "w") as handle:
            handle.write("not json")
        ledger = mp.Ledger(state_dir=self.tmp, cap=25, clock=lambda: self.at(2026, 12, 6, 12))
        self.assertEqual(ledger.why_blocked(), "budget cannot be verified: ledger unreadable")
        self.assertFalse(os.path.exists(ledger.windows_path))

    def test_a_ledger_from_before_the_window_change_is_adopted_into_the_current_window_with_its_spend(self):
        with open(self.ledger.path, "w") as handle:
            json.dump({"spent_usd": 0.40692, "calls": 12, "cap_usd": 0.9, "saved": 1}, handle)
        ledger = mp.Ledger(state_dir=self.tmp, cap=25, clock=lambda: self.at(2026, 11, 23, 12))
        self.assertEqual((ledger.spent, ledger.calls, ledger.start, ledger.unverified), (0.40692, 12, self.at(2026, 11, 5, 18), ""))

    def test_a_restart_after_the_boundary_starts_the_new_window(self):
        with open(self.ledger.path, "w") as handle:
            json.dump({"spent_usd": 3.0, "calls": 5, "cap_usd": 25, "window_start": self.at(2026, 11, 5, 18), "window_end": self.at(2026, 12, 5, 18), "saved": 1}, handle)
        ledger = mp.Ledger(state_dir=self.tmp, cap=25, clock=lambda: self.at(2026, 12, 20, 9))
        self.assertEqual((ledger.spent, ledger.calls, ledger.start, ledger.end), (0.0, 0, self.at(2026, 12, 5, 18), self.at(2027, 1, 5, 18)))

    def test_health_names_the_window_and_the_cap(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/health")
        doc = json.loads(conn.getresponse().read())
        self.assertEqual(sorted(doc), ["blocked", "cap_usd", "ok", "spent_usd", "stopped", "window"])
        self.assertEqual(sorted(doc["window"]), ["end", "start"])
