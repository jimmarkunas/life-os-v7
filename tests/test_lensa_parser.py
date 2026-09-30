"""Synthetic Lensa-shaped emails (invented companies/URLs only; never paste real mail here)."""
import unittest

from pipeline.parsers import lensa

CARD = """<a href="https://email.lensa.com/f/a/{id}" style="text-decoration:none;">
<table style="width:100%"><tr><td>{company}</td><td rowspan=4>&rsaquo;</td></tr>
<tr><td>{title_html}</td></tr>{salary}<tr><td>{loc}</td></tr></table></a>"""
SAL = "<tr><td>$100K-$120K / yr. (est.)</td></tr>"


def card(id, company, title, salary=SAL, loc="Remote, Remote", nested=True):
    t = f'<a href="https://email.lensa.com/f/a/{id}">{title}</a>' if nested else title
    return CARD.format(id=id, company=company, title_html=t, salary=salary, loc=loc)


class LensaParserTests(unittest.TestCase):
    def test_advocate_layout_with_nested_title_link(self):
        html = "<div>Hi Sam,</div>" + card("A1", "Acme Corp&#8228;", "PROJECT MANAGER") + card("B2", "Globex", "Program Manager")
        cards = lensa.parse(html)
        self.assertEqual([(c.company, c.title) for c in cards], [("Acme Corp.", "PROJECT MANAGER"), ("Globex", "Program Manager")])
        self.assertEqual(cards[0].salary_text, "$100K-$120K / yr. (est.)")
        self.assertEqual(cards[0].location_text, "Remote, Remote")
        self.assertTrue(cards[0].url.endswith("/A1"))

    def test_digest_layout_without_nested_link_and_extra_fields(self):
        html = card("C3", "Initech", "Delivery Lead", loc="US-Remote</td></tr><tr><td>&bull;</td></tr><tr><td>Remote", nested=False)
        (c,) = lensa.parse(html)
        self.assertEqual((c.company, c.title), ("Initech", "Delivery Lead"))
        self.assertEqual(c.location_text, "US-Remote / Remote")

    def test_missing_salary_and_duplicate_links(self):
        html = card("D4", "Hooli", "Scrum Master", salary="") + card("D4", "Hooli", "Scrum Master", salary="")
        cards = lensa.parse(html)
        self.assertEqual(len(cards), 1)
        self.assertIsNone(cards[0].salary_text)

    def test_ignores_non_card_links_and_empty_mail(self):
        html = '<a href="https://email.lensa.com/f/a/Z">See more jobs</a><a href="https://example.com/x">x</a>'
        self.assertEqual(lensa.parse(html), [])
        self.assertEqual(lensa.parse(""), [])


if __name__ == "__main__":
    unittest.main()
