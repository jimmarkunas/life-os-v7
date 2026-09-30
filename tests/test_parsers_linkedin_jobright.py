"""Synthetic LinkedIn / Jobright shapes (invented data only)."""
import unittest

from pipeline.parsers import jobright, linkedin

TRACK = "?trackingId=SYNTH&midToken=SYNTH&otpToken=SYNTH&eid=SYNTH"
LINKEDIN = (
    f'<a href="https://www.linkedin.com/comm/jobs/view/1111111111/{TRACK}"><img src="x"></a>'
    f'<a href="https://www.linkedin.com/comm/jobs/view/1111111111/{TRACK}">Project Manager</a>'
    f'<td>Acme · Austin, TX (Remote)</td>'
    f'<a href="https://www.linkedin.com/comm/jobs/view/1111111111/{TRACK}">Acme · Austin, TX (Remote)</a>'
    f'<a href="https://www.linkedin.com/comm/jobs/view/2222222222/{TRACK}">Program Lead</a>'
    f'<a href="https://www.linkedin.com/comm/jobs/view/2222222222/{TRACK}">Globex · United States (Remote)</a>'
    f'<a href="https://www.linkedin.com/help/x">Help</a>')

JOBRIGHT = ('<a href="https://jobright.ai/jobs/info/abc123def456?utm_source=1&imp_id=SYNTH">'
            '<span>Acme</span><span>Software &amp; Services</span><span>· Mid Level</span><span>89</span><span>%</span>'
            '<span>Senior Program Manager</span><span>$120K/yr - $150K/yr</span><span>Austin, TX</span>'
            '<span>5+ referrals</span><span>34 minutes ago</span><span>·</span><span>Be among the first applicants</span>'
            '<span>Apply Now</span></a>'
            '<a href="https://jobright.ai/jobs/info/abc123def456?x=2"><span>Acme</span></a>'
            '<a href="https://jobright.ai/jobs/info/zzz999"><span>Globex</span><span>Consulting</span><span>· Senior</span>'
            '<span>95</span><span>%</span><span>Delivery Manager</span><span>Remote, US</span><span>3 days ago</span></a>')


class LinkedInTests(unittest.TestCase):
    def test_two_links_per_job_merge_and_url_is_clean(self):
        cards = linkedin.parse(LINKEDIN)
        self.assertEqual([(c.company, c.title, c.location_text) for c in cards],
                         [("Acme", "Project Manager", "Austin, TX (Remote)"), ("Globex", "Program Lead", "United States (Remote)")])
        self.assertEqual(cards[0].url, "https://www.linkedin.com/jobs/view/1111111111")
        self.assertTrue(all("SYNTH" not in c.url and "?" not in c.url for c in cards))   # no personal tokens kept


class JobrightTests(unittest.TestCase):
    def test_fields_age_and_clean_url(self):
        first, second = jobright.parse(JOBRIGHT)
        self.assertEqual((first.company, first.title, first.salary_text), ("Acme", "Senior Program Manager", "$120K/yr - $150K/yr"))
        self.assertEqual((first.age_days, first.url), (0, "https://jobright.ai/jobs/info/abc123def456"))
        self.assertIn("Austin, TX", first.location_text)
        self.assertEqual((second.title, second.age_days, second.salary_text), ("Delivery Manager", 3, None))

    def test_match_percentage_is_not_carried(self):
        first = jobright.parse(JOBRIGHT)[0]
        self.assertNotIn("89", " ".join(str(getattr(first, s)) for s in first.__slots__))


if __name__ == "__main__":
    unittest.main()
