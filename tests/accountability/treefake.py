"""A fake Notion for the LIFE-918 block writers (synthetic): nested block creation (children inline, tables with rows), `after` placement and has_children, as the API gives them."""
from tests.accountability import fakes as F


def _pieces(rich):
    return [F.rich(n["text"]["content"], (n["text"].get("link") or {}).get("url"))[0] | {"annotations": n.get("annotations") or {}} for n in rich or []]


class TreeNotion(F.Notion):
    def _make(self, parent, child):
        kind = child["type"]
        self.made += 1
        body = child[kind]
        block = {"id": f"n{self.made}", "type": kind, "has_children": bool(body.get("children"))}
        if kind == "table_row":
            block[kind] = {"cells": [_pieces(cell) for cell in body["cells"]]}
        elif kind == "table":
            block[kind] = {"table_width": body["table_width"], "has_column_header": body.get("has_column_header")}
        else:
            block[kind] = {"rich_text": _pieces(body.get("rich_text"))}
        self.blocks[block["id"]] = block
        self.kids.setdefault(block["id"], [])
        for inner in body.get("children") or []:
            self.kids[block["id"]].append(self._make(block["id"], inner))
        return block["id"]

    def call_once(self, method, path, body=None, *args, **kwargs):
        if method == "PATCH" and path.endswith("/children"):
            parent = path.split("/blocks/")[1].split("/")[0]
            self.writes.append(parent)
            order = self.kids.setdefault(parent, [])
            at = order.index(body["after"]) + 1 if body.get("after") else len(order)
            for child in body["children"]:
                order.insert(at, self._make(parent, child))
                at += 1
            self.blocks[parent]["has_children"] = True
            return {"results": []}
        if method == "PATCH" and self.blocks[path.split("/blocks/")[1].split("/")[0]]["type"] == "table_row":
            block_id = path.split("/blocks/")[1].split("/")[0]
            self.writes.append(block_id)
            self.blocks[block_id]["table_row"] = {"cells": [_pieces(cell) for cell in body["table_row"]["cells"]]}
            return self.blocks[block_id]
        if method == "DELETE":
            self.writes.append(path.split("/blocks/")[1])
        return super().call_once(method, path, body, *args, **kwargs)
