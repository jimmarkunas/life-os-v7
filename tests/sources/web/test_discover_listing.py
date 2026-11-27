"""a no-board Scale-Up sponsor's job that passes the Scale-Up rules is posted from its listing page (Review, never Admit)"""

import unittest
from datetime import datetime

from lifeos.jobs import lanes
from lifeos.sources.web import discover_jobs as dj

NOW = datetime(2026, 11, 15, 8, 0)
URL = "https://talents.example.test/job/product-manager-sixfold-123"


class Cursor:
    """Returns `rows` for the one SELECT and records every other statement"""

    def __init__(self, rows, rowcount=1):
        self.rows, self.rowcount, self.sql = rows, rowcount, []

    def execute(self, sql, args=()):
        self.sql.append((sql, args))
        return self.rowcount

    def fetchall(self):
        return self.rows

    def kinds(self):
        return [s.split()[0] for s, _ in self.sql]


def job(job_id=1, url=URL, title="Product Manager", company="Sixfold Bioscience Limited", place="London, UK"):
    return (job_id, url, title, company, place)


class Promote(unittest.TestCase):
    def test_a_london_job_with_a_clean_title_is_posted_from_its_listing_with_the_snippet(self):
        cur = Cursor([job()])
        counts = dj.promote_leads(cur, {URL: "Lead the product team in London"}, NOW)
        self.assertEqual(
            counts, {"promoted": 1, "not_london": 0, "title_excluded": 0, "sponsor_directory": 0, "not_a_job": 0}
        )
        update = next(args for sql, args in cur.sql if sql.startswith("UPDATE"))
        self.assertEqual((update[0], update[1]), (URL, lanes.LISTING_PROOF))
        self.assertIn("apply_kind='aggregator'", next(sql for sql, _ in cur.sql if sql.startswith("UPDATE")))
        self.assertIn("status='RESOLVED'", next(sql for sql, _ in cur.sql if sql.startswith("UPDATE")))
        insert = next(args for sql, args in cur.sql if sql.startswith("INSERT"))
        self.assertEqual(insert[1], "listing")
        self.assertIn("Lead the product team in London", insert[2])

    def test_without_a_snippet_the_description_is_the_title_and_a_plain_note(self):
        cur = Cursor([job()])
        dj.promote_leads(cur, {}, NOW)
        text = next(args for sql, args in cur.sql if sql.startswith("INSERT"))[2]
        self.assertEqual(text, f"Product Manager\n{dj.LISTING_NOTE}")

    def test_no_stated_place_or_a_place_that_is_not_london_is_never_posted(self):
        cur = Cursor([job(1, place=""), job(2, place="Manchester, UK"), job(3, place="Remote - Ireland")])
        counts = dj.promote_leads(cur, {}, NOW)
        self.assertEqual((counts["promoted"], counts["not_london"]), (0, 3))
        self.assertEqual(cur.kinds(), ["SELECT"])  # no write at all

    def test_a_hard_title_exclusion_is_never_posted(self):
        cur = Cursor(
            [job(1, title="Enterprise Sales Executive"), job(2, title="Nursery Assistant")]
        )  # the default hard rules
        counts = dj.promote_leads(cur, {}, NOW)
        self.assertEqual((counts["promoted"], counts["title_excluded"]), (0, 2))
        self.assertEqual(cur.kinds(), ["SELECT"])

    def test_a_job_that_left_hold_meanwhile_is_not_touched_again(self):
        cur = Cursor([job()], rowcount=0)  # the guarded UPDATE changed nothing
        counts = dj.promote_leads(cur, {}, NOW)
        self.assertEqual(counts["promoted"], 0)
        self.assertEqual(cur.kinds(), ["SELECT", "UPDATE"])  # no description row for a job that did not move

    def test_only_discover_no_match_holds_without_a_page_are_considered(self):
        cur = Cursor([])
        dj.promote_leads(cur, {}, NOW)
        sql = cur.sql[0][0]
        self.assertIn("source=%s", sql)
        self.assertIn("status='HOLD'", sql)
        self.assertIn("unresolved_reason LIKE 'no_match%%'", sql)
        self.assertIn("notion_page_id IS NULL", sql)
        self.assertEqual(cur.sql[0][1], (dj.SOURCE,))


DIRECTORY_ROWS = [
    ("UK Sponsor Licence Status", "https://www.legitsponsors.co.uk/status/acme"),
    ("LegitSponsors", "https://legitsponsors.co.uk/"),
    ("Sama Talent Group", "https://www.sponsordirectory.co.uk/sama"),
    ("licensed sponsor in London", "https://hirenza.com/licensed-sponsor-london"),
    ("Quantum Group Jobs", "https://www.getborderless.co.uk/quantum"),
    ("MyVisaJobs", "https://www.myvisajobs.co.uk/"),
    ("Product Manager", "https://atlaspriorityservices.com/jobs/1"),
    ("Engineer", "https://sub.jobvisaai.com/x"),  # a subdomain of a directory host
]
NOT_JOB_TITLES = [
    "UK Sponsor Licence Status",
    "LegitSponsors",
    "Sama Talent Group",
    "licensed sponsor in London",
    "Quantum Group Jobs",
    "MyVisaJobs",
    "TikTok LIVE Presenter / Host",
    "Jobs",
    "Sixfold Bioscience Limited",
    "Sponsor Licence Directory Manager",
]
REAL_TITLES = [
    "Product Manager",
    "Senior Backend Engineer",
    "Head of Growth",
    "Data Analyst",
    "Operations Lead",
    "Founding Designer",
    "VP Engineering",
]


class NotPosted(unittest.TestCase):
    def test_a_known_sponsor_directory_host_is_never_posted_and_is_excluded_with_a_fixed_reason(self):
        for n, (title, url) in enumerate(DIRECTORY_ROWS, 1):
            cur = Cursor([job(n, url=url, title=title)])
            counts = dj.promote_leads(cur, {}, NOW)
            self.assertEqual((counts["promoted"], counts["sponsor_directory"]), (0, 1), (title, url))
            self.assertEqual(
                cur.kinds(), ["SELECT", "UPDATE"], (title, url)
            )  # one guarded exclusion, no description row
            sql, args = cur.sql[1]
            self.assertIn("status='EXCLUDED_FIT'", sql)
            self.assertIn("status='HOLD'", sql)
            self.assertEqual(args[0], "sponsor_directory")

    def test_a_title_that_is_not_a_job_is_never_posted_and_is_excluded_with_a_fixed_reason(self):
        for n, title in enumerate(NOT_JOB_TITLES, 1):
            cur = Cursor([job(n, title=title)])
            counts = dj.promote_leads(cur, {}, NOW)
            self.assertEqual((counts["promoted"], counts["not_a_job"]), (0, 1), title)
            self.assertEqual(cur.kinds(), ["SELECT", "UPDATE"], title)
            self.assertEqual(cur.sql[1][1][0], "not_a_job")

    def test_a_directory_host_is_matched_by_host_not_by_substring(self):
        self.assertTrue(dj.is_sponsor_directory("https://WWW.LegitSponsors.co.uk/x"))
        self.assertTrue(dj.is_sponsor_directory("https://a.b.myvisajobs.co.uk/x"))
        self.assertFalse(dj.is_sponsor_directory("https://notlegitsponsors.co.uk/x"))
        self.assertFalse(dj.is_sponsor_directory("https://jobs.lever.co/legitsponsors.co.uk"))
        self.assertFalse(dj.is_sponsor_directory(""))

    def test_real_listings_still_post_exactly_as_before(self):
        for n, title in enumerate(REAL_TITLES, 1):
            self.assertFalse(dj.is_not_a_job(title, "Sixfold Bioscience Limited"), title)
        cur = Cursor(
            [job(n, url=f"https://talents.example.test/job/{n}", title=t) for n, t in enumerate(REAL_TITLES, 1)]
        )
        counts = dj.promote_leads(cur, {}, NOW)
        self.assertEqual(
            (counts["promoted"], counts["sponsor_directory"], counts["not_a_job"]), (len(REAL_TITLES), 0, 0)
        )

    def test_the_two_rules_run_before_the_place_and_title_rules_so_a_directory_row_is_one_reason_only(self):
        cur = Cursor(
            [job(1, url="https://legitsponsors.co.uk/x", title="Enterprise Sales Executive", place="Manchester, UK")]
        )
        counts = dj.promote_leads(cur, {}, NOW)
        self.assertEqual((counts["sponsor_directory"], counts["not_london"], counts["title_excluded"]), (1, 0, 0))

    def test_both_reasons_are_registered(self):
        from lifeos.platform import codes

        for reason in ("sponsor_directory", "not_a_job"):
            self.assertIn(reason, codes.REASONS)


class Place(unittest.TestCase):
    def test_london_and_uk_wide_remote_get_a_place_and_other_places_do_not(self):
        def got(snippet):
            return dj.candidates(
                "Sixfold Bioscience Limited",
                [{"url": URL, "title": "Product Manager at Sixfold Bioscience | talents", "snippet": snippet}],
            )[0]

        self.assertEqual(got("Product Manager, London")["location"], "London, UK")
        self.assertEqual(got("Remote: UK, full time")["location"], "London, UK")
        self.assertEqual(got("Manchester, hybrid")["location"], "")
        self.assertEqual(got("")["location"], "")
        self.assertEqual(got("Product Manager, London")["snippet"], "Product Manager, London")


class Lane(unittest.TestCase):
    def facts(self, **kw):
        return lanes.Facts(
            **{
                "fit": 85,
                "market": "UK",
                "work_mode": "onsite",
                "posted": datetime(2026, 11, 14).date(),
                "route": {"Scale-up": lanes.POSITIVE},
                "geography": lanes.POSITIVE,
                "located": True,
                "kept": True,
                "listing": True,
                **kw,
            }
        )

    def test_a_listing_lead_that_passes_the_scale_up_rules_is_review_never_admit(self):
        decision = lanes.qualify(lanes.POLICIES["Scale-Up"], self.facts(), datetime(2026, 11, 15).date())
        self.assertEqual((decision.status, decision.reason), (lanes.REVIEW, lanes.LISTING_REASON))
        self.assertEqual(lanes.public_reason(decision.reason), "listing_only")

    def test_a_low_fit_does_not_exclude_a_listing_lead_but_a_wrong_place_still_does(self):
        today = datetime(2026, 11, 15).date()
        self.assertEqual(
            lanes.qualify(lanes.POLICIES["Scale-Up"], self.facts(fit=40), today).status, lanes.REVIEW
        )  # the Scale-Up rules only, no Fit exclusion
        wrong = lanes.qualify(lanes.POLICIES["Scale-Up"], self.facts(geography=lanes.UNRESOLVED), today)
        self.assertEqual((wrong.status, wrong.reason), (lanes.EXCLUDE, "not in London"))

    def test_the_listing_proof_is_the_only_marker(self):
        self.assertTrue(lanes.from_listing("listing"))
        self.assertFalse(lanes.from_listing(None))
        self.assertFalse(lanes.from_listing("title"))


if __name__ == "__main__":
    unittest.main()
