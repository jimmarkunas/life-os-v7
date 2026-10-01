import unittest
from datetime import date

from pipeline import classify, jsonld

PAGE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"WebSite"},{"@type":"JobPosting","title":"PM",
"datePosted":"2026-09-20T08:00:00Z","description":"<p>Do things</p>"}]}</script></head></html>"""


class ClassifyTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(classify.apply_kind("https://boards.greenhouse.io/acme/jobs/1"), "ats")
        self.assertEqual(classify.apply_kind("https://acme.wd5.myworkdayjobs.com/en-US/x"), "ats")
        self.assertEqual(classify.apply_kind("https://www.linkedin.com/jobs/view/1"), "aggregator")
        self.assertEqual(classify.apply_kind("https://careers.acme.example/job/1"), "employer")
        self.assertEqual(classify.apply_kind("https://notlinkedin.com/x"), "employer")   # suffix match needs a dot boundary


class JsonLdTests(unittest.TestCase):
    def test_graph_jobposting_and_date(self):
        posting = jsonld.job_posting(PAGE)
        self.assertEqual(posting["title"], "PM")
        self.assertEqual(jsonld.posted_date(posting), date(2026, 9, 20))

    def test_missing_or_bad_data(self):
        self.assertIsNone(jsonld.job_posting("<html></html>"))
        self.assertIsNone(jsonld.job_posting('<script type="application/ld+json">{bad json</script>'))
        self.assertIsNone(jsonld.posted_date({"datePosted": "soon"}))
        self.assertIsNone(jsonld.posted_date(None))


if __name__ == "__main__":
    unittest.main()


class TinyFishSummaryTests(unittest.TestCase):
    def test_missing_key_is_a_fixed_code(self):
        import os
        from pipeline import tinyfish
        os.environ.pop("TINYFISH_API_KEY", None)
        with self.assertRaises(tinyfish.TinyFishError) as ctx:
            tinyfish.fetch_many(["https://example.com"])
        self.assertEqual(str(ctx.exception), "TINYFISH_KEY_MISSING")
