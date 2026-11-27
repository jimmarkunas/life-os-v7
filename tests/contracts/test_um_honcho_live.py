"""the live entry point's gate fails closed."""
import base64
import json
import pathlib
import unittest
from tests.kit.tool_modules import load_tool

ROOT = pathlib.Path(__file__).resolve().parents[2]


load = load_tool


live = load("um_honcho_live")
NOW = 1_800_000_000


def jwt(**claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return enc({"alg": "HS256"}) + "." + enc(claims) + "." + "sig"


def good_env(**over):
    env = {"UM_T2_RELEASE": live.RELEASE, "UM_T2_SCOPE": "um-t2-test", "UM_T2_TOKEN": jwt(w="um-t2-fixture-v1", exp=NOW + 7000)}
    env.update(over)
    return env


class Gate(unittest.TestCase):
    def check(self, env, expect, live_flag=True, host="lifeos-edge-01", url=live.HONCHO_URL):
        self.assertEqual(live.gate(live_flag, env, host, NOW, url), expect)

    def test_everything_in_place_opens_the_gate(self):
        self.check(good_env(), (True, "open"))

    def test_each_missing_or_wrong_condition_closes_it_with_its_own_word(self):
        self.check(good_env(), (False, "not_live"), live_flag=False)
        self.check({k: v for k, v in good_env().items() if k != "UM_T2_RELEASE"}, (False, "not_released"))
        self.check(good_env(UM_T2_RELEASE="RELEASED-LIFE-1003:other"), (False, "not_released"))
        self.check(good_env(), (False, "wrong_host"), host="Jims-Mac.local")
        self.check(good_env(), (False, "url_not_loopback"), url="http://100.64.0.9:8000")
        self.check(good_env(HONCHO_ADMIN_JWT="x"), (False, "admin_token_present"))
        self.check(good_env(UM_T2_SCOPE=""), (False, "scope_id_missing"))
        self.check({k: v for k, v in good_env().items() if k != "UM_T2_SCOPE"}, (False, "scope_id_missing"))
        self.check(good_env(UM_T2_TOKEN=""), (False, "token_missing_or_not_jwt"))
        self.check(good_env(UM_T2_TOKEN="not-a-jwt"), (False, "token_missing_or_not_jwt"))

    def test_a_token_that_is_not_scoped_to_exactly_this_workspace_is_refused(self):
        for claims in ({"ad": True, "exp": NOW + 7000}, {"w": "personal", "exp": NOW + 7000}, {"w": "um-t2-fixture-v1", "p": "agent-a", "exp": NOW + 7000},
                       {"w": "um-t2-fixture-v1", "s": "s1", "exp": NOW + 7000}, {"w": "um-t2-fixture-v1", "ad": True, "exp": NOW + 7000}, {"exp": NOW + 7000}):
            self.check(good_env(UM_T2_TOKEN=jwt(**claims)), (False, "token_not_workspace_scoped"))

    def test_the_token_lifetime_must_be_between_five_minutes_and_two_hours(self):
        for exp in (NOW + 60, NOW - 5, NOW + 3 * 3600, None):
            claims = {"w": "um-t2-fixture-v1"}
            if exp:
                claims["exp"] = exp
            self.check(good_env(UM_T2_TOKEN=jwt(**claims)), (False, "token_lifetime"))


class Main(unittest.TestCase):
    def test_a_closed_gate_builds_nothing_and_prints_one_word(self):
        import io
        from contextlib import redirect_stdout
        built = []
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = live.main(["--live"], env={}, host="lifeos-edge-01", now=NOW, builder=lambda env: built.append(1))
        self.assertEqual((code, built, buf.getvalue().strip()), (2, [], "not_released"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(live.main([], env=good_env(), host="lifeos-edge-01", now=NOW, builder=lambda env: built.append(1)), 2)
        self.assertEqual(built, [])

    def test_an_open_gate_builds_once_and_never_prints_the_token(self):
        import io
        import tempfile
        from contextlib import redirect_stdout
        built = []
        env = good_env()

        class Driver:
            closed = False

            def close(self):
                Driver.closed = True

        def builder(e):
            built.append(1)
            raise RuntimeError("stop here: the build is not the unit under test")
        buf = io.StringIO()
        with redirect_stdout(buf), tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                live.main(["--live", "--evidence", tmp + "/e.json"], env=env, host="lifeos-edge-01", now=NOW, builder=builder)
        self.assertEqual(built, [1])
        self.assertNotIn(env["UM_T2_TOKEN"], buf.getvalue())

    def test_the_meter_reader_returns_the_whole_health_document_for_the_guard_to_check(self):
        class Answer:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self.body

        seen = []
        doc = b'{"ok": true, "spent_usd": 0.7029, "cap_usd": 25.0, "blocked": null, "scope": {"id": "s", "cap_usd": 2.0, "spent_usd": 0.0, "state": "open"}}'
        m = live.MeterHttp(opener=lambda url, timeout: seen.append(url) or Answer(doc))
        self.assertEqual(m.reading()["scope"], {"id": "s", "cap_usd": 2.0, "spent_usd": 0.0, "state": "open"})
        self.assertFalse(hasattr(m, "spent"))  # no monthly-delta shortcut around the scope
        self.assertEqual(seen, [live.METER_URL])


if __name__ == "__main__":
    unittest.main()
