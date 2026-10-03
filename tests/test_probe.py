import unittest

from lifeos import probe
from lifeos.platform.http import Fetched


class ProbeTests(unittest.TestCase):
    def test_open_jobs_reports_key_names_only(self):
        def fetcher(url, **kw):
            return Fetched(url, 200, '{"generation": "g1", "days": [{"date": "2026-10-01", "n": 3}]}')
        out = probe.open_jobs(fetcher)
        self.assertEqual(out["manifest.json"]["shape"]["generation"], "str")
        self.assertNotIn("g1", str(out))

    def test_teamtailor_counts(self):
        html = '<a href="/jobs/123-pm">x</a><a href="/jobs/123-pm">x</a><a href="/jobs/456-eng">y</a>'
        out = probe.teamtailor(lambda url, **kw: Fetched(url, 200, html))
        self.assertEqual(len(out), 6)
        self.assertEqual(next(iter(out.values()))["job_links"], 2)

    def test_dice_without_links_is_a_clean_count(self):
        out = probe.dice(lambda url, **kw: Fetched(url, 403, ""))
        self.assertEqual((out["search_status"], out["detail_links"], [p["status"] for p in out["pages"]]), (403, 0, [403]))


class Discover(unittest.TestCase):
    def test_finds_a_listable_board_or_an_own_site_and_never_prints_urls(self):
        sources = [{"id": "su-a", "company": "Acme Robotics Ltd"}, {"id": "su-b", "company": "Beta Foods Limited"}, {"id": "su-c", "company": "Gamma Labs Ltd"}]
        results = {
            "Acme Robotics Ltd careers jobs UK": [{"url": "https://uk.linkedin.com/company/acme", "title": "Acme Robotics", "snippet": ""},
                                                   {"url": "https://boards.greenhouse.io/acmerobotics", "title": "Acme Robotics jobs", "snippet": ""}],
            "Beta Foods Limited careers jobs UK": [{"url": "https://www.betafoods.co.uk/careers", "title": "Careers at Beta Foods", "snippet": ""}],
            "Gamma Labs Ltd careers jobs UK": [{"url": "https://www.indeed.com/cmp/gamma", "title": "Gamma Labs", "snippet": ""}],
        }
        out = probe.discover(lambda query: results[query], sources)
        self.assertEqual(out, {"su-a": "greenhouse:acmerobotics", "su-b": "own:betafoods.co.uk", "su-c": None})

    def test_an_unrelated_result_is_not_a_match(self):
        out = probe.discover(lambda q: [{"url": "https://boards.greenhouse.io/other", "title": "Other Co", "snippet": ""}], [{"id": "su-a", "company": "Acme Robotics Ltd"}])
        self.assertEqual(out, {"su-a": None})


class InterviewAccess(unittest.TestCase):
    def test_only_the_interview_token_is_ever_used_never_the_jobs_token(self):
        self.assertIsNone(probe.interview_environ({"NOTION_API_TOKEN": "jobs-token"}))
        mapped = probe.interview_environ({"NOTION_API_TOKEN": "jobs-token", "NOTION_INTERVIEW_TOKEN": "interview-token"})
        self.assertEqual(mapped["NOTION_API_TOKEN"], "interview-token")

    def test_missing_config_is_a_fixed_code_not_an_empty_success(self):
        self.assertEqual(probe.interview_access({"NOTION_API_TOKEN": "jobs-token", "HIRING_PIPELINE_PAGE_ID": "p"}),
                         {"status": "config_missing", "token": False, "page": True})
        self.assertEqual(probe.interview_access({"NOTION_INTERVIEW_TOKEN": "t"})["status"], "config_missing")

    def test_reports_counts_only_and_builds_the_client_from_the_interview_token(self):
        from unittest import mock
        from lifeos.jobs import hiring_pipeline as hp
        seen = []
        opps = [hp.Opportunity("p1", "Acme — PM", hp.ACTIVE, 2, "Acme", "PM"), hp.Opportunity("p2", "weird title", hp.ACTIVE, 0, "weird title", "")]

        def factory(env):
            seen.append(env)
            return object()
        with mock.patch.object(hp, "snapshot", return_value=(opps, "ok")):
            out = probe.interview_access({"NOTION_INTERVIEW_TOKEN": "SECRETVALUE123", "NOTION_API_TOKEN": "jobs", "HIRING_PIPELINE_PAGE_ID": "page"}, factory)
        self.assertEqual(out, {"status": "ok", "token": True, "page": True, "active": 2, "retired": 0, "with_rounds": 1, "unparsed_titles": 1})
        self.assertEqual(seen[0]["NOTION_API_TOKEN"], "SECRETVALUE123")
        self.assertNotIn("jobs", seen[0].values())
        self.assertNotIn("SECRETVALUE123", str(out))


class GlassdoorProbeTests(unittest.TestCase):
    def test_reports_per_route_facts_only_and_never_stops_on_a_failing_route(self):
        from lifeos.platform.http import Fetched

        blocked = Fetched("u", 403, "<html>Just a moment... Verify you are human</html>")
        page = Fetched("u", 200, '<a href="/job-listing/x-JV_1.htm">x</a><script>{"@type":"JobPosting"}</script>')

        def boom(url):
            raise OSError("down")

        def tinyfish(urls, fmt="html", links=True):
            return {urls[0]: {"html": page.html, "links": ["a", "b"]}}, []

        out = probe.glassdoor(plain=lambda url, **kw: blocked, reader=boom, tinyfish_fetch=tinyfish)
        self.assertEqual(sorted(out), ["page1", "page2", "page3"])                          # employer London page, UK search, US remote search
        first = out["page1"]
        self.assertEqual(first["plain"]["status"], 403)
        self.assertEqual(first["plain"]["blocked"], ["just a moment", "verify you are human"])
        self.assertEqual(first["reader_proxy"], {"error": "OSError"})
        self.assertEqual((first["tinyfish_fetch"]["job_links"], first["tinyfish_fetch"]["links"], first["tinyfish_fetch"]["blocked"]), (1, 2, []))
        self.assertNotIn("glassdoor.co.uk", repr(out))                                    # no url, title or company in the output
