import unittest

from lifeos.jobs import jd

SYNTHETIC = """<div><h2>About the Role</h2><p>Acme is hiring a Project Manager to run launches.</p>
<h3>Responsibilities</h3><ul><li>Plan launches</li><li>Report status</li></ul>
<h3>Requirements</h3><ul><li>5+ years managing projects</li><li>PMP preferred? No - required</li></ul>
<h3>Preferred Qualifications</h3><ul><li>Experience with Jira</li></ul>
<script>var x = 1;</script></div>"""


class JdTests(unittest.TestCase):
    def test_sections_split_and_full_text_kept(self):
        d = jd.describe(SYNTHETIC)
        self.assertIn("run launches", d["summary"])
        self.assertIn("Plan launches", d["responsibilities"])
        self.assertIn("5+ years", d["requirements"])
        self.assertIn("Jira", d["qualifications"])
        self.assertNotIn("var x", d["full_text"])
        self.assertIn("Plan launches", d["full_text"])          # nothing is lost
        self.assertEqual(len(d["fingerprint"]), 64)

    def test_unknown_headings_keep_everything_in_summary(self):
        d = jd.describe("<p>Wonderful team.</p><p>Apply now!</p>")
        self.assertIn("Wonderful team.", d["summary"])
        self.assertEqual((d["requirements"], d["qualifications"]), ("", ""))

    def test_bullets_are_not_mistaken_for_headings(self):
        text = "Requirements\n• Experience with budgets\n• Strong skills"
        s = jd.split_sections(text)
        self.assertIn("Experience with budgets", s["requirements"])
        self.assertEqual(s["qualifications"], "")


LINKEDIN_PAGE = """<html><head><title>Principal Technical Program Manager | Oracle | LinkedIn</title></head><body>
<h1>Principal Technical Program Manager</h1><a>Join or sign in to find your next job</a>
<form><label>Email or phone</label><input type="text"><label>Password</label><input type="password"><button>Sign in</button></form>
<p>New to LinkedIn? Join now. By clicking Continue to join or sign in, you agree to LinkedIn's User Agreement, Privacy Policy, and Cookie Policy.</p>
<div class="show-more-less-html"><div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5 relative overflow-hidden">
<p>Lead complex technical programs.<br>Align engineering and product on milestones.</p><div><strong>Responsibilities</strong><ul><li>Own the program plan</li><li>Report risks</li></ul></div>
<img src="x.png"/><p>Requirements</p><ul><li>10 years of program delivery</li></ul></div><button>Show more</button><button>Show less</button></div>
<ul><li>Seniority level</li><li>Mid-Senior level</li></ul><p>Report this job</p></body></html>"""


class LinkedInDescriptionTests(unittest.TestCase):
    def test_only_the_description_element_is_read(self):
        text = jd.linkedin_description(LINKEDIN_PAGE)
        self.assertIn("Lead complex technical programs.", text)
        self.assertIn("Own the program plan", text)
        self.assertIn("10 years of program delivery", text)
        for chrome in ("sign in", "Password", "User Agreement", "Show more", "Report this job", "Seniority level"):
            self.assertNotIn(chrome, text)

    def test_a_page_without_the_element_gives_nothing_so_the_whole_page_reading_stays(self):
        self.assertIsNone(jd.linkedin_description("<html><body><p>Join or sign in</p></body></html>"))
        self.assertIsNone(jd.linkedin_description(""))
        self.assertIsNone(jd.linkedin_description(None))
        self.assertEqual(jd.linkedin_description('<div class="show-more-less-html__markup"></div><p>Join or sign in</p>'), "")

    def test_an_unclosed_void_tag_inside_the_element_does_not_end_it_early(self):
        page = '<div class="show-more-less-html__markup"><p>Line one<br>Line two</p><p>Line three</p></div><p>After</p>'
        text = jd.linkedin_description(page)
        self.assertIn("Line three", text)
        self.assertNotIn("After", text)


if __name__ == "__main__":
    unittest.main()
