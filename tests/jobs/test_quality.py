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
