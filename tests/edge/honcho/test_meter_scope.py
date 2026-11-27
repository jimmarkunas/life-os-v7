"""LIFE-1021: the optional scope budget in the Honcho meter. A fake Anthropic and a fake Ollama on loopback; no network, no real key, no spend, nothing deployed."""
import http.client
import json
import os
import threading
import unittest
from unittest import mock

from lifeos.edge.honcho import meter_proxy as mp
from tests.edge.honcho.test_meter_proxy import Case, Upstream

MODEL = "claude-haiku-5-5"
CAP = 0.125                                                    # 125,000,000 nano-dollars
LIMIT = 225_000                                                # max_tokens the scope allows


def body(size=95_904, max_tokens=LIMIT, **extra):
    """-> a /v1/messages body of exactly `size` bytes (so its bound is (size + 4096) tokens, exactly 100,000 by default: the short tier's edge) asking for `max_tokens` of output."""
    doc = {"model": MODEL, "max_tokens": max_tokens, "messages": [{"role": "user", "content": ""}], **extra}
    pad = size - len(json.dumps(doc))
    doc["messages"][0]["content"] = "x" * pad
    raw = json.dumps(doc)
    assert len(raw) == size, (len(raw), size)
    return raw.encode()


EXACT = (95_904 + mp.SCOPE_OVERHEAD_TOKENS) * 125 + LIMIT * 500            # 12,500,000 + 112,500,000 = 125,000,000 nano


class ScopeCase(Case):
    default_cap = CAP                                                      # the scope every test starts with (None: none)

    def setUp(self):
        super().setUp()
        self.now = 1_800_000_000                                           # a fixed instant inside one monthly window
        if self.default_cap is not None:
            self.scope(self.default_cap)

    def scope(self, cap, **over):
        doc = {"id": "t2-test", "cap_usd": cap, "expires": self.now + 3600, "models": [MODEL], "max_tokens": LIMIT, **over}
        with open(os.path.join(self.tmp, mp.SCOPE_FILE), "w") as handle:
            json.dump(doc, handle)
        self.start(25)
        self.ledger.clock = lambda: self.now
        self.ledger.start, self.ledger.end = mp.window(self.now)
        self.ledger.why_blocked()                                          # opens the scope
        return doc

    def raw_post(self, raw, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("POST", "/v1/messages", raw, {"Content-Type": "application/json", "x-api-key": "dummy-local", **(headers or {})})
        answer = conn.getresponse()
        return answer.status, answer.read()

    def messages(self):
        return [s for s in Upstream.seen if s["path"] == "/v1/messages"]


class Bound(ScopeCase):
    def test_the_worst_case_is_proven_from_the_request_and_priced_at_the_dearest_input_class(self):
        self.assertEqual(mp.scope_bound(body(), self.ledger.scope_file()[0])[0], EXACT)
        self.assertEqual(EXACT, 125_000_000)
        long_body = mp.scope_bound(body(100_000), self.ledger.scope_file()[0])[0]                # 104,096 tokens: the long tier, 1.00 cache write
        self.assertEqual(long_body, 104_096 * 1000 + LIMIT * 2500)

    def test_a_request_that_exactly_fills_the_cap_is_forwarded(self):
        self.assertEqual(self.raw_post(body())[0], 200)
        self.assertEqual(len(self.messages()), 1)

    def test_one_byte_more_than_the_cap_is_refused_and_nothing_is_forwarded(self):
        status, text = self.raw_post(body(95_905))
        self.assertEqual(status, 400)
        self.assertIn(b"cap reached", text)
        self.assertEqual(Upstream.seen, [])


class HalfDollar(ScopeCase):
    default_cap = 0.50

    def test_holds_fill_the_half_dollar_scope_exactly_and_a_fifth_is_refused(self):
        ids = [self.ledger.admit(body())[0] for _ in range(4)]
        self.assertTrue(all(ids))
        self.assertEqual(sum(h["nano"] for h in self.ledger.inflight.values()), 500_000_000)
        self.assertFalse(self.ledger.admit(body(95_905))[0])
        self.assertFalse(self.ledger.admit(body())[0])
        self.assertEqual(Upstream.seen, [])

    def test_the_monthly_cap_still_applies_with_a_scope_standing(self):
        self.ledger.cap, self.ledger.spent = 25, 24.95
        self.assertFalse(self.ledger.admit(body())[0])                                          # 0.125 would pass 25
        self.assertEqual(Upstream.seen, [])

    def test_concurrent_requests_reserve_atomically_against_both_budgets(self):
        got, lock = [], threading.Lock()

        def go():
            rid = self.ledger.admit(body())[0]
            with lock:
                got.append(bool(rid))
        threads = [threading.Thread(target=go) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(got), 4)

    def test_nothing_unprovable_is_forwarded(self):
        cases = {"wrong model": body().replace(MODEL.encode(), b"claude-opus-9"), "no max_tokens": json.dumps({"model": MODEL, "messages": []}).encode(),
                 "over the limit": body(max_tokens=LIMIT + 1), "bool max_tokens": json.dumps({"model": MODEL, "max_tokens": True, "messages": []}).encode(),
                 "not json": b"{", "a list": b"[]", "unknown field": json.dumps({"model": MODEL, "max_tokens": 5, "messages": [], "service_tier": "priority"}).encode(),
                 "image": json.dumps({"model": MODEL, "max_tokens": 5, "messages": [{"role": "user", "content": [{"type": "image", "source": {}}]}]}).encode(),
                 "document in a tool result": json.dumps({"model": MODEL, "max_tokens": 5, "messages": [{"role": "user", "content": [{"type": "tool_result", "content": [{"type": "document"}]}]}]}).encode(),
                 "server tool": json.dumps({"model": MODEL, "max_tokens": 5, "messages": [], "tools": [{"type": "web_search_20250305", "name": "web_search"}]}).encode(),
                 "system as a number": json.dumps({"model": MODEL, "max_tokens": 5, "messages": [], "system": 7}).encode()}
        for name, raw in cases.items():
            with self.subTest(name):
                self.assertEqual(self.raw_post(raw)[0], 400)
        self.assertEqual(self.raw_post(body(2000, 5), {"anthropic-beta": "context-1m-2025-08-07"})[0], 400)
        self.assertEqual(Upstream.seen, [])
        self.assertEqual((self.ledger.inflight, self.ledger.reserved), ({}, 0.0))

    def test_text_tools_and_tool_results_are_provable(self):
        ok = json.dumps({"model": MODEL, "max_tokens": 50, "system": [{"type": "text", "text": "s"}], "tools": [{"name": "t", "input_schema": {"type": "object"}}],
                         "messages": [{"role": "user", "content": [{"type": "tool_result", "content": "r"}, {"type": "text", "text": "q"}]}]}).encode()
        self.assertEqual(self.raw_post(ok)[0], 200)

    def test_a_request_with_no_scope_proof_is_refused_while_a_scope_stands(self):
        self.assertFalse(self.ledger.reserve(0.001))
        self.assertEqual(self.ledger.inflight, {})


class Crashes(ScopeCase):
    def restart(self):
        again = mp.Ledger(state_dir=self.tmp, cap=25, clock=lambda: self.now)
        return again

    def test_a_crash_after_the_reservation_and_before_the_forward_charges_it_in_full_on_restart(self):
        rid, worst, _ = self.ledger.admit(body())
        self.assertTrue(rid)
        self.assertEqual(json.load(open(self.ledger.path))["inflight"][str(rid)]["nano"], EXACT)         # saved before anything was forwarded
        again = self.restart()
        self.assertEqual((again.inflight, again.reserved, again.scope["spent_nano"]), ({}, 0.0, EXACT))
        self.assertAlmostEqual(again.spent, worst)
        self.assertTrue(any(row.get("recovered") for row in [json.loads(l) for l in open(again.audit_path)]))
        self.assertFalse(again.admit(body(95_905))[0])                                                  # the scope is full: nothing more is forwarded

    def test_a_crash_never_frees_room(self):
        self.assertTrue(self.ledger.admit(body())[0])                                                  # the whole scope is held
        again = self.restart()
        self.assertEqual(again.scope["spent_nano"], EXACT)
        self.assertFalse(again.admit(body(2000, 100))[0])
        self.assertEqual(self.restart().scope["spent_nano"], EXACT)                                    # restarting again changes nothing

    def test_a_reply_that_breaks_off_keeps_the_whole_scope_reservation(self):
        Upstream.mode = "broken"
        try:
            self.raw_post(body(2000, 100))
        except (http.client.HTTPException, OSError):
            pass
        for _ in range(100):
            if self.ledger.calls:
                break
            threading.Event().wait(0.05)
        want = mp.scope_bound(body(2000, 100), self.ledger.scope["config"])[0]
        self.assertEqual(self.ledger.scope["spent_nano"], want)

    def test_a_reply_with_no_usage_keeps_the_whole_reservation(self):
        Upstream.mode = "nousage"
        self.assertEqual(self.raw_post(body(2000, 100))[0], 200)
        self.assertEqual(self.ledger.scope["spent_nano"], mp.scope_bound(body(2000, 100), self.ledger.scope["config"])[0])

    def test_a_connection_that_fails_after_the_request_was_sent_keeps_the_whole_reservation(self):
        Upstream.mode = "reset"
        self.assertEqual(self.raw_post(body(2000, 100))[0], 502)
        self.assertEqual(self.ledger.scope["spent_nano"], mp.scope_bound(body(2000, 100), self.ledger.scope["config"])[0])
        self.assertEqual(self.ledger.inflight, {})

    def test_a_connection_that_never_opened_costs_nothing(self):
        class Dead:
            def connect(self):
                raise ConnectionRefusedError()
        with mock.patch.object(mp.http.client, "HTTPSConnection", lambda host, timeout=None: Dead()):
            self.assertEqual(self.raw_post(body(2000, 100))[0], 502)
        self.assertEqual((self.ledger.scope["spent_nano"], self.ledger.inflight), (0, {}))

    def test_a_reply_that_cost_more_than_its_reservation_breaches_the_scope_and_stops_everything(self):
        Upstream.mode = "huge"
        self.assertEqual(self.raw_post(body(2000, 100))[0], 200)
        self.assertEqual(self.ledger.scope["state"], "breached")
        Upstream.mode = "json"
        before = len(self.messages())
        self.assertEqual(self.raw_post(body(2000, 100))[0], 400)
        self.assertEqual(len(self.messages()), before)

    def test_a_settled_reply_costs_its_actual_usage_in_whole_nano_dollars(self):
        self.assertEqual(self.raw_post(body(2000, 100))[0], 200)
        self.assertEqual(self.ledger.scope["spent_nano"], 2000 * 100 + 100 * 500)                      # the fake replies with 2000 input and 100 output tokens
        self.assertEqual(self.restart().scope["spent_nano"], 2000 * 100 + 100 * 500)

    def test_a_ledger_that_cannot_be_saved_refuses_before_forwarding_and_a_corrupt_one_refuses_everything(self):
        with mock.patch.object(mp.Ledger, "save", side_effect=OSError("disk full")):
            self.assertEqual(self.raw_post(body(2000, 100))[0], 400)
        self.assertEqual(Upstream.seen, [])
        for junk in ("not json", json.dumps({"spent_usd": 0, "calls": 0, "scope": {"x": 1}}), json.dumps({"spent_usd": 0, "calls": 0, "inflight": {"1": {}}})):
            self.restart_with(junk)

    def restart_with(self, junk):
        with open(self.ledger.path, "w") as handle:
            handle.write(junk)
        again = self.restart()
        self.assertEqual(again.unverified, "ledger unreadable")
        self.assertFalse(again.admit(body(2000, 100))[0])


class Lifecycle(ScopeCase):
    def test_expiry_stops_spending_and_never_resets_the_cap(self):
        self.assertEqual(self.raw_post(body(2000, 100))[0], 200)
        spent = self.ledger.scope["spent_nano"]
        self.now += 3601
        status, text = self.raw_post(body(2000, 100))
        self.assertEqual(status, 400)
        self.assertIn(b"scope expired and not closed", text)
        self.assertEqual(self.ledger.scope["spent_nano"], spent)
        self.assertEqual(self.post("/v1/messages/count_tokens", {"messages": []})[0], 400)
        self.assertEqual(self.post("/v1/embeddings", {"input": "x"})[0], 200)                          # embeddings stay free

    def test_removing_or_editing_the_scope_file_while_it_is_open_refuses_everything(self):
        path = os.path.join(self.tmp, mp.SCOPE_FILE)
        saved = open(path).read()
        os.remove(path)
        self.assertIn("scope removed", self.ledger.why_blocked())
        self.assertEqual(self.raw_post(body(2000, 100))[0], 400)
        open(path, "w").write(saved.replace("0.125", "9.0"))
        self.assertIn("scope file changed", self.ledger.why_blocked())
        open(path, "w").write("{")
        self.assertIn("scope removed or unreadable", self.ledger.why_blocked())
        open(path, "w").write(saved)
        self.assertEqual(self.ledger.why_blocked(), "")
        self.assertEqual(Upstream.seen, [])

    def test_the_scope_spend_survives_a_restart_and_a_monthly_rollover(self):
        self.assertEqual(self.raw_post(body(2000, 100))[0], 200)
        spent = self.ledger.scope["spent_nano"]
        self.now = mp.window(self.now)[1] + 60                                                         # the next monthly window
        self.ledger.scope["config"]["expires"] = self.now + 3600                                       # keep it open across the boundary (the file below matches)
        doc = json.load(open(os.path.join(self.tmp, mp.SCOPE_FILE)))
        doc["expires"] = self.now + 3600
        json.dump(doc, open(os.path.join(self.tmp, mp.SCOPE_FILE), "w"))
        self.assertEqual(self.ledger.why_blocked(), "")
        self.assertEqual((self.ledger.spent, self.ledger.scope["spent_nano"]), (0.0, spent))           # the month restarted, the scope did not
        again = mp.Ledger(state_dir=self.tmp, cap=25, clock=lambda: self.now)
        self.assertEqual(again.scope["spent_nano"], spent)

    def test_stop_comes_first(self):
        open(self.ledger.stop_path, "w").close()
        self.assertEqual(self.ledger.why_blocked(), "cap reached or stopped")
        self.assertEqual(self.raw_post(body(2000, 100))[0], 400)

    def test_a_scope_is_closed_only_by_a_named_close_file_and_only_once_drained(self):
        rid = self.ledger.admit(body(2000, 100))[0]
        close = os.path.join(self.tmp, mp.SCOPE_CLOSE)
        json.dump({"scope": "other"}, open(close, "w"))
        self.assertEqual(self.ledger.why_blocked(), "scope close names another scope")
        json.dump({"scope": "t2-test"}, open(close, "w"))
        self.assertEqual(self.ledger.why_blocked(), "scope close waits for requests in flight")
        self.ledger.settle(0.0, 0.0, "/v1/messages", 200, {"output_tokens": 1}, rid=rid)
        self.assertEqual(self.ledger.why_blocked(), "")
        self.assertIsNone(self.ledger.scope)
        self.assertFalse(os.path.exists(close))
        row = [json.loads(line) for line in open(self.ledger.scopes_path)]
        self.assertEqual((row[0]["id"], row[0]["spent_nano"] > 0), ("t2-test", True))
        self.assertEqual(mp.Ledger(state_dir=self.tmp, cap=25).closed, ["t2-test"])

    def test_a_closed_scope_id_is_never_reused_and_a_new_scope_starts_at_zero(self):
        json.dump({"scope": "t2-test"}, open(os.path.join(self.tmp, mp.SCOPE_CLOSE), "w"))
        self.assertEqual(self.ledger.why_blocked(), "")
        self.assertEqual(self.raw_post(json.dumps({"max_tokens": 5, "messages": []}).encode())[0], 200)      # no scope: the old estimate, any shape
        self.assertIsNone(self.ledger.scope)                                                           # the old file is ignored: no scope, the old estimate applies
        self.scope(CAP, id="t2-next")
        self.ledger.why_blocked()
        self.assertEqual(self.ledger.scope["spent_nano"], 0)

    def test_health_shows_the_scope_in_dollars_and_words_only(self):
        self.raw_post(body(2000, 100))
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("GET", "/health")
        doc = json.loads(conn.getresponse().read())
        self.assertEqual(doc["scope"], {"id": "t2-test", "cap_usd": CAP, "spent_usd": (2000 * 100 + 100 * 500) / 1e9, "state": "open"})


class NoScope(ScopeCase):
    default_cap = None

    def test_with_no_scope_file_everything_is_as_before(self):
        self.assertEqual(self.ledger.why_blocked(), "")
        self.assertIsNone(self.ledger.scope)
        self.assertEqual(self.raw_post(json.dumps({"max_tokens": 5, "messages": []}).encode())[0], 200)      # the old estimate, any shape
        self.assertEqual(self.ledger.inflight, {})


if __name__ == "__main__":
    unittest.main()
