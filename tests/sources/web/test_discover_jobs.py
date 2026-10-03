import unittest
from unittest import mock

from lifeos.sources.web import discover_jobs as dj
from tests.kit.db import FakeConn

SPONSOR = {"id": "su-x", "company": "Sixfold Bioscience Limited", "enabled": True, "status": "fallback"}


class Roles(unittest.TestCase):
    def test_role_is_read_out_of_the_page_title(self):
        self.assertEqual(dj.role_from_title("Product Manager at Sixfold Bioscience | startup.jobs", "Sixfold Bioscience Limited"), "Product Manager")
        self.assertEqual(dj.role_from_title("Senior Program Manager - Sixfold Bioscience - Indeed", "Sixfold Bioscience Limited"), "Senior Program Manager")

    def test_company_names_site_names_and_generic_pages_are_not_roles(self):
        self.assertEqual(dj.role_from_title("Sixfold Bioscience | Jobs", "Sixfold Bioscience Limited"), "")
        self.assertEqual(dj.role_from_title("Careers at Sixfold Bioscience", "Sixfold Bioscience Limited"), "")
        self.assertEqual(dj.role_from_title("Jobs in London - Reed.co.uk", "Sixfold Bioscience Limited"), "")


class Candidates(unittest.TestCase):
    def test_only_single_job_pages_that_name_the_sponsor_are_candidates(self):
        results = [
            {"url": "https://startup.jobs/product-manager-sixfold-bioscience-123", "title": "Product Manager at Sixfold Bioscience | startup.jobs", "snippet": "London"},
            {"url": "https://startup.jobs/companies/sixfold-bioscience", "title": "Sixfold Bioscience jobs", "snippet": ""},
            {"url": "https://www.reed.co.uk/jobs/data-analyst/555", "title": "Data Analyst - Other Co - Reed", "snippet": "Other Co"},
            {"url": "https://www.reed.co.uk/jobs", "title": "Sixfold Bioscience jobs", "snippet": ""},
        ]
        got = dj.candidates(SPONSOR["company"], results)
        self.assertEqual([(c["title"], c["location"], c["host"]) for c in got], [("Product Manager", "London, UK", "startup.jobs")])


class Run(unittest.TestCase):
    RESULTS = [{"url": "https://startup.jobs/product-manager-sixfold-bioscience-123", "title": "Product Manager at Sixfold Bioscience | startup.jobs", "snippet": ""}]

    def test_dry_run_counts_and_writes_nothing(self):
        with mock.patch.object(dj.store, "connect", side_effect=AssertionError("dry run must not connect")):
            counts = dj.run(12, False, search=lambda q: self.RESULTS, sources=[SPONSOR])
        self.assertEqual((counts["searched"], counts["candidates"], counts["added"], counts["hosts"]), (1, 1, 0, {"startup.jobs": 1}))

    def test_live_adds_new_candidates_as_scale_up_leads_then_resolves(self):
        seen = []
        conn = FakeConn()
        with mock.patch.object(dj.store, "connect", side_effect=lambda: conn), \
                mock.patch.object(dj.store, "ensure_schema"), \
                mock.patch.object(dj.intake, "add_job", side_effect=lambda cursor, job, now: seen.append(job) or ("k", True)), \
                mock.patch.object(dj.stage, "run_rows", return_value={"picked": 1}) as resolve:
            counts = dj.run(12, True, search=lambda q: self.RESULTS, sources=[SPONSOR])
        self.assertEqual((counts["added"], counts["resolve"]), (1, {"picked": 1}))
        self.assertEqual((seen[0]["status"], seen[0]["lane"], seen[0]["source"], seen[0]["company"]), ("NEW", "Scale-Up", "discover", "Sixfold Bioscience Limited"))
        self.assertEqual(resolve.call_args.args[:3], ("discover", 60, True))

    def test_a_search_error_is_counted_not_fatal(self):
        def boom(query):
            raise dj.TinyFishError("TINYFISH_SEARCH_HTTP_429")
        counts = dj.run(12, False, search=boom, sources=[SPONSOR])
        self.assertEqual((counts["search_errors"], counts["searched"]), (1, 0))

    def test_a_result_on_the_sponsors_own_domain_or_an_ats_is_the_final_link(self):
        got = dj.direct_link("https://careers.sixfold.example/jobs/product-manager-4412", "Sixfold Bioscience Limited")
        self.assertEqual((got["outcome"], got["via"], got["kind"]), ("landed", "discover_direct", "employer"))
        got = dj.direct_link("https://job-boards.greenhouse.io/sixfold/jobs/4412", "Sixfold Bioscience Limited")
        self.assertEqual((got["kind"], got["via"]), ("ats", "discover_direct"))

    def test_aggregators_recruiters_and_unsound_links_need_the_second_search(self):
        for url in ("https://uk.indeed.com/viewjob?jk=abc123def456", "https://www.glassdoor.co.uk/job-listing/product-manager-sixfold-JV_1234.htm",
                    "https://recruiter.example/jobs/product-manager-4412", "https://careers.sixfold.example/", "https://sixfold.example/careers/search"):
            self.assertIsNone(dj.direct_link(url, "Sixfold Bioscience Limited"), url)

    def test_a_short_or_generic_first_word_is_not_trusted_in_a_host(self):
        self.assertIsNone(dj.direct_link("https://ta-recruitment.example/jobs/product-manager-4412", "TA Digital Limited"))

    def test_only_unresolved_rows_are_searched(self):
        rows = [(1, "https://careers.sixfold.example/jobs/product-manager-4412", "Sixfold Bioscience Limited", "Product Manager", ""),
                (2, "https://uk.indeed.com/viewjob?jk=abc123def456", "Sixfold Bioscience Limited", "Product Manager", "")]
        seen = []
        with mock.patch.object(dj.linkedin, "match_rows", side_effect=lambda r, results, budget=None: seen.extend(x["outcome"] for x in results) or results):
            out = dj._resolve(rows, {"left": 5})
        self.assertEqual((out[0]["outcome"], seen), ("landed", ["landed", "external_hidden"]))


if __name__ == "__main__":
    unittest.main()
