"""A synthetic Job Ledger in Notion: pages with a Stable Job Key, a data source, and a body of blocks (a toggle holds its children). GET/PATCH(append)/DELETE only."""
from urllib.parse import unquote

from lifeos.jobs import ledger
from lifeos.platform.notion_client import NotionError

SOURCE = "00000000000000000000000000000abc"


class FakeLedger:
    source = SOURCE

    def __init__(self):
        self.pages, self.blocks, self.kids, self.n, self.writes, self.fail_get = {}, {}, {}, 0, [], False
        self.drop_appends = False

    def _new(self, kind, text, checked=None, children=()):
        self.n += 1
        bid = f"blk{self.n}"
        inner = {"rich_text": [{"plain_text": text}]}
        if checked is not None:
            inner["checked"] = checked
        self.blocks[bid] = {"id": bid, "type": kind, kind: inner, "has_children": bool(children)}
        self.kids[bid] = [self._new(c["type"], "".join(r["text"]["content"] for r in c[c["type"]]["rich_text"]), (c[c["type"]].get("checked")), c[c["type"]].get("children", ())) for c in children]
        return bid

    def add_page(self, page_id, job_key, description=True, **flags):
        self.pages[page_id] = {"key": job_key, "source": flags.get("source", SOURCE), "archived": flags.get("archived", False)}
        self.kids[page_id] = []
        if description:
            self.kids[page_id].append(self._new("paragraph", f"v7-jd:1 | key={job_key}"))
            self.kids[page_id].append(self._new("heading_2", "Description"))
            self.kids[page_id].append(self._new("paragraph", "Invented job description."))

    def top(self, page_id):
        return [self.blocks[b] for b in self.kids[page_id]]

    def texts(self, page_id):
        return ["".join(r["plain_text"] for r in b[b["type"]]["rich_text"]) for b in self.top(page_id)]

    def owned(self, page_id):
        for b in self.top(page_id):
            if b["type"] == "toggle":
                return [self.blocks[k] for k in self.kids[b["id"]]]
        return None

    def tick(self, page_id, prefix):
        for child in self.owned(page_id) or []:
            if child["type"] == "to_do" and child["to_do"]["rich_text"][0]["plain_text"].startswith(prefix):
                child["to_do"]["checked"] = True
                return
        raise AssertionError("no such tick")

    def call(self, method, path, body=None):
        if self.fail_get and method == "GET" and path.startswith("/pages/"):
            raise NotionError("NOTION_HTTP_500")
        if method == "GET" and path.startswith("/data_sources/"):
            return {"properties": {n: {"type": t} for n, t in ledger.REQUIRED.items()}}
        if method == "GET" and path.startswith("/pages/"):
            page = self.pages[path.rsplit("/", 1)[1]]
            return {"archived": page["archived"], "parent": {"data_source_id": page["source"]}, "properties": {"Stable Job Key": {"type": "rich_text", "rich_text": [{"plain_text": page["key"]}]}}}
        if method == "GET" and "/children" in path:
            owner = unquote(path.split("/blocks/")[1].split("/children")[0])
            return {"results": [dict(self.blocks[b]) for b in self.kids[owner]], "has_more": False}
        if method == "DELETE" and path.startswith("/blocks/"):
            self.writes.append("DELETE")
            bid = path.rsplit("/", 1)[1]
            for kids in self.kids.values():
                if bid in kids:
                    kids.remove(bid)
            return {}
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None):
        assert method == "PATCH" and path.endswith("/children")
        self.writes.append("APPEND")
        if self.drop_appends:
            return {}
        page_id = unquote(path.split("/blocks/")[1].split("/children")[0])
        for child in body["children"]:
            kind = child["type"]
            self.kids[page_id].append(self._new(kind, "".join(r["text"]["content"] for r in child[kind]["rich_text"]), child[kind].get("checked"), child[kind].get("children", ())))
        return {}
