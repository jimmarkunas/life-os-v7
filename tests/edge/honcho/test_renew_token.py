"""the Honcho memory token is renewed when 30 days or fewer are left"""

import base64
import json
import os
import stat
import tempfile
import unittest
import urllib.error
from types import SimpleNamespace

from lifeos.edge.honcho import renew_token as rt

NOW = 1_795_000_000
DAY = 86400


def jwt(exp, w="personal", tag="x"):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return enc({"alg": "HS256"}) + "." + enc({"t": tag, "exp": exp, "w": w}) + ".SIGNATURE" + tag


class Reply:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class World:
    """A fake host: config file, docker minting, Honcho, systemctl, the MCP and the alert command."""

    def __init__(self, old_exp, mint=None, accepts=True, mcp_active=True, mcp_code=401, after_swap_bad=False):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "config.env")
        self.old = jwt(old_exp, tag="old")
        with open(self.path, "w") as handle:
            handle.write(f"BUZZ_URL_LIFE_OS=https://example.invalid\n{rt.KEY}={self.old}\nBUZZ_RELAY=on\n")
        os.chmod(self.path, 0o600)
        self.new = mint if mint is not None else jwt(NOW + 60 * DAY, tag="new")
        self.accepts, self.mcp_active, self.mcp_code, self.after_swap_bad = (
            accepts,
            mcp_active,
            mcp_code,
            after_swap_bad,
        )
        self.calls, self.printed, self.alerts, self.restarts = [], [], [], 0

    def run(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[0] == "docker":
            return SimpleNamespace(
                returncode=0 if self.new else 1, stdout=(self.new + "\n") if self.new else "", stderr=""
            )
        if cmd[0] == "systemctl" and cmd[1] == "restart":
            self.restarts += 1
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == "systemctl":
            active = self.mcp_active and not (self.after_swap_bad and rt.read_token(self.path) == self.new)
            return SimpleNamespace(returncode=0 if active else 3, stdout="", stderr="")
        if cmd[0] == rt.ALERT:
            self.alerts.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(cmd)

    def opener(self, request, timeout=None):
        if request.full_url.startswith(rt.API):
            token = request.headers["Authorization"].split(" ", 1)[1]
            return Reply(200 if self.accepts and token == self.new else 401)
        raise urllib.error.HTTPError(request.full_url, self.mcp_code, "x", {}, None)

    def go(self):
        code = rt.renew(
            now=NOW, path=self.path, run=self.run, opener=self.opener, sleep=lambda s: None, out=self.printed.append
        )
        return code, rt.read_token(self.path)


class Renewal(unittest.TestCase):
    def test_a_token_with_more_than_30_days_left_is_left_alone(self):
        w = World(NOW + 45 * DAY)
        self.assertEqual(w.go(), (0, w.old))
        self.assertEqual((w.restarts, w.alerts, [c[0] for c in w.calls]), (0, [], []))

    def test_a_token_with_30_days_or_fewer_left_is_replaced_proved_swapped_and_the_mcp_restarted(self):
        w = World(NOW + 12 * DAY)  # the live token ends in about 12 days
        code, token = w.go()
        self.assertEqual((code, token, w.restarts, w.alerts), (0, w.new, 1, []))
        self.assertEqual(rt.claims(token)["w"], "personal")
        text = open(w.path).read()
        self.assertIn("BUZZ_URL_LIFE_OS=https://example.invalid\n", text)  # other lines untouched
        self.assertTrue(text.endswith("BUZZ_RELAY=on\n"))
        self.assertEqual(stat.S_IMODE(os.stat(w.path).st_mode), 0o600)
        self.assertNotIn(w.new, "\n".join(w.printed))
        self.assertIn("--workspace personal --expires 60d", " ".join(" ".join(c) for c in w.calls if c[0] == "docker"))

    def test_exactly_30_days_left_renews_and_31_does_not(self):
        edge = World(NOW + 30 * DAY)
        self.assertEqual(edge.go(), (0, edge.new))
        keep = World(NOW + 31 * DAY)
        self.assertEqual(keep.go(), (0, keep.old))

    def test_a_failed_mint_keeps_the_old_token_and_alerts_once(self):
        w = World(NOW + 12 * DAY, mint="")
        self.assertEqual(w.go(), (1, w.old))
        self.assertEqual((len(w.alerts), w.restarts), (1, 0))

    def test_a_new_token_for_the_wrong_workspace_or_too_short_is_refused(self):
        for bad in (jwt(NOW + 60 * DAY, w="bytalos"), jwt(NOW + 20 * DAY)):
            w = World(NOW + 12 * DAY, mint=bad)
            self.assertEqual(w.go(), (1, w.old))
            self.assertEqual(len(w.alerts), 1)

    def test_a_token_honcho_does_not_accept_is_never_swapped_in(self):
        w = World(NOW + 12 * DAY, accepts=False)
        self.assertEqual(w.go(), (1, w.old))
        self.assertEqual((w.restarts, len(w.alerts)), (0, 1))

    def test_an_mcp_that_does_not_come_back_gets_the_old_token_and_a_second_restart(self):
        w = World(NOW + 12 * DAY, after_swap_bad=True)
        self.assertEqual(w.go(), (1, w.old))
        self.assertEqual((w.restarts, len(w.alerts)), (2, 1))

    def test_an_mcp_answering_something_other_than_401_counts_as_failed(self):
        w = World(NOW + 12 * DAY, mcp_code=200)
        self.assertEqual(w.go(), (1, w.old))

    def test_a_missing_or_garbled_token_alerts_without_touching_anything(self):
        w = World(NOW + 12 * DAY)
        with open(w.path, "w") as handle:
            handle.write("BUZZ_RELAY=on\n")
        self.assertEqual(
            rt.renew(now=NOW, path=w.path, run=w.run, opener=w.opener, sleep=lambda s: None, out=w.printed.append), 1
        )
        self.assertEqual((len(w.alerts), open(w.path).read()), (1, "BUZZ_RELAY=on\n"))

    def test_the_alert_has_no_token_text_in_it(self):
        w = World(NOW + 12 * DAY, accepts=False)
        w.go()
        for cmd in w.alerts:
            self.assertNotIn("SIGNATURE", " ".join(cmd))
            self.assertEqual(cmd[:2], [rt.ALERT, "send"])

    def test_the_program_prints_days_and_outcome_only(self):
        w = World(NOW + 12 * DAY)
        w.go()
        self.assertEqual(w.printed, ["token days left: 12", "renewed: new token days left: 60"])


if __name__ == "__main__":
    unittest.main()
