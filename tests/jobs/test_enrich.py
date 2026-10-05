import json
import unittest
from unittest import mock

from lifeos.jobs import enrich


class Page:
    def __init__(self, status=200, html=""):
        self.status, self.html = status, html


JOB = ("<html><head><title>Senior Data Engineer</title><script type='application/ld+json'>"
       '{"@type":"JobPosting","title":"Senior Data Engineer","datePosted":"%s","description":"%s"}'
       "</script></head><body>x</body></html>")
LONG = "<p>" + ("Responsibilities: build pipelines and own data quality across the platform. You will need experience with SQL and Python skills. " * 4) + "</p>"


class EnrichTests(unittest.TestCase):
    def _read(self, html, title="Senior Data Engineer", status=200):
        with mock.patch.object(enrich, "fetch", return_value=Page(status, html)):
            return enrich.read_page("https://careers.example.com/j/1", title)

    def test_fresh_posting_is_ready(self):
        today = enrich._now().date().isoformat()
        self.assertEqual(self._read(JOB % (today, LONG.replace('"', "'")))["outcome"], "ready")

    def test_old_posting_is_stale(self):
        self.assertEqual(self._read(JOB % ("2020-01-01", LONG.replace('"', "'")))["outcome"], "stale")

    def test_dead_page_is_closed(self):
        self.assertEqual(self._read("", status=404)["outcome"], "closed")

    def test_other_job_is_a_mismatch(self):
        today = enrich._now().date().isoformat()
        self.assertEqual(self._read(JOB % (today, LONG.replace('"', "'")), title="Registered Nurse Manager")["outcome"],
                         "mismatch")

    def test_blocked_page_is_retryable(self):
        self.assertEqual(self._read("", status=403)["outcome"], "blocked")

    def test_title_check_is_lenient_but_not_blind(self):
        self.assertTrue(enrich._title_ok("Sr. Data Engineer (Remote)", "Senior Data Engineer - Acme"))
        self.assertFalse(enrich._title_ok("Data Engineer", "Account Executive"))


class BlockedReportTests(unittest.TestCase):
    def test_counts_reasons_and_host_families_without_naming_anyone(self):
        results = [(1, {"outcome": "blocked", "reason": "http_403"}), (2, {"outcome": "blocked", "reason": "http_403"}),
                   (3, {"outcome": "blocked", "reason": "jd_thin"}), (4, {"outcome": "ready"})]
        urls = {1: "https://acme.wd5.myworkdayjobs.com/en/job/1", 2: "https://careers.example.com/j/2",
                3: "https://www.linkedin.com/jobs/view/3", 4: "https://boards.greenhouse.io/x/jobs/4"}
        report = enrich.blocked_report(results, urls)
        self.assertEqual(report["reasons"], {"http_403": 2, "jd_thin": 1})
        self.assertEqual(report["families"], {"myworkdayjobs.com": 1, "employer_site": 1, "linkedin": 1})
        self.assertNotIn("acme", str(report))


class FallbackOnceTests(unittest.TestCase):
    def test_empty_pages_get_one_browser_fetch_then_are_marked_and_skipped(self):
        from unittest import mock
        from tests.kit.db import FakeConn
        rows = [(1, "https://a.example/j/1", "PM", "ats", None), (2, "https://b.example/j/2", "PM", "ats", enrich.TRIED_REASON)]
        results = [(1, {"outcome": "blocked", "reason": "description_empty"}),
                   (2, {"outcome": "blocked", "reason": enrich.TRIED_REASON})]
        counts = {"outcome": {"blocked": 2}}
        asked = []
        with mock.patch.object(enrich.store, "connect", FakeConn), \
                mock.patch.object(enrich.usage, "reserve", lambda c, n: n), \
                mock.patch.object(enrich.tinyfish, "fetch_many", lambda urls, **kw: (asked.extend(urls) or ({}, []))):
            enrich._fallback(results, rows, counts)
        self.assertEqual(asked, ["https://a.example/j/1"])                 # the already-tried page was not sent again
        self.assertEqual(results[0][1]["reason"], enrich.TRIED_REASON)
        self.assertEqual(counts["fallback_attempted"], 1)


class EmbeddedDataTests(unittest.TestCase):
    def test_a_shell_page_with_the_posting_in_next_data_is_read(self):
        body = ("Responsibilities: build pipelines and own data quality. You will need experience with SQL and Python skills. " * 5)
        page = ('<html><title>Senior Data Engineer | Acme</title><body><div id="root"></div>'
                '<script id="__NEXT_DATA__" type="application/json">{"props": {"job": {"description": "' + body + '"}}}</script></body></html>')
        result = enrich.parse_html("https://careers.example.com/j/1", "Senior Data Engineer", page)
        self.assertEqual((result["outcome"], result["source_kind"]), ("ready", "next_data"))


if __name__ == "__main__":
    unittest.main()


class FreshnessAuthority(unittest.TestCase):
    """The lane policy is the only freshness authority: enrich consults it, it never carries its own day count."""

    def desc(self):
        from lifeos.jobs import jd
        return jd.describe(LONG, is_html=True)

    def test_scale_up_keeps_a_20_day_old_job_us_remote_does_not(self):
        from datetime import timedelta
        from lifeos.jobs import enrich
        posted = enrich._now().date() - timedelta(days=20)
        scale = enrich.finish("Program Manager", self.desc(), "Program Manager", posted, "ats_api", None, "Scale-Up")
        us = enrich.finish("Program Manager", self.desc(), "Program Manager", posted, "ats_api", None, "Newsletter")
        self.assertEqual((scale["outcome"], us["outcome"]), ("ready", "stale"))
        old = enrich.finish("Program Manager", self.desc(), "Program Manager", enrich._now().date() - timedelta(days=45), "ats_api", None, "Scale-Up")
        self.assertEqual(old["outcome"], "stale")                                  # 30 days for Scale-Up

    def test_no_module_outside_lanes_defines_its_own_freshness_number(self):
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / "lifeos"
        bad = [str(p.relative_to(root)) for p in root.rglob("*.py") if p.name != "lanes.py"
               and re.search(r"^(MAX_AGE_DAYS|MAX_AGE|FRESH\w*DAYS)\s*=\s*\d+", p.read_text(), re.M)]
        self.assertEqual(bad, [])


class DiceEasyApply(unittest.TestCase):
    def test_a_dice_page_with_the_easy_apply_marker_is_flagged(self):
        from unittest import mock
        html = JOB % (enrich._now().date().isoformat(), LONG.replace('"', "'")) + "<button>Easy Apply</button>"
        with mock.patch.object(enrich, "fetch", return_value=Page(200, html)):
            got = enrich.read_page("https://www.dice.com/job-detail/1dde497d-8758-4eed-8aac-816bed294b63", "Senior Data Engineer")
            plain = enrich.read_page("https://careers.example.com/j/1", "Senior Data Engineer")
        self.assertEqual((got["outcome"], got.get("apply_kind")), ("ready", "easy_apply"))
        self.assertNotIn("apply_kind", plain)


class MismatchTests(unittest.TestCase):
    """A rejected link must be counted by reason, producer and site, and must cost an attempt so it cannot bounce forever."""

    def test_mismatch_report_counts_reason_producer_family_and_repeats_without_content(self):
        results = [(1, {"outcome": "mismatch", "reason": "jd_listing"}), (2, {"outcome": "mismatch"}), (3, {"outcome": "ready"}),
                   (4, {"outcome": "mismatch", "reason": "jd_listing"})]
        urls = {1: "https://boards.example.com/a/1", 2: "https://jobs.example.com/b/2", 3: "https://x.example.com/3", 4: "https://boards.example.com/a/4"}
        out = enrich.mismatch_report(results, urls, {1: "lensa", 2: "linkedin-alerts", 4: "lensa"}, {1: "jd_listing", 2: None, 4: None})
        self.assertEqual(out["reasons"], {"jd_listing": 2, "title": 1})
        self.assertEqual(out["by_source"], {"lensa": 2, "linkedin-alerts": 1})
        self.assertEqual(out["repeat"], 1)
        self.assertNotIn("example.com/a", json.dumps(out))

    def test_a_mismatch_is_an_attempt_and_parks_on_hold_at_the_cap(self):
        from lifeos.platform import limits
        from tests.kit.db import FakeConn
        seen = []
        conn = FakeConn(handler=lambda sql, args, cursor: seen.append((sql, args)))
        enrich.save(conn, 7, {"outcome": "mismatch", "reason": "jd_listing"})
        sql, args = seen[-1]
        self.assertIn("resolve_attempts=resolve_attempts+1", sql)
        self.assertIn("IF(resolve_attempts+1>=%s, 'HOLD', 'NEW')", sql)
        self.assertEqual((args[0], args[1], args[3]), (limits.RESOLVE_MAX_ATTEMPTS, "jd_listing", 7))


class TitleProofTests(unittest.TestCase):
    """A link whose shape cannot vouch for it (resolver accepted it on provenance) must be proven by the page's own title."""
    AMBIGUOUS_URL = "https://careers-acme.icims.com/jobs/intro"          # no id anywhere: no_job_id

    def _read(self, html, proof="unproven", url=None):
        with mock.patch.object(enrich, "fetch", return_value=Page(200, html)):
            return enrich.read_page(url or self.AMBIGUOUS_URL, "Senior Data Engineer", None, proof)

    def _page(self, page_title, body="x"):
        today = enrich._now().date().isoformat()
        return (f"<html><head><title>{page_title}</title><script type='application/ld+json'>"
                '{"@type":"JobPosting","title":"%s","datePosted":"%s","description":"%s"}'
                "</script></head><body>%s</body></html>") % (page_title, today, LONG.replace('"', "'"), body)

    def test_the_right_title_proves_it_and_marks_the_job(self):
        result = self._read(self._page("Senior Data Engineer"))
        self.assertEqual((result["outcome"], result.get("proof")), ("ready", "title"))

    def test_a_portal_page_whose_body_lists_the_title_is_not_proof(self):
        body = "Open roles: Senior Data Engineer, Account Manager, Nurse, Welder, Driver"
        result = self._read(self._page("Careers at Acme", body))
        self.assertEqual(result["outcome"], "mismatch")

    def test_a_login_wall_is_not_proof(self):
        self.assertNotEqual(self._read("<html><head><title>Sign in</title></head><body>Please sign in to continue</body></html>")["outcome"], "ready")

    def test_without_provenance_the_shape_still_refuses_before_any_fetch(self):
        with mock.patch.object(enrich, "fetch", side_effect=AssertionError("must not fetch")):
            self.assertEqual(enrich.read_page(self.AMBIGUOUS_URL, "Senior Data Engineer", None, None), {"outcome": "mismatch", "reason": "no_job_id"})

    def test_a_bare_root_is_refused_even_with_provenance(self):
        with mock.patch.object(enrich, "fetch", side_effect=AssertionError("must not fetch")):
            self.assertEqual(enrich.read_page("https://careers-acme.icims.com/", "Senior Data Engineer", None, "unproven")["reason"], "root")

    def test_a_sound_link_is_not_made_strict(self):
        result = self._read(self._page("Software Engineer Data Platform Senior"), proof="unproven", url="https://careers.example.com/j/1")
        self.assertNotIn("proof", result)                                  # shape is fine: the ordinary lenient check applies

    def test_the_rendered_fallback_keeps_the_strict_proof(self):
        self.assertTrue(enrich._strict(1, {1: (self.AMBIGUOUS_URL, "T")}, {1}))
        self.assertFalse(enrich._strict(2, {2: (self.AMBIGUOUS_URL, "T")}, {1}))
        self.assertFalse(enrich._strict(1, {1: ("https://careers.example.com/j/1", "T")}, {1}))


class OneBadPageTests(unittest.TestCase):
    def test_a_page_that_raises_is_blocked_and_the_rest_of_the_batch_still_runs(self):
        from tests.kit.db import FakeConn
        rows = [(1, "https://a.example.com/jobs/111111", "Senior Engineer", "ats", None, "US Remote", "web:a", None),
                (2, "https://b.example.com/jobs/222222", "Senior Engineer", "ats", None, "US Remote", "web:b", None)]

        def handler(sql, args, cursor):
            if sql.startswith("SELECT id, final_apply_url"):
                cursor.fetchall = lambda: rows

        seen = []

        def read(url, *a):
            seen.append(url)
            if "a.example" in url:
                raise ValueError("boom")
            return {"outcome": "closed"}

        with mock.patch.object(enrich.store, "connect", return_value=FakeConn(handler=handler)), mock.patch.object(enrich.store, "ensure_schema"), \
                mock.patch.object(enrich, "read_page", side_effect=read):
            counts = enrich.run(10, False)
        self.assertEqual(len(seen), 2)
        self.assertEqual(counts["outcome"], {"blocked": 1, "closed": 1})


class EmployerFromThePageTests(unittest.TestCase):
    HTML = ('<html><title>Senior Program Manager</title><script type="application/ld+json">{"@type":"JobPosting","title":"Senior Program Manager",'
            '"hiringOrganization":{"@type":"Organization","name":"Acme Robotics, Inc."},"datePosted":"%s","description":"%s"}</script></html>')

    def test_the_posting_names_its_employer_and_a_board_host_is_replaced_only_when_it_looks_like_one(self):
        import re
        from datetime import date
        text = "<p>Responsibilities: lead delivery of the program and own stakeholder communication. Requirements: 8+ years of program management experience, strong skills.</p>" * 4
        page = self.HTML % (date.today().isoformat(), text.replace('"', "'"))
        result = enrich.parse_html("https://x.example/jobs/1", "Senior Program Manager", page)
        self.assertEqual(result["outcome"], "ready")
        self.assertEqual(result["company"], "Acme Robotics, Inc.")
        for board in ("Edtech Com", "Showbizjobs Com", "Nomadjob Com", "Foundationccc Wd1 Myworkdayjobs Com", "Edtechjobs Io"):
            self.assertTrue(re.search(enrich.BOARD_HOST_COMPANY, board, re.I), board)
        for real in ("Stripe", "Acme Robotics, Inc.", "ServiceNow", "Community Health"):
            self.assertFalse(re.search(enrich.BOARD_HOST_COMPANY, real, re.I), real)


class FirstPartyRelinkTests(unittest.TestCase):
    URL = "https://www.revolut.com/en-GB/careers/position/chief-risk-officer-06107fff-99e7-4841-b66c-e101da5f1031/"

    class Cur:
        def __init__(self, rows):
            self.rows, self.sql, self.rowcount = rows, [], 3

        def execute(self, sql, params=()):
            sql % tuple("x" for _ in params)                 # the statement must survive %-formatting (the 5 PM crash)
            self.sql.append((sql, params))

        def fetchall(self):
            return self.rows

    def test_requeues_a_first_party_link_that_passes_the_link_test(self):
        cur = self.Cur([(7, self.URL), (8, "https://www.revolut.com/"), (9, "https://careers.example.com/careers/product-manager")])
        self.assertEqual(enrich.relink_first_party(cur), 2 + 3)                  # two relinked, three stale first-party rows read again (D111)
        update = [(s, p) for s, p in cur.sql if s.startswith("UPDATE")]
        self.assertEqual(len(update), 3)
        self.assertEqual((update[0][1][0], update[0][1][1]), (self.URL, None))   # a sound link needs no proof
        self.assertEqual((update[1][1][0], update[1][1][1]), ("https://careers.example.com/careers/product-manager", "unproven"))   # an ambiguous shape is proved by the page title
        self.assertIn("EXCLUDED_STALE", update[2][0])
        self.assertIn("notion_page_id IS NULL", update[2][0])

    def test_revolut_403_is_retried_as_chrome(self):
        html = JOB % ("2020-01-01", LONG.replace('"', "'"))
        with mock.patch.object(enrich, "fetch", return_value=Page(403, "")), \
                mock.patch.object(enrich.impersonate, "fetch", return_value=Page(200, html)) as chrome:
            result = enrich.read_page(self.URL, "Senior Data Engineer")
        chrome.assert_called_once()
        self.assertEqual(result["outcome"], "stale")

    def test_other_hosts_403_stays_blocked(self):
        with mock.patch.object(enrich, "fetch", return_value=Page(403, "")), \
                mock.patch.object(enrich.impersonate, "fetch") as chrome:
            result = enrich.read_page("https://careers.example.com/j/1", "x")
        chrome.assert_not_called()
        self.assertEqual(result["reason"], "http_403")

    def test_a_first_party_403_is_retried_as_the_announced_bot(self):
        html = JOB % ("2020-01-01", LONG.replace('"', "'"))
        with mock.patch.object(enrich, "fetch", return_value=Page(403, "")), \
                mock.patch.object(enrich.egress, "honest", return_value=Page(200, html)) as honest:
            result = enrich.read_page("https://careers.example.com/j/1", "Senior Data Engineer", first_party=True)
        honest.assert_called_once()
        self.assertEqual(result["outcome"], "ready")

    def test_a_greenhouse_embed_page_is_read_from_the_boards_own_api(self):
        shell = "<html><title>Job openings</title><script src='https://boards.greenhouse.io/embed/job_board/js?for=acme'></script>" + "<p>all roles</p>" * 500
        api = {"title": "Senior Data Engineer", "html": LONG, "posted": "2026-10-01"}
        with mock.patch.object(enrich, "fetch", return_value=Page(200, shell)), mock.patch.object(enrich, "_api_job", side_effect=[None, api]) as read:
            result = enrich.read_page("https://acme.example/job-openings/?gh_jid=4567", "Senior Data Engineer", first_party=True)
        read.assert_called_with("https://boards.greenhouse.io/embed/job_app?for=acme&token=4567")
        self.assertEqual(result["outcome"], "ready")
        self.assertIsNone(enrich._greenhouse_embed("https://acme.example/jobs/", shell))               # no gh_jid, no embed read

    def test_the_board_name_is_read_when_for_is_the_first_query_parameter(self):
        seen = []
        with mock.patch.object(enrich, "fetch", side_effect=lambda url, **k: seen.append(url) or Page(404, "")):
            enrich._api_job("https://boards.greenhouse.io/embed/job_app?for=acme&token=4567")
            enrich._api_job("https://boards.greenhouse.io/embed/job_app?token=4567&for=acme")
        self.assertEqual(seen, ["https://boards-api.greenhouse.io/v1/boards/acme/jobs/4567"] * 2)

    def test_a_role_on_its_employers_own_board_is_accepted_when_the_page_carries_its_words(self):
        body = LONG.replace('"', "'")
        page = f"<html><head><title>Careers</title></head><body><p>Senior Data Engineer</p>{body}</body></html>"
        other = page.replace("Senior Data Engineer", "Office Manager")
        for strict in (True, False):                                                                     # a link that names no job, or one that does
            self.assertEqual(enrich.parse_html("https://x.example/careers", "Senior Data Engineer", page, "Scale-Up", strict, True)["outcome"], "ready")
            self.assertEqual(enrich.parse_html("https://x.example/careers", "Senior Data Engineer", other, "Scale-Up", strict, True)["outcome"], "mismatch")
        self.assertEqual(enrich.parse_html("https://x.example/careers", "Senior Data Engineer", page, "Scale-Up", True, False)["outcome"], "mismatch")      # a link that names no job and no provenance: the <title> must prove it

    def test_the_board_name_is_read_when_for_is_the_first_query_parameter(self):
        seen = []
        with mock.patch.object(enrich, "fetch", side_effect=lambda url, **k: seen.append(url) or Page(404, "")):
            enrich._api_job("https://boards.greenhouse.io/embed/job_app?for=acme&token=4567")
            enrich._api_job("https://boards.greenhouse.io/embed/job_app?token=4567&for=acme")
        self.assertEqual(seen, ["https://boards-api.greenhouse.io/v1/boards/acme/jobs/4567"] * 2)

    def test_a_heading_that_names_the_role_proves_an_ambiguous_link_on_an_employers_own_page(self):
        body = LONG.replace('"', "'")
        page = f"<html><head><title>Careers</title></head><body><h2>Senior Data Engineer</h2>{body}</body></html>"
        other = page.replace("Senior Data Engineer", "Office Manager")
        loose = enrich.parse_html("https://x.example/careers", "Senior Data Engineer", page, "Scale-Up", True, True)
        self.assertEqual(loose["outcome"], "ready")
        self.assertEqual(enrich.parse_html("https://x.example/careers", "Senior Data Engineer", other, "Scale-Up", True, True)["outcome"], "mismatch")
        self.assertEqual(enrich.parse_html("https://x.example/careers", "Senior Data Engineer", page, "Scale-Up", True, False)["outcome"], "mismatch")   # no provenance, no heading proof

    def test_a_mismatch_hold_is_read_again_once_a_day(self):
        cur = self.Cur([(11, "https://careers.example.com/careers/product-manager")])
        enrich.relink_first_party(cur)
        select = cur.sql[0][0]
        self.assertIn("'link_mismatch', 'jd_listing', 'jd_template'", select)
        self.assertIn("LEAST(resolve_attempts", [s for s, _ in cur.sql if s.startswith("UPDATE")][0])


class LocationFromThePageTests(unittest.TestCase):
    """D111: a board that states no place is read off the job page's own JSON-LD."""

    def page(self, location):
        today = enrich._now().date().isoformat()
        return ("<html><head><title>Senior Data Engineer</title><script type='application/ld+json'>"
                '{"@type":"JobPosting","title":"Senior Data Engineer","datePosted":"%s","description":"%s","jobLocation":%s}'
                "</script></head><body>x</body></html>") % (today, LONG.replace('"', "'"), location)

    def test_the_posting_location_is_carried_in_the_result(self):
        for raw, expected in (('{"@type":"Place","address":{"addressLocality":"London","addressCountry":"GB"}}', "London, GB"),
                              ('[{"address":{"addressLocality":"London"}},{"address":{"addressLocality":"Leeds","addressCountry":{"name":"United Kingdom"}}}]', "London | Leeds, United Kingdom"),
                              ('{"address":"London, UK"}', "London, UK")):
            result = enrich.parse_html("https://careers.example.com/j/1", "Senior Data Engineer", self.page(raw))
            self.assertEqual((result["outcome"], result.get("location")), ("ready", expected), raw)

    def test_no_location_in_the_page_adds_nothing(self):
        today = enrich._now().date().isoformat()
        html = JOB % (today, LONG.replace('"', "'"))
        self.assertNotIn("location", enrich.parse_html("https://careers.example.com/j/1", "Senior Data Engineer", html))

    def test_a_stored_place_is_never_overwritten(self):
        class Cur:
            def __init__(self):
                self.sql, self.rowcount = [], 0

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=()):
                sql % tuple("x" for _ in params)
                self.sql.append(sql)

        class Conn:
            def __init__(self, cur):
                self.cur = cur

            def cursor(self):
                return self.cur

        cur = Cur()
        result = {"outcome": "ready", "description": {"full_text": "t" * 300, "summary": "s", "responsibilities": "", "requirements": "", "qualifications": "", "fingerprint": "f"},
                  "source_kind": "jsonld", "location": "London, GB", "posted": None, "final_url": "https://x"}
        enrich.save(Conn(cur), 5, result)
        update = next(s for s in cur.sql if "SET location_text" in s)
        self.assertIn("location_text IS NULL OR location_text=''", update)
