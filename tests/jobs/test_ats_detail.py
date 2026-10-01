import json
import unittest
from unittest import mock

from lifeos.jobs import ats_detail
from lifeos.platform.http import Fetched


def serve(routes):
    """fetch() double: the first route whose key is contained in the URL answers; anything else is a 404."""
    def fake(url, **kwargs):
        for key, (status, body) in routes.items():
            if key in url:
                return Fetched(url, status, body if isinstance(body, str) else json.dumps(body))
        return Fetched(url, 404, "")
    return fake


def read(url, routes):
    with mock.patch.object(ats_detail, "fetch", serve(routes)):
        return ats_detail.read(url)


class ReaderTests(unittest.TestCase):
    def test_workday_skips_the_locale_and_reads_the_cxs_json(self):
        info = {"jobPostingInfo": {"title": "PM", "jobDescription": "<p>desc</p>", "startDate": "2026-09-24"}}
        job = read("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Remote/PM_R1",
                   {"/wday/cxs/acme/Careers/job/Remote/PM_R1": (200, info)})
        self.assertEqual((job["title"], job["html"], job["posted"]), ("PM", "<p>desc</p>", "2026-09-24"))
        self.assertEqual(read("https://acme.wd5.myworkdayjobs.com/Careers/job/x/PM_R2", {}), {"closed": True})

    def test_workable_finds_the_shortcode_in_the_account_widget(self):
        widget = {"jobs": [{"shortcode": "ABC123", "title": "PM", "description": "<p>d</p>", "published_on": "2026-09-20"}]}
        routes = {"widget/accounts/acme": (200, widget)}
        self.assertEqual(read("https://apply.workable.com/acme/j/abc123/", routes)["posted"], "2026-09-20")
        self.assertEqual(read("https://apply.workable.com/acme/j/ZZZ999/", routes), {"closed": True})

    def test_bamboohr_and_oracle_read_their_detail_json(self):
        bamboo = {"result": {"jobOpening": {"jobOpeningName": "PM", "description": "<p>d</p>", "datePosted": "2026-09-21"}}}
        self.assertEqual(read("https://acme.bamboohr.com/careers/55", {"/careers/55/detail": (200, bamboo)})["title"], "PM")
        oracle = {"items": [{"Title": "PM", "ExternalDescriptionStr": "<p>a</p>", "ExternalResponsibilitiesStr": "<p>b</p>"}]}
        job = read("https://x.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/9001",
                   {"recruitingCEJobRequisitionDetails": (200, oracle)})
        self.assertEqual(job["html"], "<p>a</p>\n\n<p>b</p>")
        empty = read("https://x.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/9002",
                     {"recruitingCEJobRequisitionDetails": (200, {"items": []})})
        self.assertEqual(empty, {"closed": True})

    def test_paylocity_decodes_the_entity_encoded_json_ld(self):
        page = '<script type="application/ld+json">{"title":"PM","description":"&lt;p&gt;Real text&lt;/p&gt;"}</script>'
        self.assertEqual(read("https://recruiting.paylocity.com/Recruiting/Jobs/Details/7", {"/Details/7": (200, page)})["html"],
                         "<p>Real text</p>")

    def test_ukg_reads_the_embedded_description_and_unknown_hosts_are_left_alone(self):
        page = 'x "Description":"<p>Line one\\u003c/p\\u003e" y'
        self.assertEqual(read("https://recruiting.ultipro.com/ACM/JobBoard/g/OpportunityDetail?opportunityId=1",
                              {"OpportunityDetail": (200, page)})["html"], "<p>Line one</p>")
        self.assertIsNone(read("https://careers.example.com/job/1", {}))

    def test_a_broken_reader_returns_none_instead_of_raising(self):
        with mock.patch.object(ats_detail, "fetch", side_effect=RuntimeError("boom")):
            self.assertIsNone(ats_detail.read("https://acme.bamboohr.com/careers/5"))


if __name__ == "__main__":
    unittest.main()
