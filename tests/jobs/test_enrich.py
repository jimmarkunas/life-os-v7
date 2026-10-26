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


class LinkedInGuestPageTests(unittest.TestCase):
    def test_the_description_is_the_employers_text_not_the_sign_in_page(self):
        from tests.jobs.test_jd import LINKEDIN_PAGE
        body = LINKEDIN_PAGE.replace("<ul><li>10 years of program delivery</li></ul>", "<ul><li>10 years of program delivery</li></ul>" + "<p>Experience with program governance and risk. </p>" * 8)
        result = enrich.parse_html("https://www.linkedin.com/jobs/view/4443205698", "Principal Technical Program Manager", body)
        self.assertEqual((result["outcome"], result["source_kind"]), ("ready", "page_text"))
        text = result["description"]["full_text"]
        self.assertIn("Own the program plan", text)
        self.assertNotIn("Password", text)
        self.assertNotIn("Join or sign in", text)

    def test_a_guest_page_with_an_empty_description_is_not_made_ready_by_its_sign_in_text(self):
        page = ('<html><head><title>Principal Technical Program Manager | Oracle | LinkedIn</title></head><body><h1>Principal Technical Program Manager</h1>'
                '<p>Join or sign in to find your next job. Email or phone. Password. Sign in. New to LinkedIn? Join now. By clicking Continue to join or sign in, '
                'you agree to the User Agreement, Privacy Policy and Cookie Policy.</p><div class="show-more-less-html__markup"></div></body></html>')
        result = enrich.parse_html("https://www.linkedin.com/jobs/view/4443205698", "Principal Technical Program Manager", page)
        self.assertEqual((result["outcome"], result.get("reason")), ("blocked", "description_empty"))


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

    def test_a_thin_page_on_a_scale_up_sponsors_own_board_is_ready_for_review_and_nothing_else_is_let_through(self):
        """Jim, Oct 21: Review it as long as there is a link. Every other lane and every aggregator still holds a thin page (jd_thin)."""
        from lifeos.jobs import enrich, jd
        thin = jd.describe("<p>" + "We are hiring a project lead to join our team. " * 6 + "</p>", is_html=True)               # about 280 characters: past the empty floor, under the thin floor
        self.assertGreaterEqual(len(thin["full_text"]), 200)
        ready = enrich.finish("Program Manager", thin, "Program Manager", None, "ats_api", None, "Scale-Up", False, True)
        self.assertEqual(ready["outcome"], "ready")
        for lane, first_party in (("Scale-Up", False), ("Newsletter", True), ("US Remote", True)):
            held = enrich.finish("Program Manager", thin, "Program Manager", None, "ats_api", None, lane, False, first_party)
            self.assertEqual((held["outcome"], held["reason"]), ("blocked", "jd_thin"), (lane, first_party))
        empty = jd.describe("<p>Short.</p>", is_html=True)
        self.assertEqual(enrich.finish("Program Manager", empty, "Program Manager", None, "ats_api", None, "Scale-Up", False, True)["reason"], "description_empty")     # an empty page is still not a role

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


class DrainPassTests(unittest.TestCase):
    """LIFE-76: the drain pass never spends the TinyFish Fetch day cap and leaves a page that needs that fallback untouched for the hourly pass."""
    def _run(self, drain):
        from tests.kit.db import FakeConn
        rows = [(1, "https://a.example.com/jobs/111111", "Senior Engineer", "ats", None, "US Remote", "web:a", None),
                (2, "https://b.example.com/jobs/222222", "Senior Engineer", "ats", None, "US Remote", "web:b", None)]

        def handler(sql, args, cursor):
            if sql.startswith("SELECT id, final_apply_url"):
                cursor.fetchall = lambda: rows

        saved, fallback = [], []

        def read(url, *a):
            return {"outcome": "blocked", "reason": "http_403"} if "a.example" in url else {"outcome": "closed"}

        with mock.patch.object(enrich.store, "connect", return_value=FakeConn(handler=handler)), mock.patch.object(enrich.store, "ensure_schema"), \
                mock.patch.object(enrich, "relink_first_party", return_value=0), mock.patch.object(enrich, "read_page", side_effect=read), \
                mock.patch.object(enrich, "_fallback", side_effect=lambda *a, **k: fallback.append(1)), \
                mock.patch.object(enrich, "save", side_effect=lambda c, job_id, r: saved.append(job_id)):
            counts = enrich.run(10, True, drain=drain)
        return counts, saved, fallback

    def test_the_drain_skips_the_fallback_and_saves_only_what_it_read(self):
        counts, saved, fallback = self._run(True)
        self.assertEqual((saved, fallback, counts["left_for_hourly"]), ([2], [], 1))      # the blocked page keeps its row as it was: no attempt counted

    def test_a_page_the_fallback_reads_keeps_the_link_it_was_read_from(self):
        from tests.kit.db import FakeConn
        rows = [(1, "https://a.example.com/jobs/111111", "Senior Engineer", "ats", None, "US Remote", "web:a", None)]

        def handler(sql, args, cursor):
            if sql.startswith("SELECT id, final_apply_url"):
                cursor.fetchall = lambda: rows

        def fallback(results, *a, **k):                              # the rendered read replaces the result, as the real fallback does
            results[0] = (1, {"outcome": "ready", "posted": None, "description": {"full_text": "x"}, "source_kind": "page_text"})

        saved = {}
        with mock.patch.object(enrich.store, "connect", return_value=FakeConn(handler=handler)), mock.patch.object(enrich.store, "ensure_schema"), \
                mock.patch.object(enrich, "relink_first_party", return_value=0), \
                mock.patch.object(enrich, "read_page", return_value={"outcome": "blocked", "reason": "jd_thin"}), \
                mock.patch.object(enrich, "_fallback", side_effect=fallback), \
                mock.patch.object(enrich, "save", side_effect=lambda c, job_id, r: saved.update({job_id: r})):
            enrich.run(10, True)
        self.assertEqual(saved[1]["final_url"], "https://a.example.com/jobs/111111")      # never None: save() would clear final_apply_url and the audit demote it as a root

    def test_the_hourly_pass_still_uses_the_fallback_and_saves_every_row(self):
        counts, saved, fallback = self._run(False)
        self.assertEqual((saved, fallback), ([1, 2], [1]))
        self.assertNotIn("left_for_hourly", counts)


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
        self.assertEqual(enrich.relink_first_party(cur), 2 + 3 + 3)              # two relinked, three held rows read again, three stale first-party rows read again (D111)
        update = [(s, p) for s, p in cur.sql if s.startswith("UPDATE")]
        self.assertEqual(len(update), 4)
        self.assertEqual((update[0][1][0], update[0][1][1]), (self.URL, None))   # a sound link needs no proof
        self.assertEqual((update[1][1][0], update[1][1][1]), ("https://careers.example.com/careers/product-manager", "unproven"))   # an ambiguous shape is proved by the page title
        self.assertIn("status='HOLD' AND final_apply_url IS NOT NULL", update[2][0])             # a parked role that has a link is read once a day, in its own hour
        self.assertIn(enrich.DAILY_SLOT, update[2][0])
        self.assertIn("EXCLUDED_STALE", update[3][0])
        self.assertIn("notion_page_id IS NULL", update[3][0])

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
        self.assertIn(enrich.DAILY_SLOT, select)
        self.assertNotIn("updated_at <", select)                                          # a hold stamp the daily listing refresh keeps touching can never say "a day ago"
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

            def fetchone(self):
                return None                                           # no stored row to compare with

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


class PostedDateAuthority(unittest.TestCase):
    """JOBS-1.1A: the accepted Posting Date follows D4 (sources in order of trust; the earliest credible date is the original posting date, a repost cannot make an old job look new, D6).
    The exact SELECT and UPDATE enrich.save sends are run against a real row, then the stored date goes through the lane decision the Fit stage makes
    (stored posted_date -> facts_for -> decide_all)."""
    TODAY = __import__("datetime").date(2026, 10, 11)
    DESC = {"full_text": "t" * 300, "summary": "s", "responsibilities": "", "requirements": "", "qualifications": "", "fingerprint": "f"}

    def saved(self, seed_date, seed_source, posted, outcome="ready", source=None, seed_age=40):
        import sqlite3
        from tests.kit.mysqlite import translate

        raw = sqlite3.connect(":memory:")
        raw.execute("CREATE TABLE v7_jobs (id INTEGER PRIMARY KEY, status TEXT, posted_date TEXT, posted_source TEXT, final_apply_url TEXT, posted_age_days INTEGER,"
                    " link_proof TEXT, unresolved_reason TEXT, updated_at TEXT)")
        raw.execute("INSERT INTO v7_jobs (id, status, posted_date, posted_source, posted_age_days) VALUES (7, 'RESOLVED', ?, ?, ?)", (seed_date, seed_source, seed_age))

        class Cur:
            rowcount = 0

            def __init__(self):
                self.last = None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=()):
                if sql.startswith("SELECT posted_date") or sql.startswith("UPDATE v7_jobs SET status"):          # the production statements, with their production arguments
                    self.last = raw.execute(translate(sql), tuple(str(x) if hasattr(x, "isoformat") else x for x in params))

            def fetchone(self):
                return self.last.fetchone()

        class Conn:
            def cursor(self):
                return Cur()

        result = {"outcome": outcome, "description": self.DESC, "source_kind": "jsonld", "posted": posted, "final_url": "https://example.com/j/1"}
        if source:
            result["posted_source"] = source
        with mock.patch.object(enrich, "_now", return_value=__import__("datetime").datetime(2026, 10, 11, 12, 0)):
            enrich.save(Conn(), 7, result)
        row = raw.execute("SELECT status, posted_date, posted_source, posted_age_days FROM v7_jobs WHERE id=7").fetchone()
        raw.close()
        return row

    def decision(self, stored_date):
        import datetime as dt
        from lifeos.jobs import lanes
        posted = dt.date.fromisoformat(stored_date) if stored_date else None
        facts = lanes.facts_for(85, "Program Manager", "Remote, US", "A fully remote role in the United States.", "$150,000 - $170,000", posted, dt.datetime(2026, 10, 10))
        return lanes.decide_all("US Remote", facts, self.TODAY)[0]

    def day(self, y, m, d):
        return __import__("datetime").date(y, m, d)

    def test_a_later_read_with_no_date_keeps_the_accepted_employer_date_and_the_job_stays_excluded(self):
        status, posted, source, age = self.saved("2026-09-01", "employer", posted=None)
        self.assertEqual((posted, source, age), ("2026-09-01", "employer", 40))
        self.assertEqual((self.decision(posted).status, self.decision(posted).reason), ("EXCLUDE", "older than 14 days"))   # US Remote: 14 days

    def test_erasing_the_date_would_have_admitted_the_same_job_as_new(self):
        self.assertEqual(self.decision(None).status, "ADMIT")                       # why the stored date matters: with it gone the job is aged by First Surfaced

    def test_a_job_with_no_date_stays_undated_and_a_date_is_never_invented(self):
        status, posted, source, age = self.saved(None, None, posted=None)
        self.assertEqual((posted, source, age), (None, None, 40))

    def test_a_reposts_later_employer_date_never_replaces_the_original_and_the_job_stays_excluded(self):
        status, posted, source, age = self.saved("2026-09-01", "employer", posted=self.day(2026, 10, 9))     # the employer page now says it was posted two days ago
        self.assertEqual((posted, source), ("2026-09-01", "employer"))
        self.assertEqual(age, 40)                                                       # the age is that of the retained date: 2026-10-11 minus 2026-09-01
        self.assertEqual((self.decision(posted).status, self.decision(posted).reason), ("EXCLUDE", "older than 14 days"))
        self.assertEqual(self.decision("2026-10-09").status, "ADMIT")                  # what the replaced date would have done

    def test_the_original_date_wins_between_two_employer_reads_so_an_earlier_date_replaces_a_later_one(self):
        status, posted, source, age = self.saved("2026-10-05", "employer", posted=self.day(2026, 9, 20))
        self.assertEqual((posted, source, age), ("2026-09-20", "employer", 21))
        self.assertEqual(self.decision(posted).status, "EXCLUDE")

    def test_the_same_date_read_again_changes_nothing(self):
        self.assertEqual(self.saved("2026-10-05", "employer", posted=self.day(2026, 10, 5))[1:3], ("2026-10-05", "employer"))

    def test_a_weaker_source_never_replaces_the_employer_date_even_when_it_is_earlier(self):
        for weak in ("aggregator", "newsletter", "email"):
            with self.subTest(weak):
                status, posted, source, age = self.saved("2026-10-05", "employer", posted=self.day(2026, 9, 1), source=weak)
                self.assertEqual((posted, source), ("2026-10-05", "employer"))
                self.assertEqual(age, 6)

    def test_a_weaker_source_fills_a_missing_date_and_a_stronger_one_later_replaces_it(self):
        status, posted, source, age = self.saved(None, None, posted=self.day(2026, 10, 1), source="aggregator")
        self.assertEqual((posted, source, age), ("2026-10-01", "aggregator", 10))
        status, posted, source, age = self.saved("2026-10-01", "aggregator", posted=self.day(2026, 10, 8))      # the employer's own date, later than the aggregator's, is the stronger evidence
        self.assertEqual((posted, source), ("2026-10-08", "employer"))

    def test_a_date_stored_before_the_source_was_recorded_counts_as_the_employers(self):
        status, posted, source, age = self.saved("2026-09-01", None, posted=self.day(2026, 10, 9))
        self.assertEqual((posted, source), ("2026-09-01", "employer"))

    def test_the_rule_is_one_pure_function_with_the_d4_order(self):
        d = self.day
        self.assertEqual(enrich.accepted_date(d(2026, 9, 1), "employer", d(2026, 10, 9)), (d(2026, 9, 1), "employer"))
        self.assertEqual(enrich.accepted_date(None, None, None), (None, None))
        self.assertEqual(enrich.accepted_date(d(2026, 9, 1), "employer", None), (d(2026, 9, 1), "employer"))
        self.assertEqual(list(enrich.TRUST), ["employer", "aggregator", "newsletter", "email"])


if __name__ == "__main__":
    unittest.main()



class BoardApiTests(unittest.TestCase):
    """Oct 25 7:40 PM CT drain: 154 pages stayed blocked (jd_thin 80, empty 49); Lever and Greenhouse-listed jobs among them were read short."""

    def test_a_lever_job_is_read_whole_not_just_its_opening(self):
        data = {"text": "Senior Data Engineer", "createdAt": 1792454400000, "description": "<p>We build data tools.</p>",
                "descriptionPlain": "We build data tools.", "additional": "<p>Benefits: health cover.</p>",
                "lists": [{"text": "What you will do", "content": "<li>" + "Own the pipelines and the warehouse. " * 12 + "</li>"},
                          {"text": "Requirements", "content": "<li>Five years of experience with SQL and Python.</li>"}]}
        with mock.patch.object(enrich, "fetch", return_value=Page(200, json.dumps(data))):
            api = enrich._api_job("https://jobs.lever.co/acme/0f3a1b2c-1111-2222-3333-444455556666")
            got = enrich.read_page("https://jobs.lever.co/acme/0f3a1b2c-1111-2222-3333-444455556666", "Senior Data Engineer")
        self.assertNotIn("plain", api)
        for part in ("We build data tools", "What you will do", "Own the pipelines", "Requirements", "Five years", "Benefits"):
            self.assertIn(part, got["description"]["full_text"])
        self.assertEqual(got["outcome"], "ready")

    def test_greenhouse_content_is_unescaped_before_it_is_read(self):
        data = {"title": "Senior Data Engineer", "first_published": "2026-10-20", "content": "&lt;p&gt;About the role&lt;/p&gt;"}
        with mock.patch.object(enrich, "fetch", return_value=Page(200, json.dumps(data))):
            api = enrich._api_job("https://boards.greenhouse.io/acme/jobs/4567")
        self.assertEqual(api["html"], "<p>About the role</p>")

    def test_a_board_job_linked_to_the_employers_site_is_read_from_the_boards_api(self):
        api = {"title": "Senior Data Engineer", "html": LONG, "posted": "2026-10-20"}
        with mock.patch.object(enrich, "_api_job", side_effect=lambda u: api if "boards.greenhouse.io/acme/jobs/7654321" in u else None) as read, \
                mock.patch.object(enrich, "fetch") as page:
            got = enrich.read_page("https://acme.example/careers/?gh_jid=7654321", "Senior Data Engineer", first_party=True, board=("greenhouse", "acme"))
        page.assert_not_called()                                                     # no JavaScript shell read at all
        read.assert_called_with("https://boards.greenhouse.io/acme/jobs/7654321")
        self.assertEqual(got["outcome"], "ready")

    def test_a_board_api_that_does_not_know_the_id_falls_back_to_the_page(self):
        html = JOB % ("2026-10-20", LONG.replace('"', "'"))
        with mock.patch.object(enrich, "_api_job", side_effect=lambda u: {"closed": True} if "greenhouse" in u else None), \
                mock.patch.object(enrich, "fetch", return_value=Page(200, html)):
            got = enrich.read_page("https://acme.example/careers/position/7654321", "Senior Data Engineer", first_party=True, board=("greenhouse", "acme"))
        self.assertEqual(got["outcome"], "ready")                                   # never closed on the board's word about another address

    def test_board_urls_by_kind(self):
        self.assertEqual(enrich.board_job_url(("greenhouse", "acme"), "https://acme.example/jobs?gh_jid=4567123"), "https://boards.greenhouse.io/acme/jobs/4567123")
        self.assertEqual(enrich.board_job_url(("greenhouse", "acme"), "https://acme.example/careers/position/7654321/"), "https://boards.greenhouse.io/acme/jobs/7654321")
        self.assertIsNone(enrich.board_job_url(("greenhouse", "acme"), "https://acme.example/careers/data-engineer"))
        uid = "0f3a1b2c-1111-2222-3333-444455556666"
        self.assertEqual(enrich.board_job_url(("lever", "acme"), f"https://acme.example/jobs/{uid}/apply"), f"https://jobs.lever.co/acme/{uid}")
        self.assertEqual(enrich.board_job_url(("ashby", "acme"), f"https://acme.example/careers?ashby_jid={uid}"), f"https://jobs.ashbyhq.com/acme/{uid}")

    def test_the_source_names_its_board(self):
        self.assertEqual(enrich.board_of("web:gh-digitalocean98"), ("greenhouse", "digitalocean98"))
        self.assertIsNone(enrich.board_of("linkedin-search"))
        self.assertIsNone(enrich.board_of("web:no-such-board-here"))

    def test_the_run_passes_each_jobs_board(self):
        seen = []
        rows = [(1, "https://acme.example/careers/?gh_jid=7654321", "Senior Data Engineer", "ats", None, "US Remote", "web:gh-digitalocean98", None)]
        conn = mock.MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value.fetchall.return_value = rows
        with mock.patch.object(enrich.store, "connect", return_value=conn), mock.patch.object(enrich.store, "ensure_schema"), \
                mock.patch.object(enrich, "read_page", side_effect=lambda *a: seen.append(a[-1]) or {"outcome": "closed"}):
            enrich.run(10, live=False, drain=True)
        self.assertEqual(seen, [("greenhouse", "digitalocean98")])
