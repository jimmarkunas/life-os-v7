import unittest

from lifeos.jobs import private_report as pr
from lifeos.platform.notion_client import NotionError

PARENT = pr.REPORTS_PAGE


class FakeClient:
    """Just enough Notion: the ledger's data source -> database -> page chain, search, page create (with its table children), and block children."""
    source = "s" * 32

    def __init__(self, parent=PARENT, existing=None, fail=False, lose_rows=0):
        self.parent, self.existing, self.fail, self.lose_rows, self.writes, self.blocks = parent, existing, fail, lose_rows, [], {}

    def call(self, method, path, body=None):
        if self.fail:
            raise NotionError("NOTION_HTTP_500")
        if path.startswith("/data_sources/"):
            return {"parent": {"type": "database_id", "database_id": "d" * 32}}
        if path.startswith("/databases/"):
            return {"parent": {"type": "page_id", "page_id": self.parent} if self.parent else {"type": "workspace"}}
        if path == "/search":
            return {"results": self.existing or []}
        base = path.split("?")[0]
        return {"results": self.blocks.get(base.split("/")[2], []), "has_more": False}

    def call_once(self, method, path, body=None, version=None):
        self.writes.append((method, path, body))
        tables = []
        for index, table in enumerate(body["children"]):
            rows = table["table"]["children"]
            self.blocks[f"t{index}"] = [{"id": f"r{n}"} for n in range(len(rows) - self.lose_rows)]
            tables.append({"id": f"t{index}", "type": "table"})
        self.blocks["page1"] = tables
        return {"id": "page1"}


HEADER = ["company", "link"]
ROWS = [["ExampleCo", "https://example.com/1"], ["OtherCo", "https://example.com/2"]]


class Private(unittest.TestCase):
    def test_a_dry_run_counts_and_writes_nothing(self):
        client = FakeClient()
        self.assertEqual(pr.publish(client, "T", HEADER, ROWS, False), {"rows": 2, "page": "dry", "failed": 0, "why": {}})
        self.assertEqual(client.writes, [])

    def test_a_live_run_writes_one_page_under_the_ledgers_parent_and_reads_the_rows_back(self):
        client = FakeClient()
        counts = pr.publish(client, "T", HEADER, ROWS, True, links=(1,))
        self.assertEqual((counts["page"], counts["failed"]), ("new", 0))
        method, path, body = client.writes[0]
        self.assertEqual((method, path, body["parent"]), ("POST", "/pages", {"type": "page_id", "page_id": PARENT}))
        cells = body["children"][0]["table"]["children"][1]["table_row"]["cells"]
        self.assertEqual(cells[1][0]["text"]["link"]["url"], "https://example.com/1")

    def test_a_page_that_already_exists_is_left_alone(self):
        found = [{"id": "p", "parent": {"page_id": PARENT}, "properties": {"title": {"title": [{"plain_text": "T"}]}}}]
        client = FakeClient(existing=found)
        self.assertEqual(pr.publish(client, "T", HEADER, ROWS, True)["page"], "present")
        self.assertEqual(client.writes, [])

    def test_a_notion_error_is_a_fixed_code(self):
        self.assertEqual(pr.publish(FakeClient(fail=True), "T", HEADER, ROWS, True)["why"], {"PRIVATE_REPORT_NOTION_FAILED": 1})

    def test_rows_that_do_not_read_back_are_reported(self):
        self.assertEqual(pr.publish(FakeClient(lose_rows=1), "T", HEADER, ROWS, True)["why"], {"PRIVATE_REPORT_READBACK_MISMATCH": 1})

    def test_long_reports_are_split_into_tables_each_with_the_header(self):
        blocks = pr.tables(HEADER, [["c", "l"]] * 200)
        self.assertEqual([len(b["table"]["children"]) for b in blocks], [91, 91, 21])


if __name__ == "__main__":
    unittest.main()
