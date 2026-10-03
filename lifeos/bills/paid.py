"""Process explicit Paid flags against the configured Bill Tracker; output is counts only."""
import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.platform import db, limits
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform.snapshot_store import Store
from . import snapshot, state

LOCAL_TZ = ZoneInfo("America/Chicago")
LEDGER = Store("v7_bills_paid_ledger", key_column="page_id", key_type="VARCHAR(64)")
MAX_LIMIT = limits.BILLS_PAID_PER_RUN


class PaidError(NotionError):
    """Fixed codes only; never include a bill's content or identifiers."""


def _today(value=None):
    value = value or datetime.now(LOCAL_TZ)
    return value.astimezone(LOCAL_TZ).date() if value.tzinfo else value.date()


def _day(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _source_id(client):
    return (client.source or "").strip().replace("collection://", "").replace("-", "").lower()


def _page_key(page_id):
    return page_id.replace("-", "").lower()


def _compact_page(page, source):
    if not isinstance(page, dict) or not isinstance(page.get("id"), str):
        raise PaidError("BILLS_PAGE_INVALID")
    parent = page.get("parent")
    actual = parent.get("data_source_id") if isinstance(parent, dict) and parent.get("type") == "data_source_id" else None
    if not actual or actual.replace("-", "").lower() != source:
        raise PaidError("BILLS_SOURCE_MISMATCH")
    row = snapshot.compact(page)
    props = page["properties"]
    if props.get("Paid", {}).get("type") != "checkbox":
        raise PaidError("BILLS_PROPERTY_INVALID")
    if props.get("Last Paid", {}).get("type") not in (None, "date"):
        raise PaidError("BILLS_PROPERTY_INVALID")
    if props.get("Due Date", {}).get("type") not in (None, "date"):
        raise PaidError("BILLS_PROPERTY_INVALID")
    if props.get("Status", {}).get("type") not in ("select", "status"):
        raise PaidError("BILLS_PROPERTY_INVALID")
    return row


ROW_CODES = ("BILLS_PAGE_INVALID", "BILLS_PROPERTY_INVALID")     # a bad row is skipped and counted; a wrong source, pagination or duplicate stays fatal


def _query(client, source):
    rows, cursor, seen_cursor, seen_ids, invalid = [], None, set(), set(), 0
    try:
        while True:
            body = {"page_size": 100}
            if cursor:
                if cursor in seen_cursor:
                    raise PaidError("BILLS_PAGINATION_INVALID")
                seen_cursor.add(cursor)
                body["start_cursor"] = cursor
            result = client.query_data_source(body=body)
            items, more = result.get("results"), result.get("has_more")
            if not isinstance(items, list) or not isinstance(more, bool):
                raise PaidError("BILLS_PAGE_INCOMPLETE")
            for item in items:
                try:
                    row = _compact_page(item, source)
                except snapshot.BillsError:
                    invalid += 1
                    continue
                except PaidError as error:
                    if str(error) not in ROW_CODES:
                        raise
                    invalid += 1
                    continue
                key = _page_key(row["page_id"])
                if key in seen_ids:
                    raise PaidError("BILLS_DUPLICATE_PAGE_ID")
                seen_ids.add(key)
                rows.append(row)
            if not more:
                return rows, invalid
            cursor = result.get("next_cursor")
            if not isinstance(cursor, str) or not cursor:
                raise PaidError("BILLS_PAGE_INCOMPLETE")
    except NotionError:
        raise
    except PaidError:
        raise
    except Exception:
        raise PaidError("BILLS_READ_FAILED") from None


def _fresh(client, page_id, source):
    try:
        page = client.get_page(page_id)
        if page.get("id") != page_id:
            raise PaidError("BILLS_PAGE_ID_MISMATCH")
        return _compact_page(page, source), page
    except NotionError:
        raise
    except PaidError:
        raise
    except Exception:
        raise PaidError("BILLS_READ_FAILED") from None


def _write(client, page_id, properties, counts):
    counts["writes"] += 1
    try:
        client.update_page_properties(page_id, properties)
        return True
    except Exception:
        # A timed-out PATCH may have committed. The caller must inspect the row before retrying.
        return False


def _ledger_all(connection):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT page_id, payload FROM {LEDGER.table}")
        raw = cursor.fetchall()
    result = {}
    for page_id, payload in raw:
        try:
            entry = json.loads(payload)
        except (TypeError, ValueError):
            raise PaidError("BILLS_LEDGER_INVALID") from None
        if not isinstance(entry, dict) or entry.get("page_id") != page_id or entry.get("step") not in (
                "prepared", "advanced", "verified", "complete"):
            raise PaidError("BILLS_LEDGER_INVALID")
        result[page_id] = entry
    return result


def _ledger_save(connection, entry):
    try:
        entry["schema"] = 1
        entry["taken_at"] = datetime.now(LOCAL_TZ).isoformat()
        LEDGER.save_verified(connection, entry["page_id"], entry,
                             lambda code: PaidError("BILLS_LEDGER_" + code))
    except PaidError:
        raise
    except Exception:
        raise PaidError("BILLS_LEDGER_WRITE_FAILED") from None


def _date_property(value):
    return {"date": {"start": value}}


def _status_property(page, name):
    kind = page["properties"]["Status"]["type"]
    return {kind: {"name": name}}


def _targets_match(row, entry, recurring):
    if row.get("Last Paid") != entry["today"]:
        return False
    if recurring:
        due = _day(entry.get("captured_next_due"))
        next_due = _day(row.get("Next Due"))
        return bool(due and _day(row.get("Due Date")) == due and next_due and next_due > due)
    return row.get("Status") == "Inactive"


def _verify_source(client, page_id, source, entry, recurring, paid):
    page = client.get_page(page_id)
    if page.get("id") != page_id:
        raise PaidError("BILLS_PAGE_ID_MISMATCH")
    row = _compact_page(page, source)
    if row.get("Paid") is not paid or not _targets_match(row, entry, recurring):
        return None, page
    return row, page


def _process(client, connection, row, source, today, entry, counts, live):
    if entry is not None and entry.get("step") == "complete" and entry.get("today") == today.isoformat():
        counts["review"] += 1                       # D86: Paid ticked again on the day it was processed is not a second payment; left checked for a person
        return
    is_new = entry is None or entry.get("step") == "complete"
    is_resume = not is_new
    cycle = row.get("Cycle")
    recurring = cycle in state.SUPPORTED_CYCLES
    one_time = cycle == "One Time"
    status_ok = row.get("Status") == "Active" or (
        is_resume and one_time and row.get("Status") == "Inactive" and row.get("Last Paid") == entry.get("today"))
    if not status_ok or not (recurring or one_time):
        counts["review"] += 1
        return
    if is_new:
        if row.get("Paid") is not True:
            counts["review"] += 1
            return
        if live:
            try:
                row, page = _fresh(client, row["page_id"], source)  # capture only from the exact current row
            except Exception:
                counts["failed"] += 1
                return
            if row.get("Paid") is not True or row.get("Status") != "Active" or row.get("Cycle") != cycle:
                counts["review"] += 1
                return
        captured_day = _day(row.get("Next Due")) if recurring else None
        if recurring and not captured_day:
            counts["review"] += 1
            return
        entry = {"page_id": row["page_id"], "captured_next_due": captured_day.isoformat() if captured_day else None,
                 "today": today.isoformat(), "step": "prepared", "cycle": cycle}
        if not live:
            counts["advanced" if recurring else "one_time"] += 1
            return
        _ledger_save(connection, entry)                # write-ahead, before the first Notion mutation
    else:
        counts["resumed"] += 1
        if (not _day(entry.get("today")) or entry.get("cycle") != cycle
                or (recurring and not _day(entry.get("captured_next_due")))):
            counts["failed"] += 1
            return
        today = date.fromisoformat(entry["today"])
        if entry.get("step") == "verified" and row.get("Paid") is False:
            try:
                verified, _ = _verify_source(client, row["page_id"], source, entry, recurring, False)
            except Exception:
                verified = None
            if verified is None:
                counts["failed"] += 1
                return
            entry["step"] = "complete"
            _ledger_save(connection, entry)
            counts["advanced" if recurring else "one_time"] += 1
            return
        if row.get("Paid") is not True:
            counts["failed"] += 1
            return

    # Re-fetch the exact command row before any Notion write; on resume, retain the ledger date.
    if is_new:
        fresh = row
    else:
        try:
            fresh, page = _fresh(client, row["page_id"], source)
        except Exception:
            counts["failed"] += 1
            return
    expected_status = "Inactive" if is_resume and one_time and fresh.get("Last Paid") == entry.get("today") else "Active"
    if fresh.get("Paid") is not True or fresh.get("Status") != expected_status or fresh.get("Cycle") != cycle:
        counts["review"] += 1
        return
    if recurring:
        due = _day(entry.get("captured_next_due"))
        if not due:
            counts["review"] += 1
            return
        target_due = due.isoformat()
        need_write = fresh.get("Last Paid") != entry["today"] or _day(fresh.get("Due Date")) != due
        if need_write:
            ok = _write(client, row["page_id"], {
                "Last Paid": _date_property(entry["today"]), "Due Date": _date_property(target_due)}, counts)
            try:
                fresh, page = _verify_source(client, row["page_id"], source, entry, True, True)
            except Exception:
                fresh = None
            if fresh is None:
                counts["failed"] += 1
                return
        entry["step"] = "advanced"
        _ledger_save(connection, entry)
        try:
            fresh, page = _verify_source(client, row["page_id"], source, entry, True, True)
        except Exception:
            fresh = None
        if fresh is None:
            counts["failed"] += 1
            return
    else:
        if fresh.get("Last Paid") != entry["today"] or fresh.get("Status") != "Inactive":
            status_property = _status_property(page, "Inactive")
            _write(client, row["page_id"], {"Last Paid": _date_property(entry["today"]),
                                            "Status": status_property}, counts)
        entry["step"] = "advanced"
        _ledger_save(connection, entry)
        try:
            fresh, page = _verify_source(client, row["page_id"], source, entry, False, True)
        except Exception:
            fresh = None
        if fresh is None:
            counts["failed"] += 1
            return
    entry["step"] = "verified"
    _ledger_save(connection, entry)
    if fresh.get("Paid") is True:
        _write(client, row["page_id"], {"Paid": {"checkbox": False}}, counts)
    try:
        verified, _ = _verify_source(client, row["page_id"], source, entry, recurring, False)
    except Exception:
        verified = None
    if verified is None:
        counts["failed"] += 1
        return
    entry["step"] = "complete"
    _ledger_save(connection, entry)
    counts["advanced" if recurring else "one_time"] += 1


def run(limit, live, environ=os.environ, client=None, now=None, connect=None):
    client = client or Client(environ, token_name="NOTION_BILLS_TOKEN", source_name="NOTION_BILLS_DATA_SOURCE_ID")
    source = _source_id(client)
    if len(source) != 32 or any(ch not in "0123456789abcdef" for ch in source):
        raise PaidError("BILLS_SOURCE_INVALID")
    rows, invalid = _query(client, source)           # a partial answer is never interpreted as an empty queue; one malformed row never blocks the rest
    paid = [row for row in rows if row.get("Paid") is True]
    now = now or datetime.now(LOCAL_TZ)
    today = _today(now)
    cap = min(MAX_LIMIT, max(0, int(limit)))
    by_id = {row["page_id"]: row for row in rows}
    counts = {"paid_rows": len(paid), "advanced": 0, "one_time": 0, "review": 0,
              "failed": 0, "resumed": 0, "skipped": 0, "writes": 0, "invalid": invalid}
    if not live:
        counts["skipped"] = max(0, len(paid) - cap)
        for row in paid[:cap]:
            if row.get("Status") != "Active" or not (row.get("Cycle") in state.SUPPORTED_CYCLES or row.get("Cycle") == "One Time"):
                counts["review"] += 1
            elif row.get("Cycle") in state.SUPPORTED_CYCLES and not _day(row.get("Next Due")):
                counts["review"] += 1
            else:
                counts["advanced" if row.get("Cycle") in state.SUPPORTED_CYCLES else "one_time"] += 1
        return counts
    try:
        with (connect or db.connect)() as connection:
            LEDGER.ensure(connection)
            ledger = _ledger_all(connection)
            queued = {row["page_id"]: by_id[row["page_id"]] for row in paid}
            for page_id, entry in ledger.items():
                if entry.get("step") != "complete":
                    queued[page_id] = by_id.get(page_id)
            counts["skipped"] = max(0, len(queued) - cap)
            for page_id, row in list(queued.items())[:cap]:
                if row is None:
                    counts["failed"] += 1
                    continue
                if row.get("Paid") is not True and ledger.get(page_id, {}).get("step") != "verified":
                    counts["review"] += 1
                    continue
                try:
                    _process(client, connection, row, source, today, ledger.get(page_id), counts, True)
                except Exception:
                    counts["failed"] += 1
    except NotionError:
        raise
    except PaidError:
        raise
    except Exception:
        raise PaidError("BILLS_PAID_FAILED") from None
    return counts
