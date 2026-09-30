import unittest

from pipeline import search_match


class SearchMatchTests(unittest.TestCase):
    ok = {"url": "https://job-boards.greenhouse.io/acmedata/jobs/42", "title": "Senior Data Engineer - Acme Data",
          "snippet": "Acme Data is hiring"}

    def test_accepts_matching_title_and_company(self):
        self.assertTrue(search_match._good(self.ok, "Acme Data, Inc.", "Senior Data Engineer", False))

    def test_rejects_other_title_other_company_and_aggregators(self):
        self.assertFalse(search_match._good(self.ok, "Acme Data", "Staff Data Engineer", False))
        self.assertFalse(search_match._good(self.ok, "Globex", "Senior Data Engineer", False))
        agg = {"url": "https://www.indeed.com/viewjob?jk=1", "title": "Senior Data Engineer - Acme Data", "snippet": ""}
        self.assertFalse(search_match._good(agg, "Acme Data", "Senior Data Engineer", False))

    def test_employer_site_needs_company_in_host(self):
        other = {"url": "https://careers.example.org/j/1", "title": "Senior Data Engineer Acme Data", "snippet": ""}
        self.assertFalse(search_match._good(other, "Acme Data", "Senior Data Engineer", True))
        mine = {"url": "https://www.acmedata.com/careers/1", "title": "Senior Data Engineer | Acme Data", "snippet": ""}
        self.assertTrue(search_match._good(mine, "Acme Data", "Senior Data Engineer", True))


if __name__ == "__main__":
    unittest.main()
