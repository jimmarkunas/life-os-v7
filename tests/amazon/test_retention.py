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
        many = "\n".join(f"orderID={100+n}-1111111-1111111" for n in range(11))
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


class TotalLineTests(unittest.TestCase):
    def total(self, body):
        from lifeos.amazon import events
        return events.extract(message("t", "auto-confirm", body=body))

    def test_the_current_confirmation_layout_gives_a_total_and_subtotals_never_do(self):
        order = "114-5655798-1950618"
        self.assertEqual(self.total(f"Order #\n{order}\n\n* Item\n  Quantity: 1\n  19.45 USD\n\nTotal\n21.2 USD\n\n(c) 2025")["grand_total"], "21.20")
        self.assertIsNone(self.total(f"Order #\n{order}\n\nSubtotal\n19.45 USD")["grand_total"])
        self.assertEqual(self.total(f"Order {order}\nGrand Total: $12.34")["grand_total"], "12.34")
        self.assertEqual(self.total(f"Order {order}\nOrder Total: $9")["grand_total"], "9.00")

    def test_two_different_totals_in_one_message_still_conflict(self):
        got = self.total("Order 114-5655798-1950618\nTotal\n21.2 USD\nGrand Total: $30.00")
        self.assertEqual((got["status"], got["reason"]), ("REVIEW", "TOTAL_CONFLICT"))


class GroupedTotalTests(unittest.TestCase):
    BODY = ("Thanks for your order!\nOrder #\n{a}\n\nView or edit order\nhttps://www.amazon.com/your-orders/order-details?orderID={a}&ref_=x\n\nGrand Total:\n15.72 USD\n\n"
            "Order #\n{b}\n\nView\nhttps://www.amazon.com/your-orders/order-details?orderID={b}&ref_=x\n\nOrder #\n{b}\n\nOrder #\n{b}\n\nGrand Total:\n64.39 USD\n\n(c) Amazon")

    def test_a_confirmation_with_each_order_followed_by_its_total_gives_each_order_its_own_total(self):
        from lifeos.amazon import events
        got = events.extract_all(message("c1", "auto-confirm", body=self.BODY.format(a=A, b=B)))
        self.assertEqual([(e["order_id"], e["status"], e["grand_total"]) for e in got], [(A, "ORDERED", "15.72"), (B, "ORDERED", "64.39")])

    def test_an_order_number_after_the_last_total_or_shared_by_two_totals_is_not_guessed(self):
        from lifeos.amazon import events
        trailing = self.BODY.format(a=A, b=B) + f"\nSee also {A}"
        shared = self.BODY.format(a=A, b=A)
        for body in (trailing, shared):
            got = events.extract_all(message("c2", "auto-confirm", body=body))
            self.assertTrue(all(e.get("total") is None for e in got))
            self.assertNotEqual([e.get("grand_total") for e in got], ["15.72", "64.39"])
