"""LIFE-544: test helpers that more than one test module (often in another domain) uses. Moved here out of test_*.py files, unchanged."""
from lifeos.interview import create, notion, prep, template, ui_probe, view
from lifeos.interview import create, notion, stage
from lifeos.interview.transcript import normalize_granz
import json
from lifeos.platform.notion_client import NotionError
from lifeos.interview import notion
import copy


ENV = {"NOTION_INTERVIEW_TOKEN": "synthetic", "HIRING_PIPELINE_PAGE_ID": "root", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "ds-synthetic"}     # LIFE-361: a new pursuit is created in the Ledger


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

    def query_data_source(self, source_id=None, body=None):
        return {"results": [], "has_more": False, "next_cursor": None}             # an empty Job Ledger: readers fall back to the page tree as before


UUID = "3f2a1c6e-1b7a-4c1e-9d55-0a1b2c3d4e5f"


ROUND = "round-page"


def envelope(n=3):
    rows = [{"id": f"s{i}", "document_id": UUID, "text": f"Line {i} " + "x" * (2500 if i == 1 else 5), "start_timestamp": f"2026-10-13T{17 + i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z",
             "end_timestamp": f"2026-10-13T{17 + (i + 1) // 3600:02d}:{(i + 1) // 60 % 60:02d}:{(i + 1) % 60:02d}Z", "source": "microphone" if i % 2 == 0 else "system", "is_final": True} for i in range(n)]
    return normalize_granz(UUID, json.dumps({"transcript": rows}))


class FakeNotion:
    def __init__(self, existing=(), fail_on=None, drop_last=False):
        self.pages = {ROUND: list(existing)}
        self.calls, self.fail_on, self.drop_last = [], fail_on, drop_last

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if self.fail_on == method:
            raise NotionError("NOTION_NETWORK")
        if method == "POST" and path == "/pages":
            pid = f"page-{len(self.pages)}"
            self.pages[pid] = list(body["children"])
            title = body["properties"]["title"]["title"][0]["text"]["content"]
            self.pages[body["parent"]["page_id"]].append({"id": pid, "type": "child_page", "child_page": {"title": title}})
            return {"id": pid}
        if method == "PATCH" and path.endswith("/children"):
            self.pages[path.split("/")[2]].extend(body["children"])
            return {"results": []}
        if method == "GET" and "/children" in path:
            blocks = self.pages[path.split("/")[2]]
            blocks = blocks[:-1] if self.drop_last and path.split("/")[2] != ROUND else blocks
            return {"results": [dict(b, id=b.get("id", "b")) if b.get("type") == "child_page" else {"id": "b", "type": b["type"], b["type"]: b[b["type"]]} for b in blocks],
                    "has_more": False, "next_cursor": None}
        raise AssertionError((method, path))


class WritableFake(Fake):
    def __init__(self):
        super().__init__()
        self.blocks["root"] = [{"id": "active", "type": "child_page", "child_page": {"title": "Active Opportunities"}}]
        self.pages["active"] = page("Active Opportunities")
        self.blocks["active"] = []
        self.blocks["root"].insert(0, {"id": "columns", "type": "column_list", "has_children": True})
        self.blocks["columns"] = [{"id": "left", "type": "column", "has_children": True}, {"id": "right", "type": "column", "has_children": True}]
        self.blocks["left"] = [block("Active Opportunities", "heading_2"), {"id": "human", "type": "child_page", "child_page": {"title": "Legacy — Manager"}}]
        self.blocks["right"] = [block("Retired Opportunities", "heading_2")]
        self.pages["human"] = page("Legacy — Manager")
        self.pages["human"]["parent"] = {"type": "block_id", "block_id": "left"}
        self.blocks["human"] = [block("human notes")]
        self.rows = {}                                         # LIFE-361: the Job Ledger data source: row id -> properties
        self.sandbox_source = None                             # a proof's own sandbox Ledger (POST /databases), the same rows
        self.sandbox_schema = None
        self.stage_options = ["Found", "Applied", "Interviewing", "Offer", "Working", "Ended"]
        self.posts = []
        self.post_error = False
        self.bad_readback = False
        self.change_notes = False
        self.hide_created = False

    MUST = ("column_list", "column", "table", "tab")

    def register(self, prefix, items, depth=0):
        """Like Notion: every block gets an id, nested children become readable child lists, a request carries at most two
        nesting levels, and a column list, column, table or tab must arrive with its children."""
        items = copy.deepcopy(items)
        for index, item in enumerate(items):
            item["id"] = f"{prefix}-block-{index}"
            inner = (item.get(item["type"]) or {}).pop("children", None)
            if item["type"] in self.MUST and not inner:
                raise NotionError("NOTION_HTTP_400")
            if inner and depth >= 2:
                raise NotionError("NOTION_HTTP_400")
            if inner:
                item["has_children"] = True
                self.blocks[item["id"]] = self.register(item["id"], inner, depth + 1)
        return items

    @staticmethod
    def _value(prop):
        if not prop:
            return None
        for kind in ("title", "rich_text"):
            if kind in prop:
                return "".join(x.get("plain_text", (x.get("text") or {}).get("content", "")) for x in prop[kind])
        return (prop.get("select") or {}).get("name")

    def row_of(self, rid):
        return {"id": rid, "properties": copy.deepcopy(self.rows[rid])}

    def query_data_source(self, source_id=None, body=None):
        """Like the Job Ledger: the Stage reader (parents_from_ledger) and the company read (ledger_row). A hidden row is missing from the Stage reader only."""
        self.calls.append(("GET", f"/data_sources/{source_id}/query"))           # a read (Notion's query is a POST, but it changes nothing)
        if f"/data_sources/{source_id}/query" in self.fail:
            raise NotionError("NOTION_NETWORK")
        wanted = (body or {}).get("filter") or {}
        if self.sandbox_schema is not None and source_id == self.sandbox_source:         # like Notion: a filter on a property the data source lacks is a 400
            named = [f["property"] for f in wanted.get("or", wanted.get("and", [wanted])) if "property" in f]
            if any(n not in self.sandbox_schema for n in named):
                raise NotionError("NOTION_HTTP_400")
        if "or" in wanted:                                                       # LIFE-353: the decision writers' pursuit read, Applied ticked or Stage set
            found = [r for r in self.rows if self._value(self.rows[r].get("Stage")) or (self.rows[r].get("Applied") or {}).get("checkbox")]
        elif wanted.get("property") == "Stage":
            found = [r for r in self.rows if self._value(self.rows[r].get("Stage")) and not (self.hide_created and r.startswith("created-"))]
        else:
            word = wanted["rich_text"]["contains"].lower()
            found = [r for r in self.rows if word in (self._value(self.rows[r].get("Company")) or "").lower()]
        return {"results": [self.row_of(r) for r in found], "has_more": False, "next_cursor": None}

    def add_row(self, rid, company, job, stage=None, decision=None, key=None):
        self.rows[rid] = {"Job": {"title": [{"plain_text": job}]}, "Company": {"rich_text": [{"plain_text": company}]},
                          "Stage": {"select": {"name": stage} if stage else None}, "Hiring Decision": {"select": {"name": decision} if decision else None},
                          "Stable Job Key": {"rich_text": [{"plain_text": key}] if key else []}}
        self.pages[rid] = page(job, "ledger")
        self.blocks.setdefault(rid, [])
        return rid

    def find(self, key):
        return next((b for blocks in self.blocks.values() for b in blocks if b.get("id") == key), None)

    def call_once(self, method, path, body=None, version=None):
        return self.call(method, path, body)

    def call(self, method, path, body=None):
        if method in ("PATCH", "DELETE"):
            self.calls.append((method, path))
            key = path.split("/")[2]
            if method == "DELETE":
                for blocks in self.blocks.values():
                    blocks[:] = [item for item in blocks if item["id"] != key]
                return {"id": key, "in_trash": True}
            if path.endswith("/children"):
                self.appends = getattr(self, "appends", 0) + 1
                siblings = self.blocks.setdefault(key, [])
                first = key in self.pages and not siblings                    # a page created from a template, filled now
                items = self.register(key if first else f"{key}-add{self.appends}", body["children"])
                at = [b["id"] for b in siblings].index(body["after"]) + 1 if body.get("after") else 0 if body.get("position") == {"type": "start"} else len(siblings)
                siblings[at:at] = items
                if self.find(key):
                    self.find(key)["has_children"] = True
                if first and self.bad_readback:
                    siblings[0] = block("missing marker")
                return {"results": copy.deepcopy(items)}
            if path.startswith("/pages/"):
                if key in self.rows and "properties" in body:            # the Ledger row's Stage, or (LIFE-353) its Hiring Decision and history
                    self.rows[key].update(copy.deepcopy(body["properties"]))
                    self.edits = getattr(self, "edits", 0) + 1
                    return {"id": key}
                self.pages.setdefault(key, page())["icon"] = body.get("icon")
                return {"id": key}
            target = self.find(key)
            kind = next(k for k in body)
            target[kind]["rich_text"] = copy.deepcopy(body[kind]["rich_text"])
            return copy.deepcopy(target)
        if method == "GET" and path.startswith("/pages/") and path.split("/")[2] in self.rows:      # a Ledger row page, as the decision writer reads it
            rid = path.split("/")[2]
            return {**self.row_of(rid), "archived": False, "in_trash": False, "last_edited_time": f"2026-10-22T00:00:{getattr(self, 'edits', 0):02d}.000Z",
                    "parent": {"type": "data_source_id", "data_source_id": self.sandbox_source or "ds-synthetic"}}
        if method == "GET" and path.startswith("/data_sources/"):
            self.calls.append((method, path))
            if path in self.fail:
                raise NotionError("NOTION_NETWORK")
            if self.sandbox_schema is not None and path == f"/data_sources/{self.sandbox_source}":
                return {"properties": {n: {"type": next(iter(v)), **v} for n, v in self.sandbox_schema.items()}}
            return {"properties": {"Stage": {"type": "select", "select": {"options": [{"name": n} for n in self.stage_options]}},
                                   "Source Types": {"type": "multi_select", "multi_select": {"options": [{"name": "Manual"}, {"name": "LinkedIn"}]}}}}
        if method != "POST":
            return super().call(method, path, body)
        if path == "/databases":                                       # a proof's sandbox Ledger: one data source
            self.calls.append((method, path))
            self.sandbox_source = "5a" * 16                             # Notion-shaped: the decision writer checks a row's parent by id
            self.sandbox_schema = copy.deepcopy(body["initial_data_source"]["properties"])
            return {"id": "sandbox-db", "data_sources": [{"id": self.sandbox_source}]}
        assert path == "/pages"
        self.calls.append((method, path))
        self.posts.append(copy.deepcopy(body))
        if self.post_error:
            raise NotionError("private error body")
        if body["parent"]["type"] == "data_source_id":               # LIFE-361: a new Manual Ledger row (no Stage yet; no body)
            assert body["parent"]["data_source_id"] in ("ds-synthetic", self.sandbox_source) and "children" not in body
            rid = f"created-{len(self.posts)}" if body["parent"]["data_source_id"] == "ds-synthetic" else f"{len(self.posts):032x}"      # a sandbox row gets a Notion-shaped id
            props = copy.deepcopy(body["properties"])
            for prop in props.values():                                # Notion answers with plain_text on every text run
                for run in prop.get("title") or prop.get("rich_text") or []:
                    run["plain_text"] = run["text"]["content"]
            self.rows[rid] = {**props, "Stage": {"select": None}, "Hiring Decision": {"select": None}}
            self.pages[rid] = page(self._value(body["properties"]["Job"]), "ledger")
            self.blocks[rid] = []
            return {"id": rid}
        assert set(body["properties"]) == {"title"}
        parent_id = body["parent"]["page_id"]
        title = "".join(x["text"]["content"] for x in body["properties"]["title"]["title"])
        page_id = f"created-{len(self.posts)}"
        self.pages[page_id] = page(title, parent_id)
        self.blocks[page_id] = self.register(page_id, body.get("children", []))
        if not self.hide_created:
            self.blocks[parent_id].append({"id": page_id, "type": "child_page", "child_page": {"title": title}})
        if self.bad_readback and self.blocks[page_id]:
            self.blocks[page_id][0] = block("missing marker")
        if self.change_notes:
            self.blocks[parent_id].insert(-1, block("changed human content"))
        return {"id": page_id}


REQUEST = "https://mail.google.com/mail/u/0/#all/synthetic-request"


def opportunity(rounds=(), **facts):
    return view.Opportunity("ExampleCo", "Program Manager", "exampleco-pm-1", None, "row" if facts else None,
                            "linked" if facts else "not_linked", REQUEST, "root", rounds=rounds, **facts)


def texts(client, block_id):
    out = []
    for b in client.blocks.get(block_id, []):
        out.append(notion.text(b))
        if b["type"] == "table_row":
            out += ["".join(r.get("plain_text", (r.get("text") or {}).get("content", "")) for r in cell) for cell in b["table_row"]["cells"]]
        out += texts(client, b["id"])
    return out


class Sim(WritableFake):
    """The production-shaped writable fake."""


def written(current=None):
    """A UI 3c page written by the staged writer under "active", with Jim's own note and an interview page after it."""
    client = Sim()
    current = current or opportunity()
    blocks = create.rendered(current, tail=None)[0]
    page_id = notion.write_tree(client, "active", blocks, title="ExampleCo — Program Manager")
    assert create.finalize(client, page_id, current, blocks) == "readback_ok"
    live = [b["id"] for b in client.blocks[page_id] if notion.text(b) == "Live Notes · protected space"][0]
    client.blocks[live].append(block("Jim's private note", id="jim-note"))
    client.blocks[page_id].append({"id": "round-1", "type": "child_page", "child_page": {"title": "Interview 1"}})
    return client, page_id
