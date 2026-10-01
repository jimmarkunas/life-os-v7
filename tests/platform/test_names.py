import unittest

from lifeos.platform import names


class Names(unittest.TestCase):
    def test_british_spellings_normalise_and_punctuation_is_dropped(self):
        self.assertEqual(names.norm("Technical Programme Manager"), names.norm("technical program  manager"))
        self.assertEqual(names.norm("Acme, Inc."), " acme inc. ")

    def test_company_and_role_matching_rules(self):
        self.assertTrue(names.same_company("Walmart", "Walmart Global Tech"))
        self.assertFalse(names.same_company("Wal", "Walmart"))
        self.assertFalse(names.same_company("Smith", "Smith & Wesson"))
        self.assertTrue(names.same_company("Acme Inc", "ACME"))
        self.assertTrue(names.same_role("Senior Programme Manager", "senior program manager"))
        self.assertFalse(names.same_role("Program Manager", "Product Manager"))
        self.assertFalse(names.same_role("", "Program Manager"))

    def test_employer_core_tokens(self):
        self.assertEqual(names.core("Monzo Bank Limited"), ["monzo"])
        self.assertTrue(names.same_employer("Monzo Bank Limited", "Monzo"))
        self.assertFalse(names.same_employer("Global Solutions", "Global Services"))

    def test_canonical_and_malformed_titles(self):
        self.assertEqual(names.split_title("Acme — Senior Program Manager"), ("Acme", "Senior Program Manager"))
        self.assertEqual(names.split_title("Acme - Senior Program Manager"), ("Acme", "Senior Program Manager"))
        self.assertEqual(names.split_title("Acme"), ("Acme", ""))          # never guessed into shape

    def test_jobs_reexports_the_same_functions(self):
        from lifeos.jobs import hiring_pipeline, names as jobs_names
        self.assertIs(hiring_pipeline.same_company, names.same_company)
        self.assertIs(jobs_names.core, names.core)


if __name__ == "__main__":
    unittest.main()
