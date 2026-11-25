"""LIFE-976: the one writer of a Runtime Configuration row, and it can change exactly one: `V2_OFFICIAL`, only its Value (and Revision + 1).

The LIFE-918 promotion check (lifeos.accountability.promotion) calls `official_setter(...)` as its `setters` argument after a pass, with the kill switch on and the quarter boundary reached; those three gates stay in the check itself.
The allow-list is enforced here, before any read or write: any other row name fails closed with RUNTIME_CONFIG_WRITE_NOT_ALLOWED. A dry run (the default) reads the row and reports the intended write; it writes nothing.
A live write reads the row back and must see the new Value and Revision, or it fails with RUNTIME_CONFIG_WRITE_VERIFY_FAILED. A second run on a row that already holds the value writes nothing. Uses the existing Notion client;
no new token, integration or scheduler. Fixed codes and counts only, never a value."""
from lifeos.platform import runtime_config

ALLOWED = ("V2_OFFICIAL",)
NOT_ALLOWED = "RUNTIME_CONFIG_WRITE_NOT_ALLOWED"
ROW_MISSING = "RUNTIME_CONFIG_WRITE_ROW_MISSING"
ROW_REPEATED = "RUNTIME_CONFIG_WRITE_ROW_REPEATED"
VERIFY_FAILED = "RUNTIME_CONFIG_WRITE_VERIFY_FAILED"


class WriteError(RuntimeError):
    """A fixed code, never a message with content."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _rows(client, source, name):
    found = []
    for page in runtime_config.fetch(client, source):
        row = runtime_config._row(page)
        if row and row[0] == name and row[4]:
            found.append((page, row))
    return found


def set_value(client, source, name, value, live=False, allowed=ALLOWED):
    """-> {"status": "dry_run" | "unchanged" | "written", "row": name}. `allowed` is for tests only; production calls use the module constant."""
    if name not in allowed:
        raise WriteError(NOT_ALLOWED)
    found = _rows(client, source, name)
    if not found:
        raise WriteError(ROW_MISSING)
    if len(found) > 1:
        raise WriteError(ROW_REPEATED)
    page, (_, current, _kind, revision, _active) = found[0]
    if current == value:
        return {"status": "unchanged", "row": name}
    if not live:
        return {"status": "dry_run", "row": name}
    new_revision = int(revision or 0) + 1
    client.update_page_properties(page["id"], {"Value": {"rich_text": [{"type": "text", "text": {"content": value}}]}, "Revision": {"number": new_revision}})
    after = _rows(client, source, name)
    if len(after) != 1 or after[0][1][1] != value or after[0][1][3] != new_revision:
        raise WriteError(VERIFY_FAILED)
    return {"status": "written", "row": name}


def official_setter(client, source, live=False):
    """The `setters` argument of promotion.the check: sets V2_OFFICIAL to "on". A dry run prints one counts-only line and writes nothing."""
    def setter():
        out = set_value(client, source, "V2_OFFICIAL", "on", live=live)
        print(f"promotion: V2_OFFICIAL {out['status']}")
        return out
    return setter
