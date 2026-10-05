"""MegIBOW state (D134): two tiny private tables, never messages or events. Counts, hashed keys and Jim's decisions only.

v7_megibow_weeks: week_start (a Monday, or LEGACY) -> the five counts, a trustworthy flag, when frozen, a note.   Closed weeks never change on their own.
v7_megibow_overrides: a hashed key -> Jim's decision (an item) or a settled contact type (a person, reused).   The durable memory of the review list."""
from datetime import date, datetime, timezone

from lifeos.megibow import classify as C
from lifeos.platform.snapshot_store import Store

WEEKS = Store("v7_megibow_weeks", "week_start", "CHAR(10)")
OVERRIDES = Store("v7_megibow_overrides", "key_hash", "CHAR(16)")
LEGACY = "LEGACY"


class StoreError(RuntimeError):
    """Fixed codes only."""


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure(connection):
    WEEKS.ensure(connection)
    OVERRIDES.ensure(connection)


def _rows(connection, store):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {store.key_column}, payload FROM {store.table}")
        rows = cursor.fetchall()
    import json  # noqa: PLC0415
    return {r[0]: json.loads(r[1]) for r in rows}


def load(connection):
    """-> (frozen {monday: counts}, legacy counts or None, overrides {key: decision}, contacts {contact_key: activity}, trusted {monday: bool}). A missing table reads as empty."""
    try:
        weeks, over = _rows(connection, WEEKS), _rows(connection, OVERRIDES)
    except Exception as error:                                       # noqa: BLE001 - a table that is not there yet reads as empty; a real outage is caught by the caller's other reads
        if "doesn't exist" in str(error) or "1146" in str(error):
            return {}, None, {}, {}, {}
        raise StoreError("MEGIBOW_STORE_READ_FAILED") from None
    frozen = {date.fromisoformat(k): v["counts"] for k, v in weeks.items() if k != LEGACY}
    trusted = {date.fromisoformat(k): bool(v.get("trustworthy")) for k, v in weeks.items() if k != LEGACY}
    legacy = weeks.get(LEGACY, {}).get("counts")
    decisions = {k: v["decision"] for k, v in over.items() if v.get("kind") == "ACTIVITY"}
    contacts = {k: v["decision"] for k, v in over.items() if v.get("kind") == "CONTACT"}
    return frozen, legacy, decisions, contacts, trusted


def freeze(connection, week, counts, trustworthy, note=""):
    snapshot = {"schema": 1, "taken_at": _now(), "counts": counts, "trustworthy": bool(trustworthy), "frozen_at": _now(), "note": note}
    WEEKS.save(connection, week.isoformat(), snapshot)
    if WEEKS.load(connection, week.isoformat()) != snapshot:
        raise StoreError("MEGIBOW_FREEZE_READBACK_MISMATCH")


def remember(connection, key, kind, decision):
    snapshot = {"schema": 1, "taken_at": _now(), "kind": kind, "decision": decision}
    OVERRIDES.save(connection, key, snapshot)
    if OVERRIDES.load(connection, key) != snapshot:
        raise StoreError("MEGIBOW_OVERRIDE_READBACK_MISMATCH")


def activity_of(decision):
    """'Count as Recruiter Call' -> 'Recruiter Calls'; anything else -> None."""
    if decision.startswith("Count as "):
        wanted = decision[len("Count as "):]
        for a in C.ACTIVITIES:
            if a == wanted or a == wanted + "s":
                return a
    return None
