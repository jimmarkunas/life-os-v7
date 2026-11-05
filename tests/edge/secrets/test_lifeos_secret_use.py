import base64
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.parse
from unittest import mock

from lifeos.edge.secrets import lifeos_secret_use as su

VALUE = "sk-TEST-0123456789abcdefghijklmnopqrstuvwxyz"       # a synthetic value; the real credential never appears in code or tests
HERE = os.path.dirname(su.__file__)


class Fake:
    """Replaces the two things that touch the outside: the credential decryption and the one HTTPS GET."""

    def __init__(self, value=VALUE, reply=None):
        self.value, self.reply, self.asked, self.fetched = value, reply, [], []

    def decrypt(self, name):
        self.asked.append(name)
        if self.value is None:
            raise su.Refused("SECRET_CRED_UNREADABLE")
        return self.value

    def fetch(self, url, bearer):
        self.fetched.append((url, bearer == VALUE))
        return json.dumps(self.reply if self.reply is not None else {"data": {"limit": 100, "limit_remaining": 99.5, "usage": 0.5, "label": "x" * 5}}).encode()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.reg, self.log = os.path.join(self.tmp, "purposes.d"), os.path.join(self.tmp, "use.jsonl")
        os.mkdir(self.reg)
        os.chmod(self.reg, 0o755)
        for name in os.listdir(os.path.join(HERE, "purposes.d")):          # the registry files that ship with the program
            shutil.copy(os.path.join(HERE, "purposes.d", name), self.reg)
            os.chmod(os.path.join(self.reg, name), 0o644)
        self.fake = Fake()
        self.now = 1_800_000_000.0

    def broker(self, **kw):
        args = dict(registry=self.reg, creds=self.tmp, log=self.log, decrypt=self.fake.decrypt, fetch=self.fake.fetch, euid=0, clock=lambda: self.now, owner=os.getuid())
        args.update(kw)
        return su.Broker(**args)

    def call(self, argv, caller="opc", **kw):
        status, text = self.broker(**kw).run(argv, caller)
        return status, json.loads(text)

    def audit(self):
        with open(self.log) as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def put(self, name, spec):
        path = os.path.join(self.reg, name + ".json")
        with open(path, "w") as handle:
            json.dump(spec, handle)
        os.chmod(path, 0o644)


class Working(Case):
    def test_key_info_returns_only_the_listed_fields(self):
        status, out = self.call(["openrouter_key_info"])
        self.assertEqual((status, out["ok"]), (0, True))
        self.assertEqual(out["result"], {"data.limit": 100, "data.limit_remaining": 99.5, "data.usage": 0.5})     # "label" was in the reply and is not returned
        self.assertEqual(self.fake.fetched, [("https://openrouter.ai/api/v1/key", True)])
        self.assertNotIn(VALUE, json.dumps(out))

    def test_credential_check_returns_name_length_and_a_short_hash_never_the_value(self):
        status, out = self.call(["check_openrouter_credential"])
        self.assertEqual(status, 0)
        self.assertEqual(sorted(out["result"]), ["length", "name", "sha256_prefix"])
        self.assertEqual((out["result"]["name"], out["result"]["length"], len(out["result"]["sha256_prefix"])), ("openrouter_api_key", len(VALUE), 8))
        self.assertNotIn(VALUE, json.dumps(out))

    def test_every_call_is_audited_with_caller_purpose_secret_name_central_time_and_result_and_never_the_value(self):
        self.call(["openrouter_key_info"])
        rows = self.audit()
        self.assertEqual([r["result"] for r in rows], ["START", "OK"])
        last = rows[-1]
        self.assertEqual((last["caller"], last["purpose"], last["secret"]), ("opc", "openrouter_key_info", "openrouter_api_key"))
        self.assertRegex(last["ct"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d (AM|PM) CT$")        # 12-hour Central Time, never UTC
        self.assertGreater(last["bytes"], 0)
        with open(self.log) as handle:
            self.assertNotIn(VALUE, handle.read())
        self.assertEqual(os.stat(self.log).st_mode & 0o007, 0)                  # created 0640: nothing for "other"


class Refusals(Case):
    def test_an_unregistered_purpose_is_refused_and_logged(self):
        status, out = self.call(["print_everything"])
        self.assertEqual((status, out), (1, {"ok": False, "code": "SECRET_UNKNOWN_PURPOSE"}))
        self.assertEqual(self.audit()[-1]["result"], "SECRET_UNKNOWN_PURPOSE")
        self.assertEqual(self.fake.asked, [])                                  # no credential was touched

    def test_anything_beyond_one_purpose_name_is_refused_a_name_a_url_a_command_a_value(self):
        for argv in ([], ["openrouter_key_info", "openrouter_api_key"], ["openrouter_key_info", "https://evil.example/"], ["openrouter_key_info", "NAME=groq_api_key"],
                     ["openrouter_key_info;cat /etc/passwd"], ["../../etc/passwd"], ["OpenRouter_Key_Info"], ["x"], [""]):
            status, out = self.call(argv)
            self.assertEqual((status, out["code"]), (1, "SECRET_BAD_ARGS"), argv)
        self.assertEqual(self.fake.asked + self.fake.fetched, [])

    def test_a_caller_not_on_the_purpose_is_refused_and_the_credential_is_not_touched(self):
        status, out = self.call(["openrouter_key_info"], caller="somebody")
        self.assertEqual((status, out["code"]), (1, "SECRET_CALLER_NOT_ALLOWED"))
        self.assertEqual(self.fake.asked, [])
        self.assertEqual(self.audit()[-1]["caller"], "somebody")

    def test_not_root_is_refused(self):
        self.assertEqual(self.call(["openrouter_key_info"], euid=1000)[1]["code"], "SECRET_NOT_ROOT")
        self.assertEqual(self.fake.asked, [])

    def test_twenty_one_calls_in_a_minute_get_a_rate_limit_and_the_credential_is_not_touched(self):
        codes = [self.call(["check_openrouter_credential"])[1].get("ok") for _ in range(20)]
        self.assertEqual(codes, [True] * 20)
        asked = len(self.fake.asked)
        status, out = self.call(["check_openrouter_credential"])
        self.assertEqual((status, out["code"]), (1, "SECRET_RATE_LIMITED"))
        self.assertEqual(len(self.fake.asked), asked)
        self.now += 61                                                         # a minute later it works again
        self.assertTrue(self.call(["check_openrouter_credential"])[1]["ok"])

    def test_an_unwritable_audit_log_refuses_before_anything_is_used(self):
        status, out = self.call(["openrouter_key_info"], log=os.path.join(self.tmp, "no", "such", "dir", "log"))
        self.assertEqual((status, out["code"]), (1, "SECRET_AUDIT_UNAVAILABLE"))
        self.assertEqual(self.fake.asked + self.fake.fetched, [])

    def test_an_unreadable_credential_is_a_fixed_code(self):
        self.fake.value = None
        self.assertEqual(self.call(["openrouter_key_info"])[1]["code"], "SECRET_CRED_UNREADABLE")

    def test_a_group_writable_registry_file_or_directory_is_refused(self):
        os.chmod(os.path.join(self.reg, "openrouter_key_info.json"), 0o664)
        self.assertEqual(self.call(["openrouter_key_info"])[1]["code"], "SECRET_REGISTRY_UNSAFE")
        os.chmod(os.path.join(self.reg, "openrouter_key_info.json"), 0o644)
        os.chmod(self.reg, 0o775)
        self.assertEqual(self.call(["openrouter_key_info"])[1]["code"], "SECRET_REGISTRY_UNSAFE")

    def test_a_registry_entry_owned_by_somebody_else_is_refused(self):
        self.assertEqual(self.call(["openrouter_key_info"], owner=os.getuid() + 1)[1]["code"], "SECRET_REGISTRY_UNSAFE")

    def test_a_registry_entry_with_a_bad_shape_is_refused_http_urls_odd_names_wrong_purpose_empty_callers_unknown_action(self):
        good = {"purpose": "p_one", "secret": "openrouter_api_key", "callers": ["opc"], "action": "http_get_fields", "url": "https://openrouter.ai/api/v1/key", "fields": ["data.usage"]}
        for change in ({"url": "http://openrouter.ai/api/v1/key"}, {"url": "https://x.example/a b"}, {"secret": "../groq_api_key"}, {"secret": "Groq"}, {"purpose": "other"},
                       {"callers": []}, {"callers": [1]}, {"action": "print_value"}, {"fields": []}, {"fields": ["data..usage"]}, {"fields": ["Data.Usage"]}):
            self.put("p_one", dict(good, **change))
            self.assertEqual(self.call(["p_one"])[1]["code"], "SECRET_REGISTRY_UNSAFE", change)


class Redaction(Case):
    def spec(self):
        self.put("p_echo", {"purpose": "p_echo", "secret": "openrouter_api_key", "callers": ["opc"], "action": "http_get_fields", "url": "https://openrouter.ai/api/v1/key", "fields": ["data.echo"]})

    def test_an_answer_that_would_contain_the_value_in_any_spelling_is_refused_and_prints_nothing_else(self):
        self.spec()
        spellings = [VALUE, base64.b64encode(VALUE.encode()).decode(), base64.urlsafe_b64encode(VALUE.encode()).decode().rstrip("="), urllib.parse.quote(VALUE, safe=""), VALUE.encode().hex()]
        for spelling in spellings:
            self.fake.reply = {"data": {"echo": spelling}}
            status, out = self.call(["p_echo"])
            self.assertEqual((status, out), (1, {"ok": False, "code": "SECRET_OUTPUT_CONTAINS_SECRET"}), spelling)
            self.assertEqual(self.audit()[-1]["result"], "SECRET_OUTPUT_CONTAINS_SECRET")

    def test_a_nested_or_long_field_is_refused_rather_than_returned(self):
        self.spec()
        self.fake.reply = {"data": {"echo": {"a": 1}}}
        self.assertEqual(self.call(["p_echo"])[1]["code"], "SECRET_FIELD_NOT_SCALAR")
        self.fake.reply = {"data": {"echo": "y" * 101}}
        self.assertEqual(self.call(["p_echo"])[1]["code"], "SECRET_FIELD_NOT_SCALAR")

    def test_a_reply_that_is_not_json_or_too_big_is_a_fixed_code(self):
        self.spec()
        self.fake.fetch = lambda url, bearer: b"<html>not json</html>"
        self.assertEqual(self.call(["p_echo"])[1]["code"], "SECRET_BAD_REPLY")
        self.fake.fetch = lambda url, bearer: b"x" * (su.FETCH_MAX + 1)
        self.assertEqual(self.call(["p_echo"])[1]["code"], "SECRET_BAD_REPLY")

    def test_a_failed_fetch_is_a_fixed_code(self):
        def boom(url, bearer):
            raise su.Refused("SECRET_FETCH_FAILED")
        self.fake.fetch = boom
        self.assertEqual(self.call(["openrouter_key_info"])[1]["code"], "SECRET_FETCH_FAILED")

    def test_main_prints_one_json_line_and_returns_the_status(self):
        out = io.StringIO()
        old, built = su.Broker, self.broker()
        su.Broker = lambda: built
        try:
            with mock.patch.dict(os.environ, {"SUDO_USER": "opc"}):                # the caller name comes from sudo
                self.assertEqual(su.main(["check_openrouter_credential"], out), 0)
        finally:
            su.Broker = old
        self.assertEqual(len(out.getvalue().strip().splitlines()), 1)
        self.assertNotIn(VALUE, out.getvalue())


class Shipped(unittest.TestCase):
    def test_the_shipped_registry_entries_are_valid_and_name_only_registered_credentials(self):
        names = sorted(os.listdir(os.path.join(HERE, "purposes.d")))
        self.assertEqual(names, ["check_openrouter_credential.json", "openrouter_key_info.json"])
        for name in names:
            with open(os.path.join(HERE, "purposes.d", name)) as handle:
                spec = json.load(handle)
            self.assertEqual(spec["purpose"] + ".json", name)
            self.assertEqual(spec["secret"], "openrouter_api_key")
            self.assertIn(spec["action"], su.ACTIONS)
            self.assertTrue(su.NAME.fullmatch(spec["secret"]) and su.PURPOSE.fullmatch(spec["purpose"]))

    def test_the_program_has_no_listener_timer_or_shell(self):
        with open(su.__file__) as handle:
            source = handle.read()
        for banned in ("socket.", "shell=True", "os.system", "sched", "threading.Timer", "while True"):
            self.assertNotIn(banned, source, banned)


if __name__ == "__main__":
    unittest.main()
