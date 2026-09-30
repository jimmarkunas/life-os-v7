import unittest

from pipeline import li_apply

WRAPPED = '<a class="apply" href="https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F42&amp;urlHash=x">Apply</a>'
JSONISH = '{"companyApplyUrl":"https:\\/\\/careers.acme.example\\/apply\\/9","other":1}'
ONSITE = '<a data-tracking-control-name="public_jobs_apply-link-onsite">Easy Apply</a>'
OFFSITE_NO_URL = '<a data-tracking-control-name="public_jobs_apply-link-offsite">Apply</a>'


class LinkedInApplyTests(unittest.TestCase):
    def test_unwraps_redirect_wrapper(self):
        self.assertEqual(li_apply.read(WRAPPED), ("external", "https://boards.greenhouse.io/acme/jobs/42"))

    def test_json_style_key(self):
        self.assertEqual(li_apply.read(JSONISH), ("external", "https://careers.acme.example/apply/9"))

    def test_native_easy_apply_and_unlinked_external(self):
        self.assertEqual(li_apply.read(ONSITE), ("easy_apply", None))
        self.assertEqual(li_apply.read(OFFSITE_NO_URL), ("external_unlinked", None))

    def test_absence_is_unknown_never_inferred(self):
        self.assertEqual(li_apply.read("<html>nothing here</html>"), ("unknown", None))
        self.assertEqual(li_apply.read(""), ("unknown", None))

    def test_linkedin_internal_links_are_not_external(self):
        self.assertIsNone(li_apply.unwrap("https://www.linkedin.com/jobs/view/123456789"))
        self.assertEqual(li_apply.external_urls('<a href="https://www.linkedin.com/help/x">h</a>'), [])

    def test_job_id(self):
        self.assertEqual(li_apply.job_id("https://www.linkedin.com/jobs/view/1234567890"), "1234567890")
        self.assertEqual(li_apply.job_id("https://www.linkedin.com/jobs/view/project-manager-at-acme-1234567890"), "1234567890")


if __name__ == "__main__":
    unittest.main()


class ClosedAndShapeTests(unittest.TestCase):
    def test_closed_posting_is_detected(self):
        self.assertEqual(li_apply.read("<div>No longer accepting applications</div>"), ("closed", None))

    def test_offsite_anchor_shape_is_generic(self):
        page = '<a data-tracking-control-name="public_jobs_apply-link-offsite" href="https://www.linkedin.com/jobs/view/externalApply/123?x=1">Apply</a>'
        self.assertEqual(li_apply.apply_href_shape(page), "externalApply")
        self.assertEqual(li_apply.apply_href_shape("<p>none</p>"), "no_offsite_anchor")
