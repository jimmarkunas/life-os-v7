"""A synthetic Job Ledger in Notion: pages with a Stable Job Key, a data source, and a body of blocks (a toggle holds its children). GET/PATCH(append)/DELETE only."""
from tests.kit.notion import LANE_OPTIONS, filter_depth, _select_names  # noqa: F401
from urllib.parse import unquote

from lifeos.jobs import ledger
from lifeos.platform.notion_client import NotionError

SOURCE = "00000000000000000000000000000abc"


class FakeLedger:
    source = SOURCE

    def __init__(self):
        self.pages, self.blocks, self.kids, self.n, self.writes, self.fail_get = {}, {}, {}, 0, [], False
        self.drop_appends, self.fail_delete = False, False
        self.calls, self.edit_n, self.edited = 0, 0, {}          # LIFE-572: Notion call count and a per-page last_edited_time that moves on every write to the page or inside it

    def _new(self, kind, runs, checked=None, children=()):
        """`runs` is a plain string or the rich_text a writer sent; the stored runs carry plain_text and href like Notion's reply."""
        self.n += 1
        bid = f"blk{self.n}"
        runs = [{"plain_text": runs}] if isinstance(runs, str) else [{"plain_text": r["text"]["content"], "href": (r["text"].get("link") or {}).get("url")} for r in runs]
        inner = {"rich_text": runs}
        if checked is not None:
            inner["checked"] = checked
        self.blocks[bid] = {"id": bid, "type": kind, kind: inner, "has_children": bool(children)}
        self.kids[bid] = [self._new(c["type"], c[c["type"]].get("rich_text", []), (c[c["type"]].get("checked")), c[c["type"]].get("children", ())) for c in children]
        return bid

    def create(self, props, blocks):
        """The publish client's create: a page with the key from its properties and the blocks as sent (children nested as Notion would store them)."""
        self.n += 1
        page_id = f"pg{self.n}"
        key = "".join(r["text"]["content"] for r in props["Stable Job Key"]["rich_text"])
        self.pages[page_id], self.kids[page_id] = {"key": key, "source": SOURCE, "archived": False}, []
        for b in blocks:
            inner = b[b["type"]]
            self.kids[page_id].append(self._new(b["type"], inner.get("rich_text", []), inner.get("checked"), inner.get("children", ())))
        return page_id

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

    def right(self, page_id):
        """The right-hand column's block id on a two-column page (the description's column_list: ledger_body.description_list), else None."""
        from lifeos.platform import ledger_body
        found = ledger_body.description_list(self.top(page_id), self.pages[page_id]["key"])
        return self.kids[found["id"]][1] if found else None

    def owned(self, page_id):
        """The children of the page's Network Leads block (a top-level toggle or callout, or one in the right column of the two-column page)."""
        right = self.right(page_id)
        for b in self.top(page_id) + ([self.blocks[k] for k in self.kids[right]] if right else []):
            if b["type"] in ("toggle", "callout"):
                return [c for k in self.kids[b["id"]] for c in self._card(self.blocks[k])]
        return None

    def _card(self, block):
        """An approved card (a callout) flattened to its name paragraph and its status ticks, so a card reads like one lead of the older flat block."""
        if block["type"] != "callout":
            return [block]
        out = []
        for lst in self.kids[block["id"]]:
            left, right = self.kids[lst][0], self.kids[lst][1]
            out.append(self.blocks[self.kids[left][0]])
            for toggle in self.kids[right]:
                out += [self.blocks[t] for t in self.kids[toggle]]
        return out

    def tick(self, page_id, prefix):
        """Tick the first to_do whose text or hidden ref starts with `prefix` (the ref is the link target after the ZW character)."""
        for child in self.owned(page_id) or []:
            runs = child["type"] == "to_do" and child["to_do"]["rich_text"]
            if runs and (runs[0]["plain_text"].startswith(prefix) or unquote((runs[0].get("href") or "").rsplit("/lifeos/", 1)[-1]).startswith(prefix)):
                child["to_do"]["checked"] = True
                self._touch(child["id"])                      # LIFE-572: Notion moves the page's last_edited_time when a checkbox is ticked
                return
        raise AssertionError("no such tick")

    def _touch(self, owner):
        """LIFE-572: a write to a block moves the last_edited_time of the page that holds it (Notion does the same)."""
        while owner not in self.pages:
            parent = next((o for o, kids in self.kids.items() if owner in kids), None)
            if parent is None:
                return
            owner = parent
        self.edit_n += 1
        self.edited[owner] = self.edit_n

    def edited_time(self, page_id):
        from datetime import datetime, timedelta
        return (datetime(2026, 10, 7, 9, 0) + timedelta(minutes=self.edited.get(page_id, 0))).strftime("%Y-%m-%dT%H:%M:00.000Z")

    def call(self, method, path, body=None):
        self.calls += 1
        if self.fail_get and method == "GET" and path.startswith("/pages/"):
            raise NotionError("NOTION_HTTP_500")
        if method == "GET" and path.startswith("/data_sources/"):
            return {"properties": {n: {"type": t} for n, t in ledger.REQUIRED.items()}}
        if method == "GET" and path.startswith("/pages/"):
            page = self.pages[path.rsplit("/", 1)[1]]
            return {"archived": page["archived"], "last_edited_time": self.edited_time(path.rsplit("/", 1)[1]), "parent": {"data_source_id": page["source"]}, "properties": {"Stable Job Key": {"type": "rich_text", "rich_text": [{"plain_text": page["key"]}]}}}
        if method == "GET" and "/children" in path:
            owner = unquote(path.split("/blocks/")[1].split("/children")[0])
            return {"results": [dict(self.blocks[b]) for b in self.kids[owner]], "has_more": False}
        if method == "PATCH" and path.endswith("/children"):
            return self.call_once(method, path, body)
        if method == "PATCH" and path.startswith("/blocks/"):               # a block's own text replaced in place (the marker), its id kept
            self.writes.append("UPDATE")
            self._touch(path.rsplit("/", 1)[1])
            block = self.blocks[path.rsplit("/", 1)[1]]
            kind = block["type"]
            block[kind]["rich_text"] = [{"plain_text": r["text"]["content"], "href": (r["text"].get("link") or {}).get("url")} for r in body[kind]["rich_text"]]
            return {}
        if method == "DELETE" and path.startswith("/blocks/"):
            if self.fail_delete:
                raise NotionError("NOTION_HTTP_500")
            self.writes.append("DELETE")
            bid = path.rsplit("/", 1)[1]
            self._touch(bid)
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
        self._touch(page_id)
        at = self.kids[page_id].index(body["after"]) + 1 if body.get("after") else len(self.kids[page_id])
        for child in body["children"]:
            kind = child["type"]
            self.kids[page_id].insert(at, self._new(kind, child[kind].get("rich_text", []), child[kind].get("checked"), child[kind].get("children", ())))
            at += 1
        return {}


class LedgerWithFilterLimit:
    """A fake Job Ledger client that refuses what Notion refuses: a compound filter nested more than two levels, and a select filter naming an option
    the property does not have (observed on the real API: HTTP 400, validation_error). `pages` are returned for every query; `queries` records each
    filter it accepted."""

    def __init__(self, pages=(), properties=None, lane_options=LANE_OPTIONS):
        from lifeos.jobs import ledger
        self.source, self.pages, self.queries, self.log = "ledger", list(pages), [], []
        self.properties = {n: {"type": k} for n, k in (properties or ledger.REQUIRED).items()}
        self.properties["Visible Lane"] = {"type": "select", "select": {"options": [{"name": n} for n in lane_options]}}
        self.lane_options = lane_options

    def call(self, method, path, body=None):
        from lifeos.platform.notion_client import NotionError
        self.log.append((method, path))
        if method == "GET":
            return {"properties": self.properties}
        if method == "PATCH":
            return {}
        if filter_depth((body or {}).get("filter")) > 2:
            raise NotionError("NOTION_HTTP_400")
        if any(name not in self.lane_options for name in _select_names(body["filter"]) if isinstance(name, str)):
            raise NotionError("NOTION_HTTP_400")
        self.queries.append(body["filter"])
        return {"results": self.pages, "has_more": False}
