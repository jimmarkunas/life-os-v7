import unittest
from datetime import datetime, timezone

from lifeos.amazon import retention, stage
from lifeos.platform.gmail import Gmail
from tests.kit.amazon import AmazonGmail, AmazonNotion, message

TOKEN = {"NOTION_AMAZON_TOKEN": "synthetic", "NOTION_AMAZON_DATA_SOURCE_ID": "source-example"}
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


class RetentionGmail(AmazonGmail):
    def __init__(self, messages):
        super().__init__(messages)
        self.trashed = []

    def list_ids(self, query, limit=5000):
        self.calls.append(("list_ids", query, limit))
        return [key for key, row in self.messages.items() if "label-amazon" in row["label_ids"]][:limit]

    def trash(self, message_id):
        self.trashed.append(message_id)
        self.messages[message_id]["label_ids"].append("TRASH")


def recorded_store():
    """One recorded order (m-1) and one message that the order row does not list (m-2), both under the Amazon label."""
    gmail = RetentionGmail({"m-1": message("m-1", "auto-confirm"), "m-2": message("m-2", "order-update", order_id="999-9999999-9999999")})
    notion = AmazonNotion()
    stage.run(10, True, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
    gmail.messages["m-2"]["label_ids"] = ["label-amazon"]                 # filed by a Gmail filter, never recorded
    notion.rows.pop("999-9999999-9999999", None)
    gmail.trashed.clear()
    return gmail, notion


class RetentionTests(unittest.TestCase):
    def test_query_is_the_allowlist_plus_the_label_plus_thirty_days(self):
        gmail, notion = recorded_store()
        retention.run(100, False, environ=TOKEN, gmail=gmail, notion=notion)
        query = next(call[1] for call in gmail.calls if call[0] == "list_ids")
        self.assertIn("label:Amazon", query)
        self.assertIn("older_than:30d", query)
        self.assertEqual(query.count("from:"), 3)

    def test_dry_run_counts_and_trashes_nothing(self):
        gmail, notion = recorded_store()
        counts = retention.run(100, False, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertEqual((counts["candidates"], counts["recorded"], counts["kept_unrecorded"], counts["trashed"]), (2, 1, 1, 0))
        self.assertEqual(gmail.trashed, [])
        self.assertEqual((counts["no_row"], counts["no_order_id"], counts["not_listed"]), (1, 0, 0))     # m-2's order was never recorded
        self.assertEqual(counts["oldest_days"], 0)

    def test_kept_mail_is_counted_by_fixed_reason_never_by_content(self):
        gmail, notion = recorded_store()
        counts = retention.run(100, False, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertEqual(counts["kept_detail"], {"no_row:": 1})
        self.assertNotIn("999-9999999-9999999", str(counts))

    def test_the_default_batch_is_two_hundred(self):
        self.assertEqual(retention.DEFAULT_LIMIT, 200)

    def test_live_trashes_only_mail_whose_order_row_lists_it(self):
        gmail, notion = recorded_store()
        counts = retention.run(100, True, environ=TOKEN, gmail=gmail, notion=notion)
        self.assertEqual(gmail.trashed, ["m-1"])
        self.assertEqual((counts["trashed"], counts["kept_unrecorded"], counts["failed"]), (1, 1, 0))

    def test_replay_after_trashing_changes_nothing(self):
        gmail, notion = recorded_store()
        retention.run(100, True, environ=TOKEN, gmail=gmail, notion=notion)
        counts = retention.run(100, True, environ=TOKEN, gmail=gmail, notion=notion)
        self.assertEqual(gmail.trashed, ["m-1"])
        self.assertEqual(counts["trashed"], 0)

    def test_the_trash_call_is_a_trash_not_a_delete(self):
        seen = []
        gmail = Gmail("a", "b", "c")
        gmail._request = lambda method, url, **k: seen.append((method, url)) or {}
        gmail.trash("m-1")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "POST")
        self.assertTrue(seen[0][1].endswith("/messages/m-1/trash"))


A, B = "111-1111111-1111111", "222-2222222-2222222"
LINKS = f"https://www.amazon.com/your-orders/order-details?orderID={A}\nhttps://www.amazon.com/your-orders/order-details?orderID={B}"


class MultiOrderTests(unittest.TestCase):
    def test_a_message_whose_every_order_is_one_of_its_own_links_yields_one_event_per_order_without_a_total(self):
        from lifeos.amazon import events
        got = events.extract_all(message("s1", "shipment-tracking", body=LINKS))
        self.assertEqual([(e["order_id"], e["status"], e["grand_total"]) for e in got], [(A, "SHIPPED", None), (B, "SHIPPED", None)])
        self.assertEqual(len(events.extract_all(message("o1", "auto-confirm", body=LINKS + "\nGrand Total: $50.00"))), 2)       # a total cannot belong to one of two orders

    def test_a_message_with_an_unlinked_order_number_or_too_many_stays_review(self):
        from lifeos.amazon import events
        for body in (LINKS + f"\nsee also 333-3333333-3333333", f"order {A} and order {B}", f"orderID={A}\nand {B}"):
            got = events.extract_all(message("x", "shipment-tracking", body=body))
            self.assertEqual([(e["status"], e["reason"]) for e in got], [("REVIEW", "ORDER_ID_AMBIGUOUS")])
        many = "\n".join(f"orderID=10{n}-1111111-1111111" for n in range(6))
        self.assertEqual(events.extract_all(message("y", "shipment-tracking", body=many))[0]["status"], "REVIEW")

    def test_both_orders_are_recorded_and_the_message_is_trashed_only_when_both_list_it(self):
        gmail = RetentionGmail({"m-1": message("m-1", "shipment-tracking", body=LINKS)})
        notion = AmazonNotion()
        stage.run(10, True, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertEqual(sorted(notion.rows), [A, B])
        gmail.messages["m-1"]["label_ids"] = ["label-amazon"]
        counts = retention.run(100, True, environ=TOKEN, gmail=gmail, notion=notion)
        self.assertEqual((counts["recorded"], counts["trashed"]), (1, 1))
        gmail.messages["m-1"]["label_ids"] = ["label-amazon"]
        from tests.kit.amazon import notion_page
        page = notion.rows[B]
        row = notion._row(page)
        row["Source Message IDs"] = ""
        notion.rows[B] = notion_page(B, row, page["id"])                  # one of the two orders does not list it: it stays
        counts = retention.run(100, True, environ=TOKEN, gmail=gmail, notion=notion)
        self.assertEqual((counts["recorded"], counts["kept_unrecorded"], counts["trashed"]), (0, 1, 0))


class ReviewAgeTests(unittest.TestCase):
    def store(self, at):
        from tests.kit.amazon import notion_page
        order = "555-5555555-5555555"
        gmail = RetentionGmail({"m-9": message("m-9", "order-update", order_id=order, at=at)})
        gmail.messages["m-9"]["label_ids"] = ["label-amazon"]
        notion = AmazonNotion()
        notion.rows[order] = notion_page(order, {"Status": "REVIEW", "Needs Review": True}, "page-9")          # a conflicted order: identity and the two review fields only
        return gmail, notion

    def test_mail_for_a_review_order_goes_to_the_trash_only_after_ninety_days(self):
        old = self.store("2026-06-01T12:00:00+00:00")                       # 124 days before NOW
        counts = retention.run(100, True, environ=TOKEN, gmail=old[0], notion=old[1], now=NOW)
        self.assertEqual((counts["trashed"], counts["review_aged"], counts["kept_unrecorded"]), (1, 1, 0))
        young = self.store("2026-08-01T12:00:00+00:00")                     # 63 days
        counts = retention.run(100, True, environ=TOKEN, gmail=young[0], notion=young[1], now=NOW)
        self.assertEqual((counts["trashed"], counts["kept_unrecorded"], counts["kept_detail"]), (0, 1, {"not_listed:REVIEW": 1}))

    def test_the_review_order_row_itself_is_never_touched(self):
        gmail, notion = self.store("2026-06-01T12:00:00+00:00")
        before = repr(notion.rows)
        retention.run(100, True, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertEqual(repr(notion.rows), before)
