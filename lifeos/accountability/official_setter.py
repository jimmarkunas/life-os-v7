"""The one writer of a Runtime Configuration row, and it can change exactly one: `V2_OFFICIAL`, only its Value (and Revision + 1).

Any other row fails closed (RUNTIME_CONFIG_WRITE_NOT_ALLOWED) before any read or write. A dry run (the default)
only reports; a live write is read back and fails (RUNTIME_CONFIG_WRITE_VERIFY_FAILED) unless the new Value and
Revision are seen."""

from lifeos.platform import runtime_config

ALLOWED = ("V2_OFFICIAL",)
NOT_ALLOWED = "RUNTIME_CONFIG_WRITE_NOT_ALLOWED"
ROW_MISSING = "RUNTIME_CONFIG_WRITE_ROW_MISSING"
ROW_REPEATED = "RUNTIME_CONFIG_WRITE_ROW_REPEATED"
VERIFY_FAILED = "RUNTIME_CONFIG_WRITE_VERIFY_FAILED"
PROOF_EXPECTATION = "RUNTIME_CONFIG_PROOF_EXPECTATION_MISMATCH"
PROOF_UNVERIFIED = "RUNTIME_CONFIG_PROOF_UNVERIFIED"
PROOF_VALUE = "off"


class WriteError(RuntimeError):
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
    """-> {"status": "dry_run" | "unchanged" | "written", "row": name}. `allowed` is for tests only."""
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
    client.update_page_properties(
        page["id"],
        {"Value": {"rich_text": [{"type": "text", "text": {"content": value}}]}, "Revision": {"number": new_revision}},
    )
    after = _rows(client, source, name)
    if len(after) != 1 or after[0][1][1] != value or after[0][1][3] != new_revision:
        raise WriteError(VERIFY_FAILED)
    return {"status": "written", "row": name}


def prove_same_value(client, source, live=False, expected_revision=None):
    """-> {"status": "dry_run" | "proved", ...}: one manually started same-value update of `V2_OFFICIAL` (off over off)
    that shows the integration can edit the row without changing it. The row must hold exactly `off`, once, and
    (when given) at the expected revision before the request. Exactly one request is sent and it is never retried;
    the proof holds only when the provider accepts it and an independent read-back shows every property of the page
    (value, revision and everything else) unchanged. Any drift, refusal or unreadable result raises
    RUNTIME_CONFIG_PROOF_UNVERIFIED: stop and report."""
    found = _rows(client, source, "V2_OFFICIAL")
    if not found:
        raise WriteError(ROW_MISSING)
    if len(found) > 1:
        raise WriteError(ROW_REPEATED)
    page, (_, value, _kind, revision, _active) = found[0]
    if value != PROOF_VALUE or (expected_revision is not None and revision != expected_revision):
        raise WriteError(PROOF_EXPECTATION)
    report = {"row": "V2_OFFICIAL", "value": value, "revision": revision}
    if not live:
        return {"status": "dry_run", **report}
    before = page.get("properties")
    try:
        client.update_page_properties(
            page["id"],
            {
                "Value": {"rich_text": [{"type": "text", "text": {"content": PROOF_VALUE}}]},
                "Revision": {"number": revision},
            },
        )
        after = _rows(client, source, "V2_OFFICIAL")
    except Exception:  # noqa: BLE001 - a refused or unreadable request is not a proof, and is never retried
        raise WriteError(PROOF_UNVERIFIED) from None
    if (
        len(after) != 1
        or after[0][0].get("properties") != before
        or after[0][1][1] != value
        or after[0][1][3] != revision
    ):
        raise WriteError(PROOF_UNVERIFIED)
    return {"status": "proved", **report, "edited": after[0][0].get("last_edited_time") != page.get("last_edited_time")}


def official_setter(client, source, live=False):
    def setter():
        out = set_value(client, source, "V2_OFFICIAL", "on", live=live)
        print(f"promotion: V2_OFFICIAL {out['status']}")
        return out

    return setter
