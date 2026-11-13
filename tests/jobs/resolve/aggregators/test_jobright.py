import unittest

from lifeos.jobs.resolve.aggregators import jobright as jb


class OutcomeTests(unittest.TestCase):
    def test_landing_kinds(self):
        self.assertEqual(jb.outcome_for("https://boards.greenhouse.io/acme/jobs/9", True)[0], "ats")
        self.assertEqual(jb.outcome_for("https://careers.acme.example/j/9", True)[0], "employer")
        self.assertEqual(jb.outcome_for("https://jobright.ai/jobs/info/1", False), ("internal", None))
        self.assertEqual(jb.outcome_for("", True), ("internal", None))

    def test_tracking_param_is_stripped(self):
        self.assertEqual(jb.strip_tracking("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1&jr_id=abc"),
                         "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1")
        self.assertEqual(jb.strip_tracking("https://careers.acme.example/j/9"), "https://careers.acme.example/j/9")

    def test_missing_credentials_never_launch_a_browser(self):
        import asyncio
        self.assertEqual(asyncio.run(jb.resolve_many(["https://jobright.ai/jobs/info/1"], {})),
                         [{"outcome": "auth_unavailable"}])


class JobResultLinkTests(unittest.TestCase):
    """The employer link comes from THIS job's jobResult only; a link of another job in the page data never does (link_mismatch HOLDs)."""
    def data(self, job, **extra):
        import json
        return json.dumps({"props": {"pageProps": {"dataSource": {"jobResult": job, **extra}}}})

    def test_this_jobs_original_url_is_taken_and_a_similar_jobs_link_is_ignored(self):
        text = self.data({"originalUrl": "https://boards.greenhouse.io/acme/jobs/9?jr_id=x"},
                         similarJobs=[{"applyLink": "https://boards.greenhouse.io/other/jobs/1"}])
        self.assertEqual(jb.job_result_link(text), "https://boards.greenhouse.io/acme/jobs/9")

    def test_a_link_only_in_another_jobs_data_gives_none(self):
        self.assertIsNone(jb.job_result_link(self.data({"title": "PM"}, similarJobs=[{"applyLink": "https://boards.greenhouse.io/other/jobs/1"}])))

    def test_agreeing_links_count_once_and_conflicting_links_give_none(self):
        same = {"originalUrl": "https://acme.example/j/9", "applyLink": "https://acme.example/j/9?jr_id=1"}
        self.assertEqual(jb.job_result_link(self.data(same)), "https://acme.example/j/9")
        self.assertIsNone(jb.job_result_link(self.data({"originalUrl": "https://acme.example/j/9", "applyLink": "https://acme.example/j/10"})))

    def test_internal_missing_or_malformed_data_gives_none(self):
        self.assertIsNone(jb.job_result_link(self.data({"originalUrl": "https://jobright.ai/jobs/info/1"})))
        for text in (None, "", "not json", "{}", '{"props": []}'):
            self.assertIsNone(jb.job_result_link(text))

