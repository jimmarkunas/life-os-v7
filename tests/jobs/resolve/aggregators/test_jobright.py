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


if __name__ == "__main__":
    unittest.main()


class WorkModelProbeTests(unittest.TestCase):
    def test_the_page_state_probe_reports_work_model_fields_with_short_words_only(self):
        from lifeos.jobs.resolve.aggregators import jobright
        self.assertIn("work.?(model|type|place|mode|arrangement)|remote|on.?site|hybrid|workplace", jobright.STATE_JS)
        self.assertIn("/^[A-Za-z _-]{2,20}$/.test(v)", jobright.STATE_JS)           # a long string (a title, a location, a description) is never shown, only its type
