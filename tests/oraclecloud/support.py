"""A synthetic Daily Report holding the Jira card (in a column, so the page is found by climbing) and the System callout
(heading, one 5-column table of.
"""
from copy import deepcopy

from lifeos.oraclecloud import system_table as st

PAGE = "page"


def seg(text, color=None, bold=False):
    return {"type": "text", "plain_text": text, "text": {"content": text, "link": None}, "annotations": {"bold": bold, "color": color or "default"}}


def cells(row):
    """[(text, colour)] per column -> table_row cells."""
    return [[seg(t, None if c == "bold" else c, c == "bold") for t, c in col] for col in row]


def plain_row(label, usage="██████████", reading="1%", resets="—", status="OK", color="green"):
    return [[(label, None)], [(usage, "gray")], [(reading, None)], [(resets, None)], [(status, color)]]


class SystemPage:
    def __init__(self, heading="System (11/24 · 11:01 PM CT)", rows=None, extra_callout=False, table_rows=17):
        self.blocks, self.kids, self.patches, self.log = {}, {}, [], []
        self.pages = {}  # System health rows: page id -> properties
        self.add("col", "column", {"type": "block_id", "block_id": "cl"})
        self.add("cl", "column_list", {"type": "page_id", "page_id": PAGE})
        self.kids["cl"] = ["col"]
        self.blocks["jira"] = {"id": "jira", "type": "callout", "parent": {"type": "block_id", "block_id": "col"}, "callout": {"rich_text": []}}
        self.kids["col"] = ["jira"]
        self.kids[PAGE] = ["cl", "dividers"]
        self.blocks["dividers"] = {"id": "dividers", "type": "divider", "parent": {"type": "page_id", "page_id": PAGE}, "divider": {}}
        self.add_callout("sys", heading, rows or default_rows(), table_rows)
        if extra_callout:
            self.add_callout("sys2", heading, rows or default_rows(), table_rows)

    def add(self, block_id, kind, parent):
        self.blocks[block_id] = {"id": block_id, "type": kind, "parent": parent, kind: {}}

    def add_callout(self, cid, heading, rows, table_rows):
        parent = {"type": "page_id", "page_id": PAGE}
        self.blocks[cid] = {"id": cid, "type": "callout", "parent": parent, "callout": {"rich_text": []}}
        self.kids[PAGE].append(cid)
        head = {"id": cid + "-h", "type": "heading_3", "parent": {"type": "block_id", "block_id": cid}, "heading_3": {"rich_text": [seg(heading, bold=True)]}}
        table = {"id": cid + "-t", "type": "table", "parent": {"type": "block_id", "block_id": cid}, "table": {"table_width": 5, "has_column_header": True}}
        self.blocks[head["id"]], self.blocks[table["id"]] = head, table
        self.kids[cid] = [head["id"], table["id"]]
        self.kids[table["id"]] = []
        for i, row in enumerate(rows[:table_rows]):
            rid = f"{cid}-r{i}"
            self.blocks[rid] = {"id": rid, "type": "table_row", "parent": {"type": "block_id", "block_id": table["id"]}, "table_row": {"cells": cells(row)}}
            self.kids[table["id"]].append(rid)

    def row_text(self, cid="sys"):
        return [[("".join(i["plain_text"] for i in cell)) for cell in self.blocks[r]["table_row"]["cells"]] for r in self.kids[cid + "-t"]]

    def plain(self, block_id):
        b = self.blocks[block_id]
        return "".join(i["plain_text"] for i in b[b["type"]]["rich_text"])

    # the client surface the writers use
    def call(self, method, path, body=None):
        parts = path.split("?")[0].strip("/").split("/")
        if method == "GET" and parts[0] == "blocks" and len(parts) == 2:
            return deepcopy(self.blocks[parts[1]])
        if method == "GET" and parts[0] == "blocks" and parts[2:] == ["children"]:
            return {"results": [deepcopy(self.blocks[i]) for i in self.kids.get(parts[1], [])], "has_more": False}
        if method == "POST" and parts[0] == "data_sources" and parts[2:] == ["query"]:
            return {"results": [deepcopy(p) for p in self.pages.values() if not p.get("in_trash")], "has_more": False}
        if method == "PATCH" and parts[0] == "pages":
            page = self.pages[parts[1]]
            if body.get("in_trash"):
                page["in_trash"] = True
            for name, value in (body.get("properties") or {}).items():
                page["properties"][name] = value if "select" not in value else {"type": "select", "select": value["select"]}
                if "rich_text" in value:
                    page["properties"][name] = {"type": "rich_text", "rich_text": [{"plain_text": i["text"]["content"]} for i in value["rich_text"]]}
            self.log.append(("PAGE", parts[1], deepcopy(body)))
            return deepcopy(page)
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None, **_):
        parts = path.strip("/").split("/")
        if method != "PATCH" or parts[0] != "blocks" or len(parts) != 2:
            raise AssertionError((method, path))
        block = self.blocks[parts[1]]
        kind = block["type"]
        if kind == "table_row":
            if set(body) != {"table_row"} or set(body["table_row"]) != {"cells"}:
                raise AssertionError("only a row's own cells are written")
            block["table_row"]["cells"] = [[seg(i["text"]["content"], (i.get("annotations") or {}).get("color"), bool((i.get("annotations") or {}).get("bold"))) for i in cell] for cell in body["table_row"]["cells"]]
        else:
            if set(body) != {kind} or set(body[kind]) != {"rich_text"}:
                raise AssertionError("only a block's own rich text is written")
            block[kind]["rich_text"] = [seg(i["text"]["content"], (i.get("annotations") or {}).get("color"), bool((i.get("annotations") or {}).get("bold"))) for i in body[kind]["rich_text"]]
        self.patches.append((parts[1], deepcopy(body)))
        return deepcopy(block)

    def health(self, record, health, last_ok, source="Jira", workspace="Personal"):
        pid = "pg-" + record
        self.pages[pid] = {"id": pid, "properties": {"Record ID": {"type": "rich_text", "rich_text": [{"plain_text": record}]}, "Source": {"type": "title", "title": [{"plain_text": source}]},
                                                      "Workspace": {"type": "select", "select": {"name": workspace}}, "Health": {"type": "select", "select": {"name": health}},
                                                      "Last OK": {"type": "rich_text", "rich_text": [{"plain_text": last_ok}]}}}
        return pid


def default_rows():
    return [[[(h, "bold")] for h in st.HEADER]] + [plain_row(label) for label in st.LABELS]
