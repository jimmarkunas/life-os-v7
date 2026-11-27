"""Synthetic Jira and Notion for the accountability tests (LIFE-671): fictional keys and text only."""
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.platform.jira import JiraError

NOW = datetime(2026, 11, 18, 9, 5, tzinfo=ZoneInfo("America/Chicago"))        # a Wednesday in Q4 2026


def rich(content, url=None):
    return [{"type": "text", "plain_text": content, "href": url, "text": {"content": content, "link": {"url": url} if url else None}}]


def para(block_id, content):
    return {"id": block_id, "type": "paragraph", "paragraph": {"rich_text": rich(content)}}


def summary_line(page_id):
    return {"id": "s-" + page_id, "type": "paragraph", "paragraph": {"rich_text": [
        {"type": "text", "plain_text": "Summary for Claude: ", "text": {"content": "Summary for Claude: "}},
        {"type": "mention", "plain_text": "page", "mention": {"type": "page", "page": {"id": page_id}}}]}}


def canonical(url):
    """What Notion does to an address it stores: characters it treats as safe come back unescaped (observed on the live page: %28 %29 %21 became ( ) !)."""
    return url.replace("%28", "(").replace("%29", ")").replace("%21", "!") if url else url


class Notion:
    """Just enough of the Notion API: blocks by id with children, PATCH of text and table rows, children insert."""

    def __init__(self):
        self.blocks, self.kids, self.writes, self.made = {}, {}, [], 0

    def add(self, parent, block):
        self.blocks[block["id"]] = block
        self.kids.setdefault(parent, []).append(block["id"])
        return block

    def callout(self, block_id, heading, stamp, middle, page, legacy=True):
        self.blocks[block_id] = {"id": block_id, "type": "callout"}
        self.add(block_id, {"id": block_id + "-h", "type": "heading_3", "heading_3": {"rich_text": rich(heading)}})
        self.add(block_id, para(block_id + "-st", stamp))
        for block in middle:
            self.add(block_id, block)
        if legacy:
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

    def index_page(self, key, title, lines):
        """A register row `key` pointing at an index page titled `title` whose bullets are `lines` [(text, link)]."""
        self.indexes = getattr(self, "indexes", {})
        page = f"ix-{key}"
        self.indexes[key] = page
        self.blocks[page] = {"id": page, "type": "page", "object": "page", "properties": {"title": {"type": "title", "title": rich(title)}}}
        for i, (content, url) in enumerate(lines):
            self.add(page, {"id": f"{page}-b{i}", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich(content, url)}})

    def text(self, block_id):
        block = self.blocks[block_id]
        return "".join(p["plain_text"] for p in block[block["type"]]["rich_text"])

    def call(self, method, path, body=None, *args, **kwargs):
        if method == "GET" and "/children" in path:
            block_id = path.split("/blocks/")[1].split("/")[0]
            return {"results": [self.blocks[k] for k in self.kids.get(block_id, [])], "has_more": False}
        if method == "GET" and path.startswith("/pages/"):
            return self.blocks[path.split("/pages/")[1]]
        if method == "GET" and path.startswith("/blocks/"):
            return self.blocks[path.split("/blocks/")[1]]
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None, *args, **kwargs):
        assert method in ("PATCH", "DELETE"), (method, path)
        block_id = path.split("/blocks/")[1].split("/")[0]
        self.writes.append(block_id)
        if method == "DELETE":
            for order in self.kids.values():
                if block_id in order:
                    order.remove(block_id)
            self.deleted = getattr(self, "deleted", []) + [block_id]
            return {"id": block_id}
        if path.endswith("/children"):
            after = body["after"]
            for child in body["children"]:
                self.made += 1
                new = {"id": f"{block_id}-new{self.made}", "type": child["type"], child["type"]: {"rich_text": [rich(n["text"]["content"], (n["text"].get("link") or {}).get("url"))[0] for n in child[child["type"]]["rich_text"]]}}
                self.blocks[new["id"]] = new
                order = self.kids[block_id]
                order.insert(order.index(after) + 1, new["id"])
                after = new["id"]
                for inner in child[child["type"]].get("children") or []:
                    self.made += 1
                    made = {"id": f"{new['id']}-in{self.made}", "type": inner["type"], inner["type"]: {"rich_text": [rich(n["text"]["content"], (n["text"].get("link") or {}).get("url"))[0] for n in inner[inner["type"]]["rich_text"]]}}
                    self.blocks[made["id"]] = made
                    self.kids.setdefault(new["id"], []).append(made["id"])
            return {"results": []}
        block = self.blocks[block_id]
        if block["type"] == "table_row":
            block["table_row"]["cells"] = [[rich(n["text"]["content"], canonical((n["text"].get("link") or {}).get("url")))[0] for n in c] for c in body["table_row"]["cells"]]
        else:
            block[block["type"]] = {"rich_text": [rich(n["text"]["content"], canonical((n["text"].get("link") or {}).get("url")))[0] for n in body[block["type"]]["rich_text"]]}
        return block

    def links(self, block_id):
        """[(text, link)] the way a reader sees the block: pieces with their link."""
        return [(i["plain_text"], i["href"]) for i in self.blocks[block_id][self.blocks[block_id]["type"]]["rich_text"]]


class Jira:
    """JQL answered by substring of the query; `down` makes every read fail like an unreachable Jira."""

    def __init__(self, answers, down=False, remote=None):
        self.answers, self.down, self.queries, self.remote = answers, down, [], remote or {}

    def get(self, path, params=None):
        if self.down:
            raise JiraError("JIRA_NETWORK")
        if path.endswith("/remotelink"):
            return [{"object": {"url": u}} for u in self.remote.get(path.split("/")[-2], [])]
        jql = params["jql"]
        self.queries.append(jql)
        for needle, issues in self.answers.items():
            if needle in jql:
                return {"issues": issues, "isLast": True}
        return {"issues": [], "isLast": True}


def child(key, parent, done, resolved=None, subtask=False, resolution="Done", labels=()):
    return {"key": key, "fields": {"parent": {"key": parent}, "issuetype": {"name": "Task", "subtask": subtask},
                                   "status": {"statusCategory": {"key": "done" if done else "indeterminate"}}, "resolutiondate": resolved,
                                   "resolution": {"name": resolution} if done else None, "labels": list(labels)}}


def epic(key, name):
    return {"key": key, "fields": {"summary": name}}
