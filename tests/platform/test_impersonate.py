import unittest
from unittest import mock

from lifeos.platform import impersonate
from lifeos.sources.web import lister


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class Session:
    def __init__(self, script, log):
        self.script, self.log = script, log

    def get(self, url, **kw):
        self.log.append(url)
        return self.script.pop(0) if self.script else Resp(403)


def factory(script, log):
    return lambda: Session(script, log)


class Impersonate(unittest.TestCase):
    def test_warms_up_then_returns_the_first_good_page(self):
        log = []
        got = impersonate.fetch("https://a.example/careers/", warm_url="https://a.example/", must_contain="__NEXT_DATA__",
                                session_factory=factory([Resp(200, "home"), Resp(200, "x __NEXT_DATA__ y")], log), sleep=lambda s: None)
        self.assertEqual((got.status, "__NEXT_DATA__" in got.html, log), (200, True, ["https://a.example/", "https://a.example/careers/"]))

    def test_retries_then_reports_the_last_status(self):
        log = []
        got = impersonate.fetch("https://a.example/c", must_contain="x", session_factory=factory([], log), sleep=lambda s: None)
        self.assertEqual((got.status, got.html, len(log)), (403, "", 3))

    def test_a_200_without_the_marker_is_not_accepted(self):
        got = impersonate.fetch("https://a.example/c", must_contain="__NEXT_DATA__", rounds=1, session_factory=factory([Resp(200, "blocked page")], []))
        self.assertEqual(got.status, 200)
        self.assertNotIn("__NEXT_DATA__", got.html)

    def test_missing_library_is_a_clean_failure(self):
        with mock.patch.dict("sys.modules", {"curl_cffi": None}):
            self.assertEqual(impersonate.fetch("https://a.example/c").error, "no_impersonation")

    def test_lister_uses_it_only_for_flagged_sources(self):
        source = {"id": "su-x", "kind": "revolut_html", "url": "https://a.example/careers/", "company": "A", "impersonate": True}
        plain = mock.Mock(side_effect=AssertionError("plain fetch must not be used"))
        with mock.patch.object(lister.impersonate, "fetch", return_value=impersonate.Fetched("u", 403, "", 0, "http")) as imp:
            got = lister.list_source(source, plain)
        self.assertEqual((got.status, got.reason, imp.call_count), ("FAILED", "rate_limited", 1))


if __name__ == "__main__":
    unittest.main()


class AnnouncedAgent(unittest.TestCase):
    def test_lister_uses_the_announced_bot_agent_only_for_flagged_sources(self):
        from lifeos.platform import egress
        source = {"id": "su-x", "kind": "static_complete_html", "url": "https://a.example/careers/", "company": "A", "announced_ua": True}
        plain = mock.Mock(side_effect=AssertionError("plain fetch must not be used"))
        page = impersonate.Fetched("u", 200, '<a href="/careers/data-engineer">Data Engineer</a>', 0)
        with mock.patch.object(egress, "honest", return_value=page) as honest:
            got = lister.list_source(source, plain)
        self.assertEqual((got.status, [j["title"] for j in got.jobs], honest.call_count), ("COMPLETE", ["Data Engineer"], 1))
