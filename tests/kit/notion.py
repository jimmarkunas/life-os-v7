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


class PaidBillTracker:
    """Mutable synthetic Bill Tracker rows; records property writes and recalculates Next Due."""

    SOURCE = "11111111-1111-4111-8111-111111111111"

    def __init__(self, *pages, responses=None, event_log=None, recalculate_formula=True,
                 fail_read_after_update=False):
        self.pages = {page["id"]: page for page in pages}
        self.responses = list(responses) if responses is not None else None
        self.event_log = event_log if event_log is not None else []
        self.recalculate_formula = recalculate_formula
        self.writes = []
        self.source = self.SOURCE
        self.requests = []
        self.fail_read_after_update = fail_read_after_update
        self.updated = False

    def query_data_source(self, source_id=None, body=None):
        self.requests.append((source_id, dict(body or {})))
        self.event_log.append(("query", source_id or self.source))
        if self.responses is not None:
            if not self.responses:
                raise AssertionError("unexpected extra Notion query")
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return {"results": [self.pages[key] for key in self.pages], "has_more": False}

    def get_page(self, page_id):
        self.event_log.append(("read", page_id))
        if self.fail_read_after_update and self.updated:
            self.fail_read_after_update = False
            from lifeos.platform.notion_client import NotionError
            raise NotionError("NOTION_NETWORK")
        if page_id not in self.pages:
            from lifeos.platform.notion_client import NotionError
            raise NotionError("NOTION_HTTP_404")
        import copy
        return copy.deepcopy(self.pages[page_id])

    def update_page_properties(self, page_id, properties):
        allowed = {"Last Paid", "Due Date", "Status", "Paid"}
        if not set(properties).issubset(allowed):
            raise AssertionError("unexpected Bill Tracker property")
        self.event_log.append(("update", tuple(properties)))
        self.writes.append(tuple(properties))
        self.updated = True
        page = self.pages[page_id]
        for name, value in properties.items():
            prop = page["properties"][name]
            kind = prop["type"]
            prop[kind] = value[kind]
        if "Due Date" in properties and self.recalculate_formula:
            from datetime import date, timedelta
            due = date.fromisoformat(page["properties"]["Due Date"]["date"]["start"])
            cycle = page["properties"]["Cycle"]["select"]["name"]
            days = {"Weekly": 7, "Bi-Weekly": 14, "Monthly": 30, "Quarterly": 91,
                    "Yearly": 365}.get(cycle, 30)
            next_due = due + timedelta(days=days)
            page["properties"]["Next Due"]["formula"] = {
                "type": "date", "date": {"start": next_due.isoformat()}}
        return {"id": page_id}


class AgendaRegions:
    """Nested Calendar target and independently readable protected JIRA callout."""

    def __init__(self, calendar_title="Calendar", heading_type="heading_3", extra_calendar=False,
                 calendar_type="callout", change_jira_on=None):
        self.page_id, self.log, self.serial = "page-example", [], 0
        self.roots, self.children, self.metas = [], {}, {}
        self.change_jira_on = change_jira_on
        self._add_region("calendar-callout", calendar_title, [self._paragraph("Foreign writer content")],
                         heading_type=heading_type, block_type=calendar_type)
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

    def _add_region(self, block_id, heading, body, heading_type="heading_4", block_type="callout"):
        root = {"id": block_id, "type": block_type, "has_children": True,
                "parent": {"type": "block_id", "block_id": "column-parent"}, block_type: {}}
        self.roots.append(root)
        self.metas[block_id] = root
        self.children[block_id] = [self._text_block(f"heading-{block_id}", heading_type, heading), *body]

    def call(self, method, path, body=None):
        self.log.append((method, path))
        clean = path.split("?", 1)[0]
        if method == "GET" and clean.startswith("/blocks/") and clean.endswith("/children"):
            block_id = clean.split("/")[2]
            return {"results": [self._api_copy(block) for block in self.children.get(block_id, [])], "has_more": False}
        if method == "GET" and clean.startswith("/blocks/"):
            block_id = clean.rsplit("/", 1)[1]
            root = self.metas.get(block_id)
            if root:
                return self._api_copy(root)
            for blocks in self.children.values():
                block = next((item for item in blocks if item["id"] == block_id), None)
                if block:
                    return self._api_copy(block)
            from lifeos.platform.notion_client import NotionError
            raise NotionError("NOTION_HTTP_404")
        if method == "DELETE":
            block_id = clean.rsplit("/", 1)[1]
            for key, blocks in self.children.items():
                self.children[key] = [block for block in blocks if block["id"] != block_id]
            if self.change_jira_on == "DELETE":
                self.children["jira-callout"].append(self._paragraph("Changed protected content"))
                self.change_jira_on = None
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
        if self.change_jira_on == "APPEND":
            self.children["jira-callout"].append(self._paragraph("Changed protected content"))
            self.change_jira_on = None
        return {"results": made}

    def full_tree(self, block_id):
        root = dict(self.metas[block_id])
        root["agenda_children"] = [dict(child) for child in self.children[block_id]]
        return root

    @staticmethod
    def _api_copy(block):
        copied = dict(block)
        kind = block.get("type")
        if kind:
            copied[kind] = dict(block.get(kind) or {})
            copied[kind]["rich_text"] = [dict(part) for part in (block.get(kind) or {}).get("rich_text", [])]
        return copied


class BillsRegions(AgendaRegions):
    """The production-shaped Daily Report: Calendar, JIRA and the ChatGPT region (protected) plus the Bills callout as it stood on 2026-10-04, a
    heading, the frozen status paragraph with its Bill Tracker link and the linked 'View of Bills' database view."""

    def __init__(self, view_title="View of Bills", extra_kid=None, change_on=None, change_region="calendar-callout"):
        super().__init__()
        self.change_on, self.change_region = change_on, change_region
        status = self._text_block("bills-status-old", "paragraph", "STALE · last accepted 9/14 9:44 AM CT · 4 active recurring items were overdue")
        status["paragraph"]["rich_text"].append({"plain_text": "Bill Tracker", "text": {"content": "Bill Tracker", "link": {"url": "https://example.com/tracker"}}})
        view = {"id": "bills-view-old", "type": "child_database", "has_children": False, "child_database": {"title": view_title}}
        kids = [status, view] + ([extra_kid] if extra_kid else [])
        self._add_region("bills-callout", "Bills: This Week", kids, heading_type="heading_3")

    def _poke(self, op):
        if self.change_on == op:
            self.children[self.change_region].append(self._paragraph("Changed protected content"))
            self.change_on = None

    def call(self, method, path, body=None):
        result = super().call(method, path, body)
        if method == "DELETE":
            self._poke("DELETE")
        return result

    def call_once(self, method, path, body=None):
        block_id = path.split("/")[2]
        result = super().call_once(method, path, {"children": body["children"]})
        after = body.get("after")
        if after:                                           # Notion inserts the new blocks right after the named block
            kids = self.children[block_id]
            made = kids[-len(body["children"]):]
            del kids[-len(body["children"]):]
            at = next(i for i, k in enumerate(kids) if k["id"] == after) + 1
            kids[at:at] = made
        self._poke("APPEND")
        return result
