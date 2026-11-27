"""(REQ-85 T2): the budget model and the guard, played against a deterministic simulator of the meter and of Honcho's
single-worker queue.
"""

import importlib.util
import pathlib
import random
import unittest
from tests.kit.tool_modules import load_tool

ROOT = pathlib.Path(__file__).resolve().parents[2]


load = load_tool


budget = load("um_budget")
guard = load("um_budget_guard")
BASE = 1.0


class Crash(BaseException):
    """The guard process dies (kill -9): nothing after it runs."""


class World:
    def __init__(self, cost="worst", seed=0, dream_attempts=1, rep_attempts=budget.BILLED["modeled"], explicit=None):
        self.t, self.spent, self.others = 0.0, BASE, 0.0
        self.queue, self.completed, self.posted, self.dead = [], 0, 0, False
        self.flags, self.cost, self.rng = [False, False], cost, random.Random(seed)
        if cost == "typical":
            dream_attempts, rep_attempts = 1, 1
        self.dream_attempts, self.rep_attempts, self.explicit = dream_attempts, rep_attempts, explicit
        self.burst_at, self.burst = None, 0.0
        self.dream_at_worst = False
        self.volume = None
        self.sleeps, self.crash_at, self.other_rate = 0, None, 0.0
        self.meter_fail = set()  # reading numbers that raise
        self.meter_frozen_after = None
        self.frozen_value = None
        self.token_dead_at = None
        self.flag_fail = False
        self.dream_attempt_cost = None
        self.reads, self.refusing_from, self.expire_from, self.queue_at_arm = 0, None, None, []
        self.scope_id, self.scope_state, self.scope_cap, self.blocked, self.scope_gone, self.bad_doc = (
            "t2",
            "open",
            budget.SCOPE_CAP,
            None,
            False,
            False,
        )
        self.dream_in_queue = False
        self.armed_posts = 0

    def call_cost(self, worst):
        return worst if self.cost == "worst" else (worst * 0.3 if self.cost == "typical" else worst * self.rng.random())

    def step(self, dt):
        for _ in range(int(dt)):
            self.t += 1
            if self.burst_at is not None and self.t == self.burst_at:
                self.spent += self.burst
                self.others += self.burst
            self.spent += self.other_rate
            self.others += self.other_rate
            if self.refusing_from is not None and self.t >= self.refusing_from:
                continue
            if not self.queue:
                continue
            item = self.queue[0]
            if item["kind"] == "attempt" and not item["started"]:
                if not (self.flags[0] and self.flags[1]):
                    self.queue.pop(0)
                    continue
                item["started"] = True
            cost = item["costs"].pop(0)
            self.spent += cost
            if not item["costs"]:
                self.queue.pop(0)
                self.completed += 1 if item["kind"] == "unit" else 0
                if item["kind"] == "attempt" and not any(i["kind"] == "attempt" for i in self.queue):
                    self.dream_in_queue = False

    def drain(self):
        while self.queue and not (
            self.refusing_from is not None and self.t >= self.refusing_from
        ):  # a refusing meter never drains
            self.step(1)

    def ours(self):
        return self.spent - BASE - self.others


class Meter:
    def __init__(self, w):
        self.w = w

    def reading(self):
        """The meter's /health document: the monthly figure and the scope, whose spend is everything metered since it
        opened (here: all spend above BASE).
        """
        w = self.w
        w.reads += 1
        if w.reads in w.meter_fail:
            raise TimeoutError("meter")
        if w.bad_doc:
            return {"ok": True, "spent_usd": w.spent, "scope": {"id": w.scope_id}}
        spent = w.spent
        if w.meter_frozen_after is not None and w.reads > w.meter_frozen_after:
            if w.frozen_value is None:
                w.frozen_value = w.spent
            spent = w.frozen_value
        blocked = w.blocked or (
            "scope expired and not closed" if w.expire_from is not None and w.t >= w.expire_from else None
        )
        doc = {"ok": True, "spent_usd": spent, "cap_usd": 25.0, "stopped": False, "blocked": blocked}
        if not w.scope_gone:
            doc["scope"] = {"id": w.scope_id, "cap_usd": w.scope_cap, "spent_usd": spent - BASE, "state": w.scope_state}
        return doc


class Honcho:
    def __init__(self, w):
        self.w = w

    def _live(self):
        if self.w.dead:
            raise guard.HonchoError("process_dead")
        if self.w.token_dead_at is not None and self.w.t >= self.w.token_dead_at:
            raise guard.Unauthorized("401")

    def queue_idle(self):
        self._live()
        return not self.w.queue

    def completed_units(self):
        self._live()
        return self.w.completed

    def volume_chars(self):
        self._live()
        return self.w.volume if self.w.volume is not None else self.w.posted * 200

    def explicit_count(self):
        self._live()
        return self.w.explicit if self.w.explicit is not None else self.w.posted * 2

    def get_flags(self):
        self._live()
        return tuple(self.w.flags)

    def set_flags(self, reasoning, dream):
        self._live()
        if self.w.flag_fail and (reasoning or dream):
            raise guard.HonchoError("config_refused")
        if self.flag_fail_disarm and not (reasoning or dream):
            raise guard.HonchoError("disarm_refused")
        self.w.flags = [reasoning, dream]

    flag_fail_disarm = False

    def post_messages(self, batch, armed):
        self._live()
        self.w.queue_at_arm.append(len(self.w.queue))
        for _ in batch:
            self.w.posted += 1
            if armed or self.w.flags[0]:
                self.w.armed_posts += 1
                worst = budget.call_worst(budget.DERIVER_IN, budget.DERIVER_OUT)
                self.w.queue.append(
                    {"kind": "unit", "costs": [self.w.call_cost(worst) for _ in range(self.w.rep_attempts)]}
                )

    def schedule_dream(self):
        self._live()
        self.w.queue_at_arm.append(len(self.w.queue))
        worst = budget.dream_attempt_worst() / sum(budget.DREAM_ITER)
        for _ in range(self.w.dream_attempts):
            self.w.queue.append(
                {
                    "kind": "attempt",
                    "started": False,
                    "costs": [
                        worst if self.w.dream_at_worst else self.w.call_cost(worst)
                        for _ in range(sum(budget.DREAM_ITER))
                    ],
                }
            )
        self.w.dream_in_queue = True

    def dream_finished(self):
        self._live()
        return not self.w.dream_in_queue


class Clock:
    def __init__(self, w):
        self.w = w

    def now(self):
        return self.w.t

    def sleep(self, dt):
        self.w.sleeps += 1
        if self.w.crash_at is not None and self.w.sleeps == self.w.crash_at:
            self.w.dead = True
            raise Crash()
        self.w.step(dt)


def run(world, messages=20, scope_id="t2"):
    w = world
    g = guard.Guard(Meter(w), Honcho(w), Clock(w), scope_id=scope_id)
    try:
        report = g.run([{"content": f"m{i}"} for i in range(messages)])
    except Crash:
        report = {"stopped": "crashed"}
    w.drain()
    return g, report


class Model(unittest.TestCase):
    def test_the_prices_are_the_meters_prices(self):
        meter = (ROOT / "lifeos" / "edge" / "honcho" / "meter_proxy.py").read_text()
        self.assertIn('PRICES = {"input": 0.10, "cache_write": 0.125, "cache_read": 0.01, "output": 0.50}', meter)
        self.assertEqual((budget.PRICE_IN, budget.PRICE_OUT), (0.10, 0.50))

    def test_the_modeled_exposures_fit_the_ceiling_in_the_only_orders_the_guard_allows(self):
        self.assertLess(
            budget.tranche_worst() + budget.dream_worst(), budget.CEILING
        )  # one tranche then the dream (one attempt)
        self.assertLess(budget.dream_worst(), budget.CEILING)
        n = budget.numbers()
        self.assertAlmostEqual(
            n["message_worst_modeled_usd"], 9 * budget.call_worst(budget.DERIVER_IN, budget.DERIVER_OUT), places=3
        )
        self.assertAlmostEqual(n["dream_attempt_worst_usd"], 0.1654, places=3)

    def test_the_code_limits_the_model_rests_on_are_not_the_earlier_guess(self):
        self.assertEqual((budget.DREAM_OUT, budget.DERIVER_OUT, budget.DERIVER_IN), (8192, 2500, 25000))
        self.assertEqual(budget.DREAM_ITER, (13, 11))

    def test_at_two_dollars_even_the_strict_profile_and_the_queue_limit_fit_the_model(self):
        self.assertEqual((budget.CEILING, budget.TRIP, budget.SCOPE_CAP), (2.00, 0.80, 2.00))
        self.assertLess(budget.dream_worst(budget.DREAM_ATTEMPTS_QUEUE) + budget.tranche_worst(1), budget.CEILING)
        self.assertLessEqual(budget.tranche_worst(1, "strict") + budget.dream_worst(profile="strict"), budget.CEILING)
        self.assertLess(budget.TRIP, budget.SCOPE_CAP - budget.RESERVATION_MAX)

    def test_the_reservation_is_the_meters_documented_maximum(self):
        spec = importlib.util.spec_from_file_location(
            "meter_proxy_under_test", ROOT / "lifeos" / "edge" / "honcho" / "meter_proxy.py"
        )
        meter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(meter)
        self.assertAlmostEqual(budget.RESERVATION_MAX, meter.reservation_nano(budget.DREAM_OUT) / 1e9, places=9)

    def test_one_reservation_must_still_fit_the_scope_to_arm(self):
        limit = budget.SCOPE_CAP - budget.RESERVATION_MAX  # 0.97952
        self.assertTrue(budget.admissible(limit))
        self.assertFalse(budget.admissible(limit + 0.01))
        self.assertAlmostEqual(budget.numbers()["admission_limit_usd"], 0.97952, places=5)

    def test_arming_refuses_a_step_that_would_not_fit_and_a_negative_reading(self):
        self.assertTrue(budget.can_arm(0.0, budget.tranche_worst(), budget.dream_worst()))
        self.assertFalse(budget.can_arm(1.85, budget.tranche_worst(), budget.dream_worst()))
        self.assertFalse(budget.can_arm(-0.01, 0.0, 0.0))

    def test_the_explicit_limit_keeps_every_automatic_dream_out_of_reach(self):
        self.assertLess(budget.EXPLICIT_MAX, 50)  # Honcho's DREAM.DOCUMENT_THRESHOLD


class Bound(unittest.TestCase):
    def check(self, w, report=None):
        self.assertLessEqual(w.ours(), budget.CEILING + 1e-9, report)

    def test_a_full_run_at_typical_prices_finishes_and_costs_cents(self):
        w = World("typical")
        g, report = run(w)
        self.assertIsNone(report["stopped"], report)
        self.assertEqual(w.armed_posts, 20)
        self.assertLess(w.ours(), 0.10)
        self.assertEqual(w.flags, [False, False])

    def test_every_call_at_the_worst_price_and_every_retry_stays_inside_the_ceiling(self):
        w = World("worst")
        g, report = run(w)
        self.check(w, report)
        self.assertIsNotNone(report["stopped"])

    def test_a_crash_at_every_single_step_leaves_only_bounded_spend(self):
        probe = World("worst")
        run(probe)
        total = probe.sleeps
        self.assertGreater(total, 30)
        for k in range(1, total + 1):
            w = World("worst")
            w.crash_at = k
            run(w)
            self.check(w, k)

    def test_a_crash_while_a_dream_with_all_its_retries_is_queued_stays_inside(self):
        for k in range(1, 400):
            w = World("typical")
            w.crash_at = k
            run(w)
            self.check(w, k)
            w2 = World("worst")
            w2.crash_at = k
            run(w2)
            self.check(w2, k)

    def test_cheap_seeding_then_a_dream_at_the_worst_price_and_a_crash_at_every_step(self):
        probe = World("typical")
        probe.dream_at_worst = True
        run(probe)
        peak = 0.0
        for k in range(0, probe.sleeps + 1):
            w = World("typical")
            w.dream_at_worst = True
            w.crash_at = k or None
            run(w)
            self.check(w, k)
            peak = max(peak, w.ours())
        self.assertGreater(peak, 0.15)
        self.assertLessEqual(peak, budget.CEILING)

    def test_a_dead_guard_and_all_the_queues_dream_attempts_no_longer_pass_the_ceiling_in_the_model(self):
        """At $0.50 this was a documented residual only the meter could close"""
        peak = 0.0
        probe = World("typical")
        probe.dream_attempts, probe.dream_at_worst = 3, True
        run(probe)
        for k in range(1, probe.sleeps + 1):
            w = World("typical")
            w.dream_attempts, w.dream_at_worst = 3, True
            w.crash_at = k
            run(w)
            peak = max(peak, w.ours())
        self.assertGreater(peak, 0.3)
        self.assertLessEqual(peak, budget.CEILING)

    def test_random_prices_and_random_failures_never_break_the_ceiling(self):
        for seed in range(250):
            rng = random.Random(seed)
            w = World("random", seed=seed)
            w.crash_at = rng.choice([None, rng.randint(1, 500)])
            w.meter_fail = {rng.randint(1, 120) for _ in range(rng.randint(0, 6))}
            w.flag_fail = rng.random() < 0.15
            w.token_dead_at = rng.choice([None, rng.randint(5, 400)])
            w.other_rate = rng.choice([0.0, 0.0, 0.00005])
            run(w)
            self.check(w, seed)


class Scope(unittest.TestCase):
    """the hardened meter's own scoped health is the authority"""

    def stopped(self, **change):
        w = World("typical")
        for key, value in change.items():
            setattr(w, key, value)
        g, report = run(w)
        return w, report

    def test_the_old_fifty_cent_defaults_no_longer_gate_the_run(self):
        w = World("worst")
        g, report = run(w)
        self.assertGreater(w.ours(), 0.50)  # past the old ceiling, still armed and running
        self.assertLessEqual(w.ours(), budget.CEILING)
        self.assertIn(report["stopped"], (None, "tripped"))  # only the $0.80 brake or the end of the run
        self.assertEqual(report["scope"]["cap_usd"], budget.SCOPE_CAP)

    def test_a_full_typical_run_reports_the_scope_and_the_cleanup_evidence(self):
        w, report = self.stopped()
        self.assertIsNone(report["stopped"], report)
        self.assertEqual(report["scope"]["state"], "open")
        self.assertEqual(report["cleanup"], {"flags": [False, False], "queue_idle": True})

    def test_each_wrong_scope_stops_before_anything_is_touched_or_armed(self):
        cases = {
            "scope_missing": {"scope_gone": True},
            "scope_mismatch": {"scope_id": "someone-elses"},
            "scope_cap_wrong": {"scope_cap": 25.0},
            "scope_not_open": {"scope_state": "breached"},
            "meter_blocked": {"blocked": "scope expired and not closed"},
            "meter_malformed": {"bad_doc": True},
        }
        for reason, change in cases.items():
            with self.subTest(reason):
                w, report = self.stopped(**change)
                self.assertEqual(report["stopped"], reason)
                self.assertEqual((w.armed_posts, w.spent), (0, BASE))
                self.assertEqual(w.flags, [False, False])

    def test_a_guard_given_no_scope_id_stops_and_arms_nothing(self):
        w = World("typical")
        g, report = run(w, scope_id=None)
        self.assertEqual((report["stopped"], w.armed_posts), ("scope_mismatch", 0))

    def test_the_scope_expiring_mid_run_disarms_and_stops(self):
        w, report = self.stopped(expire_from=12)
        self.assertEqual(report["stopped"], "meter_blocked")
        self.assertEqual(w.flags, [False, False])
        self.assertIn({"t": report["events"][-1]["t"], "e": "disarmed"}, report["events"])
        self.assertLessEqual(w.ours(), budget.CEILING)

    def test_a_meter_that_refuses_every_call_is_a_stall_the_guard_stops_on_without_retrying(self):
        w, report = self.stopped(refusing_from=8)
        self.assertEqual(report["stopped"], "no_progress")
        self.assertEqual(w.flags, [False, False])
        posts = w.armed_posts
        self.assertLess(posts, 20)  # nothing was armed after the stall began
        self.assertEqual(report["cleanup"]["flags"], [False, False])
        self.assertIs(report["cleanup"]["queue_idle"], False)

    def test_spend_at_the_brake_trips_and_disarms(self):
        w = World("typical")
        w.burst_at, w.burst = 6, 0.85
        g, report = run(w)
        self.assertIn(report["stopped"], ("tripped", "exposure_exceeded"))
        self.assertEqual(w.flags, [False, False])
        self.assertGreaterEqual(w.spent - BASE, budget.TRIP)

    def test_no_step_is_armed_unless_one_more_reservation_fits_the_scope(self):
        w = World("typical")
        w.spent = BASE + 0.99
        g, report = run(w)
        self.assertEqual((report["stopped"], w.armed_posts), ("admission_limit", 0))
        w2 = World("typical")
        w2.spent = BASE + 0.5
        self.assertIsNone(run(w2)[1]["stopped"])

    def test_every_step_is_armed_with_nothing_in_flight(self):
        for cost in ("typical", "worst"):
            w = World(cost)
            run(w)
            self.assertGreater(len(w.queue_at_arm), 5)
            self.assertEqual(set(w.queue_at_arm), {0})

    def test_a_meter_timeout_while_armed_is_counted_and_never_arms_more(self):
        w = World("typical")
        w.meter_fail = set(range(8, 14))
        g, report = run(w)
        self.assertLessEqual(w.ours(), budget.CEILING)
        self.assertEqual(w.flags, [False, False])


class Failures(unittest.TestCase):
    def test_a_meter_timeout_before_any_arming_arms_nothing(self):
        w = World("typical")
        w.meter_fail = {1, 2, 3}
        g, report = run(w)
        self.assertEqual((w.armed_posts, w.spent), (0, BASE))
        self.assertTrue(report["stopped"].startswith("meter_"))

    def test_a_meter_timeout_at_each_later_reading_never_arms_more_after_it(self):
        base = World("typical")
        run(base)
        for r in range(1, base.reads + 1):
            w = World("typical")
            w.meter_fail = {r}
            g, report = run(w)
            self.assertLessEqual(w.ours(), budget.CEILING)
            self.assertTrue(report["stopped"] is None or isinstance(report["stopped"], str))

    def test_a_stale_meter_after_work_finished_is_detected_and_stops_the_run(self):
        w = World("typical")
        w.meter_frozen_after = 8
        g, report = run(w)
        self.assertIn(report["stopped"], ("meter_stale", "exposure_exceeded"))
        self.assertLessEqual(w.ours(), budget.tranche_worst() + 1e-9)

    def test_a_refused_arming_of_the_dream_schedules_nothing_and_costs_nothing_more(self):
        w = World("typical")
        orig = Honcho.set_flags

        def flaky(self, reasoning, dream):
            if reasoning and dream:
                raise guard.HonchoError("config_refused")
            return orig(self, reasoning, dream)

        Honcho.set_flags = flaky
        try:
            g, report = run(w)
        finally:
            Honcho.set_flags = orig
        self.assertEqual(report["stopped"], "arm_failed")
        self.assertFalse(w.dream_in_queue)
        self.assertLess(w.ours(), 0.10)

    def test_a_refused_disarm_is_recorded_not_raised_and_the_bound_holds(self):
        w = World("worst")
        Honcho.flag_fail_disarm = True
        try:
            g, report = run(w)
        finally:
            Honcho.flag_fail_disarm = False
        self.assertLessEqual(w.ours(), budget.CEILING)
        self.assertTrue(any(e["e"] == "disarm_failed" for e in g.events) or report["stopped"])

    def test_an_expired_token_stops_the_run_at_every_point(self):
        for when in range(1, 300, 7):
            w = World("worst")
            w.token_dead_at = when
            g, report = run(w)
            self.assertLessEqual(w.ours(), budget.CEILING)
            self.assertTrue(report["stopped"], when)

    def test_flags_that_cannot_be_verified_off_stop_before_anything_is_armed(self):
        w = World("typical")
        w.flags = [True, True]
        orig = Honcho.set_flags
        Honcho.set_flags = lambda self, r, d: None
        try:
            g, report = run(w)
        finally:
            Honcho.set_flags = orig
        self.assertEqual((report["stopped"], w.armed_posts), ("flags_unverified", 0))

    def test_a_message_over_the_limit_and_too_much_workspace_text_are_refused_before_anything_is_armed(self):
        w = World("typical")
        g = guard.Guard(Meter(w), Honcho(w), Clock(w), scope_id="t2")
        self.assertEqual(g.run([{"content": "x" * (budget.MESSAGE_CHARS_MAX + 1)}])["stopped"], "message_too_long")
        self.assertEqual((w.armed_posts, w.spent), (0, BASE))
        w2 = World("typical")
        w2.volume = budget.VOLUME_CHARS_MAX
        g2 = guard.Guard(Meter(w2), Honcho(w2), Clock(w2), scope_id="t2")
        self.assertEqual(g2.run([{"content": "ok"}])["stopped"], "volume_exceeded")
        self.assertEqual((w2.armed_posts, w2.spent), (0, BASE))

    def test_seeded_directly_only_the_dream_is_armed_and_the_text_limit_still_applies(self):
        w = World("typical", explicit=40)
        w.posted = 40
        g = guard.Guard(Meter(w), Honcho(w), Clock(w), scope_id="t2")
        report = g.run([], seeded=True)
        self.assertIsNone(report["stopped"], report)
        self.assertEqual(w.armed_posts, 0)
        self.assertLessEqual(w.ours(), budget.dream_worst() + 1e-9)

    def test_too_many_conclusions_stops_before_the_dream(self):
        w = World("typical", explicit=60)
        g, report = run(w)
        self.assertEqual(report["stopped"], "too_many_conclusions_for_isolation")
        self.assertFalse(w.dream_in_queue)

    def test_a_cost_above_the_models_assumption_stops_further_arming(self):
        w = World("typical")
        orig = World.call_cost
        World.call_cost = lambda self, worst: worst * 40
        try:
            g, report = run(w)
        finally:
            World.call_cost = orig
        self.assertIn(report["stopped"], ("exposure_exceeded", "tripped"))
        self.assertEqual(w.armed_posts, 3)

    def test_spend_by_someone_else_counts_against_the_run_so_it_only_stops_sooner(self):
        w = World("typical")
        w.other_rate = 0.004
        g, report = run(w)
        self.assertLessEqual(w.ours(), budget.CEILING)
        self.assertTrue(report["stopped"])

    def test_a_constant_concurrent_spender_means_the_ledger_never_settles_and_nothing_is_armed(self):
        w = World("typical")
        w.other_rate = 0.02
        g, report = run(w)
        self.assertEqual((report["stopped"], w.armed_posts), ("drain_timeout", 0))

    def test_a_burst_of_other_spend_during_a_step_trips_the_brake_and_disarms(self):
        w = World("typical")
        w.burst_at, w.burst = 4, 0.85
        g, report = run(w)
        self.assertIn(report["stopped"], ("tripped", "exposure_exceeded"))
        self.assertEqual(w.flags, [False, False])
        self.assertEqual(w.armed_posts, 3)  # nothing more was armed after the burst


if __name__ == "__main__":
    unittest.main()
