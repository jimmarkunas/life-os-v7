"""LIFE-88 first gate: the UI 3c REST capability probe writes only its one QA probe page and reports codes, block types and counts."""
import json
import unittest
from lifeos.interview import ui_probe as P
from lifeos.platform.notion_client import NotionError

LIVE = {"PROBE_CONFIRM": "QA", "NOTION_INTERVIEW_TOKEN": "synthetic"}


class Fake:
    """Children per block id; PATCH /children appends (nested children become readable); DELETE removes; refuses listed block types."""
    def __init__(self, refuse=()):
        self.kids = {P.QA_PARENT: [{"id": "other", "type": "child_page", "child_page": {"title": "Smartsheet notes"}, "has_children": True}],
                     P.UI3C: [{"id": "u1", "type": "column_list", "has_children": True}],
                     "u1": [{"id": "u2", "type": "column", "has_children": True}],
                     "u2": [{"id": "u3", "type": "tab", "tab": {}, "has_children": True},
                            {"id": "u4", "type": "table", "has_children": False}],
                     "u3": [{"id": "u5", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Private tab title"}]}, "has_children": True},
                            {"id": "u6", "type": "paragraph", "paragraph": {"rich_text": []}, "has_children": True}],
                     "u5": [{"id": "u7", "type": "heading_3", "has_children": False}], "u6": [{"id": "u8", "type": "paragraph", "has_children": False}]}
        self.refuse, self.calls, self.n = set(refuse), [], 0

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
                P.run(0, live, environ=env, client=Fake())

    def test_builds_the_ui_3c_structures_once_with_a_tab_mirroring_the_preview(self):
        client = Fake()
        first = P.run(0, True, environ=LIVE, client=client)
        self.assertEqual(first["page"], "created")
        self.assertEqual((first["nonstandard_types"], first["nonstandard_shapes"]), (["tab"], {"tab": []}))
        self.assertEqual(first["ui3c_tab_tree"], ["1:paragraph", "2:heading_3", "1:paragraph", "2:paragraph"])   # panes and their content, types only
        for name in ("read_ui3c_tree", "probe_page", "action_center_callout", "action_center_columns_67_33", "main_columns_63_37", "create_tab",
                     "read_tab_panes", "update_tab_pane", "interviews_callout", "table_in_callout", "read_table_rows", "update_table_row",
                     "update_callout", "link_to_page", "read_probe_tree"):
            self.assertEqual(first["checks"][name], "ok", name)
        self.assertEqual(first["failed"], 0)
        self.assertEqual(first["column_ratios"], [0.63, 0.37])
        tab = first["probe_tree"].index("2:tab")                            # columns → column → tab → panes → content
        self.assertEqual(first["probe_tree"][tab + 1:tab + 5], ["3:paragraph", "4:paragraph", "3:paragraph", "4:paragraph"])
        self.assertIn("3:table", first["probe_tree"])                       # columns → column → callout → table
        second = P.run(0, True, environ=LIVE, client=client)
        self.assertEqual(second["page"], "reused")
        probes = [b for b in client.kids[P.QA_PARENT] if b["type"] == "child_page" and b["child_page"]["title"] == P.TITLE]
        self.assertEqual(len(probes), 1)                                     # no duplicate probe page
        self.assertEqual(second["probe_tree"], first["probe_tree"])           # rebuilt identically after the clear
        self.assertEqual(client.kids[P.QA_PARENT][0]["id"], "other")          # nothing else under the QA parent touched
        self.assertNotIn(("POST", "/pages"), client.calls[len(client.calls) // 2:])

    def test_writes_only_inside_the_probe_page_and_prints_no_content(self):
        client = Fake(refuse=())
        counts = P.run(0, True, environ=LIVE, client=client)
        self.assertEqual(counts["failed"], 0)
        probe = next(b["id"] for b in client.kids[P.QA_PARENT] if b["type"] == "child_page" and b["child_page"]["title"] == P.TITLE)
        inside = {probe} | {b["id"] for blocks in client.kids.values() for b in blocks if b["id"].startswith("b")}
        for method, path in client.calls:
            if method in ("PATCH", "DELETE"):
                self.assertIn(path.split("/")[2], inside)
        text = json.dumps(counts)
        for private in ("Private tab title", "Smartsheet", P.QA_PARENT, P.UI3C, probe, "synthetic"):
            self.assertNotIn(private, text)

    def test_a_refused_tab_is_a_code_and_the_rest_still_runs(self):
        counts = P.run(0, True, environ=LIVE, client=Fake(refuse=("tab",)))
        self.assertEqual((counts["checks"]["create_tab"], counts["failed"]), ("NOTION_HTTP_400 validation_error body.children[0].type", 1))
        self.assertEqual(counts["checks"]["update_callout"], "ok")

    def test_a_second_probe_page_stops_before_any_write(self):
        client = Fake()
        client.kids[P.QA_PARENT] += [{"id": f"dup{i}", "type": "child_page", "child_page": {"title": P.TITLE}} for i in range(2)]
        counts = P.run(0, True, environ=LIVE, client=client)
        self.assertEqual(counts["checks"]["probe_page"], "UI_PROBE_DUPLICATE_PAGE")
        self.assertFalse([c for c in client.calls if c[0] != "GET"])


if __name__ == "__main__":
    unittest.main()
