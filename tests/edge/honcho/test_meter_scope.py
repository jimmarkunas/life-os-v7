"""the optional scope budget in the Honcho meter."""

import http.client
import json
import os
import threading
import unittest
from unittest import mock

from lifeos.edge.honcho import meter_proxy as mp
from tests.edge.honcho.test_meter_proxy import Case, Upstream

MODEL = mp.MODEL
LIMIT = 8192  # max_tokens the scope allows
R = mp.reservation_nano(LIMIT)
CAP = R / 1e9  # a scope that holds exactly one such request


def body(size=2000, max_tokens=LIMIT, **extra):
    """> a /v1/messages body of exactly `size` bytes asking for `max_tokens` of output."""
    doc = {"model": MODEL, "max_tokens": max_tokens, "messages": [{"role": "user", "content": ""}], **extra}
    pad = size - len(json.dumps(doc))
    doc["messages"][0]["content"] = "x" * pad
    raw = json.dumps(doc)
    assert len(raw) == size, (len(raw), size)
    return raw.encode()


class ScopeCase(Case):
    default_cap = CAP  # the scope every test starts with (None: none)

    def setUp(self):
        super().setUp()
        self.now = 1_800_000_000  # a fixed instant inside one monthly window
        if self.default_cap is not None:
            self.scope(self.default_cap)

    def scope(self, cap, wipe=True, **over):
        if wipe:
            for name in (
                "ledger.json",
                "audit.jsonl",
                "scopes.jsonl",
                "windows.jsonl",
                mp.SCOPE_CLOSE,
                "EXHAUSTED",
                "STOP",
            ):
                path = os.path.join(self.tmp, name)
                if os.path.lexists(path):
                    os.remove(path)
        doc = {
            "id": "t2-test",
            "cap_usd": cap,
            "expires": self.now + 3600,
            "models": [MODEL],
            "max_tokens": LIMIT,
            **over,
        }
        with open(os.path.join(self.tmp, mp.SCOPE_FILE), "w") as handle:
            json.dump(doc, handle)
        self.start(25)
        self.ledger.clock = lambda: self.now
        self.ledger.start, self.ledger.end = mp.window(self.now)
        self.ledger.why_blocked()  # opens the scope
        return doc

    def raw_post(self, raw, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request(
            "POST",
            "/v1/messages",
            raw,
            {"Content-Type": "application/json", "x-api-key": "dummy-local", **(headers or {})},
        )
        answer = conn.getresponse()
        return answer.status, answer.read()

    def messages(self):
        return [s for s in Upstream.seen if s["path"] == "/v1/messages"]


class Bound(ScopeCase):
    """One strict admission, reserving the documented maximum, for the scope and the monthly cap alike."""

    def test_a_request_whose_reservation_exactly_fills_the_scope_is_forwarded(self):
        self.assertEqual(self.raw_post(body())[0], 200)
        self.assertEqual(len(self.messages()), 1)

    def test_one_nano_dollar_under_the_reservation_is_refused_and_nothing_is_forwarded(self):
        self.scope((R - 1) / 1e9, id="t2-under")
        status, text = self.raw_post(body())
        self.assertEqual(status, 400)
        self.assertIn(b"cap reached", text)
        self.assertEqual(Upstream.seen, [])

    def test_reservations_are_whole_nano_dollars_and_add_up_exactly(self):
        self.assertIsInstance(R, int)
        self.scope(2 * R / 1e9, id="t2-two")
        first, second = (self.ledger.admit(body())[0] for _ in range(2))
        self.assertTrue(first and second)
        self.assertEqual(sum(h["nano"] for h in self.ledger.inflight.values()), 2 * R)
        self.assertFalse(self.ledger.admit(body(max_tokens=1))[0])
        self.assertEqual(Upstream.seen, [])

    def test_concurrent_requests_reserve_atomically_against_both_budgets(self):
        self.scope(2 * R / 1e9, id="t2-conc")
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
        self.assertEqual(sum(got), 2)

    def test_a_half_dollar_scope_admits_nothing_because_one_request_can_bill_over_a_dollar(self):
        """BLOCKED: the documented maximum of the smallest possible request is already above $0.50."""
        self.assertGreater(mp.MIN_RESERVATION_NANO, 500_000_000)
        self.scope(0.50, id="t2-half")
        for tokens in (1, 100, LIMIT):
            self.assertFalse(self.ledger.admit(body(max_tokens=tokens))[0], tokens)
        self.assertEqual(self.raw_post(body(max_tokens=1))[0], 400)
        self.assertEqual(Upstream.seen, [])

    def test_a_request_over_the_scope_max_tokens_is_refused_even_when_money_is_left(self):
        self.scope(5.0, id="t2-lim")
        rid, _, why = self.ledger.admit(body(max_tokens=LIMIT + 1))
        self.assertFalse(rid)
        self.assertEqual(why, "max_tokens is over the scope limit")

    def test_the_monthly_cap_applies_with_a_scope_standing_and_one_unit_over_is_refused(self):
        self.scope(5.0, id="t2-month")
        self.ledger.cap, self.ledger.spent = (
            25,
            25 - R / 1e9 + 0.0001,
        )  # a hundred-thousandth of a dollar too little room
        self.assertFalse(self.ledger.admit(body())[0])
        self.ledger.spent = 25 - R / 1e9 - 0.0001
        self.assertTrue(self.ledger.admit(body())[0])

    def test_nothing_unprovable_is_forwarded(self):
        def doc(**over):
            return json.dumps({"model": MODEL, "max_tokens": 5, "messages": [], **over}).encode()

        cases = {
            "wrong model": doc(model="claude-opus-9"),
            "dated alias": doc(model=MODEL + "-20261101"),
            "no model": json.dumps({"max_tokens": 5, "messages": []}).encode(),
            "no max_tokens": json.dumps({"model": MODEL, "messages": []}).encode(),
            "zero max_tokens": doc(max_tokens=0),
            "over the documented output": doc(max_tokens=mp.MAX_OUTPUT_TOKENS + 1),
            "bool max_tokens": doc(max_tokens=True),
            "string max_tokens": doc(max_tokens="5"),
            "not json": b"{",
            "a list": b"[]",
            "service tier": doc(service_tier="priority"),
            "inference geo": doc(inference_geo="us"),
            "speed": doc(speed="fast"),
            "mcp servers": doc(mcp_servers=[]),
            "container": doc(container="x"),
            "server tool": doc(tools=[{"type": "web_search_20250305", "name": "web_search"}]),
            "tools not a list": doc(tools={}),
        }
        for name, raw in cases.items():
            with self.subTest(name):
                self.assertEqual(self.raw_post(raw)[0], 400)
        self.assertEqual(self.raw_post(body(), {"anthropic-beta": "context-1m-2025-08-07"})[0], 400)
        self.assertEqual(Upstream.seen, [])
        self.assertEqual((self.ledger.inflight, self.ledger.reserved), ({}, 0.0))

    def test_every_documented_feature_inside_the_limits_is_admitted_at_the_same_ceiling(self):
        self.scope(5.0, id="t2-feat")
        ok = json.dumps(
            {
                "model": MODEL,
                "max_tokens": 50,
                "stream": True,
                "system": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
                "thinking": {"type": "enabled", "budget_tokens": 20},
                "tools": [{"name": "t", "input_schema": {"type": "object"}}],
                "messages": [
                    {"role": "user", "content": [{"type": "image", "source": {}}, {"type": "text", "text": "q"}]}
                ],
            }
        ).encode()
        self.assertEqual(mp.admission(ok), (mp.reservation_nano(50), ""))

    def test_a_request_with_no_proof_is_refused_while_a_scope_stands(self):
        self.assertFalse(self.ledger.reserve(0.001))
        self.assertEqual(self.ledger.inflight, {})

    def test_the_legacy_estimate_is_gone(self):
        self.assertFalse(
            hasattr(mp, "estimate") or hasattr(mp, "BYTES_PER_TOKEN") or hasattr(mp, "SCOPE_OVERHEAD_TOKENS")
        )


class Counts(ScopeCase):
    """A provider token count may refuse"""

    default_cap = None

    def admit(self, counter):
        return self.ledger.admit(body(), counter=counter)

    def test_a_valid_count_does_not_lower_the_reservation(self):
        for count in (1, 281, 10_395, mp.CONTEXT_TOKENS):
            rid, worst, why = self.admit(lambda b, n=count: n)
            self.assertTrue(rid, count)
            self.assertEqual(round(worst * mp.NANO), R)
        self.assertEqual(mp.admission(body(), counter=lambda b: 5), (R, ""))

    def test_no_counter_means_no_gate(self):
        self.assertEqual(mp.admission(body()), (R, ""))

    def test_a_missing_invalid_failed_timed_out_or_disagreeing_count_refuses(self):
        def boom(_):
            raise RuntimeError("count endpoint error")

        def slow(_):
            raise TimeoutError("count timed out")

        bad = {
            "error": boom,
            "timeout": slow,
            "none": lambda b: None,
            "zero": lambda b: 0,
            "negative": lambda b: -4,
            "string": lambda b: "5",
            "bool": lambda b: True,
            "float": lambda b: 12.5,
            "over the window": lambda b: mp.CONTEXT_TOKENS + 1,
        }
        for name, counter in bad.items():
            with self.subTest(name):
                rid, _, why = self.admit(counter)
                self.assertFalse(rid)
                self.assertIn("token count", why + " token count" if "window" in why else why)
        self.assertEqual(self.ledger.inflight, {})
        self.assertEqual(Upstream.seen, [])


class Crashes(ScopeCase):
    def restart(self):
        return mp.Ledger(state_dir=self.tmp, cap=25, clock=lambda: self.now)

    def test_a_crash_after_the_reservation_and_before_the_forward_charges_it_in_full_on_restart(self):
        rid, worst, _ = self.ledger.admit(body())
        self.assertTrue(rid)
        self.assertEqual(
            json.load(open(self.ledger.path))["inflight"][str(rid)]["nano"], R
        )  # saved before anything was forwarded
        again = self.restart()
        self.assertEqual((again.inflight, again.reserved, again.scope["spent_nano"]), ({}, 0.0, R))
        self.assertAlmostEqual(again.spent, worst)
        self.assertTrue(any(row.get("recovered") for row in [json.loads(line) for line in open(again.audit_path)]))
        self.assertFalse(again.admit(body(max_tokens=1))[0])  # the scope is full: nothing more is forwarded

    def test_a_crash_never_frees_room_and_restarting_again_changes_nothing(self):
        self.assertTrue(self.ledger.admit(body())[0])
        for _ in range(2):
            again = self.restart()
            self.assertEqual(again.scope["spent_nano"], R)

    def test_a_monthly_only_reservation_is_also_saved_first_and_charged_in_full_after_a_crash(self):
        self.scope(5.0, id="t2-closed")
        json.dump({"scope": "t2-closed"}, open(os.path.join(self.tmp, mp.SCOPE_CLOSE), "w"))
        self.assertEqual(self.ledger.why_blocked(), "")  # no scope now: the monthly cap alone
        self.assertIsNone(self.ledger.scope)
        rid, worst, _ = self.ledger.admit(body())
        self.assertTrue(rid)
        again = self.restart()
        self.assertAlmostEqual(again.spent, worst)
        self.assertEqual(again.inflight, {})

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
        self.assertEqual(self.ledger.scope["spent_nano"], mp.reservation_nano(100))

    def test_a_reply_with_no_usage_keeps_the_whole_reservation(self):
        Upstream.mode = "nousage"
        self.assertEqual(self.raw_post(body(2000, 100))[0], 200)
        self.assertEqual(self.ledger.scope["spent_nano"], mp.reservation_nano(100))

    def test_a_connection_that_fails_after_the_request_was_sent_keeps_the_whole_reservation(self):
        Upstream.mode = "reset"
        settled = threading.Event()
        settle = self.ledger.settle

        def settle_and_signal(*args, **kwargs):
            try:
                return settle(*args, **kwargs)
            finally:
                settled.set()

        with mock.patch.object(self.ledger, "settle", side_effect=settle_and_signal):
            self.assertEqual(self.raw_post(body(2000, 100))[0], 502)
            self.assertTrue(settled.wait(timeout=5), "request handler did not settle its reservation")
        self.assertEqual(self.ledger.scope["spent_nano"], mp.reservation_nano(100))
        self.assertEqual(self.ledger.inflight, {})

    def test_a_genuine_upstream_error_reply_is_not_charged(self):
        Upstream.mode = "error"
        self.assertEqual(self.raw_post(body(2000, 100))[0], 529)
        self.assertEqual((self.ledger.scope["spent_nano"], self.ledger.inflight), (0, {}))

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
        want = 2000 * 100 + 100 * 500
        self.assertEqual(self.ledger.scope["spent_nano"], want)
        self.assertEqual(self.restart().scope["spent_nano"], want)

    def test_a_ledger_that_cannot_be_saved_refuses_before_forwarding_and_a_corrupt_one_refuses_everything(self):
        with mock.patch.object(mp.Ledger, "save", side_effect=OSError("disk full")):
            self.assertEqual(self.raw_post(body(2000, 100))[0], 400)
        self.assertEqual(Upstream.seen, [])
        for junk in (
            "not json",
            json.dumps({"spent_usd": 0, "calls": 0, "scope": {"x": 1}}),
            json.dumps({"spent_usd": 0, "calls": 0, "inflight": {"1": {}}}),
        ):
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
        self.assertEqual(self.post("/v1/embeddings", {"input": "x"})[0], 200)  # embeddings stay free

    def test_removing_or_editing_the_scope_file_while_it_is_open_refuses_everything(self):
        path = os.path.join(self.tmp, mp.SCOPE_FILE)
        saved = open(path).read()
        os.remove(path)
        self.assertIn("scope removed", self.ledger.why_blocked())
        self.assertEqual(self.raw_post(body(2000, 100))[0], 400)
        edited = json.loads(saved)
        edited["cap_usd"] = 9.0
        open(path, "w").write(json.dumps(edited))
        self.assertIn("scope file changed", self.ledger.why_blocked())
        open(path, "w").write("{")
        self.assertIn("scope removed or unreadable", self.ledger.why_blocked())
        open(path, "w").write(saved)
        self.assertEqual(self.ledger.why_blocked(), "")
        self.assertEqual(Upstream.seen, [])

    def test_the_scope_spend_survives_a_restart_and_a_monthly_rollover(self):
        self.assertEqual(self.raw_post(body(2000, 100))[0], 200)
        spent = self.ledger.scope["spent_nano"]
        self.now = mp.window(self.now)[1] + 60  # the next monthly window
        self.ledger.scope["config"]["expires"] = self.now + 3600
        doc = json.load(open(os.path.join(self.tmp, mp.SCOPE_FILE)))
        doc["expires"] = self.now + 3600
        json.dump(doc, open(os.path.join(self.tmp, mp.SCOPE_FILE), "w"))
        self.assertEqual(self.ledger.why_blocked(), "")
        self.assertEqual(
            (self.ledger.spent, self.ledger.scope["spent_nano"]), (0.0, spent)
        )  # the month restarted, the scope did not
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
        self.assertIsNone(self.ledger.scope)
        self.assertEqual(self.raw_post(body(2000, 100))[0], 200)  # no scope now: the monthly cap alone
        path = os.path.join(self.tmp, mp.SCOPE_FILE)
        saved = open(path).read()
        open(path, "w").write(saved)  # the same id written again is ignored
        self.assertEqual(self.ledger.why_blocked(), "")
        self.assertIsNone(self.ledger.scope)
        self.scope(CAP, wipe=False, id="t2-next")  # a new id opens a new scope at zero
        self.assertEqual((self.ledger.scope["spent_nano"], self.ledger.closed), (0, ["t2-test"]))

    def test_health_shows_the_scope_in_dollars_and_words_only(self):
        self.raw_post(body(2000, 100))
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("GET", "/health")
        doc = json.loads(conn.getresponse().read())
        self.assertEqual(
            doc["scope"],
            {"id": "t2-test", "cap_usd": CAP, "spent_usd": (2000 * 100 + 100 * 500) / 1e9, "state": "open"},
        )


class NoScope(ScopeCase):
    default_cap = None

    def test_with_no_scope_file_everything_is_as_before(self):
        self.assertEqual(self.ledger.why_blocked(), "")
        self.assertIsNone(self.ledger.scope)
        self.assertEqual(self.raw_post(body(2000, 100))[0], 200)  # the monthly cap alone, the same strict admission
        self.assertEqual(self.ledger.inflight, {})


if __name__ == "__main__":
    unittest.main()
