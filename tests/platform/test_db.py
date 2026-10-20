import unittest

from lifeos.platform import db

ATTEMPTS = len(db.RETRY_WAITS)


class ConnectionConfigTests(unittest.TestCase):
    def test_config_error_names_settings_not_values(self):
        import os
        for field in db.FIELDS:
            os.environ.pop("LIFEOS_ACQ_" + field, None)
        with self.assertRaises(db.StoreError) as ctx:
            db._cfg()
        self.assertTrue(str(ctx.exception).startswith("STORE_CONFIG_MISSING:"))


class RetryTests(unittest.TestCase):
    """D92: a Hostinger network blip (the 8 AM and 9 AM CDT ticks failed with STORE_SSH_NETWORK) is retried; auth and host-key errors never are."""

    def setUp(self):
        import os
        from unittest import mock
        self.env = mock.patch.dict(os.environ, {"LIFEOS_ACQ_" + f: "x" for f in db.FIELDS})
        self.env.start()
        self.sleeps = []
        self.patches = [mock.patch.object(db.time, "sleep", side_effect=self.sleeps.append), mock.patch.object(db, "_jitter", return_value=0)]
        for p in self.patches:
            p.start()
        db._tunnel.update(directory=None, port=None, exit=None, check=None)        # each test starts with no open tunnel
        db._route["jump"] = False

    def tearDown(self):
        db._tunnel.update(directory=None, port=None, exit=None, check=None)
        db._route["jump"] = False
        self.env.stop()
        for p in self.patches:
            p.stop()

    def _run(self, outcomes):
        """outcomes: stderr text for each ssh start (None = success). Returns (connect result or error code, ssh starts)."""
        import subprocess
        import sys
        from unittest import mock
        starts = []

        def fake_run(cmd, **kw):
            if "-O" in cmd:
                return None                                       # the tunnel exit
            starts.append(1)
            detail = outcomes[len(starts) - 1]
            if detail is not None:
                raise subprocess.CalledProcessError(255, cmd, stderr=detail)

        fake_pymysql = mock.MagicMock()
        with mock.patch.object(db.subprocess, "run", side_effect=fake_run), mock.patch.dict(sys.modules, {"pymysql": fake_pymysql}):
            try:
                with db.connect() as connection:
                    return ("connected" if connection is fake_pymysql.connect.return_value else "wrong"), len(starts)
            except db.StoreError as error:
                return str(error), len(starts)

    def test_only_the_first_connect_of_a_process_and_retries_are_spread_out(self):
        from unittest import mock
        with mock.patch.object(db, "_jitter", return_value=3), mock.patch.object(db, "_spread", [True]):
            self._run([None])                                             # the process's first connect: spread out
            self._run([])                                                 # a later connect reuses the tunnel at once (publish opens hundreds)
            db._tunnel.update(directory=None, port=None, exit=None, check=None)
            self._run(["ssh: connect to host h port 22: Connection timed out", None])     # a new tunnel's retry is spread out again
        self.assertEqual(self.sleeps, [3, 0, 0, 33])                                  # after an unanswered connection the retry waits quietly (QUIET_WAITS)

    def test_one_tunnel_serves_every_connect_of_the_process(self):
        self.assertEqual(self._run([None]), ("connected", 1))
        self.assertEqual(self._run([]), ("connected", 0))                  # no second ssh start: the open tunnel is reused
        self.assertEqual(self._run([]), ("connected", 0))

    def test_a_dead_tunnel_is_reopened(self):
        from unittest import mock
        self.assertEqual(self._run([None]), ("connected", 1))
        with mock.patch.object(db, "_alive", return_value=False):           # the master died between stages
            self.assertEqual(self._run([None]), ("connected", 1))          # a fresh tunnel, not a failed stage

    def test_a_network_blip_is_retried_and_then_connects(self):
        result, starts = self._run(["Network is unreachable", None])
        self.assertEqual((result, starts), ("connected", 2))
        self.assertEqual(self.sleeps, [0, 4])

    def test_an_unanswered_connection_waits_long_and_quietly_and_a_recovery_is_logged(self):
        """LIFE-41: the host drops one runner for minutes; quick retries never recovered (9 of 9 series), so after a TCP timeout the next wait is QUIET_WAITS."""
        import contextlib
        import io
        timeout = "ssh: connect to host h port 22: Connection timed out"
        self.assertEqual(self._run([timeout] * ATTEMPTS), ("STORE_SSH_TCP_TIMEOUT", ATTEMPTS))
        self.assertEqual(self.sleeps, [0, 30, 60, 90])
        self.sleeps.clear()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self._run([timeout, timeout, None]), ("connected", 3))
        self.assertEqual(self.sleeps, [0, 30, 60])
        self.assertEqual(err.getvalue().splitlines()[-1], "store: connected on attempt 3 of 4 after STORE_SSH_TCP_TIMEOUT")

    def test_the_wait_follows_the_last_failure_not_the_first(self):
        self._run(["ssh: connect to host h port 22: Connection timed out", "Connection timed out during banner exchange", "Network is unreachable", None])
        self.assertEqual(self.sleeps, [0, 30, 12, 30])                 # quiet only right after an unanswered connection; a dropped or unreachable one keeps the short schedule

    def test_it_gives_up_after_the_last_attempt_with_the_fixed_code(self):
        result, starts = self._run(["Network is unreachable"] * ATTEMPTS)
        self.assertEqual((result, starts), ("STORE_SSH_NETWORK", ATTEMPTS))
        self.assertEqual(self.sleeps, [0, 4, 12, 30])                  # the last wait is long enough to outlast a minute-scale blip

    def test_a_blip_that_outlasts_three_attempts_still_connects_on_the_fourth(self):
        timeout = "ssh: connect to host h port 22: Connection timed out"
        self.assertEqual(self._run([timeout] * (ATTEMPTS - 1) + [None]), ("connected", ATTEMPTS))

    def test_a_dropped_connection_and_an_unanswered_one_get_different_codes(self):
        """MON-1.5: the 46-second failures were all three attempts timing out; the code now says whether sshd answered and dropped us or nothing answered."""
        banner = ["Connection timed out during banner exchange", "kex_exchange_identification: read: Connection reset by peer", "kex_exchange_identification: Connection closed by remote host"]
        for message in banner:
            self.assertEqual(self._run([message] * ATTEMPTS), ("STORE_SSH_BANNER", ATTEMPTS), message)
        self.assertEqual(self._run(["ssh: connect to host h port 22: Connection timed out"] * ATTEMPTS), ("STORE_SSH_TCP_TIMEOUT", ATTEMPTS))
        self.assertEqual(self._run(["No route to host"] * ATTEMPTS), ("STORE_SSH_NETWORK", ATTEMPTS))

    def test_every_failed_attempt_logs_its_fixed_code_and_nothing_else(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._run(["Connection timed out during banner exchange", "ssh: connect to host h port 22: Connection timed out", "Connection timed out during banner exchange", "Network is unreachable"])
        self.assertEqual(err.getvalue().splitlines(), ["store: attempt 1 of 4 STORE_SSH_BANNER", "store: attempt 2 of 4 STORE_SSH_TCP_TIMEOUT", "store: attempt 3 of 4 STORE_SSH_BANNER", "store: attempt 4 of 4 STORE_SSH_NETWORK"])

    def test_an_auth_or_host_key_error_is_never_retried(self):
        self.assertEqual(self._run(["Permission denied (publickey)."]), ("STORE_SSH_AUTH", 1))
        self.assertEqual(self._run(["Host key verification failed."]), ("STORE_SSH_HOSTKEY", 1))


class ConnectJitterTests(unittest.TestCase):
    """LIFE-76 / D206: lanes that start in the same second must not open their tunnels together."""
    def test_every_attempt_waits_its_retry_time_plus_a_bounded_random_extra(self):
        from unittest import mock
        with mock.patch.object(db.random, "uniform", return_value=2.5) as uniform:
            self.assertEqual(db._jitter(), 2.5)
        uniform.assert_called_once_with(0, db.JITTER_SECONDS)
        self.assertLessEqual(db.JITTER_SECONDS, 5)                         # small next to the 30 s last wait: a blip window is still covered


class OracleRouteTests(unittest.TestCase):
    """LIFE-85: a runner the store host drops (an unanswered connection) reaches the store through Oracle's fixed address on its next attempt, as the
    forward-only user, to the store's one IP and port; nothing else changes and no store secret is put on a command line."""
    TIMEOUT = "ssh: connect to host h port 65002: Connection timed out"
    JUMP = {"LIFEOS_ACQ_SSH_JUMP_KEY": "JUMPKEY-INVENTED", "LIFEOS_ACQ_SSH_JUMP_HOST": "100.64.0.7", "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS": "100.64.0.7 ssh-ed25519 AAAAinvented"}

    def setUp(self):
        import os
        from unittest import mock
        base = {"LIFEOS_ACQ_" + f: "x" for f in db.FIELDS}
        base.update(LIFEOS_ACQ_SSH_HOST="store.example", LIFEOS_ACQ_SSH_PORT="65002", LIFEOS_ACQ_SSH_PRIVATE_KEY="STOREKEY-INVENTED", LIFEOS_ACQ_DB_PASSWORD="DBPASS-INVENTED")
        self.env = mock.patch.dict(os.environ, {**base, **self.JUMP})
        self.env.start()
        self.sleeps, self.cmds, self.files = [], [], {}
        self.patches = [mock.patch.object(db.time, "sleep", side_effect=self.sleeps.append), mock.patch.object(db, "_jitter", return_value=0),
                        mock.patch.object(db.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("203.0.113.9", 65002))])]
        for p in self.patches:
            p.start()
        db._tunnel.update(directory=None, port=None, exit=None, check=None)
        db._route["jump"] = False

    def tearDown(self):
        db._tunnel.update(directory=None, port=None, exit=None, check=None)
        db._route["jump"] = False
        self.env.stop()
        for p in self.patches:
            p.stop()

    def run_(self, outcomes):
        import os
        import subprocess
        import sys
        from unittest import mock

        def fake_run(cmd, **kw):
            if "-O" in cmd:
                return None
            self.cmds.append(cmd)
            directory = db._tunnel["directory"]
            self.files = {n: (open(os.path.join(directory, n)).read(), oct(os.stat(os.path.join(directory, n)).st_mode & 0o777)) for n in os.listdir(directory)}
            detail = outcomes[len(self.cmds) - 1]
            if detail is not None:
                raise subprocess.CalledProcessError(255, cmd, stderr=detail)

        fake = mock.MagicMock()
        with mock.patch.object(db.subprocess, "run", side_effect=fake_run), mock.patch.dict(sys.modules, {"pymysql": fake}):
            try:
                with db.connect():
                    return "connected"
            except db.StoreError as error:
                return str(error)

    def reset(self):
        self.sleeps.clear(); self.cmds.clear(); self.files = {}
        db._tunnel.update(directory=None, port=None, exit=None, check=None)
        db._route["jump"] = False

    def proxy(self, cmd):
        return next((c for c in cmd if c.startswith("ProxyCommand=")), None)

    def test_an_unanswered_runner_reaches_the_store_through_oracle_on_the_next_attempt(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.run_([self.TIMEOUT, None]), "connected")
        first, second = self.cmds
        self.assertIsNone(self.proxy(first))                                                           # direct first
        p = self.proxy(second)
        self.assertIn("-W 203.0.113.9:65002 " + db.JUMP_USER + "@" + self.JUMP["LIFEOS_ACQ_SSH_JUMP_HOST"], p)                                # the store's one IP and port, the forward-only user
        self.assertIn("StrictHostKeyChecking=yes", p)
        self.assertEqual(second[-1], "store.example")                                                   # the store's own host key is still checked by name
        self.assertEqual(self.sleeps, [0, 4])                                                           # no quiet wait: the route changes instead
        self.assertEqual(err.getvalue().splitlines()[-1], "store: connected on attempt 2 of 4 via oracle after STORE_SSH_TCP_TIMEOUT")
        self.assertEqual(self.files["jump_key"], ("JUMPKEY-INVENTED\n", "0o600"))
        self.assertEqual(self.files["jump_known_hosts"][1], "0o600")
        for secret in ("JUMPKEY-INVENTED", "STOREKEY-INVENTED", "DBPASS-INVENTED"):
            self.assertFalse(any(secret in c for c in second), secret)                                  # no secret on any command line

    def test_only_an_unanswered_connection_switches_route(self):
        for detail in ("Connection timed out during banner exchange", "Network is unreachable", "Connection refused"):
            self.reset()
            self.run_([detail, None])
            self.assertTrue(all(self.proxy(c) is None for c in self.cmds), detail)

    def test_without_the_oracle_settings_the_quiet_schedule_stays(self):
        import os
        for key in self.JUMP:
            os.environ.pop(key)
        self.assertEqual(self.run_([self.TIMEOUT, None]), "connected")
        self.assertTrue(all(self.proxy(c) is None for c in self.cmds))
        self.assertEqual(self.sleeps, [0, 30])

    def test_a_refused_oracle_key_or_host_key_is_never_retried_and_names_the_route(self):
        self.assertEqual(self.run_([self.TIMEOUT, "Permission denied (publickey)."]), "STORE_SSH_JUMP_AUTH")
        self.assertEqual(len(self.cmds), 2)
        self.reset()
        self.assertEqual(self.run_([self.TIMEOUT, "Host key verification failed."]), "STORE_SSH_JUMP_HOSTKEY")

    def test_a_failing_oracle_route_keeps_retrying_through_oracle_then_gives_up_with_its_code(self):
        self.assertEqual(self.run_([self.TIMEOUT] + ["ssh: connect to host 100.64.0.7 port 22: Connection timed out"] * (ATTEMPTS - 1)), "STORE_SSH_JUMP_TCP_TIMEOUT")
        self.assertEqual([self.proxy(c) is not None for c in self.cmds], [False, True, True, True])

    def test_a_reopened_tunnel_keeps_the_oracle_route_for_the_rest_of_the_process(self):
        from unittest import mock
        self.run_([self.TIMEOUT, None])
        with mock.patch.object(db, "_alive", return_value=False):
            self.run_([None])
        self.assertIsNotNone(self.proxy(self.cmds[-1]))

    def test_jims_switch_starts_on_the_oracle_route_and_needs_the_route_configured(self):
        import os
        os.environ["LIFEOS_ACQ_SSH_VIA"] = "oracle"
        self.assertEqual(self.run_([None]), "connected")
        self.assertIsNotNone(self.proxy(self.cmds[0]))                                                 # the first attempt already goes through Oracle
        self.reset()
        for key in self.JUMP:
            os.environ.pop(key)
        self.run_([None])
        self.assertIsNone(self.proxy(self.cmds[0]))                                                    # without the route configured the switch does nothing

    def test_an_unresolvable_store_name_is_refused_with_its_own_code_and_cleaned_up(self):
        from unittest import mock
        with mock.patch.object(db.socket, "getaddrinfo", side_effect=OSError):
            self.assertEqual(self.run_([self.TIMEOUT]), "STORE_SSH_JUMP_DNS")
        self.assertIsNone(db._tunnel["directory"])


if __name__ == "__main__":
    unittest.main()
