"""LIFE-528 outcome 2: the credential list is names, providers and dates only, every entry is complete, and a dated entry warns through the existing expiry alert."""
import datetime as dt
import re
import unittest

from lifeos.jobs import alerts as job_alerts
from lifeos.platform import credentials as cred
from lifeos.sources import alerts_app as app

TODAY = dt.date(2026, 11, 10)
SECRET_SHAPES = (re.compile(r"[A-Za-z0-9+/=_]{32,}"), re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
                 re.compile(r"\b(ghp_|github_pat_|ntn_|secret_|xox[bp]-|sk-|AKIA)[A-Za-z0-9_]+"), re.compile(r"-----BEGIN"), re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
                 re.compile(r"https?://[^\s]*[?&](token|key|secret)=", re.I))


BITWARDEN_NAMES = ("openclaw/outlook/clientId", "openclaw/outlook/personal/mailboxFingerprint", "openclaw/outlook/personal/refreshToken",
                   "openclaw/outlook/work/mailboxFingerprint", "openclaw/outlook/work/refreshToken")      # secret NAMES Jim gave (LIFE-528 comment 12557); a name is not a value


def secret_like(text):
    for name in BITWARDEN_NAMES:                                                                       # only these exact names are exempt; any other long run still fails
        text = text.replace(name, " ")
    return any(p.search(text) for p in SECRET_SHAPES)


class TheList(unittest.TestCase):
    def test_every_entry_is_complete_and_keys_are_unique(self):
        entries = cred.load()
        self.assertGreaterEqual(len(entries), 30)
        self.assertEqual(len({e["key"] for e in entries}), len(entries))
        for e in entries:
            self.assertEqual(set(cred.REQUIRED) - set(e), set(), e["key"])
            self.assertIn(e["expiry_source"], cred.SOURCES, e["key"])
            for field in ("label", "provider", "used_for", "read_in", "how_read"):
                self.assertTrue(e[field].strip(), (e["key"], field))
            if e["expires"]:
                dt.date.fromisoformat(e["expires"])                                  # a real date
                self.assertNotEqual(e["expiry_source"], "none", e["key"])           # a credential with a date cannot claim "no expiry exposed"
            if e["expiry_source"] == "none":
                self.assertIsNone(e["expires"], e["key"])

    def test_no_value_looks_like_a_secret_an_id_an_email_or_a_key(self):
        for e in cred.load():
            for field, value in e.items():
                if isinstance(value, str):
                    self.assertFalse(secret_like(value), (e["key"], field))

    def test_negative_control_the_secret_check_catches_planted_values(self):
        for planted in ("ghp_" + "a" * 36, "github_pat_" + "B" * 40, "secret_" + "c" * 30, "00000000-0000-4000-8000-000000000000", "-----" + "BEGIN " + "PRIV" + "ATE KEY-----", "jim@example.com",
                        "https://x.test/path?token=abc", "A" * 40):
            self.assertTrue(secret_like(planted), planted)
        self.assertFalse(secret_like("Notion internal integration secrets have no expiry"))
        self.assertFalse(secret_like("Bitwarden secret names: " + ", ".join(BITWARDEN_NAMES)))              # the listed names are allowed
        self.assertTrue(secret_like("openclaw/outlook/work/" + "Ab1" * 12))                                 # an unlisted long run under the same prefix is still caught

    def test_the_file_is_names_only_so_nothing_in_it_could_be_a_value(self):
        fields = {k for e in cred.load() for k in e}
        self.assertNotIn("value", fields)
        self.assertTrue(fields <= set(cred.REQUIRED), fields - set(cred.REQUIRED))                       # only the known name-and-date fields


class Rows(unittest.TestCase):
    def entry(self, expires, source="manual"):
        return {"key": "k", "label": "L", "provider": "P", "used_for": "u", "read_in": "r", "expiry_source": source, "expires": expires, "how_read": "h", "renew": "do it"}

    def test_status_follows_the_days_left(self):
        for days, want in ((-1, "Expired"), (0, "Warning"), (7, "Warning"), (8, "OK"), (300, "OK")):
            when = (TODAY + dt.timedelta(days=days)).isoformat()
            self.assertEqual(cred.status(self.entry(when), TODAY), want, days)
        self.assertEqual(cred.status(self.entry(None, "none"), TODAY), "Unknown")

    def test_rows_use_the_plain_source_names_and_carry_no_renew_text_or_how_read(self):
        row = cred.rows(TODAY, [self.entry(None, "none"), self.entry("2027-01-01", "provider_reported")])
        self.assertEqual([r["source"] for r in row], ["No expiry exposed", "Provider reported"])
        self.assertEqual(row[1]["days_left"], (dt.date(2027, 1, 1) - TODAY).days)
        self.assertNotIn("renew", row[0])
        self.assertNotIn("how_read", row[0])

    def test_only_dated_entries_become_expiries_in_the_alert_shape(self):
        got = cred.expiries([self.entry(None, "none"), {**self.entry("2027-01-01"), "key": "dated"}])
        self.assertEqual(list(got), ["dated"])
        self.assertEqual(set(got["dated"]), {"label", "expires", "renew"})

    def test_the_real_list_has_the_one_dated_credential_and_many_with_no_expiry_exposed(self):
        rows = cred.rows(TODAY)
        self.assertIn("timer_token", cred.expiries())
        self.assertGreater(sum(1 for r in rows if r["source"] == "No expiry exposed"), 10)


class Warning(unittest.TestCase):
    def test_a_dated_credential_inside_seven_days_raises_the_existing_expiry_alert(self):
        counts = {"stopped_sources": [], "yield_72h": {}, "yield_7d": {}, "daily": {}}
        soon = {"k": {"label": "Test token", "expires": (TODAY + dt.timedelta(days=5)).isoformat(), "renew": "renew it"}}
        far = {"k": {"label": "Test token", "expires": (TODAY + dt.timedelta(days=60)).isoformat(), "renew": "renew it"}}
        now = dt.datetime(2026, 11, 10, 12, 0)
        found = [a for a in job_alerts.detect(counts, now, soon) if a.key == "expiry_k"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, job_alerts.alerts.PAGE)
        self.assertNotIn("expiry_k", [a.key for a in job_alerts.detect(counts, now, far)])


class Screen(unittest.TestCase):
    def test_the_section_lists_every_credential_with_days_left_and_source_and_never_a_value(self):
        html = app._credentials(dt.datetime(2026, 11, 10, 12, 0))
        self.assertIn("No expiry exposed", html)
        self.assertIn("Provider reported", html)                                                       # LIFE-565: every dated entry now has a provider reading; the "Manual" label is still covered by the Rows tests
        for e in cred.load():
            self.assertIn(app.e(e["label"]), html)
        for fragment in ("renew", "how_read", "token=", "BEGIN"):
            self.assertNotIn(fragment, html)
        self.assertFalse(secret_like(re.sub(r"<[^>]+>", " ", html)))

    def test_the_page_carries_the_credentials_section_and_reads_no_notion(self):
        status, _, body, _ = app._page({"enabled": True, "mute": "", "channels": {k: ("ntfy",) for k in app.registry.REGISTRY}}, b"secret", "jim", dt.datetime(2026, 11, 10, 12, 0))
        self.assertEqual(status, 200)
        self.assertIn(b"Credentials", body)
        source = open(app.__file__).read() + open(cred.__file__).read()
        for call in ("notion_client", "api.notion.com", "import notion", "platform.notion"):
            self.assertNotIn(call, source)                                               # the screen and the list never call Notion


if __name__ == "__main__":
    unittest.main()
