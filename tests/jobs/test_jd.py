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


if __name__ == "__main__":
    unittest.main()
