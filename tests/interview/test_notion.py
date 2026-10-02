import copy
import unittest
from lifeos.interview import notion
from lifeos.interview.models import Ownership
from lifeos.platform.notion_client import NotionError

ENV = {"NOTION_INTERVIEW_TOKEN": "synthetic", "HIRING_PIPELINE_PAGE_ID": "root"}


def block(label, kind="paragraph", id="block", **extra):
    return {"id": id, "type": kind, kind: {"rich_text": [{"plain_text": label}]}, **extra}


def page(title="Hiring Pipeline", parent="root", **extra):
    return {"parent": {"page_id": parent}, "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}}, **extra}


class Fake:
    def __init__(self):
        self.pages = {"root": page(), "p": page(parent="root"), "c": page(parent="p")}
        self.blocks = {"root": [block("Active Opportunities", "heading_1"), {"id": "p", "type": "child_page", "child_page": {"title": "Example — Program Manager"}}],
                       "p": [block(notion.MARKERS["opportunity"]), block("Live Notes", "heading_2", "live"), block("private human notes", id="note"), block("Raw Notes", "heading_2", "raw")],
                       "c": [block(notion.MARKERS["round"]), block("Live Notes", "heading_2", "live"), block("Raw Notes", "heading_2", "raw")]}
        self.calls = []
        self.fail = set()
        self.more = False

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if method != "GET":
            raise AssertionError("mutation")
        if path in self.fail:
            raise NotionError("NOTION_NETWORK")
        key = path.split("/")[2].split("?")[0]
        if path.startswith("/pages/"):
            if key not in self.pages:
                raise NotionError("NOTION_HTTP_404")
            return copy.deepcopy(self.pages[key])
        results = self.blocks.get(key, [])
        return {"results": copy.deepcopy(results[:1] if "page_size=1" in path and "page_size=100" not in path else results), "has_more": self.more, "next_cursor": None}


class NotionTests(unittest.TestCase):
    def test_token_isolation(self):
        self.assertEqual(notion.environment({"NOTION_API_TOKEN": "jobs"})["NOTION_API_TOKEN"], "")
        self.assertEqual(notion.make_client(ENV).token, "synthetic")
        with self.assertRaises(NotionError):
            notion.make_client({"NOTION_API_TOKEN": "jobs"})

    def test_targets(self):
        client = Fake()
        self.assertEqual(notion.target_check(client, ENV), "target_ok")
        for value in (page("Wrong"), page(archived=True), page(in_trash=True)):
            client.pages["root"] = value
            self.assertEqual(notion.target_check(client, ENV), "target_mismatch")
        client.fail.add("/pages/root")
        self.assertEqual(notion.target_check(client, ENV), "target_unreadable")
        self.assertEqual(notion.target_check(client, {}), "target_config_missing")

    def test_ownership_first_block_only(self):
        client = Fake()
        self.assertEqual(notion.ownership(client, "p", "opportunity"), Ownership.MACHINE)
        self.assertEqual(client.calls[-1][1], "/blocks/p/children?page_size=1")
        for marker, expected in (("human", Ownership.HUMAN), ("v7-interview:2;owner=machine", Ownership.UNKNOWN), (notion.MARKERS["round"], Ownership.UNKNOWN)):
            client.blocks["p"][0] = block(marker)
            self.assertEqual(notion.ownership(client, "p", "opportunity"), expected)
        client.fail.add("/blocks/p/children?page_size=1")
        self.assertEqual(notion.ownership(client, "p", "opportunity"), Ownership.UNKNOWN)

    def test_readback_protected_contract(self):
        client = Fake()
        before = notion.protected_snapshot(client, "p")
        self.assertEqual(notion.readback(client, "p", "root", "opportunity", before), "readback_ok")
        client.blocks["p"][2] = block("changed", id="note")
        self.assertEqual(notion.readback(client, "p", "root", "opportunity", before), "readback_protected_changed")
        self.assertEqual(notion.readback(client, "p", "elsewhere", "opportunity"), "readback_parent_mismatch")
        client.blocks["p"].pop()
        self.assertEqual(notion.readback(client, "p", "root", "opportunity"), "readback_protected_missing")
        client.blocks["p"][0] = block("human")
        self.assertEqual(notion.readback(client, "p", "root", "opportunity"), "readback_marker_missing")
        self.assertEqual(notion.readback(client, "gone", "root", "opportunity"), "readback_page_gone")
        client.fail.add("/pages/p")
        self.assertEqual(notion.readback(client, "p", "root", "opportunity"), "readback_unreadable")

    def test_nested_protection_and_reordering(self):
        client = Fake()
        client.blocks["p"][1]["has_children"] = True
        client.blocks["live"] = [block("nested")]
        before = notion.protected_snapshot(client, "p")
        client.blocks["live"] = [block("nested change")]
        self.assertNotEqual(before, notion.protected_snapshot(client, "p"))
        before = notion.protected_snapshot(client, "p")
        client.blocks["p"] = [client.blocks["p"][0], client.blocks["p"][3], client.blocks["p"][1], client.blocks["p"][2]]
        self.assertNotEqual(before, notion.protected_snapshot(client, "p"))

    def test_incomplete_scans_fail_closed(self):
        client = Fake()
        client.more = True
        self.assertFalse(notion.parent_scan(client, "root").complete)
        self.assertFalse(notion.child_scan(client, "p").complete)
