"""Process the Pay tag (Paid, Auto) against the configured Bill Tracker; output is counts only."""

import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.platform import db, limits
from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform.snapshot_store import Store
from . import snapshot, state
from .state import _day

PAY_UNPAID = {"select": {"name": "Unpaid"}}

LOCAL_TZ = ZoneInfo("America/Chicago")
LEDGER = Store("v7_bills_paid_ledger", key_column="page_id", key_type="VARCHAR(64)")
MAX_LIMIT = limits.BILLS_PAID_PER_RUN


class PaidError(NotionError):
    """Fixed codes only; never include a bill's content or identifiers."""


def _today(value=None):
    value = value or datetime.now(LOCAL_TZ)
    return value.astimezone(LOCAL_TZ).date() if value.tzinfo else value.date()


def _source_id(client):
    return (client.source or "").strip().replace("collection://", "").replace("-", "").lower()


def _page_key(page_id):
    return page_id.replace("-", "").lower()


def _compact_page(page, source):
    if not isinstance(page, dict) or not isinstance(page.get("id"), str):
        raise PaidError("BILLS_PAGE_INVALID")
    parent = page.get("parent")
    actual = (
        parent.get("data_source_id") if isinstance(parent, dict) and parent.get("type") == "data_source_id" else None
    )
    if not actual or actual.replace("-", "").lower() != source:
        raise PaidError("BILLS_SOURCE_MISMATCH")
    row = snapshot.compact(page)
    props = page["properties"]
    if props.get("Last Paid", {}).get("type") not in (None, "date"):
        raise PaidError("BILLS_PROPERTY_INVALID")
    if props.get("Due Date", {}).get("type") not in (None, "date"):
        raise PaidError("BILLS_PROPERTY_INVALID")
    if props.get("Status", {}).get("type") not in ("select", "status"):
        raise PaidError("BILLS_PROPERTY_INVALID")
    pay = props.get("Pay")
    if not isinstance(pay, dict) or pay.get("type") != "select":
        # a missing or retyped Pay column is a schema change: stop, write nothing
        raise PaidError("BILLS_PAY_PROPERTY_INVALID")
    item = pay.get("select")
    row["Pay"] = item.get("name") if isinstance(item, dict) else None
    return row


def _flag(row, kind):
    return row.get("Pay") == ("Auto" if kind == "auto" else "Paid")


def _auto_due(row, today):
    due = _day(row.get("Due Date"))
    return (
        row.get("Pay") == "Auto"
        and row.get("Status") == "Active"
        and row.get("Cycle") in state.SUPPORTED_CYCLES
        and due is not None
        and due < today
    )


def _triggered(row, kind, today):
    return _auto_due(row, today) if kind == "auto" else _flag(row, "paid")


ROW_CODES = ("BILLS_PAGE_INVALID", "BILLS_PROPERTY_INVALID")


def _query(client, source):
    rows, cursor, seen_cursor, seen_ids, invalid = [], None, set(), set(), 0
    with fixed(Exception, PaidError, "BILLS_READ_FAILED", keep=(NotionError, PaidError)):
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


def _fresh(client, page_id, source):
    with fixed(Exception, PaidError, "BILLS_READ_FAILED", keep=(NotionError, PaidError)):
        page = client.get_page(page_id)
        if page.get("id") != page_id:
            raise PaidError("BILLS_PAGE_ID_MISMATCH")
        return _compact_page(page, source), page


def _write(client, page_id, properties, counts):
    counts["writes"] += 1
    try:
        client.update_page_properties(page_id, properties)
        return True
    except Exception:
        # A timed-out PATCH may have committed: inspect the row before retrying.
        return False


def _ledger_all(connection):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT page_id, payload FROM {LEDGER.table}")
        raw = cursor.fetchall()
    result = {}
    for page_id, payload in raw:
        with fixed((TypeError, ValueError), PaidError, "BILLS_LEDGER_INVALID"):
            entry = json.loads(payload)
        if (
            not isinstance(entry, dict)
            or entry.get("page_id") != page_id
            or entry.get("step") not in ("prepared", "advanced", "verified", "complete")
        ):
            raise PaidError("BILLS_LEDGER_INVALID")
        result[page_id] = entry
    return result


def _ledger_save(connection, entry):
    with fixed(Exception, PaidError, "BILLS_LEDGER_WRITE_FAILED", keep=PaidError):
        entry["schema"] = 1
        entry["taken_at"] = datetime.now(LOCAL_TZ).isoformat()
        LEDGER.save_verified(connection, entry["page_id"], entry, lambda code: PaidError("BILLS_LEDGER_" + code))


def _date_property(value):
    return {"date": {"start": value}}


def _status_property(page, name):
    kind = page["properties"]["Status"]["type"]
    return {kind: {"name": name}}


def _targets_match(row, entry, recurring):
    if row.get("Last Paid") != entry.get("last_paid", entry["today"]):
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
    if _flag(row, entry.get("kind", "paid")) is not paid or not _targets_match(row, entry, recurring):
        return None, page
    return row, page


def _process(client, connection, row, source, today, entry, counts, live, kind="paid"):
    if entry is not None and entry.get("step") == "complete" and entry.get("today") == today.isoformat():
        counts["review"] += 1
        return
    is_new = entry is None or entry.get("step") == "complete"
    is_resume = not is_new
    # `auto` advances a due Auto bill and keeps the tag; `paid` advances and resets it
    kind = kind if is_new else entry.get("kind", "paid")
    auto = kind == "auto"
    cycle = row.get("Cycle")
    recurring = cycle in state.SUPPORTED_CYCLES
    one_time = cycle == "One Time"
    status_ok = row.get("Status") == "Active" or (
        is_resume and one_time and row.get("Status") == "Inactive" and row.get("Last Paid") == entry.get("today")
    )
    if not status_ok or not (recurring or one_time) or (auto and not recurring):
        counts["review"] += 1
        return
    if is_new:
        if not _triggered(row, kind, today):
            counts["review"] += 1
            return
        if live:
            try:
                row, page = _fresh(client, row["page_id"], source)
            except Exception:
                counts["failed"] += 1
                return
            if not _triggered(row, kind, today) or row.get("Status") != "Active" or row.get("Cycle") != cycle:
                counts["review"] += 1
                return
        captured_day = _day(row.get("Next Due")) if recurring else None
        if recurring and not captured_day:
            counts["review"] += 1
            return
        # the formula must name a date after the due date, or nothing is advanced
        if auto and not captured_day > _day(row.get("Due Date")):
            counts["review"] += 1
            return
        entry = {
            "page_id": row["page_id"],
            "captured_next_due": captured_day.isoformat() if captured_day else None,
            "today": today.isoformat(),
            "step": "prepared",
            "cycle": cycle,
            "kind": kind,
            "last_paid": _day(row.get("Due Date")).isoformat() if auto else today.isoformat(),
        }
        if not live:
            counts["advanced" if recurring else "one_time"] += 1
            return
        _ledger_save(connection, entry)
    else:
        counts["resumed"] += 1
        if (
            not _day(entry.get("today"))
            or entry.get("cycle") != cycle
            or (recurring and not _day(entry.get("captured_next_due")))
        ):
            counts["failed"] += 1
            return
        today = date.fromisoformat(entry["today"])
        if entry.get("step") == "verified" and _flag(row, kind) is auto:
            try:
                verified, _ = _verify_source(client, row["page_id"], source, entry, recurring, auto)
            except Exception:
                verified = None
            if verified is None:
                counts["failed"] += 1
                return
            entry["step"] = "complete"
            _ledger_save(connection, entry)
            counts["advanced" if recurring else "one_time"] += 1
            return
        if not _flag(row, kind):
            counts["failed"] += 1
            return

    if is_new:
        fresh = row
    else:
        try:
            fresh, page = _fresh(client, row["page_id"], source)
        except Exception:
            counts["failed"] += 1
            return
    expected_status = (
        "Inactive" if is_resume and one_time and fresh.get("Last Paid") == entry.get("today") else "Active"
    )
    if not _flag(fresh, kind) or fresh.get("Status") != expected_status or fresh.get("Cycle") != cycle:
        counts["review"] += 1
        return
    if recurring:
        due = _day(entry.get("captured_next_due"))
        if not due:
            counts["review"] += 1
            return
        target_due = due.isoformat()
        last_paid = entry.get("last_paid", entry["today"])
        need_write = fresh.get("Last Paid") != last_paid or _day(fresh.get("Due Date")) != due
        if need_write:
            _write(
                client,
                row["page_id"],
                {"Last Paid": _date_property(last_paid), "Due Date": _date_property(target_due)},
                counts,
            )
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
            _write(
                client, row["page_id"], {"Last Paid": _date_property(entry["today"]), "Status": status_property}, counts
            )
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
    reset = {}
    if not auto:
        if fresh.get("Pay") == "Paid":
            reset["Pay"] = PAY_UNPAID
    if reset:
        _write(client, row["page_id"], reset, counts)
    try:
        verified, _ = _verify_source(client, row["page_id"], source, entry, recurring, auto)
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
    rows, invalid = _query(client, source)
    now = now or datetime.now(LOCAL_TZ)
    today = _today(now)
    paid = [row for row in rows if _flag(row, "paid")]
    autos = [row for row in rows if _auto_due(row, today) and not _flag(row, "paid")]
    untagged = [
        row for row in rows if row.get("Status") == "Active" and row.get("Pay") is None and not _flag(row, "paid")
    ]
    cap = min(MAX_LIMIT, max(0, int(limit)))
    by_id = {row["page_id"]: row for row in rows}
    counts = {
        "paid_rows": len(paid),
        "auto_rows": len(autos),
        "advanced": 0,
        "one_time": 0,
        "review": 0,
        "failed": 0,
        "resumed": 0,
        "skipped": 0,
        "writes": 0,
        "invalid": invalid,
        "backfilled": 0,
    }
    if not live:
        counts["skipped"] = max(0, len(paid) + len(autos) - cap)
        counts["backfilled"] = len(untagged)
        for row in [*paid, *autos][:cap]:
            if row.get("Status") != "Active" or not (
                row.get("Cycle") in state.SUPPORTED_CYCLES or row.get("Cycle") == "One Time"
            ):
                counts["review"] += 1
            elif row.get("Cycle") in state.SUPPORTED_CYCLES and not _day(row.get("Next Due")):
                counts["review"] += 1
            elif row in autos and not _day(row.get("Next Due")) > _day(row.get("Due Date")):
                counts["review"] += 1
            else:
                counts["advanced" if row.get("Cycle") in state.SUPPORTED_CYCLES else "one_time"] += 1
        return counts
    with fixed(Exception, PaidError, "BILLS_PAID_FAILED", keep=(NotionError, PaidError)):
        with (connect or db.connect)() as connection:
            LEDGER.ensure(connection)
            ledger = _ledger_all(connection)
            queued = {row["page_id"]: (by_id[row["page_id"]], "paid") for row in paid}
            queued.update({row["page_id"]: (row, "auto") for row in autos})
            for page_id, entry in ledger.items():
                if entry.get("step") != "complete":
                    queued[page_id] = (by_id.get(page_id), entry.get("kind", "paid"))
            counts["skipped"] = max(0, len(queued) - cap)
            for page_id, (row, kind) in list(queued.items())[:cap]:
                if row is None:
                    counts["failed"] += 1
                    continue
                if not _flag(row, kind) and ledger.get(page_id, {}).get("step") != "verified":
                    counts["review"] += 1
                    continue
                try:
                    _process(client, connection, row, source, today, ledger.get(page_id), counts, True, kind)
                except Exception:
                    counts["failed"] += 1
            # an Active bill with no tag is tagged Unpaid; Auto is set by hand
            for row in [r for r in untagged if r["page_id"] not in queued][:cap]:
                if _write(client, row["page_id"], {"Pay": PAY_UNPAID}, counts):
                    counts["backfilled"] += 1
    return counts
