"""Synthetic Gmail and Notion stores for Amazon Orders tests."""
from copy import deepcopy

from lifeos.amazon import events


class AmazonGmail:
    def __init__(self, messages=None, *, fail_read=None, fail_file=None, trace=None):
        self.messages = deepcopy(messages or {})
        self.fail_read, self.fail_file = fail_read, fail_file
        self.calls, self.label_reads = [], 0
        self.trace = trace if trace is not None else []

    def label_id(self, name, create=False):
        self.calls.append(("label_id", name, create))
        self.trace.append(("gmail", "label_id"))
        if name != "Amazon" or create:
            raise AssertionError("the Amazon label must already exist")
        return "label-amazon"

    def list_ids_complete(self, query, limit):
        self.calls.append(("list", query, limit))
        self.trace.append(("gmail", "list"))
        import re
        from datetime import datetime, timezone
        after = re.search(r"after:(\d+)", query)
        floor = datetime.fromtimestamp(int(after.group(1)), timezone.utc) if after else None
        ids = [key for key, row in self.messages.items()
               if floor is None or datetime.fromisoformat(row["received_at"]) >= floor]
        if len(ids) > limit:
            raise RuntimeError("GMAIL_LIST_LIMIT_EXCEEDED")
        return ids

    def message_record(self, message_id):
        self.calls.append(("read", message_id))
        self.trace.append(("gmail", "read"))
        if self.fail_read == message_id:
            raise RuntimeError("synthetic read failure")
        return deepcopy(self.messages[message_id])

    def apply_amazon(self, message_id, label_id):
        self.calls.append(("modify", message_id, label_id))
        self.trace.append(("gmail", "modify"))
        if self.fail_file == message_id:
            raise RuntimeError("synthetic filing failure")
        row = self.messages[message_id]
        row["label_ids"] = sorted((set(row.get("label_ids", [])) | {label_id}) - {"INBOX"})
        return {}

    def message_labels(self, message_id):
        self.calls.append(("labels", message_id))
        self.trace.append(("gmail", "labels"))
        return list(self.messages[message_id].get("label_ids", []))


def _text(value):
    return [{"type": "text", "text": {"content": str(value)}, "plain_text": str(value)}] if value else []


def notion_page(order_id, row, page_id):
    properties = {
        "Order ID": {"type": "title", "title": _text(row.get("Order ID", order_id))},
        "Status": {"type": "select", "select": {"name": row.get("Status")} if row.get("Status") else None},
        "Ordered At": {"type": "date", "date": {"start": row["Ordered At"]} if row.get("Ordered At") else None},
        "Latest Event At": {"type": "date", "date": {"start": row["Latest Event At"]} if row.get("Latest Event At") else None},
        "Grand Total": {"type": "number", "number": float(row["Grand Total"]) if row.get("Grand Total") is not None else None},
        "Item Summary": {"type": "rich_text", "rich_text": _text(row.get("Item Summary"))},
        "Item Count": {"type": "number", "number": row.get("Item Count")},
        "Amazon Order URL": {"type": "url", "url": row.get("Amazon Order URL")},
        "Source Message IDs": {"type": "rich_text", "rich_text": _text(row.get("Source Message IDs"))},
        "Last Source Subject": {"type": "rich_text", "rich_text": _text(row.get("Last Source Subject"))},
        "Last Reconciled At": {"type": "date", "date": {"start": row["Last Reconciled At"]} if row.get("Last Reconciled At") else None},
        "Needs Review": {"type": "checkbox", "checkbox": bool(row.get("Needs Review", False))},
    }
    return {"id": page_id, "properties": properties}


class AmazonNotion:
    def __init__(self, rows=None, *, mismatch=False, duplicates=None, trace=None):
        self.rows = deepcopy(rows or {})
        self.duplicates = set(duplicates or ())
        self.mismatch, self.calls, self.serial = mismatch, [], 0
        self.trace = trace if trace is not None else []
        self.schema = {name: {"type": kind} for name, kind in {
            "Order ID": "title", "Status": "select", "Ordered At": "date", "Latest Event At": "date",
            "Grand Total": "number", "Item Summary": "rich_text", "Item Count": "number",
            "Amazon Order URL": "url", "Source Message IDs": "rich_text", "Last Source Subject": "rich_text",
            "Last Reconciled At": "date", "Needs Review": "checkbox"}.items()}
        self.schema["Status"]["select"] = {"options": [{"name": name} for name in ("ORDERED", "SHIPPED", "DELIVERED", "REVIEW")]}

    def call(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        self.trace.append(("notion", method))
        if method == "GET" and path.startswith("/data_sources/"):
            return {"properties": deepcopy(self.schema)}
        if method == "GET" and path.startswith("/pages/"):
            page_id = path.rsplit("/", 1)[1]
            for order_id, page in self.rows.items():
                if page["id"] == page_id:
                    actual = deepcopy(page)
                    if self.mismatch:
                        actual["properties"]["Status"]["select"]["name"] = "SHIPPED"
                    return actual
            raise RuntimeError("NOTION_HTTP_404")
        raise AssertionError((method, path))

    def query_data_source(self, source_id, body):
        self.calls.append(("QUERY", source_id, deepcopy(body)))
        self.trace.append(("notion", "QUERY"))
        if "filter" not in body:                       # watermark read: newest Latest Event At first
            dated = [p for p in self.rows.values() if p["properties"]["Latest Event At"]["date"]]
            dated.sort(key=lambda p: p["properties"]["Latest Event At"]["date"]["start"], reverse=True)
            return {"results": [deepcopy(p) for p in dated[:body["page_size"]]], "has_more": False}
        order_id = body["filter"]["title"]["equals"]
        if order_id in self.duplicates:
            page = self.rows.get(order_id)
            if page:
                return {"results": [deepcopy(page), deepcopy(page)], "has_more": False}
        page = self.rows.get(order_id)
        return {"results": [deepcopy(page)] if page else [], "has_more": False}

    def call_once(self, method, path, body=None):
        self.calls.append((method + "_ONCE", path, deepcopy(body)))
        self.trace.append(("notion", method + "_ONCE"))
        props = body["properties"]
        if method == "POST" and path == "/pages":
            order_id = "".join(x.get("text", {}).get("content", "") for x in props["Order ID"]["title"])
            self.serial += 1
            page_id = f"page-{self.serial}"
            row = self._decode(props)
            self.rows[order_id] = notion_page(order_id, row, page_id)
            return {"id": page_id}
        if method == "PATCH" and path.startswith("/pages/"):
            page_id = path.rsplit("/", 1)[1]
            for order_id, page in self.rows.items():
                if page["id"] == page_id:
                    decoded = self._decode(props)
                    old = self._row(page)
                    old.update(decoded)
                    self.rows[order_id] = notion_page(order_id, old, page_id)
                    return {}
        raise AssertionError((method, path))

    @staticmethod
    def _decode(props):
        out = {}
        for name, prop in props.items():
            kind = next(iter(prop))
            value = prop[kind]
            if kind in ("title", "rich_text"):
                out[name] = "".join(item["text"]["content"] for item in value)
            elif kind == "select":
                out[name] = value["name"] if value else None
            elif kind == "date":
                out[name] = value["start"] if value else None
            elif kind in ("number", "url", "checkbox"):
                out[name] = value
        return out

    @staticmethod
    def _row(page):
        from lifeos.amazon.stage import _row_from_page
        return _row_from_page(page)


def message(message_id, local, order_id="123-1234567-1234567", *, at="2026-10-03T12:00:00+00:00", body=None,
            subject="Example item", labels=None):
    sender = f"{local}{chr(64)}amazon.com"
    return {"id": message_id, "sender": sender, "subject": subject, "received_at": at,
            "body_text": body if body is not None else f"Order {order_id}\nGrand Total: $12.34",
            "label_ids": labels if labels is not None else ["INBOX"]}
