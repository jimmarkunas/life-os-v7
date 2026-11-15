"""Synthetic Jira and Notion for the accountability tests (LIFE-671): fictional keys and text only."""
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.platform.jira import JiraError

NOW = datetime(2026, 11, 18, 9, 5, tzinfo=ZoneInfo("America/Chicago"))        # a Wednesday in Q4 2026


def rich(content):
    return [{"type": "text", "plain_text": content, "text": {"content": content}}]


def para(block_id, content):
    return {"id": block_id, "type": "paragraph", "paragraph": {"rich_text": rich(content)}}


def summary_line(page_id):
    return {"id": "s-" + page_id, "type": "paragraph", "paragraph": {"rich_text": [
        {"type": "text", "plain_text": "Summary for Claude: ", "text": {"content": "Summary for Claude: "}},
        {"type": "mention", "plain_text": "page", "mention": {"type": "page", "page": {"id": page_id}}}]}}


class Notion:
    """Just enough of the Notion API: blocks by id with children, PATCH of text and table rows, children insert."""

    def __init__(self):
        self.blocks, self.kids, self.writes = {}, {}, []

    def add(self, parent, block):
        self.blocks[block["id"]] = block
        self.kids.setdefault(parent, []).append(block["id"])
        return block

    def callout(self, block_id, heading, stamp, middle, page):
        self.blocks[block_id] = {"id": block_id, "type": "callout"}
        self.add(block_id, {"id": block_id + "-h", "type": "heading_3", "heading_3": {"rich_text": rich(heading)}})
        self.add(block_id, para(block_id + "-st", stamp))
        for block in middle:
            self.add(block_id, block)
        self.add(block_id, summary_line(page))
        self.blocks[page] = {"id": page, "type": "page"}
        self.add(page, para(page + "-p", "old summary"))

    def table(self, table_id, rows):
        block = {"id": table_id, "type": "table", "table": {"table_width": 2}}
        self.blocks[table_id] = block
        for i, (a, b) in enumerate([("Lane", "Done / all")] + rows):
            self.add(table_id, {"id": f"{table_id}-r{i}", "type": "table_row", "table_row": {"cells": [rich(a), rich(b)]}})
        return block

    def toggle(self, toggle_id, inner):
        block = {"id": toggle_id, "type": "toggle", "toggle": {"rich_text": rich("Q4 2026 (current)")}}
        self.blocks[toggle_id] = block
        self.add(toggle_id, para(toggle_id + "-in", inner))
        return block

    def text(self, block_id):
        block = self.blocks[block_id]
        return "".join(p["plain_text"] for p in block[block["type"]]["rich_text"])

    def call(self, method, path, body=None, *args, **kwargs):
        if method == "GET" and "/children" in path:
            block_id = path.split("/blocks/")[1].split("/")[0]
            return {"results": [self.blocks[k] for k in self.kids.get(block_id, [])], "has_more": False}
        if method == "GET" and path.startswith("/blocks/"):
            return self.blocks[path.split("/blocks/")[1]]
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None, *args, **kwargs):
        assert method == "PATCH", (method, path)
        block_id = path.split("/blocks/")[1].split("/")[0]
        self.writes.append(block_id)
        if path.endswith("/children"):
            for child in body["children"]:
                new = {"id": block_id + "-new", "type": child["type"], child["type"]: {"rich_text": rich(child[child["type"]]["rich_text"][0]["text"]["content"])}}
                self.blocks[new["id"]] = new
                order = self.kids[block_id]
                order.insert(order.index(body["after"]) + 1, new["id"])
            return {"results": []}
        block = self.blocks[block_id]
        if block["type"] == "table_row":
            block["table_row"]["cells"] = [rich(c[0]["text"]["content"]) for c in body["table_row"]["cells"]]
        else:
            block[block["type"]] = {"rich_text": rich(body[block["type"]]["rich_text"][0]["text"]["content"])}
        return block


class Jira:
    """JQL answered by substring of the query; `down` makes every read fail like an unreachable Jira."""

    def __init__(self, answers, down=False):
        self.answers, self.down, self.queries = answers, down, []

    def get(self, path, params=None):
        if self.down:
            raise JiraError("JIRA_NETWORK")
        jql = params["jql"]
        self.queries.append(jql)
        for needle, issues in self.answers.items():
            if needle in jql:
                return {"issues": issues, "isLast": True}
        return {"issues": [], "isLast": True}


def child(key, parent, done, resolved=None, subtask=False):
    return {"key": key, "fields": {"parent": {"key": parent}, "issuetype": {"name": "Task", "subtask": subtask},
                                   "status": {"statusCategory": {"key": "done" if done else "indeterminate"}}, "resolutiondate": resolved}}


def epic(key, name):
    return {"key": key, "fields": {"summary": name}}
