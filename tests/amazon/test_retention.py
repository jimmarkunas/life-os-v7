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
        counts = retention.run(100, False, environ=TOKEN, gmail=gmail, notion=notion)
        self.assertEqual((counts["candidates"], counts["recorded"], counts["kept_unrecorded"], counts["trashed"]), (2, 1, 1, 0))
        self.assertEqual(gmail.trashed, [])

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
