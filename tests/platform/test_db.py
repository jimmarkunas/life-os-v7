import unittest

from lifeos.platform import db


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
        self.patches = [mock.patch.object(db.time, "sleep", side_effect=self.sleeps.append)]
        for p in self.patches:
            p.start()

    def tearDown(self):
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

    def test_a_network_blip_is_retried_and_then_connects(self):
        result, starts = self._run(["ssh: connect to host h port 22: Connection timed out", None])
        self.assertEqual((result, starts), ("connected", 2))
        self.assertEqual(self.sleeps, [0, 4])

    def test_it_gives_up_after_three_attempts_with_the_fixed_code(self):
        result, starts = self._run(["Network is unreachable"] * 3)
        self.assertEqual((result, starts), ("STORE_SSH_NETWORK", 3))

    def test_an_auth_or_host_key_error_is_never_retried(self):
        self.assertEqual(self._run(["Permission denied (publickey)."]), ("STORE_SSH_AUTH", 1))
        self.assertEqual(self._run(["Host key verification failed."]), ("STORE_SSH_HOSTKEY", 1))


if __name__ == "__main__":
    unittest.main()
