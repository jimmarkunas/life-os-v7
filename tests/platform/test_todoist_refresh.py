import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse

from lifeos.platform import todoist_refresh as r

T0 = 1_800_000_000
A = r.APPS


class Vault:
    def __init__(self, app="read", token=r.UNSET, fail_puts=0):
        ids = A[app]
        self.d = {ids["client_id"]: "cid", ids["client_secret"]: "sec", ids["token"]: token, ids["access"]: r.UNSET}
        self.fail, self.puts = fail_puts, []

    def get(self, item):
        return self.d[item]

    def put(self, item, value):
        if self.fail > 0:
            self.fail -= 1
            raise OSError("vault down")
        self.puts.append(item)
        self.d[item] = value


class Todoist:
    """Rotating refresh tokens; a consumed token reused after 60 s revokes everything (the documented behaviour)."""

    def __init__(self, scope="data:read"):
        self.valid, self.used, self.revoked, self.n, self.clock, self.calls, self.scope = (
            "R0",
            {},
            False,
            0,
            T0,
            0,
            scope,
        )

    def __call__(self, request, timeout=30):
        self.calls += 1
        form = dict(urllib.parse.parse_qsl(request.data.decode()))
        if form["grant_type"] == "authorization_code":
            return self.reply(
                200, {"access_token": "A0", "refresh_token": "R0", "expires_in": 3600, "scope": self.scope}
            )
        rt = form["refresh_token"]
        if self.revoked:
            return self.reply(400, {})
        if rt == self.valid:
            self.used[rt] = self.clock
            self.n += 1
            self.valid = f"R{self.n}"
            return self.reply(
                200,
                {"access_token": f"A{self.n}", "refresh_token": self.valid, "expires_in": 3600, "scope": self.scope},
            )
        if rt in self.used and self.clock - self.used[rt] <= 60:
            return self.reply(200, {"access_token": "Agrace", "expires_in": 3600, "scope": self.scope})
        self.revoked = True
        return self.reply(400, {})

    def reply(self, code, body):
        if code != 200:
            raise urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"{}"))
        return Body(body)


class Body:
    def __init__(self, body):
        self.raw = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.raw


class Rec:
    def __init__(self):
        self.value = None

    def read(self):
        return self.value

    def write(self, v):
        self.value = v

    def clear(self):
        self.value = None


def consented(server=None, app="read", **kw):
    server = server or Todoist(A[app]["scope"])
    vault = Vault(app, **kw)
    assert r.exchange_code(app, "code", vault, T0, server) == "consented"
    return server, vault


class Refresh(unittest.TestCase):
    def test_an_unset_record_is_skipped_quietly_with_no_request(self):
        server, vault = Todoist(), Vault()
        self.assertEqual(r.refresh("read", vault, T0, server), "not_consented")
        self.assertEqual(server.calls, 0)

    def test_first_consent_saves_the_record_and_publishes_only_the_access_token_and_never_twice(self):
        server, vault = consented()
        ids = A["read"]
        self.assertEqual(vault.d[ids["access"]], "A0")
        self.assertEqual(json.loads(vault.d[ids["token"]])["refresh_token"], "R0")
        with self.assertRaises(r.RefreshError) as e:
            r.exchange_code("read", "code", vault, T0, server)
        self.assertEqual(str(e.exception), "already_consented")

    def test_a_token_with_over_thirty_minutes_left_causes_no_request(self):
        server, vault = consented()
        self.assertEqual(r.refresh("read", vault, T0 + 1500, server), "fresh")
        self.assertEqual(server.calls, 1)  # only the consent exchange

    def test_under_thirty_minutes_it_rotates_saves_before_use_and_publishes(self):
        server, vault = consented()
        server.clock = T0 + 2000
        self.assertEqual(r.refresh("read", vault, T0 + 2000, server), "refreshed")
        ids = A["read"]
        self.assertEqual((json.loads(vault.d[ids["token"]])["refresh_token"], vault.d[ids["access"]]), ("R1", "A1"))
        self.assertLess(
            vault.puts.index(ids["token"], 2), vault.puts.index(ids["access"], 2)
        )  # the record first, then the access token

    def test_a_vault_outage_keeps_the_new_record_in_the_recovery_file_and_the_next_run_saves_it_first(self):
        server, vault = consented()
        rec, ids = Rec(), A["read"]
        vault.fail = 3
        server.clock = T0 + 2000
        self.assertEqual(r.refresh("read", vault, T0 + 2000, server, recovery=rec), "refreshed_recovery_file")
        self.assertIsNotNone(rec.value)
        calls = server.calls
        r.refresh("read", vault, T0 + 2100, server, recovery=rec)
        self.assertIsNone(rec.value)
        self.assertEqual(json.loads(vault.d[ids["token"]])["refresh_token"], "R1")
        self.assertEqual((server.calls, server.revoked), (calls, False))  # no second exchange, nothing reused

    def test_a_second_refresher_inside_the_grace_window_gets_a_race_and_nothing_is_revoked(self):
        server, vault = consented()
        server.clock = T0 + 2000
        stale = dict(vault.d)
        r.refresh("read", vault, T0 + 2000, server)
        other = Vault()
        other.d = stale
        server.clock = T0 + 2030
        with self.assertRaises(r.RefreshError) as e:
            r.refresh("read", other, T0 + 2030, server)
        self.assertEqual(str(e.exception), "rotation_race")
        self.assertFalse(server.revoked)

    def test_a_reused_token_after_sixty_seconds_is_the_revocation_this_design_prevents(self):
        server, vault = consented()
        stale = dict(vault.d)
        server.clock = T0 + 2000
        r.refresh("read", vault, T0 + 2000, server)
        other = Vault()
        other.d = stale
        server.clock = T0 + 2200
        with self.assertRaises(r.RefreshError) as e:
            r.refresh("read", other, T0 + 2200, server)
        self.assertEqual(str(e.exception), "reauth_required")
        self.assertTrue(server.revoked)

    def test_a_scope_that_is_not_exactly_the_apps_is_never_published(self):
        for scope in ("data:read_write", "data:read task:add", ""):
            server = Todoist(scope)
            vault = Vault()
            with self.assertRaises(r.RefreshError) as e:
                r.exchange_code("read", "code", vault, T0, server)
            self.assertEqual(str(e.exception), "scope_mismatch")
            self.assertEqual(vault.d[A["read"]["access"]], r.UNSET)

    def test_the_write_app_needs_exactly_read_write_and_is_isolated_from_the_read_items(self):
        server, vault = consented(app="write")
        self.assertEqual(vault.d[A["write"]["access"]], "A0")
        self.assertFalse(
            set(A["read"].values()) & set(A["write"].values()) - {None}
            and any(A["read"][k] == A["write"][k] for k in ("client_id", "client_secret", "token", "access"))
        )

    def test_the_twenty_four_hour_run_at_fifteen_minutes_never_serves_an_expired_token_or_revokes(self):
        server, vault = consented()
        ids, rec, expired = A["read"], Rec(), 0
        for i in range(1, 97):
            t = T0 + 900 * i
            server.clock = t
            if i == 11:
                vault.fail = 3  # an outage during one refresh
            r.refresh("read", vault, t, server, recovery=rec)
            if json.loads(vault.d[ids["token"]])["expires_at"] - t <= 0:
                expired += 1
        self.assertEqual((expired, server.revoked), (0, False))

    def test_file_recovery_is_private_and_clears(self):
        with tempfile.TemporaryDirectory() as d:
            f = r.FileRecovery(os.path.join(d, "state", "todoist-read.json"))
            self.assertIsNone(f.read())
            f.write("x")
            self.assertEqual((f.read(), oct(os.stat(f.path).st_mode & 0o777)), ("x", "0o600"))
            f.clear()
            self.assertIsNone(f.read())

    def test_codes_never_carry_a_secret(self):
        server, vault = consented()
        server.revoked = True
        server.clock = T0 + 2000
        try:
            r.refresh("read", vault, T0 + 2000, server)
        except r.RefreshError as e:
            for secret in ("sec", "cid", "R0", "A0"):
                self.assertNotIn(secret, str(e))


class FakeBws:
    """`bws secret get|edit` over a dict; records that the Bitwarden token is only ever in the environment."""

    def __init__(self, store):
        self.store, self.argv, self.envs = store, [], []

    def __call__(self, argv, env=None, **kw):
        self.argv.append(argv)
        self.envs.append(env)
        if argv[1:3] == ["secret", "get"]:
            out = json.dumps({"id": argv[3], "value": self.store[argv[3]]})
            return type("D", (), {"returncode": 0, "stdout": out})()
        self.store[argv[3]] = argv[argv.index("--value") + 1]
        return type("D", (), {"returncode": 0, "stdout": ""})()


class RefreshAll(unittest.TestCase):
    def test_both_apps_run_independently_through_bws_and_an_unset_app_is_quiet(self):
        server, vault = consented()  # the read app is consented
        store = dict(vault.d)
        for k, v in Vault("write").d.items():
            store[k] = v
        bws = FakeBws(store)
        server.clock = T0 + 2000
        out = r.refresh_all("0.token", {"PATH": "/usr/bin"}, now=T0 + 2000, opener=server, run=bws)
        self.assertEqual(out, {"read": "refreshed", "write": "not_consented"})
        self.assertEqual(json.loads(store[A["read"]["token"]])["refresh_token"], "R1")
        self.assertTrue(all(e["BWS_ACCESS_TOKEN"] == "0.token" for e in bws.envs))
        self.assertFalse(any("0.token" in " ".join(a) for a in bws.argv))

    def test_one_app_failing_never_stops_the_other_and_the_word_is_fixed(self):
        server, vault = consented()
        store = dict(vault.d)
        for k, v in Vault("write").d.items():
            store[k] = v
        server.revoked = True
        out = r.refresh_all("t", {}, now=T0 + 2000, opener=server, run=FakeBws(store))
        self.assertEqual(out, {"read": "reauth_required", "write": "not_consented"})

    def test_an_unreadable_vault_is_a_fixed_word(self):
        def broken(argv, env=None, **kw):
            return type("D", (), {"returncode": 1, "stdout": ""})()

        self.assertEqual(
            r.refresh_all("t", {}, now=T0, run=broken), {"read": "vault_unreadable", "write": "vault_unreadable"}
        )


if __name__ == "__main__":
    unittest.main()
