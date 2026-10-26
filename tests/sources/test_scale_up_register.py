import unittest

from lifeos.sources import scale_up_register as reg

CSV = ("Organisation Name,Town/City,County,Type & Rating,Route\n"
       "ExampleCo Limited,London,Greater London,Worker (A rating),Scale-up\n"
       "HeldCo Ltd,London,Greater London,Worker (A rating),Scale-up\n"
       "NorthCo Ltd,Leeds,West Yorkshire,Worker (A rating),Scale-up\n"
       "Skilled Ltd,London,Greater London,Worker (A rating),Skilled Worker\n")


class Page:
    def __init__(self, html, status=200):
        self.html, self.status = html, status


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

    def test_the_registry_file_loads_and_the_log_gets_counts_and_a_sealed_block_only(self):
        self.assertGreaterEqual(len(reg.held_keys()), 40)
        import contextlib
        import io
        pages = {reg.sponsor_register.PAGE: Page('{"x": "https://assets.publishing.service.gov.uk/media/a.csv"}'), "https://assets.publishing.service.gov.uk/media/a.csv": Page(CSV * 5)}
        sealed, out = [], io.StringIO()
        entries = ["ExampleCo Limited"] + [f"Filler{i} Ltd" for i in range(12)]
        text = "Organisation Name,Town/City,County,Type & Rating,Route\n" + "".join(f"{n},London,Greater London,Worker (A rating),Scale-up\n" for n in entries)
        pages["https://assets.publishing.service.gov.uk/media/a.csv"] = Page(text)
        with contextlib.redirect_stdout(out):
            counts = reg.run(0, False, fetcher=lambda url, **kw: pages[url], seal=lambda t: sealed.append(t) or "SEALED")
        self.assertEqual((counts["status"], counts["london"], counts["sealed"], out.getvalue().strip()), ("OK", 13, True, "SEALED"))
        self.assertIn("ExampleCo Limited", sealed[0])
        self.assertEqual(reg.run(0, False, fetcher=lambda url, **kw: Page("{}", 503))["status"], "FAILED:http_503")


if __name__ == "__main__":
    unittest.main()
