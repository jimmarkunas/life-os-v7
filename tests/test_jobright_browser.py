import unittest

from pipeline import jobright_browser as jb


class OutcomeTests(unittest.TestCase):
    def test_landing_kinds(self):
        self.assertEqual(jb.outcome_for("https://boards.greenhouse.io/acme/jobs/9", True)[0], "ats")
        self.assertEqual(jb.outcome_for("https://careers.acme.example/j/9", True)[0], "employer")
        self.assertEqual(jb.outcome_for("https://jobright.ai/jobs/info/1", False), ("internal", None))
        self.assertEqual(jb.outcome_for("", True), ("internal", None))

    def test_missing_credentials_never_launch_a_browser(self):
        import asyncio
        self.assertEqual(asyncio.run(jb.resolve_many(["https://jobright.ai/jobs/info/1"], {})),
                         [{"outcome": "auth_unavailable"}])


if __name__ == "__main__":
    unittest.main()
