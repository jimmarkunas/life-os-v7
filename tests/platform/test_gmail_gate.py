import contextlib
import unittest
from unittest import mock

from lifeos.platform import api_counts, gmail_gate, limits

API = "https://gmail.googleapis.com/gmail/v1/users/me"


class Store:
    """The one lease row, answered the way MySQL would, on a clock the test moves."""

    def __init__(self):
        self.now, self.holder, self.until, self.down = 0.0, "", -1.0, False

    @contextlib.contextmanager
    def connect(self):
        if self.down:
            raise RuntimeError("STORE_DB_CONNECT_FAILED")
        store = self

        class Cursor:
            row = None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, args=()):
                if sql.startswith("UPDATE") and "holder = %s" in sql.split("WHERE")[0] and "NOW(3) +" in sql:
                    me, seconds, again = args
                    if store.holder == again or store.until < store.now:
                        store.holder, store.until = me, store.now + seconds
                elif sql.startswith("UPDATE"):
                    if store.holder == args[0]:
                        store.until = store.now - 1
                elif sql.startswith("SELECT"):
                    Cursor.row = (store.holder,)

            def fetchone(self):
                return Cursor.row

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

        yield Connection()


class Units(unittest.TestCase):
    def test_every_call_is_priced_from_the_current_quota_table(self):
        price = gmail_gate.units
        self.assertEqual(price("GET", f"{API}/messages?q=x&maxResults=500"), 5)
        self.assertEqual(price("GET", f"{API}/messages/abc?format=full"), 60)
        self.assertEqual(price("GET", f"{API}/messages/abc"), 60)  # Gmail's default format is full
        self.assertEqual(price("GET", f"{API}/messages/abc?format=metadata&metadataHeaders=From"), 20)
        self.assertEqual(price("GET", f"{API}/messages/abc?format=minimal"), 20)
        self.assertEqual(price("POST", f"{API}/messages/abc/trash"), 20)
        self.assertEqual(price("POST", f"{API}/messages/abc/modify"), 5)
        self.assertEqual(price("POST", f"{API}/messages/batchModify"), 50)
        self.assertEqual(price("POST", f"{API}/messages/send"), 100)
        self.assertEqual(price("GET", f"{API}/threads/t?format=minimal"), 40)
        self.assertEqual(price("GET", f"{API}/threads/t?format=full"), 120)
        self.assertEqual(price("GET", f"{API}/labels"), 1)
        self.assertEqual(price("POST", f"{API}/labels"), 5)
        self.assertEqual(price("GET", f"{API}/profile"), 1)
        self.assertEqual(price("GET", f"{API}/history?startHistoryId=1"), 2)
        self.assertEqual(price("GET", f"{API}/somethingnew"), limits.GMAIL_UNIT_DEFAULT)  # unknown is priced high


class Budget(unittest.TestCase):
    def test_our_ceiling_and_burst_stay_under_the_provider_minute_even_across_lease_handovers(self):
        self.assertLess(limits.GMAIL_UNITS_PER_MINUTE, limits.GMAIL_UNITS_PER_MINUTE_PROVIDER)
        handovers = 10
        worst = limits.GMAIL_UNITS_PER_MINUTE + handovers * limits.GMAIL_BURST_UNITS
        self.assertLess(worst, limits.GMAIL_UNITS_PER_MINUTE_PROVIDER)
        self.assertGreaterEqual(
            limits.GMAIL_BURST_UNITS, limits.GMAIL_UNIT_MESSAGE_FULL
        )  # one full read fits the burst
        self.assertLess(limits.GMAIL_LEASE_RENEW_SECONDS, limits.GMAIL_LEASE_SECONDS)

    def test_the_bucket_pays_out_the_burst_then_makes_the_caller_wait_for_the_refill(self):
        bucket = gmail_gate.Bucket(per_minute=600, burst=100, now=0.0)  # 10 units a second
        self.assertEqual(bucket.take(60, 0.0), 0)
        self.assertEqual(bucket.take(60, 0.0), 2.0)  # 20 short: 2 s at 10 a second
        self.assertEqual(bucket.take(10, 2.0), 1.0)  # the 2 s refilled the debt exactly; 10 more is 1 s away
        self.assertEqual(bucket.take(0, 100.0), 0)  # never above the burst
        self.assertEqual(bucket.tokens, 100)

    def test_a_rate_limit_halves_the_rate_down_to_the_floor(self):
        bucket = gmail_gate.Bucket(per_minute=4500, burst=120, now=0.0)
        for _ in range(10):
            bucket.slow(limits.GMAIL_RATE_SLOWDOWN, limits.GMAIL_MIN_UNITS_PER_MINUTE)
        self.assertEqual(bucket.rate * 60, limits.GMAIL_MIN_UNITS_PER_MINUTE)


class Spend(unittest.TestCase):
    def setUp(self):
        api_counts.begin()
        self.sleeps = []
        patcher = mock.patch("time.sleep", side_effect=self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_calls_past_the_burst_wait_and_the_stage_row_gets_units_and_peaks(self):
        gate = gmail_gate.Gate(enabled=True)
        for _ in range(4):
            with gate.spend("GET", f"{API}/messages/a?format=full"):
                pass
        tally = api_counts.take()
        self.assertEqual(tally["gmail_units"], 240)
        self.assertTrue(self.sleeps and all(s > 0 for s in self.sleeps))
        self.assertGreater(tally["gmail_wait_s"], 0)
        self.assertEqual(tally["gmail_peak_in_flight"], 1)
        self.assertLessEqual(tally["gmail_peak_units_per_s"], 240)

    def test_a_disabled_gate_never_waits_and_adds_nothing(self):
        gate = gmail_gate.Gate(enabled=False)
        for _ in range(10):
            with gate.spend("GET", f"{API}/messages/a?format=full"):
                pass
        self.assertEqual((self.sleeps, api_counts.take()), ([], {}))

    def test_no_more_than_the_in_flight_cap_are_spending_at_once(self):
        gate = gmail_gate.Gate(enabled=True)
        held = [gate.spend("GET", f"{API}/profile") for _ in range(limits.GMAIL_MAX_IN_FLIGHT)]
        for each in held:
            each.__enter__()
        self.assertFalse(gate._slots.acquire(blocking=False))
        for each in held:
            each.__exit__(None, None, None)
        self.assertTrue(gate._slots.acquire(blocking=False))
        gate._slots.release()
        self.assertEqual(api_counts.take()["gmail_peak_in_flight"], limits.GMAIL_MAX_IN_FLIGHT)

    def test_slow_counts_and_lowers_the_rate(self):
        gate = gmail_gate.Gate(enabled=True)
        before = gate._bucket.rate
        gate.slow()
        self.assertEqual(gate._bucket.rate, before * limits.GMAIL_RATE_SLOWDOWN)
        self.assertEqual(api_counts.take()["gmail_slowdowns"], 1)


class Lease(unittest.TestCase):
    def setUp(self):
        api_counts.begin()
        self.store = Store()
        env = mock.patch.dict("os.environ", {"LIFEOS_ACQ_DB_PASSWORD": "x"})
        env.start()
        self.addCleanup(env.stop)
        connect = mock.patch("lifeos.platform.db.connect", self.store.connect)
        connect.start()
        self.addCleanup(connect.stop)
        self.sleeps = []

        def sleep(seconds):
            self.sleeps.append(seconds)
            self.store.now += seconds

        for name, fake in (("time.sleep", sleep), ("time.monotonic", lambda: self.store.now)):
            patcher = mock.patch(name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def use(self, gate):
        with gate.spend("GET", f"{API}/profile"):
            pass

    def test_two_processes_share_one_budget_the_second_waits_until_the_first_lets_go(self):
        first, second = gmail_gate.Gate(enabled=True), gmail_gate.Gate(enabled=True)
        self.use(first)
        self.assertEqual(self.store.holder, first._me)
        self.use(second)  # waits for the first's lease to lapse (8 s), polling every second
        self.assertEqual(self.store.holder, second._me)
        self.assertEqual(len(self.sleeps), limits.GMAIL_LEASE_SECONDS + 1)
        tally = api_counts.take()
        self.assertGreaterEqual(tally["gmail_lease_wait_s"], limits.GMAIL_LEASE_SECONDS)
        self.assertNotIn("gmail_lease_open", tally)

    def test_a_holder_that_lets_go_hands_over_at_once(self):
        first, second = gmail_gate.Gate(enabled=True), gmail_gate.Gate(enabled=True)
        self.use(first)
        first.release()
        self.use(second)
        self.assertEqual((self.store.holder, self.sleeps), (second._me, []))

    def test_the_holder_renews_only_every_few_seconds(self):
        gate = gmail_gate.Gate(enabled=True)
        self.use(gate)
        until = self.store.until
        self.store.now += 1
        self.use(gate)
        self.assertEqual(self.store.until, until)  # inside the renew interval
        self.store.now += limits.GMAIL_LEASE_RENEW_SECONDS
        self.use(gate)
        self.assertGreater(self.store.until, until)

    def test_a_holder_that_never_lets_go_costs_a_waiter_at_most_the_wait_cap_then_it_goes_ahead(self):
        self.store.holder, self.store.until = "someone", 10**9
        gate = gmail_gate.Gate(enabled=True)
        self.use(gate)  # the call still happens (spend yielded)
        self.assertEqual(sum(self.sleeps), limits.GMAIL_LEASE_WAIT_MAX_SECONDS)
        self.assertEqual(api_counts.take()["gmail_lease_open"], 1)

    def test_a_caller_that_gave_up_waiting_does_not_wait_the_cap_again_on_its_next_calls(self):
        self.store.holder, self.store.until = "someone", 10**9
        gate = gmail_gate.Gate(enabled=True)
        for _ in range(3):
            self.use(gate)
        self.assertEqual(sum(self.sleeps), limits.GMAIL_LEASE_WAIT_MAX_SECONDS)  # once, not three times
        self.assertEqual(api_counts.take()["gmail_lease_open"], 1)
        self.store.now += limits.GMAIL_LEASE_OPEN_SECONDS  # a minute later it asks again
        self.use(gate)
        self.assertEqual(sum(self.sleeps), 2 * limits.GMAIL_LEASE_WAIT_MAX_SECONDS)

    def test_a_busy_holder_lets_go_after_the_hold_cap_so_a_waiter_is_served(self):
        gate = gmail_gate.Gate(enabled=True)
        self.use(gate)
        served = []

        def other_takes_during_the_rest(seconds):
            self.sleeps.append(seconds)
            self.store.now += seconds
            if seconds == limits.GMAIL_LEASE_REST_SECONDS and not served:
                served.append(self.store.now)  # a waiting process polls and takes the freed lease
                self.store.holder, self.store.until = "other", self.store.now + limits.GMAIL_LEASE_SECONDS

        with mock.patch("time.sleep", side_effect=other_takes_during_the_rest):
            while self.store.now < 3 * limits.GMAIL_LEASE_HOLD_MAX_SECONDS and not served:
                self.store.now += (
                    limits.GMAIL_LEASE_RENEW_SECONDS
                )  # a busy holder: a call every few seconds, never quiet
                self.use(gate)
        self.assertEqual(len(served), 1)
        self.assertLessEqual(
            served[0],
            limits.GMAIL_LEASE_HOLD_MAX_SECONDS
            + 2 * limits.GMAIL_LEASE_RENEW_SECONDS
            + limits.GMAIL_LEASE_REST_SECONDS,
        )
        self.assertEqual(api_counts.take()["gmail_lease_yield"], 1)

    def test_a_holder_nobody_waits_for_takes_the_lease_straight_back_after_its_rest(self):
        gate = gmail_gate.Gate(enabled=True)
        self.use(gate)
        for _ in range(8):
            self.store.now += limits.GMAIL_LEASE_RENEW_SECONDS
            self.use(gate)
        self.assertEqual(self.store.holder, gate._me)
        self.assertIn(limits.GMAIL_LEASE_REST_SECONDS, self.sleeps)
        self.assertNotIn("gmail_lease_open", api_counts.take())

    def test_an_unreachable_store_fails_open_once_and_is_not_asked_again(self):
        self.store.down = True
        gate = gmail_gate.Gate(enabled=True)
        self.use(gate)
        self.use(gate)
        self.assertEqual(api_counts.take()["gmail_lease_open"], 1)
        self.assertTrue(gate._off)

    def test_no_store_secrets_means_no_lease_and_no_store_call(self):
        with mock.patch.dict("os.environ", {"LIFEOS_ACQ_DB_PASSWORD": ""}):
            gate = gmail_gate.Gate(enabled=True)
            self.use(gate)
        self.assertEqual((self.store.holder, api_counts.take().get("gmail_lease_open")), ("", None))


if __name__ == "__main__":
    unittest.main()
