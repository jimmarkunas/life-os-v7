import unittest

from lifeos.jobs import quality


class UrlTests(unittest.TestCase):
    def test_listing_and_form_urls(self):
        for url in ("https://jobs.jobvite.com/ninjaone/search/?p=1", "https://careers.acme.com/jobs",
                    "https://acme.com/careers/", "https://boards.greenhouse.io/acme"):
            self.assertIsNotNone(quality.url_problem(url), url)

    def test_single_job_urls(self):
        for url in ("https://boards.greenhouse.io/acme/jobs/4250537009", "https://careers.homedepot.com/job/23915306/it-pm",
                    "https://jobs.lever.co/acme/2f1c9d3e-1111-4a2b-9c3d-123456789abc",
                    "https://careers.acme.com/jobs/senior-data-engineer-remote"):
            self.assertIsNone(quality.url_problem(url), url)

    def test_apply_form_suffix_is_stripped(self):
        self.assertEqual(quality.canonical_job_url("https://x.breezy.hr/p/abc123-adobe-project-manager/apply"),
                         "https://x.breezy.hr/p/abc123-adobe-project-manager")
        self.assertEqual(quality.canonical_job_url("https://jobs.lever.co/acme/2f1c9d3e-1111/apply"),
                         "https://jobs.lever.co/acme/2f1c9d3e-1111")
        self.assertEqual(quality.canonical_job_url("https://careers.acme.com/job/1"), "https://careers.acme.com/job/1")


class JdTests(unittest.TestCase):
    def test_template_listing_thin_and_real(self):
        template = "%HEADER_X_Y% " * 4 + "word " * 100
        self.assertEqual(quality.jd_problem(template), "template")
        listing = "Senior Engineer\nRemote,\n19 Locations\n" * 6 + "word " * 100
        self.assertEqual(quality.jd_problem(listing), "listing")
        self.assertEqual(quality.jd_problem("short text"), "thin")
        real = "About the role. " + "You will build data pipelines and own quality. Experience with SQL required. " * 8
        self.assertIsNone(quality.jd_problem(real))


if __name__ == "__main__":
    unittest.main()


class LinkProblemTests(unittest.TestCase):
    def test_link_problem_matches_what_enrich_rejects_first(self):
        self.assertEqual(quality.link_problem("https://apply.workable.com/j/ABCDEF"), "workable_no_account")
        self.assertEqual(quality.link_problem("https://careers.acme.example/"), "root")
        self.assertIsNone(quality.link_problem("https://job-boards.greenhouse.io/x/jobs/12345"))


class QueryIdTests(unittest.TestCase):
    """A vacancy whose id lives in the query is one vacancy; a listing that merely has a parameter is not."""

    def test_an_id_parameter_makes_a_generic_or_root_page_a_vacancy(self):
        for url in ("https://acme.example/careers?gh_jid=4567890", "https://acme.example/?gh_jid=4567890",
                    "https://acme.example/jobs/openings?jid=99812&utm_source=x", "https://acme.example/careers/apply?job_id=R-1234"):
            self.assertIsNone(quality.url_problem(url), url)

    def test_a_parameter_without_an_id_or_with_a_search_still_refuses(self):
        for url in ("https://acme.example/careers?gh_jid=", "https://acme.example/careers?gh_jid=abc", "https://acme.example/careers?utm=123456",
                    "https://acme.example/careers?q=engineer&gh_jid=123456", "https://acme.example/careers?id=1", "https://acme.example/careers?page=2"):
            self.assertIsNotNone(quality.url_problem(url), url)

    def test_link_problem_follows(self):
        self.assertIsNone(quality.link_problem("https://acme.example/careers?gh_jid=4567890"))

    def test_host_specific_id_parameters(self):
        for url in ("https://boards.greenhouse.io/embed/job_app?for=acme&token=4567890", "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=4567890",
                    "https://acme.eightfold.ai/careers?pid=123456789&domain=acme.example"):
            self.assertIsNone(quality.url_problem(url), url)
        for url in ("https://acme.example/careers?token=4567890", "https://acme.example/careers?pid=123456789",       # the same names elsewhere prove nothing
                    "https://boards.greenhouse.io/embed/job_app?for=acme", "https://boards.greenhouse.io/embed/job_board?for=acme"):
            self.assertIsNotNone(quality.url_problem(url), url)
