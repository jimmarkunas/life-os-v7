import datetime
import unittest

from lifeos.jobs import identity, publish
from lifeos.platform import limits, notion_client


class BodyTests(unittest.TestCase):
    def test_contract_order_marker_and_headings(self):
        blocks = publish.body_blocks("k1", {"summary": "About the role", "responsibilities": "",
                                           "requirements": "\u2022 Python\n\u2022 SQL", "qualifications": "BS degree"})
        kinds = [b["type"] for b in blocks]
        self.assertEqual(kinds, ["paragraph", "heading_2", "paragraph", "heading_2", "bulleted_list_item",
                                 "bulleted_list_item", "heading_2", "paragraph"])
        self.assertEqual(blocks[0]["paragraph"]["rich_text"][0]["text"]["content"], "v7-jd:1 | key=k1")
        self.assertEqual([b["heading_2"]["rich_text"][0]["text"]["content"] for b in blocks if b["type"] == "heading_2"],
                         ["Summary", "Requirements", "Qualifications"])

    def test_long_text_is_split_under_the_rich_text_limit(self):
        parts = notion_client.rich_text("x" * 4500)
        self.assertEqual([len(p["text"]["content"]) for p in parts], [2000, 2000, 500])
        self.assertTrue(all(len(p["text"]["content"]) <= limits.NOTION_MAX_RICH_TEXT_CHARS for p in parts))


class PropertyTests(unittest.TestCase):
    def test_core_properties_and_no_fit_fields(self):
        row = {"title": "Data Engineer", "company": "Acme", "url": "https://x.example/j/1", "source": "lensa",
               "provider": "Lensa", "lane": "US Remote", "key": "abc", "first_seen": datetime.date(2026, 9, 30), "posted": datetime.date(2026, 9, 28)}
        props = publish.properties(row)
        self.assertEqual(props["Admission Status"]["select"]["name"], "Passed / Review")
        self.assertEqual(props["Source Types"]["multi_select"][0]["name"], "Lensa")
        self.assertEqual(props["Visible Lane"]["select"]["name"], "US Remote")      # the lane comes from the job row
        self.assertEqual(props["Posting Date"]["date"]["start"], "2026-09-28")
        for fit in ("LIFE OS Fit", "Saturn Decision", "Provider Score"):
            self.assertNotIn(fit, props)

    def test_fit_fields_appear_only_when_scored(self):
        row = {"title": "T", "company": "C", "url": "https://x.example/j/1", "source": "lensa", "provider": "Lensa",
               "lane": "Newsletter", "key": "k", "first_seen": datetime.date(2026, 9, 30), "posted": None}
        props = publish.properties({**row, "fit": 81, "fit_line": "[81%] Go | Strengths: a | Gaps: none"})
        self.assertEqual(props["LIFE OS Fit"]["number"], 81)
        self.assertTrue(props["Why It Fits"]["rich_text"][0]["text"]["content"].startswith("[81%] Go"))

    def test_url_key_ignores_query_case_and_trailing_slash(self):
        self.assertEqual(identity.url_key("https://Boards.Greenhouse.io/a/jobs/1/?gh_src=x"),
                         identity.url_key("https://boards.greenhouse.io/a/jobs/1"))


if __name__ == "__main__":
    unittest.main()
