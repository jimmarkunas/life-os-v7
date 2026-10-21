"""LIFE-88 first gate: the UI 3c REST capability probe writes only its one QA probe page and reports codes, block types and counts."""
import json
import unittest
import unittest.mock
from lifeos.interview import ui_probe as P
from lifeos.platform.notion_client import NotionError

LIVE = {"PROBE_CONFIRM": "QA", "NOTION_INTERVIEW_TOKEN": "synthetic"}


def setUpModule():                                  # the QA render proof has its own tests (test_ui3c); these cover the capability probe
    global RENDER
    RENDER = unittest.mock.patch("lifeos.interview.ui_probe._render", return_value={"qa": "stubbed"})
    RENDER.start()


def tearDownModule():
    RENDER.stop()
NOSLEEP = lambda seconds: None


class Fake:
    """Children per block id; PATCH /children appends (nested children become readable); DELETE removes; refuses listed block types."""
    def __init__(self, refuse=(), shell=True):
        self.kids = {P.QA_PARENT: [{"id": "other", "type": "child_page", "child_page": {"title": "Smartsheet notes"}, "has_children": True}]
                     + ([{"id": "shell", "type": "child_page", "child_page": {"title": P.SHELL_TITLE}, "has_children": False}] if shell else []),
                     "shell": [],
                     P.UI3C: [{"id": "u1", "type": "column_list", "has_children": True}],
                     "u1": [{"id": "u2", "type": "column", "has_children": True}],
                     "u2": [{"id": "u3", "type": "tab", "tab": {}, "has_children": True},
                            {"id": "u4", "type": "table", "has_children": False}],
                     "u3": [{"id": "u5", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Private tab title"}]}, "has_children": True},
                            {"id": "u6", "type": "paragraph", "paragraph": {"rich_text": []}, "has_children": True}],
                     "u5": [{"id": "u7", "type": "heading_3", "has_children": False}], "u6": [{"id": "u8", "type": "paragraph", "has_children": False}]}
        self.refuse, self.calls, self.n, self.templates = set(refuse), [], 0, []

    def get_page(self, page_id):
        return {"object": "page", "id": page_id, "properties": {}, "icon": None}

    def _new(self, prefix, blocks):
        out = []
        for block in blocks:
            if block["type"] in self.refuse or (block["type"] == "tab" and not block["tab"].get("children")):
                error = NotionError("NOTION_HTTP_400")
                error.detail = "validation_error body.children[0].type"
                raise error
            self.n += 1
            item = dict(block, id=f"{prefix}{self.n}")
            inner = (item.get(item["type"]) or {}).get("children") or []
            item["has_children"] = bool(inner)
            self.kids[item["id"]] = self._new(prefix, inner) if inner else []
            out.append(item)
        return out

    def call(self, method, path, body=None):
        return self.call_once(method, path, body)

    def call_once(self, method, path, body=None):
        self.calls.append((method, path.split("?")[0]))
        key = (path.split("/") + ["", ""])[2].split("?")[0]
        if method == "GET":
            return {"results": self.kids.get(key, []), "has_more": False}
        if method == "POST":
            if "template" in body and "template" in self.refuse:
                error = NotionError("NOTION_HTTP_400")
                error.detail = "validation_error body.template"
                raise error
            if "template" in body:
                self.templates.append(body["template"])
            self.n += 1
            page = {"id": f"probe{self.n}", "type": "child_page", "child_page": {"title": body["properties"]["title"]["title"][0]["text"]["content"]}}
            self.kids[body["parent"]["page_id"]].append(page)
            self.kids[page["id"]] = []
            return {"id": page["id"]}
        if method == "DELETE":
            for blocks in self.kids.values():
                blocks[:] = [b for b in blocks if b["id"] != key]
            return {}
        if path.endswith("/children"):
            new = self._new("b", body["children"])
            self.kids[key] += new
            for blocks in self.kids.values():
                for item in blocks:
                    if item["id"] == key:
                        item["has_children"] = True                         # as the API reports after an append
            return {"results": new}
        return {"id": key}


class ProbeTests(unittest.TestCase):
    def test_refuses_without_live_and_typed_qa(self):
        for live, env in ((False, LIVE), (True, {**LIVE, "PROBE_CONFIRM": ""}), (True, {**LIVE, "PROBE_CONFIRM": "SYNTHETIC"})):
            with self.subTest(live=live, env=env), self.assertRaises(NotionError):
                P.run(0, live, environ=env, client=Fake(), sleep=NOSLEEP)

    def test_writes_only_the_template_probe_page_and_prints_no_content(self):
        client = Fake()
        counts = P.run(0, True, environ=LIVE, client=client, sleep=NOSLEEP)
        self.assertEqual((counts["checks"], counts["failed"]), ({"template_page": "ok", "qa_render": "ok"}, 0))
        made = {b["id"] for b in client.kids[P.QA_PARENT] if b["type"] == "child_page" and b["child_page"]["title"] == P.TEMPLATE_TITLE}
        for method, path in client.calls:
            if method in ("PATCH", "DELETE"):
                self.assertIn(path.split("/")[2], made)
        self.assertEqual(client.kids[P.QA_PARENT][0], {"id": "other", "type": "child_page", "child_page": {"title": "Smartsheet notes"}, "has_children": True})
        text = json.dumps(counts)
        for private in ("Smartsheet", P.QA_PARENT, "shell", *made, "synthetic"):
            self.assertNotIn(private, text)

    def test_the_template_page_is_made_from_the_shell_once_and_never_trashed(self):
        client = Fake()
        first = P.run(0, True, environ=LIVE, client=client, sleep=NOSLEEP)
        self.assertEqual(first["checks"]["template_page"], "ok")
        self.assertEqual(client.templates[-1], {"type": "template_id", "template_id": "shell"})
        self.assertEqual((first["template_blocks_before_append"], first["template_blocks_after_append"]), (0, 1))
        second = P.run(0, True, environ=LIVE, client=client, sleep=NOSLEEP)
        self.assertEqual(second["checks"]["template_page"], "UI_PROBE_TEMPLATE_PAGE_EXISTS")
        self.assertNotIn("template_blocks_before_append", second)
        titles = [b["child_page"]["title"] for b in client.kids[P.QA_PARENT] if b["type"] == "child_page"]
        self.assertEqual(titles.count(P.TEMPLATE_TITLE), 1)
        self.assertFalse([c for c in client.calls if c[0] == "PATCH" and c[1].startswith("/pages/")])   # no page trashed or edited
        self.assertIn(P.SHELL_TITLE, titles)
        self.assertIn("Smartsheet notes", titles)

    def test_a_missing_shell_or_refused_template_is_a_code_and_writes_no_template_page(self):
        for fake, code in ((Fake(shell=False), "UI_PROBE_SHELL_MISSING"),
                           (Fake(refuse=("template",)), "NOTION_HTTP_400 validation_error body.template")):
            with self.subTest(code=code):
                counts = P.run(0, True, environ=LIVE, client=fake, sleep=NOSLEEP)
                self.assertEqual((counts["checks"]["template_page"], counts["failed"]), (code, 1))
                self.assertEqual(counts["checks"]["qa_render"], "ok")
                self.assertFalse([b for b in fake.kids[P.QA_PARENT] if b.get("child_page", {}).get("title") == P.TEMPLATE_TITLE])


if __name__ == "__main__":
    unittest.main()
