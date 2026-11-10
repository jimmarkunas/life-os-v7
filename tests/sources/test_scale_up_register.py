import unittest

from lifeos.sources import scale_up_register as reg
from tests.kit import shared

CSV = ("Organisation Name,Town/City,County,Type & Rating,Route\n"
       "ExampleCo Limited,London,Greater London,Worker (A rating),Scale-up\n"
       "HeldCo Ltd,London,Greater London,Worker (A rating),Scale-up\n"
       "NorthCo Ltd,Leeds,West Yorkshire,Worker (A rating),Scale-up\n"
       "Skilled Ltd,London,Greater London,Worker (A rating),Skilled Worker\n")


Page = shared.Page


class Register(unittest.TestCase):
    def test_only_the_scale_up_route_is_read_by_header_name(self):
        entries, why = reg.rows_of(CSV)
        self.assertEqual((why, [e[0] for e in entries]), (None, ["ExampleCo Limited", "HeldCo Ltd", "NorthCo Ltd"]))
        self.assertEqual(reg.rows_of("a,b\n1,2\n")[1], "bad_columns")

    def test_london_sponsors_the_registry_does_not_hold_are_counted_and_listed(self):
        entries, _ = reg.rows_of(CSV)
        held = {tuple(reg.names.core("HeldCo Ltd")), tuple(reg.names.core("Elsewhere Plc"))}
        counts, missing = reg.compare(entries, held)
        self.assertEqual(counts, {"register_scale_up": 3, "london": 2, "london_held": 1, "london_missing": 1, "held_total": 2, "held_in_register": 1})
        self.assertEqual([m[0] for m in missing], ["ExampleCo Limited"])

    def test_the_registry_file_loads_and_the_log_gets_counts_only(self):
        self.assertGreaterEqual(len(reg.held_keys()), 40)
        from tests.jobs.support import FakeClient, FakeStore, connect
        from unittest import mock
        from lifeos.jobs import private_report, store
        from lifeos.platform import stored_pages
        self.enterContext(mock.patch.object(stored_pages, "STORE", FakeStore()))
        self.enterContext(mock.patch.object(store, "connect", connect))
        pages = {reg.sponsor_register.PAGE: Page('{"x": "https://assets.publishing.service.gov.uk/media/a.csv"}'), "https://assets.publishing.service.gov.uk/media/a.csv": Page(CSV * 5)}
        client = FakeClient()
        entries = ["ExampleCo Limited"] + [f"Filler{i} Ltd" for i in range(12)]
        text = "Organisation Name,Town/City,County,Type & Rating,Route\n" + "".join(f"{n},London,Greater London,Worker (A rating),Scale-up\n" for n in entries)
        pages["https://assets.publishing.service.gov.uk/media/a.csv"] = Page(text)
        counts = reg.run(0, True, fetcher=lambda url, **kw: pages[url], client=client)
        self.assertEqual((counts["status"], counts["london"], counts["page"], counts["failed"]), ("OK", 13, "new", 0))
        self.assertIn("ExampleCo Limited", str(client.writes[0][2]))
        self.assertEqual(reg.run(0, False, fetcher=lambda url, **kw: Page("{}", 503))["status"], "FAILED:http_503")


if __name__ == "__main__":
    unittest.main()
