"""A synthetic Notion block tree for the floating Hiring Pipeline region and the page digest: GET block, GET children, PATCH a block's own content, PATCH-append table rows, DELETE.
Everything outside those calls is a test failure. `trip` lets a test change some other block while a write is in flight."""
from copy import deepcopy

from lifeos.platform.notion_client import NotionError


def rich(text, link=None):
    part = {"plain_text": text, "text": {"content": text}}
    if link:
        part["text"]["link"] = {"url": link}
    return [part]


class Tree:
    def __init__(self):
        self.blocks, self.kids, self.log, self.n = {}, {}, [], 0
        self.trip = None                              # (method fragment, callback) fired after a matching write
        self.fail_write = None                        # method fragment that raises

    def add(self, block_id, kind, parent, content=None, text=None, index=None, **extra):
        body = content if content is not None else ({"rich_text": rich(text)} if text is not None else {})
        block = {"id": block_id, "type": kind, "has_children": False, "parent": {"type": "page_id", "page_id": parent} if parent == "page-1" else {"type": "block_id", "block_id": parent}, kind: body, **extra}
        self.blocks[block_id] = block
        siblings = self.kids.setdefault(parent, [])
        siblings.insert(len(siblings) if index is None else index, block_id)
        if parent in self.blocks:
            self.blocks[parent]["has_children"] = True
        return block

    def row(self, row_id, table, cells):
        return self.add(row_id, "table_row", table, content={"cells": [rich(*c) if isinstance(c, tuple) else rich(c) for c in cells]})

    def table(self, table_id, parent, rows, width=4, header=True, index=None):
        self.add(table_id, "table", parent, content={"table_width": width, "has_column_header": header}, index=index)
        for n, cells in enumerate(rows):
            self.row(f"{table_id}-r{n}", table_id, cells)

    def snapshot(self, skip=()):
        return {k: deepcopy(v) for k, v in self.blocks.items() if k not in skip}

    # --- the API ---
    def call(self, method, path, body=None):
        self.log.append((method, path))
        if self.fail_write and method != "GET" and self.fail_write in path:
            raise RuntimeError("boom")
        route = path.split("?")[0]
        parts = route.strip("/").split("/")
        if len(parts) > 1 and parts[1] not in self.blocks and parts[1] != "page-1":
            raise NotionError("NOTION_HTTP_404")
        if method == "GET" and parts[0] == "blocks" and len(parts) == 2:
            return deepcopy(self.blocks[parts[1]])
        if method == "GET" and parts[0] == "blocks" and parts[2] == "children":
            return {"results": [deepcopy(self.blocks[i]) for i in self.kids.get(parts[1], [])], "has_more": False}
        if method == "PATCH" and len(parts) == 2:
            block = self.blocks[parts[1]]
            kind = block["type"]
            if kind not in body:
                raise AssertionError("a block keeps its type")
            if kind == "table_row":
                block[kind] = {"cells": [[{"plain_text": r["text"]["content"], "text": r["text"]} for r in cell] for cell in body[kind]["cells"]]}
            else:
                block[kind] = {**block[kind], "rich_text": [{"plain_text": r["text"]["content"], "text": r["text"]} for r in body[kind]["rich_text"]]}
            self._tripped(method, path)
            return deepcopy(block)
        if method == "DELETE" and len(parts) == 2:
            block = self.blocks.pop(parts[1])
            self.kids[block["parent"].get("block_id") or block["parent"]["page_id"]].remove(parts[1])
            self._tripped(method, path)
            return {}
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None):
        self.log.append((method, path))
        if self.fail_write and self.fail_write in path:
            raise RuntimeError("boom")
        parts = path.strip("/").split("/")
        if method == "PATCH" and parts[2] == "children":
            made = []
            for child in body["children"]:
                self.n += 1
                row_id = f"new-{self.n}"
                block = self.add(row_id, "table_row", parts[1], content={"cells": []})
                block["table_row"]["cells"] = [[{"plain_text": part["text"]["content"], "text": part["text"]} for part in cell] for cell in child["table_row"]["cells"]]
                made.append(deepcopy(block))
            self._tripped(method, path)
            return {"results": made}
        raise AssertionError((method, path))

    def _tripped(self, method, path):
        if self.trip and self.trip[0] in path:
            callback, self.trip = self.trip[1], None
            callback(self)
