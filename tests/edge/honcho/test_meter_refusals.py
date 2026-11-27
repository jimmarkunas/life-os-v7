"""The Honcho meter's refusal kinds: only a real budget refusal writes the once-per-window EXHAUSTED marker that starts the Agent Inbox alert.
An invalid request, an experiment-scope refusal and a momentary reservation hold never do; every refusal still writes its audit row and forwards nothing."""

import json
import os
import unittest
from unittest import mock

from lifeos.edge.honcho import meter_proxy as mp
from tests.edge.honcho.test_meter_proxy import Upstream
from tests.edge.honcho.test_meter_scope import MODEL, R, ScopeCase, body

NOW = 1_800_000_000


class Kinds(unittest.TestCase):
    def test_every_known_reason_has_its_kind_and_an_unknown_one_is_a_budget_refusal(self):
        for why in mp.INVALID_REASONS:
            self.assertEqual(mp.refusal_kind(why), mp.KIND_INVALID, why)
        for why in (
            "scope cap reached",
            "max_tokens is over the scope limit",
            "no scope proof",
            "scope expired and not closed",
            "scope breached",
        ):
            self.assertEqual(mp.refusal_kind(why), mp.KIND_SCOPE, why)
        for why in (
            "scope removed or unreadable before it was closed",
            "scope file changed before it was closed",
            "scope close waits for requests in flight",
        ):
            self.assertEqual(mp.refusal_kind(why), mp.KIND_SCOPE, why)
        for why in mp.BUSY_REASONS:
            self.assertEqual(mp.refusal_kind(why), mp.KIND_BUSY, why)
        for why in (
            "cap reached or stopped",
            "cap reached",
            "budget cannot be verified: ledger unwritable",
            "promotional credits ended: spend would be paid",
            "something new",
        ):
            self.assertEqual(mp.refusal_kind(why), mp.KIND_BUDGET, why)

    def test_the_reason_texts_the_meter_can_return_are_all_listed(self):
        source = open(mp.__file__).read()
        for why in (
            "scope cap reached",
            "no scope proof",
            "monthly reservations in flight",
            "scope reservations in flight",
            "max_tokens is over the scope limit",
        ):
            self.assertIn(f'"{why}"', source)


class Refusals(ScopeCase):
    default_cap = None

    def setUp(self):
        super().setUp()
        self.start(25)
        self.at(NOW)

    def at(self, now):
        self.ledger.clock = lambda: now
        self.ledger.start, self.ledger.end = mp.window(now)

    def marker(self):
        path = os.path.join(self.tmp, "EXHAUSTED")
        return json.load(open(path)) if os.path.exists(path) else None

    def refused(self, raw, headers=None):
        """Post a request that must be refused; -> the last audit row. Nothing may reach the provider."""
        before = len(self.messages())
        status, _ = self.raw_post(raw, headers)
        self.assertEqual(status, 400)
        self.assertEqual(len(self.messages()), before)
        return self.audit()[-1]

    def open_scope(self, cap=2.0, **over):
        self.scope(cap, wipe=False, **over)
        self.at(NOW)

    # ---- invalid requests and experiment-scope refusals never alert
    def test_invalid_requests_leave_an_audit_row_and_no_marker(self):
        cases = {
            "unsupported request field": (body(service_tier="x"), None),
            "model is not priced": (body().replace(MODEL.encode(), b"other-model"), None),
            "body is not JSON": (b"{", None),
            "beta features are not priced": (body(), {"anthropic-beta": "x"}),
        }
        for why, (raw, headers) in cases.items():
            with self.subTest(why):
                row = self.refused(raw, headers)
                self.assertEqual((row["refused"], row["kind"]), (why, "invalid"))
        self.assertIsNone(self.marker())

    def test_every_scope_refusal_leaves_an_audit_row_and_no_marker(self):
        self.open_scope(0.5)
        row = self.refused(body())
        self.assertEqual((row["refused"], row["kind"]), ("scope cap reached", "scope"))
        self.open_scope_fresh(2.0)
        self.assertEqual(
            self.refused(body(max_tokens=9000, size=2000))["refused"], "max_tokens is over the scope limit"
        )
        self.ledger.scope["state"] = "breached"
        self.assertEqual(self.refused(body())["refused"], "scope breached")
        self.ledger.scope["state"] = "open"
        self.ledger.clock = lambda: NOW + 7200
        self.assertEqual(self.refused(body())["refused"], "scope expired and not closed")
        os.remove(os.path.join(self.tmp, mp.SCOPE_FILE))
        self.assertEqual(self.refused(body())["kind"], "scope")
        self.assertIsNone(self.marker())

    def open_scope_fresh(self, cap):
        for name in ("ledger.json", "scopes.jsonl", mp.SCOPE_CLOSE):
            path = os.path.join(self.tmp, name)
            if os.path.lexists(path):
                os.remove(path)
        self.scope(cap)
        self.at(NOW)

    # ---- real budget refusals alert, once per window
    def test_a_really_spent_month_writes_the_marker_with_the_monthly_reason(self):
        self.ledger.spent = 24.5
        row = self.refused(body())
        self.assertEqual((row["refused"], row["kind"]), ("cap reached or stopped", "budget"))
        found = self.marker()
        self.assertEqual((found["reason"], found["window"]), ("cap reached or stopped", self.ledger.start))

    def test_the_stop_file_an_unverifiable_ledger_and_the_credit_end_still_alert(self):
        open(self.ledger.stop_path, "w").close()
        self.assertEqual(self.refused(body())["kind"], "budget")
        self.assertEqual(self.marker()["reason"], "cap reached or stopped")
        os.remove(self.ledger.stop_path)
        os.remove(os.path.join(self.tmp, "EXHAUSTED"))
        self.ledger.unverified = "ledger unreadable"
        self.assertEqual(self.refused(body())["refused"], "budget cannot be verified: ledger unreadable")
        self.assertEqual(self.marker()["reason"], "budget cannot be verified: ledger unreadable")
        self.ledger.unverified = ""
        os.remove(os.path.join(self.tmp, "EXHAUSTED"))
        with mock.patch.object(mp, "STOP_AFTER", NOW - 1):
            self.assertEqual(self.refused(body())["kind"], "budget")
        self.assertIn("promotional credits ended", self.marker()["reason"])

    def test_an_unknown_reason_is_treated_as_a_budget_failure(self):
        self.ledger.refuse("/v1/messages", "a reason nobody listed")
        self.assertEqual(self.marker()["reason"], "a reason nobody listed")
        self.assertEqual(self.audit()[-1]["kind"], "budget")

    # ---- a momentary hold by other requests is not exhaustion
    def test_open_reservations_alone_do_not_look_like_a_spent_month(self):
        self.start(2.0)
        self.at(NOW)
        first, _, _ = self.ledger.admit(body())
        self.assertTrue(first)
        rid, _, why = self.ledger.admit(body())  # $1.02 held: a second one does not fit for the moment
        self.assertFalse(rid)
        self.assertEqual(why, "monthly reservations in flight")
        self.ledger.refuse("/v1/messages", why)
        self.assertEqual((self.audit()[-1]["kind"], self.marker()), ("busy", None))
        self.ledger.settle(0.0, 0.0, "/v1/messages", 200, {"output_tokens": 1}, rid=first)
        self.assertTrue(self.ledger.admit(body())[0])  # released: the same request is admitted

    def test_spend_that_really_leaves_no_room_is_exhaustion_even_with_nothing_in_flight(self):
        self.start(2.0)
        self.at(NOW)
        self.ledger.spent = 1.5
        rid, _, why = self.ledger.admit(body())
        self.assertEqual((rid, why), (False, "cap reached or stopped"))
        self.ledger.refuse("/v1/messages", why)
        self.assertEqual(self.marker()["reason"], "cap reached or stopped")

    def test_the_scope_distinguishes_a_spent_cap_from_a_reservation_in_flight(self):
        self.open_scope(R / 1e9)
        first = self.ledger.admit(body())[0]
        rid, _, why = self.ledger.admit(body())
        self.assertEqual((bool(first), rid, why), (True, False, "scope reservations in flight"))
        self.ledger.refuse("/v1/messages", why)
        self.ledger.settle(0.0, 0.0, "/v1/messages", 200, {"output_tokens": 1}, rid=first)
        self.ledger.scope["spent_nano"] = R
        self.assertEqual(self.ledger.admit(body())[2], "scope cap reached")
        self.assertIsNone(self.marker())

    # ---- overlapping reasons
    def test_a_scope_refusal_while_the_month_is_really_spent_still_raises_the_monthly_alert(self):
        self.open_scope(2.0)
        self.ledger.clock = lambda: NOW + 7200  # the scope has expired
        self.ledger.spent = 24.5
        row = self.refused(body())
        self.assertEqual((row["refused"], row["kind"]), ("scope expired and not closed", "scope"))
        self.assertEqual(self.marker()["reason"], "cap reached or stopped")

    def test_a_scope_refusal_while_only_reservations_are_open_does_not(self):
        self.open_scope(2.0)
        self.ledger.clock = lambda: NOW + 7200
        self.ledger.reserved = 24.9  # transient holds, not spend
        self.refused(body())
        self.assertIsNone(self.marker())

    def test_an_invalid_request_while_the_month_is_spent_alerts_but_an_oversize_body_never_does(self):
        self.ledger.spent = 24.5
        self.assertEqual(self.refused(body(service_tier="x"))["kind"], "invalid")
        self.assertEqual(self.marker()["reason"], "cap reached or stopped")
        os.remove(os.path.join(self.tmp, "EXHAUSTED"))
        self.ledger.refuse("/v1/messages", "bad body size")
        self.assertIsNone(self.marker())

    # ---- deduplication, preservation, renewal
    def test_scope_refusals_never_consume_the_alert_and_a_later_real_exhaustion_still_creates_it(self):
        self.open_scope(0.5)
        for _ in range(3):
            self.refused(body())
        self.refused(body(service_tier="x"))
        self.assertIsNone(self.marker())
        self.ledger.spent = 24.5
        self.refused(body())
        self.assertEqual(self.marker()["reason"], "cap reached or stopped")

    def test_an_existing_marker_is_preserved_by_later_refusals_and_renewed_by_the_next_window(self):
        self.ledger.spent = 24.5
        self.refused(body())
        first = self.marker()
        self.open_scope(0.5)
        self.ledger.spent = 24.5
        self.refused(body())
        self.refused(body(service_tier="x"))
        self.ledger.refuse("/v1/messages", "cap reached or stopped")
        self.assertEqual(self.marker(), first)  # not overwritten, not reset, not duplicated in the window
        window = mp.window(NOW)[1] + 60  # the next monthly window
        self.at(window)
        self.ledger.spent = 24.5
        self.ledger.refuse("/v1/messages", "cap reached or stopped")
        self.assertEqual(self.marker()["window"], self.ledger.start)
        self.assertNotEqual(self.marker()["window"], first["window"])

    def test_a_restart_keeps_the_spend_so_the_exhaustion_is_still_refused_and_alerted(self):
        self.ledger.spent = 24.5
        self.ledger.save()
        again = mp.Ledger(state_dir=self.tmp, cap=25, clock=lambda: NOW)
        rid, _, why = again.admit(body())
        self.assertEqual((rid, why), (False, "cap reached or stopped"))
        again.refuse("/v1/messages", why)
        self.assertEqual(self.marker()["reason"], "cap reached or stopped")

    def test_free_embeddings_and_the_audit_trail_are_untouched_by_all_of_this(self):
        open(self.ledger.stop_path, "w").close()
        self.refused(body())
        self.assertEqual(self.post("/v1/embeddings", {"input": "x"})[0], 200)
        self.assertTrue(all("kind" in row for row in self.audit() if row.get("refused")))
        self.assertEqual([s for s in Upstream.seen if s["path"] == "/v1/messages"], [])


if __name__ == "__main__":
    unittest.main()
