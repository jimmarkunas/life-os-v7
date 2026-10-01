import unittest

from lifeos.jobs import fit_sync, meta


class Meta(unittest.TestCase):
    def test_pay_from_stored_field_then_explicit_range_in_the_description(self):
        self.assertEqual(meta.pay_text("$120,000 - $150,000", ""), "$120,000 - $150,000")
        self.assertEqual(meta.pay_text(None, "The base pay range is $120,000 - $150,000 per year plus equity."), "$120,000 - $150,000 per year")
        self.assertEqual(meta.pay_text(None, "Pay: $95k-$120k"), "$95k-$120k")

    def test_no_pay_is_blank_never_guessed(self):
        self.assertEqual(meta.pay_text(None, "Competitive salary, 401k, 10 years of experience, 5 days a week"), "")
        self.assertEqual(meta.pay_text(None, "Up to $5 off lunch"), "")

    def test_market_is_us_or_london_only(self):
        self.assertEqual(meta.market_label("Austin, TX"), "US")
        self.assertEqual(meta.market_label("London, UK"), "London")
        self.assertIsNone(meta.market_label("Manchester, UK"))
        self.assertIsNone(meta.market_label("Remote"))

    def test_visa_route_from_evidence_or_lane_else_unknown(self):
        self.assertEqual(meta.visa_routes("Scale-up:POSITIVE", "", "US Remote"), ["Scale-up"])
        self.assertEqual(meta.visa_routes(None, "Scale-Up,Skilled Worker", "Scale-Up"), ["Scale-up", "Skilled Worker"])
        self.assertEqual(meta.visa_routes(None, "", "US Remote"), ["None / Unknown"])

    def test_properties_only_include_what_is_known(self):
        props = meta.properties("Remote", None, "no pay here", None, "", "US Remote")
        self.assertEqual(sorted(props), ["Location / Work Mode", "Visa Route"])

    def test_fit_sync_backfills_eligible_lane_and_meta_when_the_fit_row_has_none(self):
        row_meta = {"lane": "Newsletter", "location": "Austin, TX", "salary": None, "route": None, "text": "$100,000 - $130,000"}
        props = fit_sync.properties(80, "line", "ADMIT", None, "remote", None, "", True, row_meta)
        self.assertEqual(props["Eligible Lanes"]["multi_select"], [{"name": "US Remote"}])
        self.assertEqual((props["Market"]["select"]["name"], props["Compensation"]["rich_text"][0]["text"]["content"]), ("US", "$100,000 - $130,000"))


if __name__ == "__main__":
    unittest.main()
