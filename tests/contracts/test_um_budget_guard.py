"""LIFE-1003 (REQ-85 T2): the budget model and the guard, played against a deterministic simulator of the meter and of Honcho's single-worker queue. No network, no paid call.
The property under test: whatever the guard does or fails to do (crash at any step, meter timeout, stale reading, refused configuration change, expired token, retries at the worst price, an already-running dream), the experiment's own spend never exceeds the ceiling."""
import importlib.util
import pathlib
import random
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


budget = load("um_budget")
guard = load("um_budget_guard")
BASE = 1.0                                                  # the shared ledger already holds spend that is not ours


class Crash(BaseException):
    """The guard process dies (kill -9): nothing after it runs."""


class World:
    def __init__(self, cost="worst", seed=0, dream_attempts=3, rep_attempts=budget.BILLED_ATTEMPTS, explicit=None):
        self.t, self.spent, self.others = 0.0, BASE, 0.0
        self.queue, self.completed, self.posted, self.dead = [], 0, 0, False
        self.flags, self.cost, self.rng = [False, False], cost, random.Random(seed)
        if cost == "typical":
            dream_attempts, rep_attempts = 1, 1                    # a normal run: one attempt each, about a third of the worst price
        self.dream_attempts, self.rep_attempts, self.explicit = dream_attempts, rep_attempts, explicit
        self.burst_at, self.burst = None, 0.0
        self.dream_at_worst = False
        self.sleeps, self.crash_at, self.other_rate = 0, None, 0.0
        self.meter_fail = set()        # reading numbers that raise
        self.meter_frozen_after = None
        self.frozen_value = None
        self.token_dead_at = None
        self.flag_fail = False
        self.dream_attempt_cost = None
        self.reads = 0
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
            if not self.queue:
                continue
            item = self.queue[0]
            if item["kind"] == "attempt" and not item["started"]:
                if not (self.flags[0] and self.flags[1]):          # the dream orchestrator checks the switches when an attempt starts
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
        while self.queue:
            self.step(1)

    def ours(self):
        return self.spent - BASE - self.others


class Meter:
    def __init__(self, w):
        self.w = w

    def spent(self):
        self.w.reads += 1
        if self.w.reads in self.w.meter_fail:
            raise TimeoutError("meter")
        if self.w.meter_frozen_after is not None and self.w.reads > self.w.meter_frozen_after:
            if self.w.frozen_value is None:
                self.w.frozen_value = self.w.spent
            return self.w.frozen_value
        return self.w.spent


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
        for _ in batch:
            self.w.posted += 1
            if armed or self.w.flags[0]:
                self.w.armed_posts += 1
                worst = budget.call_worst(budget.CALL_IN_MAX)
                self.w.queue.append({"kind": "unit", "costs": [self.w.call_cost(worst) for _ in range(self.w.rep_attempts)]})

    def schedule_dream(self):
        self._live()
        worst = budget.dream_attempt_worst() / sum(budget.DREAM_ITER)
        for _ in range(self.w.dream_attempts):
            self.w.queue.append({"kind": "attempt", "started": False, "costs": [worst if self.w.dream_at_worst else self.w.call_cost(worst) for _ in range(sum(budget.DREAM_ITER))]})
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


def run(world, messages=20):
    w = world
    g = guard.Guard(Meter(w), Honcho(w), Clock(w))
    try:
        report = g.run([{"content": f"m{i}"} for i in range(messages)])
    except Crash:
        report = {"stopped": "crashed"}
    w.drain()                                                # whatever was already armed keeps running after a crash
    return g, report


class Model(unittest.TestCase):
    def test_the_prices_and_the_output_cap_are_the_meters(self):
        meter = (ROOT / "lifeos" / "edge" / "honcho" / "meter_proxy.py").read_text()
        self.assertIn('PRICES = {"input": 0.10, "cache_write": 0.125, "cache_read": 0.01, "output": 0.50}', meter)
        self.assertEqual((budget.PRICE_IN, budget.PRICE_OUT), (0.10, 0.50))
        self.assertIn("DEFAULT_MAX_TOKENS = %d" % budget.OUT_MAX, meter)

    def test_the_worst_cases_fit_the_ceiling_in_the_only_orders_the_guard_allows(self):
        self.assertLess(budget.tranche_worst() + budget.dream_worst(), budget.CEILING)               # one tranche then the dream
        self.assertLess(budget.dream_worst(), budget.CEILING)
        n = budget.numbers()
        self.assertAlmostEqual(n["message_worst_usd"], 9 * budget.call_worst(budget.CALL_IN_MAX), places=3)
        self.assertLess(n["dream_worst_usd"], 0.35)

    def test_arming_refuses_a_step_that_would_not_fit_and_a_negative_reading(self):
        self.assertTrue(budget.can_arm(0.0, budget.tranche_worst(), budget.dream_worst()))
        self.assertFalse(budget.can_arm(0.12, budget.tranche_worst(), budget.dream_worst()))
        self.assertFalse(budget.can_arm(-0.01, 0.0, 0.0))

    def test_the_explicit_limit_keeps_every_automatic_dream_out_of_reach(self):
        self.assertLess(budget.EXPLICIT_MAX, 50)                                                     # Honcho's DREAM.DOCUMENT_THRESHOLD


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
        self.assertIsNotNone(report["stopped"])                                                      # the guard refuses to arm past the bound instead of spending to it

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

    def test_cheap_seeding_then_a_dream_at_the_worst_price_with_all_three_attempts_and_a_crash_at_every_step(self):
        probe = World("typical")
        probe.dream_attempts, probe.dream_at_worst = 3, True
        run(probe)
        self.assertIsNotNone(probe.sleeps)
        peak = 0.0
        for k in range(0, probe.sleeps + 1):
            w = World("typical")
            w.dream_attempts, w.dream_at_worst = 3, True
            w.crash_at = k or None
            run(w)
            self.check(w, k)
            peak = max(peak, w.ours())
        self.assertGreater(peak, 0.3)                                                                  # the dream really ran at full price in some crash schedule
        self.assertLessEqual(peak, budget.CEILING)

    def test_random_prices_and_random_failures_never_break_the_ceiling(self):
        for seed in range(250):
            rng = random.Random(seed)
            w = World("random", seed=seed, dream_attempts=rng.choice([1, 2, 3]))
            w.crash_at = rng.choice([None, rng.randint(1, 500)])
            w.meter_fail = {rng.randint(1, 120) for _ in range(rng.randint(0, 6))}
            w.flag_fail = rng.random() < 0.15
            w.token_dead_at = rng.choice([None, rng.randint(5, 400)])
            w.other_rate = rng.choice([0.0, 0.0, 0.00005])
            run(w)
            self.check(w, seed)


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

    def test_too_many_conclusions_stops_before_the_dream(self):
        w = World("typical", explicit=60)
        g, report = run(w)
        self.assertEqual(report["stopped"], "too_many_conclusions_for_isolation")
        self.assertFalse(w.dream_in_queue)

    def test_a_cost_above_the_models_assumption_stops_further_arming(self):
        w = World("typical")
        orig = World.call_cost
        World.call_cost = lambda self, worst: worst * 40                                           # one attempt per message, but each call 40 times the assumed worst price
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
        w.burst_at, w.burst = 4, 0.45
        g, report = run(w)
        self.assertIn(report["stopped"], ("tripped", "exposure_exceeded"))                           # seen live (brake) or at the next settle (counted as ours)
        self.assertEqual(w.flags, [False, False])
        self.assertEqual(w.armed_posts, 3)                                                           # nothing more was armed after the burst


if __name__ == "__main__":
    unittest.main()
