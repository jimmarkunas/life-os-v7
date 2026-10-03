"""Synthetic provider-shaped alerts; no live mailbox content."""
import unittest

from lifeos.sources.newsletters.parsers import dice, reed


DICE = ('<a href="https://www.dice.com/job-detail/EX123"><span>Platform Analyst</span>'
        '<span>Example Systems</span><span>Location: Remote</span><span>Salary: $100K / yr</span></a>'
        '<a href="https://www.dice.com/job-detail/EX999"><span>View job</span></a>')
REED = ('<a href="https://www.reed.co.uk/jobs/data-specialist/12345678"><span>Data Specialist</span>'
        '<span>Example Group</span><span>Location: Remote</span><span>Salary: £50,000</span></a>')


class DiceParserTests(unittest.TestCase):
    def test_required_fields_and_optional_details(self):
        cards, skipped = dice.parse_counted(DICE)
        self.assertEqual((len(cards), skipped), (1, 1))
        self.assertEqual((cards[0].title, cards[0].company, cards[0].location_text, cards[0].salary_text),
                         ("Platform Analyst", "Example Systems", "Remote", "$100K / yr"))
        self.assertTrue(dice.looks_like_jobs(DICE))
        parsed, _ = dice.parse_counted(DICE)
        self.assertEqual([(c.title, c.company, c.url) for c in cards], [(c.title, c.company, c.url) for c in parsed])

    def test_non_job_link_is_not_a_job_card(self):
        self.assertEqual(dice.parse("<a href='https://example.com/jobs/1'>Role</a>"), [])
        self.assertFalse(dice.looks_like_jobs("<p>Alert settings updated</p>"))


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
