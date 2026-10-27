import base64
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from lifeos.amazon import card, packages
from lifeos.platform import gmail as gmail_mod, report_region
from lifeos.platform.gmail import GmailError
from tests.amazon.test_card import ENV, ROWS, Reader
from tests.kit.notion import AmazonRegions

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
TABLE_HTML = ("<html><body><h1>Good things are heading your way!</h1><h2>Tracking Information</h2><table><thead><tr><th>Provider</th><th>Tracking Number</th><th>Date</th></tr></thead>"
              "<tbody><tr><td> DHLECOMMERCE </td><td> 6100000000000001 </td><td><time>October 21, 2026</time></td></tr></tbody></table></body></html>")


def body_of(html):
    return gmail_mod._find_text({"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()}}, True)


AT = chr(64)
STORE = '"Ejuice Store" <orders' + AT + 'shop.example>'


def record(subject, text, days_ago, sender=STORE):
    return {"id": f"m{days_ago}{subject[:3]}", "sender": sender, "subject": subject, "body_text": text, "received_at": (NOW.astimezone(timezone.utc) - timedelta(days=days_ago)).isoformat()}


class FakeGmail:
    def __init__(self, records=(), fail=False):
        self.records, self.fail, self.queries = list(records), fail, []

    def list_ids_complete(self, query, limit):
        self.queries.append(query)
        if self.fail:
            raise GmailError("GMAIL_LISTING_INCOMPLETE")
        return [str(i) for i in range(len(self.records))]

    def message_record(self, message_id, keep_lines=False):
        return self.records[int(message_id)]


class ExtractTests(unittest.TestCase):
    def test_a_provider_and_number_table_gives_the_carrier_and_number(self):
        event = packages.extract(record("Your order from Ejuice Store is on its way!", body_of(TABLE_HTML), 6))
        self.assertEqual((event["kind"], event["store"], event["numbers"]), ("SHIPPED", "Ejuice Store", [("DHL", "6100000000000001")]))

    def test_carrier_shapes_and_inline_tracking_numbers_are_found(self):
        self.assertEqual(packages.numbers("Your tracking number: 9400111899223344556677 thanks"), [("", "9400111899223344556677")])
        self.assertEqual(packages.numbers("Track 1Z999AA10123456784 now")[0], ("UPS", "1Z999AA10123456784"))

    def test_a_shipping_notice_with_no_number_is_not_listed_and_other_mail_is_ignored(self):
        self.assertIsNone(packages.extract(record("Your order has shipped", "no number here", 2)))
        self.assertIsNone(packages.extract(record("Your receipt", "Tracking number 6100000000000001", 2)))

    def test_delivered_wording_is_not_confused_with_future_or_failed_delivery(self):
        self.assertEqual(packages.extract(record("Your package was delivered", "", 1))["kind"], "DELIVERED")
        self.assertIsNone(packages.extract(record("Your package will be delivered Friday", "", 1)))
        self.assertIsNone(packages.extract(record("Delivery attempt failed", "", 1)))

    def test_the_store_comes_from_the_display_name_or_the_domain_never_the_address(self):
        self.assertEqual(packages.store_name(STORE), "Ejuice Store")
        self.assertEqual(packages.store_name("orders" + AT + "mail.somestore.example"), "Somestore")


class FoldTests(unittest.TestCase):
    def shipped(self, days):
        return packages.extract(record("Your order is on its way", body_of(TABLE_HTML), days))

    def test_delivered_by_number_closes_the_package(self):
        out = packages.fold([self.shipped(6), packages.extract(record("Delivered", "Tracking number: 6100000000000001", 2))])
        self.assertEqual([p["delivered_at"] is not None for p in out], [True])

    def test_delivered_without_a_number_closes_the_oldest_open_one_from_the_same_store(self):
        out = packages.fold([self.shipped(6), packages.extract(record("Your package was delivered", "Enjoy", 2))])
        self.assertIsNotNone(out[0]["delivered_at"])
        other = packages.fold([self.shipped(6), packages.extract(record("Your package was delivered", "Enjoy", 2, sender='"Other" <a' + AT + 'b.example>'))])
        self.assertIsNone(other[0]["delivered_at"])

    def test_split_drops_stale_in_transit_and_old_delivered(self):
        old = packages.fold([self.shipped(25)])
        self.assertEqual(packages.split(old, NOW), ([], []))
        fresh = packages.fold([self.shipped(6)])
        self.assertEqual(len(packages.split(fresh, NOW)[0]), 1)
        gone = packages.fold([self.shipped(20), packages.extract(record("Delivered", "Tracking number: 6100000000000001", 10))])
        self.assertEqual(packages.split(gone, NOW), ([], []))

    def test_query_excludes_amazon_and_the_mail_with_its_own_sections(self):
        q = packages.query(NOW)
        self.assertIn("-from:amazon.com", q)
        self.assertIn("-from:anytimemailbox.com", q)
        self.assertNotIn("@fedex", q.replace(packages.FEDEX_FOLLOW, ""))

    def test_a_failed_read_is_none_with_a_code_not_an_empty_list(self):
        self.assertEqual(packages.safe_read(FakeGmail(fail=True), NOW), (None, "PACKAGES_GMAIL_FAILED"))


class CardSectionTests(unittest.TestCase):
    def test_section_lists_in_transit_and_delivered_with_a_tracking_link(self):
        pkgs = packages.fold([packages.extract(record("Your order is on its way", body_of(TABLE_HTML), 6))])
        blocks, counts = card.render([], NOW, packages=pkgs)
        texts = [("".join(t["text"]["content"] for t in b[b["type"]]["rich_text"])) for b in blocks]
        self.assertTrue(any(t.startswith("Other packages · 1 in transit · 0 delivered") for t in texts))
        self.assertTrue(any("Ejuice Store" in t and "DHL 6100000000000001" in t for t in texts))
        self.assertEqual((counts["packages_in_transit"], counts["packages_status"]), (1, "ok"))
        link = next(b for b in blocks if b["type"] == "bulleted_list_item")["bulleted_list_item"]["rich_text"][0]["text"]["link"]["url"]
        self.assertTrue(link.startswith("https://www.dhl.com/") and link.endswith("6100000000000001"))

    def test_a_failed_read_says_unavailable_and_no_read_says_nothing(self):
        blocks, counts = card.render([], NOW, packages=None, packages_error=True)
        self.assertTrue(any("unavailable" in b["paragraph"]["rich_text"][0]["text"]["content"] for b in blocks if b["type"] == "paragraph"))
        self.assertEqual(counts["packages_status"], "unavailable")
        _, quiet = card.render([], NOW)
        self.assertEqual(quiet["packages_status"], "off")


class RunTests(unittest.TestCase):
    def go(self, gmail):
        notion = AmazonRegions()
        counts = card.run(0, True, environ=ENV, reader=Reader(ROWS), client=notion, now=NOW, gmail=gmail)
        return notion, counts

    def test_packages_are_written_under_the_amazon_lines_in_the_same_region(self):
        gmail = FakeGmail([record("Your order is on its way", body_of(TABLE_HTML), 6)])
        notion, counts = self.go(gmail)
        text = "\n".join(report_region.plain(b) for b in notion.children["amazon-callout"])
        self.assertIn("Other packages · 1 in transit", text)
        self.assertEqual(counts["packages_in_transit"], 1)
        self.assertEqual(len(gmail.queries), 1)

    def test_a_failed_mail_read_never_breaks_the_amazon_part(self):
        notion, counts = self.go(FakeGmail(fail=True))
        text = "\n".join(report_region.plain(b) for b in notion.children["amazon-callout"])
        self.assertIn("Other packages · unavailable", text)
        self.assertIn("in transit", text)
        self.assertEqual(counts["packages_status"], "unavailable")

    def test_without_mail_access_the_run_is_exactly_as_before(self):
        notion, counts = self.go(None)
        self.assertNotIn("Other packages", "\n".join(report_region.plain(b) for b in notion.children["amazon-callout"]))
        self.assertEqual(counts["packages_status"], "off")


if __name__ == "__main__":
    unittest.main()
