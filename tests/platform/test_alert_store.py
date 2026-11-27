"""the three alert switches in Bitwarden - fixed ids, the token only in the environment, fixed codes, and a read-back
guard.
"""

import json
import subprocess
import unittest
from unittest import mock

from lifeos.platform import alert_store as store

TOKEN = b"0.synthetic.token:key"


def reply(code=0, out=""):
    return mock.Mock(returncode=code, stdout=out, stderr="the token was here")


def item(name, value, ident=None):
    return json.dumps({"id": ident or store.IDS[name], "key": "lifeos/github-actions/" + name, "value": value})


class Store(unittest.TestCase):
    def test_the_three_ids_are_the_ones_proven_in_the_workflows(self):
        self.assertEqual(
            store.IDS,
            {
                "ALERTS_ENABLED": "efc8fc25-0228-4aa4-a7ee-b503015f8a8c",
                "ALERTS_MUTED_UNTIL": "141b6f81-341b-4277-b2b3-b503015f8bd0",
                "ALERT_SETTINGS": "dc0b1ad9-45a8-4e1f-8868-b503015f8c97",
            },
        )

    def test_a_read_returns_the_three_values_and_the_token_goes_in_the_environment_only(self):
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw["env"]))
            name = next(n for n, i in store.IDS.items() if i == argv[3])
            return reply(
                0, item(name, {"ALERTS_ENABLED": "on", "ALERTS_MUTED_UNTIL": "off", "ALERT_SETTINGS": "{}"}[name])
            )

        self.assertEqual(
            store.read(TOKEN, run, {"PATH": "/bin"}),
            {"ALERTS_ENABLED": "on", "ALERTS_MUTED_UNTIL": "off", "ALERT_SETTINGS": "{}"},
        )
        for argv, env in calls:
            self.assertEqual(argv[:3], ["bws", "secret", "get"])
            self.assertNotIn("0.synthetic.token:key", " ".join(argv))
            self.assertEqual(env["BWS_ACCESS_TOKEN"], "0.synthetic.token:key")
            self.assertNotEqual(env["HOME"], "")
            self.assertEqual(sorted(env), ["BWS_ACCESS_TOKEN", "HOME", "PATH"])

    def test_the_program_path_comes_from_the_environment(self):
        seen = []
        store.write(
            TOKEN,
            {"ALERTS_ENABLED": "on"},
            lambda argv, **kw: seen.append(argv) or reply(),
            {"LIFEOS_BWS_BIN": "/usr/local/bin/bws"},
        )
        self.assertEqual(seen[0][:4], ["/usr/local/bin/bws", "secret", "edit", store.IDS["ALERTS_ENABLED"]])

    def test_every_read_failure_is_one_fixed_code_that_never_carries_a_reply(self):
        wrong_item = lambda argv, **kw: reply(
            0, item("ALERTS_ENABLED", "on", ident="00000000-0000-0000-0000-000000000000")
        )  # noqa: E731
        wrong_name = lambda argv, **kw: reply(0, item("ALERT_SETTINGS", "on", ident=argv[3]))  # noqa: E731
        for run in (
            lambda argv, **kw: reply(1),
            lambda argv, **kw: reply(0, "not json"),
            wrong_item,
            wrong_name,
            mock.Mock(side_effect=subprocess.TimeoutExpired("bws", 20)),
            mock.Mock(side_effect=FileNotFoundError("bws")),
        ):
            with self.assertRaises(store.AlertStoreError) as caught:
                store.read(TOKEN, run, {})
            self.assertEqual(str(caught.exception), "ALERT_STORE_UNREADABLE")
            self.assertNotIn("token", str(caught.exception))

    def test_a_write_edits_only_the_known_items_and_fails_with_a_fixed_code(self):
        seen = []
        store.write(
            TOKEN,
            {"ALERTS_ENABLED": "off", "ALERT_SETTINGS": "{}"},
            lambda argv, **kw: seen.append(argv) or reply(),
            {},
        )
        self.assertEqual(
            [(a[3], a[a.index("--value") + 1]) for a in seen],
            [(store.IDS["ALERTS_ENABLED"], "off"), (store.IDS["ALERT_SETTINGS"], "{}")],
        )
        with self.assertRaises(store.AlertStoreError):
            store.write(TOKEN, {"V7_LIVE": "true"}, lambda argv, **kw: reply(), {})
        with self.assertRaises(store.AlertStoreError) as caught:
            store.write(TOKEN, {"ALERTS_ENABLED": "on"}, lambda argv, **kw: reply(1), {})
        self.assertEqual(str(caught.exception), "ALERT_STORE_UNWRITABLE")


if __name__ == "__main__":
    unittest.main()
