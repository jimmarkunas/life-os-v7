import unittest

from lifeos.jobs import names, sponsors
from lifeos.jobs.lanes import NEGATIVE, POSITIVE, UNRESOLVED
from lifeos.platform.http import Fetched
from lifeos.sources import sponsor_register as reg

CSV = ("Organisation Name,Town/City,County,Type & Rating,Route\n"
       "Monzo Bank Limited,London,,Worker (A rating),Skilled Worker\n"
       "Acme Widgets Ltd,Leeds,,Worker (A rating),Skilled Worker\n"
       "Acme Widgets Ltd,Leeds,,Temporary Worker (A rating),Temporary Worker\n"
       "Downgraded Co,London,,Worker (B rating),Skilled Worker\n"
       "Care Home Group,Hull,,Worker (A rating),Health and Care Worker\n")


class Names(unittest.TestCase):
    def test_same_employer_is_distinctive_token_equality(self):
        self.assertTrue(names.same_employer("Monzo", "Monzo Bank Limited"))
        self.assertTrue(names.same_employer("Acme Widgets Inc.", "ACME WIDGETS LTD"))
        self.assertFalse(names.same_employer("Smith", "Smith & Wesson"))
        self.assertFalse(names.same_employer("Apple", "Apple Tree Nursery"))
        self.assertFalse(names.same_employer("Global Services", "Global Services Ltd"))     # nothing distinctive


class Register(unittest.TestCase):
    def setUp(self):
        entries, why = reg.parse(CSV)
        self.assertIsNone(why)
        self.register = sponsors.Register(entries)

    def test_parse_keeps_skilled_worker_a_rated_only(self):
        entries, _ = reg.parse(CSV)
        self.assertEqual(sorted(entries), ["Acme Widgets Ltd", "Monzo Bank Limited"])
        self.assertEqual(reg.parse("a,b\n1,2")[1], "bad_columns")

    def test_states(self):
        r = self.register
        self.assertEqual(r.state("Monzo"), POSITIVE)
        self.assertEqual(r.state("Acme Widgets Inc"), POSITIVE)
        self.assertEqual(r.state("Downgraded Co"), NEGATIVE)               # not on the Skilled Worker, A-rated register
        self.assertEqual(r.state("Hays Recruitment"), UNRESOLVED)           # a recruiter's licence cannot qualify an unresolved client
        self.assertEqual(r.state(""), UNRESOLVED)
        self.assertEqual(sponsors.Register([]).state("Monzo"), UNRESOLVED)  # no register loaded: Review, never NEGATIVE


class Refresh(unittest.TestCase):
    def test_csv_link_comes_from_the_content_api(self):
        page = '{"details": {"attachments": [{"url": "https://assets.publishing.service.gov.uk/media/abc/2026-10-01_Worker.csv"}]}}'
        self.assertEqual(reg.csv_url(lambda url, **kw: Fetched(url, 200, page)), ("https://assets.publishing.service.gov.uk/media/abc/2026-10-01_Worker.csv", None))
        self.assertEqual(reg.csv_url(lambda url, **kw: Fetched(url, 200, "{}"))[1], "no_csv_link")
        self.assertEqual(reg.csv_url(lambda url, **kw: Fetched(url, 403, ""))[1], "http_403")
