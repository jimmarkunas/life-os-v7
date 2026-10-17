import unittest

from lifeos.jobs.resolve import search_match


class SearchMatchTests(unittest.TestCase):
    ok = {"url": "https://job-boards.greenhouse.io/acmedata/jobs/42", "title": "Senior Data Engineer - Acme Data",
          "snippet": "Acme Data is hiring"}

    def test_accepts_matching_title_and_company(self):
        self.assertTrue(search_match._good(self.ok, "Acme Data, Inc.", "Senior Data Engineer", False))

    def test_rejects_other_title_other_company_and_aggregators(self):
        self.assertFalse(search_match._good(self.ok, "Acme Data", "Staff Data Engineer", False))
        self.assertFalse(search_match._good(self.ok, "Globex", "Senior Data Engineer", False))
        agg = {"url": "https://www.indeed.com/viewjob?jk=1", "title": "Senior Data Engineer - Acme Data", "snippet": ""}
        self.assertFalse(search_match._good(agg, "Acme Data", "Senior Data Engineer", False))

    def test_listing_and_snippet_only_matches_are_rejected(self):
        listing = {"url": "https://jobs.jobvite.com/ninjaone/search/?p=1", "title": "NinjaOne Careers",
                   "snippet": "Senior Data Engineer Remote"}
        self.assertFalse(search_match._good(listing, "NinjaOne", "Senior Data Engineer", False))
        snippet_only = dict(self.ok, title="Acme Data Careers", snippet="Senior Data Engineer")
        self.assertFalse(search_match._good(snippet_only, "Acme Data", "Senior Data Engineer", False))

    def test_employer_site_needs_company_in_host(self):
        other = {"url": "https://careers.example.org/j/1", "title": "Senior Data Engineer Acme Data", "snippet": ""}
        self.assertFalse(search_match._good(other, "Acme Data", "Senior Data Engineer", True))
        mine = {"url": "https://www.acmedata.com/careers/1", "title": "Senior Data Engineer | Acme Data", "snippet": ""}
        self.assertTrue(search_match._good(mine, "Acme Data", "Senior Data Engineer", True))


class OneSearchPerJob(unittest.TestCase):
    def test_one_call_and_both_kinds_of_hit_are_accepted(self):
        from unittest import mock
        calls = []
        ats = {"url": "https://boards.greenhouse.io/acmedata/jobs/1", "title": "Senior Data Engineer - Acme Data", "snippet": "Acme Data"}
        own = {"url": "https://www.acmedata.com/careers/senior-data-engineer", "title": "Senior Data Engineer | Acme Data", "snippet": ""}
        for result, kind in ((ats, "ats"), (own, "employer")):
            def fake(query, domains=None, _r=result):
                calls.append((query, domains))
                return [_r]
            with mock.patch.object(search_match.tinyfish_search, "search", fake):
                verdict = search_match.find("Acme Data", "Senior Data Engineer")
            self.assertEqual((verdict[0], verdict[2]), ("hit", result["url"]))
        self.assertEqual(len(calls), 2)                                   # one search per job, not two
        self.assertTrue(all(domains is None for _, domains in calls))

    def test_a_miss_costs_one_search(self):
        from unittest import mock
        calls = []
        with mock.patch.object(search_match.tinyfish_search, "search", lambda q, d=None: calls.append(q) or []):
            self.assertEqual(search_match.find("Acme Data", "Senior Data Engineer"), ("miss", "no_result"))
        self.assertEqual(len(calls), 1)

    def test_a_decorated_aggregator_title_still_finds_the_employers_page(self):
        page = {"url": "https://job-boards.greenhouse.io/acmedata/jobs/42", "title": "Senior Data Engineer - Acme Data", "snippet": ""}
        self.assertTrue(search_match._good(page, "Acme Data", "Senior Data Engineer (Fully Remote)", False))
        self.assertFalse(search_match._good(page, "Acme Data", "Staff Data Engineer (Fully Remote)", False))

    def test_the_search_query_uses_the_title_without_remote_noise(self):
        from unittest import mock
        with mock.patch.object(search_match.tinyfish_search, "search", return_value=[]) as search:
            search_match.find("Acme Data", "Senior Data Engineer (Fully Remote)")
        self.assertEqual(search.call_args.args[0], "Acme Data senior data engineer careers apply")


class DegradedSearchIsNeverAMiss(unittest.TestCase):
    """A search that failed (429, outage, a 202 or changed answer) is DEGRADED, not "nothing found": the job is not charged an attempt."""
    def test_a_failed_search_is_degraded_and_pages_that_fail_the_checks_are_no_fit(self):
        from unittest import mock
        from lifeos.platform.tinyfish import TinyFishError
        for code in ("TINYFISH_SEARCH_HTTP_429", "TINYFISH_SEARCH_UNREADABLE", "TINYFISH_SEARCH_NETWORK"):
            with mock.patch.object(search_match.tinyfish_search, "search", side_effect=TinyFishError(code)):
                self.assertEqual(search_match.find("Acme Data", "Senior Data Engineer"), ("degraded", code.lower()))
        other = {"url": "https://www.facebook.com/acmedata", "title": "Acme Data", "snippet": ""}
        with mock.patch.object(search_match.tinyfish_search, "search", return_value=[other]):
            self.assertEqual(search_match.find("Acme Data", "Senior Data Engineer"), ("miss", "no_fit"))

    def test_an_answer_without_a_results_list_is_unreadable_not_empty(self):
        import io, json
        from unittest import mock
        from lifeos.platform import tinyfish_search
        from lifeos.platform.tinyfish import TinyFishError

        class Answer(io.BytesIO):
            def __init__(self, body, status=200):
                super().__init__(json.dumps(body).encode()); self.status = status
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch.dict("os.environ", {"TINYFISH_API_KEY": "sk-tinyfish-abc"}), mock.patch.object(tinyfish_search, "MIN_GAP_SECONDS", 0), \
                mock.patch.object(tinyfish_search.limits, "TINYFISH_SEARCH_GAP_SECONDS", 0):
            for body, status, code in (({"queued": True}, 200, "TINYFISH_SEARCH_UNREADABLE"), ([], 200, "TINYFISH_SEARCH_UNREADABLE"),
                                       ({"results": []}, 202, "TINYFISH_SEARCH_HTTP_202")):
                with mock.patch.object(tinyfish_search.urllib.request, "urlopen", return_value=Answer(body, status)):
                    with self.assertRaises(TinyFishError) as caught:
                        tinyfish_search.search("acme")
                    self.assertEqual(str(caught.exception), code)
            with mock.patch.object(tinyfish_search.urllib.request, "urlopen", return_value=Answer({"results": []})):
                self.assertEqual(tinyfish_search.search("acme"), [])

    def test_a_degraded_search_is_not_an_attempt_and_stops_the_runs_searches(self):
        from unittest import mock
        from lifeos.jobs.resolve.aggregators import linkedin
        rows = [(1, "u1", "Acme", "PM", ""), (2, "u2", "Globex", "PM", "")]
        with mock.patch.object(linkedin.ats_match, "match_many", return_value=["no_board", "no_board"]), \
                mock.patch.object(linkedin.search_match, "find", return_value=("degraded", "tinyfish_search_http_429")) as find:
            out = linkedin.match_rows(rows, [{"outcome": "external_hidden"}, {"outcome": "external_hidden"}], budget={"left": 5})
        self.assertEqual([r["outcome"] for r in out], ["rate_limited", "deferred"])
        self.assertEqual(find.call_count, 1)


if __name__ == "__main__":
    unittest.main()
