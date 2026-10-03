import contextlib
import io
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from lifeos.amazon import events, orders, stage
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.notion_client import NotionError
from tests.kit.amazon import AmazonGmail, AmazonNotion, message, notion_page


TOKEN = {"NOTION_AMAZON_TOKEN": "synthetic", "NOTION_AMAZON_DATA_SOURCE_ID": "source-example"}
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def sender(local, domain="amazon.com"):
    return local + chr(64) + domain


def event(message_id, status, at, *, order_id="123-1234567-1234567", total=None, subject="Example item", url=None):
    return {"id": message_id, "sender": sender({"ORDERED": "auto-confirm", "SHIPPED": "shipment-tracking", "DELIVERED": "order-update"}[status]),
            "received_at": at, "order_id": order_id, "status": status, "grand_total": total,
            "item_summary": subject, "item_count": None, "subject": subject,
            "amazon_order_url": url or events.CANONICAL_URL.format(order_id)}


class EventExtractionTests(unittest.TestCase):
    def test_each_event_class_exact_sender_and_body_identity(self):
        for local, status in (("auto-confirm", "ORDERED"), ("shipment-tracking", "SHIPPED"), ("order-update", "DELIVERED")):
            parsed = events.extract(message("m-1", local, subject="Example order"))
            self.assertEqual(parsed["status"], status)
            self.assertEqual(parsed["order_id"], "123-1234567-1234567")
            self.assertEqual(parsed["amazon_order_url"], events.CANONICAL_URL.format(parsed["order_id"]))
            self.assertNotIn("body_text", parsed)

    def test_rejects_lookalikes_subdomains_and_nonallowlisted_senders(self):
        for address in (sender("auto-confirm", "amazon.com.evil.example"), sender("xauto-confirm"),
                        sender("auto-confirm", "mail.amazon.com"), sender("auto-confirm", "amazon.co")):
            parsed = events.extract({"id": "m", "sender": address, "received_at": NOW, "body_text": "123-1234567-1234567"})
            self.assertEqual(parsed["reason"], "SENDER_UNSUPPORTED")
        uppercase_local = "AUTO-CONFIRM" + chr(64) + "amazon.com"
        parsed = events.extract({"id": "m", "sender": uppercase_local, "received_at": NOW, "body_text": "123-1234567-1234567"})
        self.assertEqual(parsed["reason"], "SENDER_UNSUPPORTED")
        parsed = events.extract({"id": "m", "sender": "Amazon <" + sender("auto-confirm") + ">, <" + sender("shipment-tracking") + ">",
                                "received_at": NOW, "body_text": "123-1234567-1234567"})
        self.assertEqual(parsed["status"], "REVIEW")

    def test_zero_or_two_distinct_body_order_ids_are_review_and_subject_is_ignored(self):
        for body, reason in (("nothing here", "ORDER_ID_MISSING"),
                             ("123-1234567-1234567 and 321-7654321-7654321", "ORDER_ID_AMBIGUOUS")):
            parsed = events.extract(message("m", "auto-confirm", body=body, subject="123-1234567-1234567"))
            self.assertEqual((parsed["status"], parsed["reason"]), ("REVIEW", reason))

    def test_grand_total_only_from_ordered_and_conflicting_totals_review(self):
        ordered = events.extract(message("m", "auto-confirm", body="123-1234567-1234567 Grand Total: $12.34"))
        shipped = events.extract(message("m", "shipment-tracking", body="123-1234567-1234567 Grand Total: $98.76"))
        conflict = events.extract(message("m", "auto-confirm", body="123-1234567-1234567 Grand Total: $12.34 Grand Total: $17.00"))
        malformed = events.extract(message("m", "auto-confirm", body="123-1234567-1234567 Grand Total: $12.345"))
        self.assertEqual(ordered["grand_total"], "12.34")
        self.assertIsNone(shipped["grand_total"])
        self.assertEqual(conflict["reason"], "TOTAL_CONFLICT")
        self.assertEqual(malformed["reason"], "TOTAL_INVALID")


class ReconciliationTests(unittest.TestCase):
    def test_monotone_merge_across_out_of_order_arrival(self):
        shipped = event("m-ship", "SHIPPED", "2026-10-02T12:00:00+00:00")
        ordered = event("m-order", "ORDERED", "2026-10-01T12:00:00+00:00", total="12.34")
        row, reason = orders.reconcile([shipped, ordered], now=NOW)
        self.assertIsNone(reason)
        self.assertEqual(row["Status"], "SHIPPED")
        self.assertEqual(row["Grand Total"], "12.34")
        self.assertEqual(row["Ordered At"], "2026-10-01T12:00:00+00:00")

    def test_later_regression_total_url_and_duplicate_event_conflicts(self):
        shipped = event("m-ship", "SHIPPED", "2026-10-02T12:00:00+00:00")
        later_ordered = event("m-order", "ORDERED", "2026-10-03T12:00:00+00:00", total="12.34")
        row, reason = orders.reconcile([shipped, later_ordered], now=NOW)
        self.assertEqual((row, reason), ({"Status": "REVIEW", "Needs Review": True}, "LIFECYCLE_REGRESSION"))
        old = {"Status": "ORDERED", "Grand Total": "11.00"}
        row, reason = orders.reconcile([event("m-order", "ORDERED", "2026-10-01T12:00:00+00:00", total="12.34")], old, NOW)
        self.assertEqual(reason, "TOTAL_CONFLICT")
        old_url = {"Status": "ORDERED", "Amazon Order URL": "https://example.com/wrong"}
        self.assertEqual(orders.reconcile([event("m-order", "ORDERED", "2026-10-01T12:00:00+00:00")], old_url, NOW)[1], "URL_CONFLICT")
        first = event("same", "ORDERED", "2026-10-01T12:00:00+00:00", total="12.34")
        second = {**first, "grand_total": "19.00"}
        self.assertEqual(orders.reconcile([first, second], now=NOW)[1], "DUPLICATE_EVENT_CONFLICT")
        shipment_with_amount = event("m-ship", "SHIPPED", "2026-10-02T12:00:00+00:00", total="99.00")
        self.assertIsNone(orders.reconcile([shipment_with_amount], now=NOW)[0]["Grand Total"])

    def test_idempotent_row_equality_ignores_only_reconciled_timestamp(self):
        first = event("m-1", "ORDERED", "2026-10-01T12:00:00+00:00", total="12.34")
        row, _ = orders.reconcile([first], now=NOW)
        same, reason = orders.reconcile([first], row, now=datetime(2026, 10, 3, 13, tzinfo=timezone.utc))
        self.assertIsNone(reason)
        self.assertTrue(orders.same(row, same))


class StageTests(unittest.TestCase):
    def test_live_persists_and_reads_back_before_filing(self):
        trace = []
        gmail = AmazonGmail({"m-1": message("m-1", "auto-confirm")}, trace=trace)
        notion = AmazonNotion(trace=trace)
        counts = stage.run(10, True, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertEqual((counts["listed"], counts["accepted"], counts["orders_new"], counts["filed"]), (1, 1, 1, 1))
        self.assertLess(trace.index(("notion", "GET")), trace.index(("gmail", "modify")))
        self.assertLess(trace.index(("notion", "POST_ONCE")), trace.index(("gmail", "modify")))
        self.assertLess(trace.index(("gmail", "modify")), trace.index(("gmail", "labels")))
        query = next(call[1] for call in gmail.calls if call[0] == "list")
        self.assertIn("in:inbox", query)
        self.assertIn("-label:Amazon", query)
        self.assertEqual(query.count("from:"), 3)
        self.assertIn("label-amazon", gmail.messages["m-1"]["label_ids"])
        self.assertNotIn("INBOX", gmail.messages["m-1"]["label_ids"])

    def test_readback_mismatch_leaves_message_unfiled(self):
        private_subject = "Example synthetic subject 4821"
        gmail = AmazonGmail({"m-1": message("m-1", "auto-confirm", subject=private_subject)})
        counts = None
        with self.assertRaises(stage.AmazonError) as caught:
            counts = stage.run(10, True, environ=TOKEN, gmail=gmail, notion=AmazonNotion(mismatch=True), now=NOW)
        self.assertIn("AMAZON_FAILED:1of1:AMAZON_NOTION_READBACK_MISMATCH", str(caught.exception))
        self.assertNotIn(private_subject, str(caught.exception))
        self.assertIn("INBOX", gmail.messages["m-1"]["label_ids"])
        self.assertNotIn("modify", [call[0] for call in gmail.calls])

    def test_dry_run_has_counts_only_and_makes_no_writes(self):
        gmail = AmazonGmail({"m-1": message("m-1", "auto-confirm")})
        notion = AmazonNotion()
        output = stage.run(10, False, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertEqual(output["orders_new"], 1)
        self.assertFalse(any(call[0] in ("POST_ONCE", "PATCH_ONCE") for call in notion.calls))
        self.assertNotIn("modify", [call[0] for call in gmail.calls])
        self.assertNotIn("Example item", str(output))

    def test_review_message_is_not_filed_and_known_order_conflict_updates_only_review_fields(self):
        gmail = AmazonGmail({"m-bad": message("m-bad", "auto-confirm", body="no id")})
        output = stage.run(10, True, environ=TOKEN, gmail=gmail, notion=AmazonNotion(), now=NOW)
        self.assertEqual(output["review"], 1)
        self.assertNotIn("modify", [call[0] for call in gmail.calls])

        prior = orders.reconcile([event("old", "ORDERED", "2026-10-01T12:00:00+00:00", total="12.34"),
                                  event("ship", "SHIPPED", "2026-10-02T12:00:00+00:00")], now=NOW)[0]
        page = notion_page(prior["Order ID"], prior, "page-old")
        gmail2 = AmazonGmail({"m-regress": message("m-regress", "auto-confirm", at="2026-10-03T12:00:00+00:00")})
        notion2 = AmazonNotion({prior["Order ID"]: page})
        output2 = stage.run(10, True, environ=TOKEN, gmail=gmail2, notion=notion2, now=NOW)
        self.assertEqual(output2["review"], 1)
        patch_call = next(call for call in notion2.calls if call[0] == "PATCH_ONCE")
        self.assertEqual(set(patch_call[2]["properties"]), {"Status", "Needs Review"})
        self.assertNotIn("modify", [call[0] for call in gmail2.calls])

    def test_duplicate_notion_orders_fail_closed(self):
        msg = message("m-1", "auto-confirm")
        row, _ = orders.reconcile([events.extract(msg)], now=NOW)
        notion = AmazonNotion({row["Order ID"]: notion_page(row["Order ID"], row, "page-1")}, duplicates={row["Order ID"]})
        gmail = AmazonGmail({"m-1": msg})
        with self.assertRaises(stage.AmazonError):
            stage.run(10, True, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertNotIn("modify", [call[0] for call in gmail.calls])

    def test_notion_schema_or_identity_mismatch_stops_before_any_write(self):
        msg = message("m-1", "auto-confirm")
        gmail = AmazonGmail({"m-1": msg})
        notion = AmazonNotion()
        notion.schema.pop("Needs Review")
        with self.assertRaises(stage.AmazonError) as caught:
            stage.run(10, True, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertIn("AMAZON_NOTION_SCHEMA_MISMATCH", str(caught.exception))
        self.assertFalse(any(call[0] in ("POST_ONCE", "PATCH_ONCE") for call in notion.calls))
        self.assertNotIn("modify", [call[0] for call in gmail.calls])

        row, _ = orders.reconcile([events.extract(msg)], now=NOW)
        wrong_page = notion_page(row["Order ID"], row, "page-1")
        wrong_page["properties"]["Order ID"]["title"][0]["plain_text"] = "different-order"
        wrong_page["properties"]["Order ID"]["title"][0]["text"]["content"] = "different-order"
        mismatch = AmazonNotion({row["Order ID"]: wrong_page})
        gmail2 = AmazonGmail({"m-1": msg})
        with self.assertRaises(stage.AmazonError) as caught2:
            stage.run(10, True, environ=TOKEN, gmail=gmail2, notion=mismatch, now=NOW)
        self.assertIn("AMAZON_NOTION_QUERY_MISMATCH", str(caught2.exception))
        self.assertNotIn("modify", [call[0] for call in gmail2.calls])

    def test_idempotent_rerun_does_not_repeat_filing(self):
        gmail = AmazonGmail({"m-1": message("m-1", "auto-confirm")})
        notion = AmazonNotion()
        first = stage.run(10, True, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        second = stage.run(10, True, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertEqual(first["filed"], 1)
        self.assertEqual(second["filed"], 0)
        self.assertEqual(second["listed"], 0)

    def test_review_readback_change_to_unrelated_property_is_rejected(self):
        prior = orders.reconcile([event("old", "ORDERED", "2026-10-01T12:00:00+00:00", total="12.34"),
                                  event("ship", "SHIPPED", "2026-10-02T12:00:00+00:00")], now=NOW)[0]
        page = notion_page(prior["Order ID"], prior, "page-old")
        gmail = AmazonGmail({"m-regress": message("m-regress", "auto-confirm", at="2026-10-04T12:00:00+00:00")})
        notion = AmazonNotion({prior["Order ID"]: page})
        original = notion.call_once
        def changed(method, path, body=None):
            result = original(method, path, body)
            if method == "PATCH":
                notion.rows[prior["Order ID"]]["properties"]["Item Summary"]["rich_text"] = []
            return result
        notion.call_once = changed
        with self.assertRaises(stage.AmazonError) as caught:
            stage.run(10, True, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertIn("AMAZON_NOTION_READBACK_MISMATCH", str(caught.exception))
        self.assertNotIn("modify", [call[0] for call in gmail.calls])

    def test_incomplete_gmail_read_does_not_start_any_notion_write_or_filing(self):
        gmail = AmazonGmail({"m-1": message("m-1", "auto-confirm"), "m-2": message("m-2", "shipment-tracking")}, fail_read="m-2")
        notion = AmazonNotion()
        with self.assertRaises(stage.AmazonError):
            stage.run(10, True, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertFalse(any(call[0] in ("POST_ONCE", "PATCH_ONCE") for call in notion.calls))
        self.assertNotIn("modify", [call[0] for call in gmail.calls])


class GmailClientTests(unittest.TestCase):
    def test_list_complete_detects_bound_overflow_and_pagination(self):
        gmail = Gmail("i", "s", "r")
        pages = [{"messages": [{"id": "m1"}], "nextPageToken": "next"}, {"messages": [{"id": "m2"}]}]
        gmail._request = lambda *args: pages.pop(0)
        self.assertEqual(gmail.list_ids_complete("q", 2), ["m1", "m2"])
        pages = [{"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "next"}]
        gmail._request = lambda *args: pages.pop(0)
        with self.assertRaises(GmailError) as caught:
            gmail.list_ids_complete("q", 1)
        self.assertEqual(str(caught.exception), "GMAIL_LIST_LIMIT_EXCEEDED")
        gmail._request = lambda *args: {"messages": None}
        with self.assertRaises(GmailError) as caught:
            gmail.list_ids_complete("q", 1)
        self.assertEqual(str(caught.exception), "GMAIL_LISTING_INCOMPLETE")

    def test_message_record_extracts_plain_or_readable_html_without_persisting_body(self):
        gmail = Gmail("i", "s", "r")
        import base64
        encode = lambda text: base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
        gmail._request = lambda *args: {"payload": {"headers": [{"name": "From", "value": sender("auto-confirm")},
                                                                      {"name": "Subject", "value": "Example item"}],
                                                   "mimeType": "text/html", "body": {"data": encode("<p>Example</p>")}},
                                        "internalDate": "1791028800000", "labelIds": ["INBOX"]}
        row = gmail.message_record("m")
        self.assertEqual(row["body_text"], "Example")
        self.assertEqual(row["subject"], "Example item")
        self.assertEqual(row["label_ids"], ["INBOX"])

    def test_amazon_filing_changes_only_the_existing_label_and_inbox(self):
        gmail = Gmail("i", "s", "r")
        calls = []
        gmail._request = lambda method, url, body=None, **kw: calls.append((method, url, body)) or {}
        gmail.apply_amazon("synthetic-id", "Label_123")
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/messages/synthetic-id/modify"))
        self.assertEqual(calls[0][2], {"addLabelIds": ["Label_123"], "removeLabelIds": ["INBOX"]})


if __name__ == "__main__":
    unittest.main()
