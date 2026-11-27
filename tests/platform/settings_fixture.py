"""test data: a fake Runtime Configuration data source (the one settings source)."""

from lifeos.platform import runtime_config as rc
from lifeos.platform import settings

SOURCE = "00000000-0000-0000-0000-000000001002"
SECRET = "synthetic-sentinel-value-7731"
ENV = {rc.SOURCE_NAME: SOURCE}


def row(name, value, kind=None, revision=1, active=True, **skip):
    kind = kind or ("address" if settings.is_address(name) else "switch")
    props = {
        "Name": {"type": "title", "title": [{"plain_text": name}]},
        "Value": {"type": "rich_text", "rich_text": [{"plain_text": value}] if value else []},
        "Kind": {"type": "select", "select": {"name": kind}},
        "Revision": {"type": "number", "number": revision},
        "Active": {"type": "checkbox", "checkbox": active},
    }
    for key in skip:
        props.pop(key)
    return {"id": f"row-{name}", "properties": props}


class Source:
    """A data source addressed by ID."""

    def __init__(self, rows, pages=1, broken=False, title="Runtime Configuration"):
        self.rows, self.pages, self.broken, self.title, self.calls = rows, pages, broken, title, []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if self.broken:
            raise RuntimeError(SECRET)
        assert (method, path) == ("POST", f"/data_sources/{SOURCE}/query"), (method, path)
        start = int((body or {}).get("start_cursor") or 0)
        size = -(-len(self.rows) // self.pages) or 1
        chunk = self.rows[start : start + size]
        more = start + size < len(self.rows)
        return {"results": chunk, "has_more": more, "next_cursor": str(start + size) if more else None}


def from_lines(lines, **kw):
    """`NAME value` lines (comment lines skipped) -> a Source with one row each"""
    rows = []
    for line in lines:
        text = line.strip()
        if text and not text.startswith("#") and " " in text:
            name, value = text.split(" ", 1)
            rows.append(row(name, value.strip()))
    return Source(rows, **kw)
