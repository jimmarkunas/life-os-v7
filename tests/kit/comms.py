"""A synthetic Daily Report column holding the Attention callout and the Communications callout (heading, two columns, four rows), with Jim's custom
icons as custom-emoji mentions. GET block, GET children and PATCH a block's rich text; anything else is a test failure. Invented ids and example.com only."""
from copy import deepcopy

LAUNCH = "https://example.com/launch/%s/"
EMOJI = {"e-msg": "messages", "e-tg": "telegram", "e-wa": "whatsapp", "e-dc": "discord"}


def text(content, link=None):
    return {"type": "text", "plain_text": content, "annotations": {"bold": False, "color": "default"},
            "text": {"content": content, "link": {"url": link} if link else None}}


def emoji(emoji_id):
    return {"type": "mention", "plain_text": ":%s:" % EMOJI[emoji_id], "annotations": {"bold": False, "color": "default"},
            "mention": {"type": "custom_emoji", "custom_emoji": {"id": emoji_id, "name": EMOJI[emoji_id], "url": "https://example.com/%s.png" % emoji_id}}}


class Report:
    def __init__(self, heading="Communications Attention (Updated 3:05 AM CT)"):
        self.blocks, self.kids, self.patches = {}, {}, []
        self.add("col", "column", "root")
        self.add("attn", "callout", "col", [text("Updated 10:01 AM CT · 3 active exceptions")])
        self.add("comms", "callout", "col", [])
        self.add("head", "heading_3", "comms", [text(heading)])
        self.add("cl", "column_list", "comms")
        self.add("left", "column", "cl")
        self.add("right", "column", "cl")
        for i, (eid, app, label) in enumerate((("e-msg", "messages", "Messages (—)"), ("e-tg", "telegram", "Telegram (—)"),
                                                ("e-wa", "whatsapp", "WhatsApp (—)"), ("e-dc", "discord", "Discord"))):
            self.add("l%d" % i, "paragraph", "left", [emoji(eid), text(" "), text(label, LAUNCH % app)])
        for i, line in enumerate(("Latest: unavailable", "Latest: unavailable", "Latest: unavailable", "Launch only")):
            self.add("r%d" % i, "paragraph", "right", [text(line)])
        self.add("bills", "callout", "col", [text("Bills")])

    def add(self, block_id, kind, parent, rich=None):
        self.blocks[block_id] = {"id": block_id, "type": kind, "parent": {"type": "block_id", "block_id": parent},
                                 kind: {"rich_text": rich} if rich is not None else {}}
        self.kids.setdefault(parent, []).append(block_id)

    def plain(self, block_id):
        return "".join(i["plain_text"] for i in self.blocks[block_id][self.blocks[block_id]["type"]]["rich_text"])

    def call(self, method, path, body=None):
        parts = path.split("?")[0].strip("/").split("/")
        if method == "GET" and len(parts) == 2:
            return deepcopy(self.blocks[parts[1]])
        if method == "GET" and parts[2:] == ["children"]:
            return {"results": [deepcopy(self.blocks[i]) for i in self.kids.get(parts[1], [])], "has_more": False}
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None):
        parts = path.strip("/").split("/")
        if method != "PATCH" or len(parts) != 2:
            raise AssertionError((method, path))
        block = self.blocks[parts[1]]
        kind = block["type"]
        if set(body) != {kind} or set(body[kind]) != {"rich_text"}:
            raise AssertionError("only a block's own rich text is written")
        self.patches.append((parts[1], deepcopy(body)))
        stored = []
        for item in body[kind]["rich_text"]:
            if item["type"] == "mention":
                ce = item["mention"]["custom_emoji"]
                if set(ce) != {"id"}:
                    raise AssertionError("a custom icon is sent by id only")
                stored.append({**emoji(ce["id"]), **({"annotations": item["annotations"]} if "annotations" in item else {})})
            else:
                link = (item["text"].get("link") or {}).get("url")
                stored.append({**text(item["text"]["content"], link), **({"annotations": item["annotations"]} if "annotations" in item else {})})
        block[kind]["rich_text"] = stored
        return deepcopy(block)
