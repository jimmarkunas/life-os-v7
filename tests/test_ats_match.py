import unittest

from pipeline import ats_match


class MatchTests(unittest.TestCase):
    jobs = [("greenhouse", "Senior Data Engineer", "https://boards.greenhouse.io/a/jobs/1", "New York, NY"),
            ("greenhouse", "Senior Data Engineer", "https://boards.greenhouse.io/a/jobs/2", "Austin, TX"),
            ("greenhouse", "Product Manager", "https://boards.greenhouse.io/a/jobs/3", "Remote")]

    def test_unique_title_matches(self):
        self.assertEqual(ats_match.pick(self.jobs, "product manager", "Denver, CO")[2], "https://boards.greenhouse.io/a/jobs/3")

    def test_same_title_is_disambiguated_by_location(self):
        self.assertEqual(ats_match.pick(self.jobs, "Senior Data Engineer", "Austin, Texas, United States")[2],
                         "https://boards.greenhouse.io/a/jobs/2")

    def test_ambiguous_or_missing_is_none(self):
        self.assertIsNone(ats_match.pick(self.jobs, "Senior Data Engineer", "Denver, CO"))
        self.assertIsNone(ats_match.pick(self.jobs, "Staff Data Engineer", "Austin, TX"))

    def test_slugs(self):
        self.assertEqual(ats_match.slug_candidates("Acme Data Corp, Inc."), ["acmedata", "acme-data", "acme"])


if __name__ == "__main__":
    unittest.main()
