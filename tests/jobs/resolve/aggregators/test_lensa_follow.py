import unittest
from unittest import mock

from lifeos.jobs.resolve.aggregators import lensa
from lifeos.platform.http import Fetched

TRACK = "https://sg3email.lensa.com/ls/click?upn=abc"
LENSA_JOB = "https://lensa.com/job-v1/acme/remote/product-manager/123"
ROW = (1, TRACK, "Acme", "Product Manager", "Remote")


def fetched(final, status=200, html=""):
    return Fetched(final, status, html, 2, codes=(302, status))


class FollowTests(unittest.TestCase):
    """V1's Lensa mechanic: the card's own tracking link, then at most the ONE ATS link on the Lensa page it lands on."""
    def follow(self, page):
        with mock.patch.object(lensa.http, "fetch", return_value=page):
            return lensa.follow(TRACK)

    def test_a_chain_that_lands_on_the_employers_ats_is_the_answer(self):
        got = self.follow(fetched("https://boards.greenhouse.io/acme/jobs/42"))
        self.assertEqual((got["outcome"], got["via"], got["kind"], got["url"]),
                         ("landed", "tracking_redirect", "ats", "https://boards.greenhouse.io/acme/jobs/42"))

    def test_the_one_ats_link_on_the_lensa_page_is_taken_and_two_are_refused(self):
        one = '<a href="https://jobs.lever.co/acme/7f1c">Apply</a><a href="/about">x</a>'
        got = self.follow(fetched(LENSA_JOB, html=one))
        self.assertEqual((got["outcome"], got["via"], got["url"]), ("landed", "lensa_page", "https://jobs.lever.co/acme/7f1c"))
        two = one + '<a href="https://jobs.lever.co/other/99aa">Similar job</a>'
        self.assertEqual(self.follow(fetched(LENSA_JOB, html=two)), {"outcome": "external_hidden", "redirect": "lensa_page_many"})
        self.assertEqual(self.follow(fetched(LENSA_JOB, html="<p>no links</p>")), {"outcome": "external_hidden", "redirect": "lensa_page_none"})

    def test_blocks_and_aggregators_fall_through_to_the_matchers(self):
        self.assertEqual(self.follow(fetched(LENSA_JOB, status=403)), {"outcome": "external_hidden", "redirect": "blocked"})
        self.assertEqual(self.follow(fetched(TRACK, status=429)), {"outcome": "external_hidden", "redirect": "blocked"})
        self.assertEqual(self.follow(Fetched(TRACK, 0, error="network")), {"outcome": "external_hidden", "redirect": "network"})
        self.assertEqual(self.follow(fetched("https://www.indeed.com/viewjob?jk=1")), {"outcome": "external_hidden", "redirect": "elsewhere"})
        self.assertEqual(lensa.follow("https://example.com/x"), {"outcome": "external_hidden", "redirect": "no_link"})

    def test_a_block_stops_the_follows_for_the_run_and_every_row_still_reaches_the_matchers(self):
        rows = [ROW, (2, TRACK, "Globex", "PM", ""), (3, TRACK, "Initech", "PM", "")]
        with mock.patch.object(lensa.http, "fetch", return_value=fetched(LENSA_JOB, status=403)) as fetch, \
                mock.patch.object(lensa.time, "sleep"), \
                mock.patch.object(lensa.linkedin, "match_rows", side_effect=lambda rows, results, budget: results) as match:
            resolve = lensa.make_resolver()
            out = resolve(rows)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual([r["outcome"] for r in out], ["external_hidden"] * 3)
        self.assertEqual(resolve.redirect, {"blocked": 1, "stopped": 2})
        self.assertTrue(all(set(r) == {"outcome"} for r in match.call_args.args[1]))      # the counter code never reaches the stage

    def test_a_landed_row_skips_the_search(self):
        with mock.patch.object(lensa.http, "fetch", return_value=fetched("https://boards.greenhouse.io/acme/jobs/42")), \
                mock.patch.object(lensa.time, "sleep"), \
                mock.patch.object(lensa.linkedin.search_match, "find") as find, \
                mock.patch.object(lensa.linkedin.ats_match, "match_many", return_value=[]):
            resolve = lensa.make_resolver()
            out = resolve([ROW])
        self.assertEqual(out[0]["url"], "https://boards.greenhouse.io/acme/jobs/42")
        find.assert_not_called()
        self.assertEqual(resolve.redirect, {"landed_tracking_redirect": 1})


if __name__ == "__main__":
    unittest.main()
