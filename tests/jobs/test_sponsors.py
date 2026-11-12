import unittest

from lifeos.jobs import names, sponsors
from lifeos.jobs.lanes import NEGATIVE, POSITIVE, UNRESOLVED
from lifeos.platform.http import Fetched
from lifeos.sources.discovery import sponsor_register as reg

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


class LoadUsesTheConnectionContextManager(unittest.TestCase):
    """Run 88 crashed fit here: store.connect() is a context manager, not a connection."""

    def test_load_reads_the_register_through_store_connect(self):
        from unittest import mock
        from tests.kit.db import FakeConn
        conn = FakeConn()
        conn.cur.fetchall = lambda: [("Monzo Bank Limited",), ("Acme Widgets Ltd",)]
        with mock.patch.object(sponsors.store, "connect", return_value=conn):
            register = sponsors.load()
        self.assertEqual((len(register), register.state("Monzo")), (2, POSITIVE))


class TheStageThroughItsContract(unittest.TestCase):
    """LIFE-589 pilot: the sponsors stage called through reg.CONTRACT, in dry-run and in write mode, gives the same counts as calling run() and does exactly what the contract declares."""
    NOW = __import__("datetime").datetime(2026, 11, 12, 20, 0)

    class Store:
        def __init__(self):
            self.statements = []

        def connect(self):
            outer = self

            class Cursor:
                def __enter__(self_):
                    return self_

                def __exit__(self_, *exc):
                    return False

                def execute(self_, sql, args=()):
                    outer.statements.append(" ".join(sql.split()).split(" ")[0].upper())

                def executemany(self_, sql, rows):
                    outer.statements.append("INSERT:%d" % len(rows))

                def fetchone(self_):
                    return (None,)                                  # no earlier refresh: the register is due

            class Conn:
                def __enter__(self_):
                    return self_

                def __exit__(self_, *exc):
                    return False

                def cursor(self_):
                    return Cursor()

            return Conn()

        def ensure_schema(self, connection):
            pass

    def pages(self):
        rows = "".join(f"Qx{i:05d}z Ltd,Leeds,,Worker (A rating),Skilled Worker\n" for i in range(reg.MIN_ROWS + 25))
        csv_text = "Organisation Name,Town/City,County,Type & Rating,Route\n" + rows
        link = "https://assets.publishing.service.gov.uk/media/x/2026-11-01_Worker.csv"
        pages = {reg.PAGE: '{"details": {"attachments": [{"url": "%s"}]}}' % link, link: csv_text}
        return lambda url, **kw: Fetched(url, 200, pages[url])

    def call(self, live, through_contract=True):
        from unittest import mock
        store = self.Store()
        fake = mock.patch.multiple(reg.store, connect=store.connect, ensure_schema=store.ensure_schema)
        with fake:
            target = reg.CONTRACT if through_contract else reg.run
            counts = target(40, live, now=self.NOW, fetcher=self.pages(), environ={})
        return counts, store.statements

    def test_dry_run_through_the_contract_counts_and_changes_nothing(self):
        counts, statements = self.call(False)
        self.assertEqual((counts["status"], counts["saved"]), ("OK", False))
        self.assertGreaterEqual(counts["sponsors"], reg.MIN_ROWS)
        self.assertEqual(statements, ["SELECT"])                            # it only read the newest refresh time
        self.assertEqual(counts, self.call(False, through_contract=False)[0])

    def test_write_mode_through_the_contract_replaces_the_table_and_reports_the_same_counts(self):
        counts, statements = self.call(True)
        self.assertEqual((counts["status"], counts["saved"]), ("OK", True))
        self.assertIn("DELETE", statements)
        self.assertTrue(any(s.startswith("INSERT:") for s in statements))
        self.assertEqual(counts, self.call(True, through_contract=False)[0])

    def test_the_contract_declares_what_the_stage_returns_and_is_what_the_registry_runs(self):
        self.assertEqual(set(reg.CONTRACT.outputs) - set(self.call(False)[0]), set())
        from lifeos import run
        self.assertEqual(run.STAGES["sponsors"].target, ("lifeos.sources.discovery.sponsor_register", "CONTRACT"))
        self.assertTrue(reg.CONTRACT.dry_run and reg.CONTRACT.write)
