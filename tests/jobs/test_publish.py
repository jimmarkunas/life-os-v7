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

    def test_lane_admission_work_mode_and_pay_are_written(self):
        row = {"title": "T", "company": "C", "url": "https://x.example/j/1", "source": "lensa", "provider": "Lensa",
               "lane": "Newsletter", "key": "k", "first_seen": datetime.date(2026, 9, 30), "posted": None, "fit": 80,
               "fit_line": "[80%] Go", "admission": "REVIEW", "admission_reason": "work mode unresolved",
               "work_mode": "unknown", "salary": "$100K/yr"}
        props = publish.properties(row)
        self.assertEqual(props["Visible Lane"]["select"]["name"], "US Remote")        # a newsletter job is a US Remote job
        self.assertEqual(props["Admission Status"]["select"]["name"], "Passed / Review")
        self.assertEqual(props["Review Reason"]["rich_text"][0]["text"]["content"], "work mode unresolved")
        self.assertEqual(props["Work Mode"]["select"]["name"], "Unknown")
        self.assertEqual(props["Fit Authority"]["select"]["name"], "Authoritative")
        admitted = publish.properties({**row, "admission": "ADMIT", "work_mode": "remote"})
        self.assertEqual(admitted["Admission Status"]["select"]["name"], "Admitted")
        self.assertNotIn("Review Reason", admitted)

    def test_easy_apply_is_flagged_in_source_types(self):
        row = {"title": "T", "company": "C", "url": "https://www.linkedin.com/jobs/view/1", "source": "linkedin", "provider": "LinkedIn",
               "lane": "Newsletter", "key": "k", "first_seen": datetime.date(2026, 9, 30), "posted": None, "apply_kind": "easy_apply"}
        names = [o["name"] for o in publish.properties(row)["Source Types"]["multi_select"]]
        self.assertEqual(names, ["LinkedIn", "Easy Apply"])
        plain = publish.properties({**row, "apply_kind": "ats"})["Source Types"]["multi_select"]
        self.assertEqual([o["name"] for o in plain], ["LinkedIn"])
        agg = publish.properties({**row, "apply_kind": "aggregator"})["Source Types"]["multi_select"]
        self.assertEqual([o["name"] for o in agg], ["LinkedIn", "Aggregator Link"])                # D3 rank 4: flagged, never passed off as the employer's link

    def test_url_key_ignores_query_case_and_trailing_slash(self):
        self.assertEqual(identity.url_key("https://Boards.Greenhouse.io/a/jobs/1/?gh_src=x"),
                         identity.url_key("https://boards.greenhouse.io/a/jobs/1"))


if __name__ == "__main__":
    unittest.main()


class VisibleLaneLabel(unittest.TestCase):
    def test_scale_up_uses_the_ledgers_existing_option_spelling(self):
        row = {"title": "T", "company": "C", "url": "https://x.example/j/1", "source": "web", "provider": "Ashby", "lane": "Scale-Up",
               "key": "k", "first_seen": datetime.date(2026, 10, 1), "posted": None}
        props = publish.properties(row)
        self.assertEqual(props["Visible Lane"]["select"]["name"], "Scale-up")          # not a new "Scale-Up" option
        self.assertEqual(props["Eligible Lanes"]["multi_select"], [{"name": "Scale-Up"}])  # Eligible Lanes' own option is "Scale-Up"
