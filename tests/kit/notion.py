"""A fake Notion block store for region writers (Jira card, and any later Bills/Calendar region): GET lists the children, DELETE removes one,
PATCH of a block replaces its paragraph. Anything else is a test failure."""


class FakeBlocks:
    def __init__(self, kids):
        self.kids, self.log, self.n = list(kids), [], 100

    def call(self, method, path, body=None):
        self.log.append(method)
        if method == "GET":
            return {"results": [dict(k) for k in self.kids], "has_more": False}
        if method == "DELETE":
            bid = path.rsplit("/", 1)[1]
            self.kids = [k for k in self.kids if k["id"] != bid]
            return {}
        if method == "PATCH" and path.startswith("/blocks/") and "children" not in path:
            for k in self.kids:
                if k["id"] == path.rsplit("/", 1)[1]:
                    k["paragraph"] = body["paragraph"]
            return {}
        raise AssertionError(path)

    def call_once(self, method, path, body=None):
        self.log.append("APPEND")
        if path != "/blocks/card-1/children":
            return {"results": [self._made(b) for b in body["children"]]}
        made = []
        for b in body["children"]:
            made.append(self._made(b))
            self.kids.append(made[-1])
        return {"results": made}

    def _made(self, b):
        self.n += 1
        t = b["type"]
        return {"id": f"b{self.n}", "type": t, t: {"rich_text": [{"plain_text": r["text"]["content"]} for r in b[t]["rich_text"]]}}


class DataSourcePages:
    """Scripted synthetic Notion data-source pages for complete-read and failure tests."""

    def __init__(self, *pages):
        self.pages, self.requests = list(pages), []

    def query_data_source(self, source_id=None, body=None):
        self.requests.append((source_id, dict(body or {})))
        if not self.pages:
            raise AssertionError("unexpected extra Notion query")
        page = self.pages.pop(0)
        if isinstance(page, BaseException):
            raise page
        return page


class AgendaRegions:
    """Synthetic Daily Report page with isolated Calendar and protected callouts."""

    def __init__(self, calendar_title="Calendar", extra_calendar=False):
        self.page_id, self.log, self.serial = "page-example", [], 0
        self.roots, self.children = [], {}
        self._add_region("calendar-callout", calendar_title, [self._paragraph("Old event content")])
        if extra_calendar:
            self._add_region("calendar-callout-duplicate", "Calendar", [])
        self._add_region("jira-callout", "JIRA Execution", [self._paragraph("Protected JIRA content")])
        self._add_region("dcc-callout", "ChatGPT · Daily Command Center", [self._paragraph("Protected DCC content")])

    def _text_block(self, block_id, kind, text):
        return {"id": block_id, "type": kind, "has_children": False,
                kind: {"rich_text": [{"plain_text": text, "text": {"content": text}}]}}

    def _paragraph(self, text):
        self.serial += 1
        return self._text_block(f"paragraph-{self.serial}", "paragraph", text)

    def _add_region(self, block_id, heading, body):
        root = {"id": block_id, "type": "callout", "has_children": True,
                "parent": {"type": "page_id", "page_id": self.page_id}, "callout": {}}
        self.roots.append(root)
        self.children[block_id] = [self._text_block(f"heading-{block_id}", "heading_4", heading), *body]

    def call(self, method, path, body=None):
        self.log.append((method, path))
        clean = path.split("?", 1)[0]
        if method == "GET" and clean == f"/blocks/{self.page_id}/children":
            return {"results": [dict(block) for block in self.roots], "has_more": False}
        if method == "GET" and clean.startswith("/blocks/") and clean.endswith("/children"):
            block_id = clean.split("/")[2]
            return {"results": [dict(block) for block in self.children.get(block_id, [])], "has_more": False}
        if method == "GET" and clean.startswith("/blocks/"):
            block_id = clean.rsplit("/", 1)[1]
            root = next((block for block in self.roots if block["id"] == block_id), None)
            if root:
                return dict(root)
            for blocks in self.children.values():
                block = next((item for item in blocks if item["id"] == block_id), None)
                if block:
                    return dict(block)
            from lifeos.platform.notion_client import NotionError
            raise NotionError("NOTION_HTTP_404")
        if method == "DELETE":
            block_id = clean.rsplit("/", 1)[1]
            for key, blocks in self.children.items():
                self.children[key] = [block for block in blocks if block["id"] != block_id]
            return {}
        if method == "PATCH" and clean.startswith("/blocks/"):
            block_id = clean.rsplit("/", 1)[1]
            for blocks in self.children.values():
                for block in blocks:
                    if block["id"] == block_id:
                        block[block["type"]] = body.get(block["type"], body)
                        return {}
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None):
        self.log.append(("APPEND", path))
        block_id = path.split("/")[2]
        made = []
        for block in body["children"]:
            self.serial += 1
            new_id = f"agenda-{self.serial}"
            kind = block["type"]
            plain = "".join(part["text"]["content"] for part in block[kind]["rich_text"] if "text" in part)
            made.append({"id": new_id, "type": kind, "has_children": False,
                         kind: {"rich_text": [{"plain_text": plain, "text": {"content": plain}}]}})
        self.children.setdefault(block_id, []).extend(made)
        return {"results": made}
