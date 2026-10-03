"""Synthetic provider-shaped alerts; no live mailbox content."""
import unittest
from datetime import datetime, timezone

from lifeos.sources.newsletters.parsers import dice, reed


DICE = '''<p>New matches in your job alert</p><table><tr><td><table>
<tr><td><a href="https://elinks.dice.com/s/c/image1"><img src="https://example.com/image"></a></td></tr>
</table></td></tr>
<tr><td style="font-size:0px;padding:0 0 0 0"><div style="font-size:20px;font-weight:bold;color:#006699"><p><a href="https://elinks.dice.com/a/sc/example1">Platform Analyst</a></p></div></td></tr>
<tr><td><p><strong>Example Systems</strong></p><p>Remote, example.com</p></td></tr>
<tr></tr><tr><td><p>Posted: 10-05-2026</p></td></tr>
<tr><td><table><tr><td><a href="https://elinks.dice.com/s/c/image2"><img src="https://example.com/image"></a></td></tr></table></td></tr>
<tr><td style="font-size:0px;padding:0 0 0 0"><div style="font-size:20px;font-weight:bold;color:#006699"><p><a href="https://elinks.dice.com/a/sc/example2">Platform Analyst</a></p></div></td></tr>
<tr><td><p><strong>Example Systems</strong></p><p>Remote, example.com</p></td></tr>
<tr></tr><tr><td><p>Posted: 10-05-2026</p></td></tr>
</table>'''
DICE_DECOYS = '''<p>Job alert</p>
<a href="https://elinks.dice.com/a/sc/d1">Log In</a>
<a href="https://elinks.dice.com/a/sc/d2">View All Jobs</a>
<a href="https://elinks.dice.com/a/sc/d3">Manage your daily job alert &gt;</a>
<a href="https://elinks.dice.com/a/sc/d4">Dice Knowledge Center (FAQs)</a>
<a href="https://elinks.dice.com/a/sc/d5">Unsubscribe</a>
<a href="https://elinks.dice.com/a/sc/d6">Terms &amp; Conditions</a>
<a href="https://elinks.dice.com/s/c/image"><img src="https://example.com/image"></a>'''
DICE_DAY2 = '''<table><tr><td style="font-size:0px;padding:0"><div style="font-size:20px;font-weight:bold;color:#006699"><p>
<a href="https://elinks.dice.com/a/sc/nextday">Platform Analyst</a></p></div></td></tr>
<tr><td><p><strong>Example Systems</strong></p><p>Remote, example.com</p></td></tr><tr></tr>
<tr><td><p>Posted: 10-06-2026</p></td></tr></table>'''
REED = ('<a href="https://www.reed.co.uk/jobs/data-specialist/12345678"><span>Data Specialist</span>'
        '<span>Example Group</span><span>Location: Remote</span><span>Salary: £50,000</span></a>')


class DiceParserTests(unittest.TestCase):
    def test_tracking_links_dedupe_by_title_and_company_and_age_uses_mail_date(self):
        received = int(datetime(2026, 10, 7, tzinfo=timezone.utc).timestamp())
        cards, skipped = dice.parse_counted(DICE, received)
        self.assertEqual((len(cards), skipped), (1, 0))
        self.assertEqual((cards[0].title, cards[0].company, cards[0].location_text, cards[0].age_days),
                         ("Platform Analyst", "Example Systems", "Remote, example.com", 2))
        self.assertEqual(cards[0].url, "https://elinks.dice.com/a/sc/example1")
        self.assertTrue(dice.looks_like_jobs(DICE))

    def test_decoys_and_image_links_do_not_make_cards(self):
        self.assertEqual(dice.parse(DICE_DECOYS), [])

    def test_alert_wording_without_cards_is_still_job_mail(self):
        html = "<p>Your job alert has no new matches to display.</p>"
        self.assertTrue(dice.looks_like_jobs(html))
        self.assertEqual(dice.parse(html), [])


class ReedParserTests(unittest.TestCase):
    def test_job_card_and_optional_details(self):
        (card,) = reed.parse(REED)
        self.assertEqual((card.title, card.company, card.location_text, card.salary_text),
                         ("Data Specialist", "Example Group", "Remote", "£50,000"))
        self.assertEqual(card.url, "https://www.reed.co.uk/jobs/data-specialist/12345678")

    def test_course_link_is_not_a_job(self):
        html = '<a href="https://courses.reed.co.uk/course/EX123">Project skills</a>'
        self.assertEqual(reed.parse(html), [])
        self.assertFalse(reed.looks_like_jobs(html))


if __name__ == "__main__":
    unittest.main()
