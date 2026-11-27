"""Interview OS's mail, read early in the tick and handed to the late Interview job through one private-store table.

The early run files the records, per-source status and a fingerprint under the tick's hour and writes nothing else.
The
late job uses a snapshot only when it is complete, for the same window and taken after this tick started; otherwise it
reads the mail itself. Output is counts and fixed codes only.
"""

import json
import os
import zlib
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from lifeos.jobs import store

UTC = timezone.utc
TABLE = "v7_interview_mail"
GRACE = timedelta(minutes=2)  # a snapshot taken this long before the tick started still belongs to it
KEEP = timedelta(hours=6)
MAX_BYTES = 12_000_000  # a larger compressed payload is not filed (MySQL's MEDIUMBLOB holds 16 MB)


def hour_key(moment):
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H")


def parse(value):
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _canon(records):
    return json.dumps(sorted(records, key=lambda r: (r["provider"], r["id"])), sort_keys=True, separators=(",", ":"))


def fingerprint(records):
    return sha256(_canon(records).encode()).hexdigest()


def judge(row, tick_started, now, days):
    """Pure: 'used' when complete, for the same window and taken since this tick's start (less the grace); else a
    fixed code saying why not."""
    if row is None:
        return "none"
    taken = row["taken_at"]
    if row["tick_hour"] != hour_key(taken):
        return "wrong_hour"
    if row["days"] != days:
        return "days"
    if not row["complete"]:
        return "incomplete"
    if taken < tick_started - GRACE:
        return "stale"
    if taken > now:
        return "future"
    return "used"


def _create():
    return next(statement for statement in store.SCHEMA if TABLE in statement)


def save(connect, taken, days, complete, records, down, detail, seconds):
    """File the snapshot under its hour and remove rows older than KEEP. Raises on a store error."""
    payload = (
        zlib.compress(
            json.dumps(
                {"records": records, "down": down, "detail": detail, "seconds": seconds}, separators=(",", ":")
            ).encode(),
            6,
        )
        if complete
        else b""
    )
    if len(payload) > MAX_BYTES:
        complete = False
    if complete:
        fingerprint_ = fingerprint(records)
    else:
        payload, fingerprint_ = zlib.compress(b"{}"), "0" * 64
    taken = taken.astimezone(UTC).replace(tzinfo=None)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(_create())
        cursor.execute(
            f"REPLACE INTO {TABLE} (tick_hour, taken_at, days, complete, fingerprint, payload) VALUES (%s,%s,%s,%s,%s,%s)",
            (hour_key(taken.replace(tzinfo=UTC)), taken, int(days), 1 if complete else 0, fingerprint_, payload),
        )
        cursor.execute(f"DELETE FROM {TABLE} WHERE taken_at < %s", (taken - KEEP,))
        connection.commit()
    return complete


def load(connect, tick_started, now, days):
    """-> {'why': 'used', 'records', 'down', 'detail', 'seconds', 'taken_at'} for a usable snapshot, else {'why':
    <code>}. Never raises: any problem means 'read the mail yourself'."""
    try:
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT tick_hour, taken_at, days, complete, fingerprint, payload FROM {TABLE} ORDER BY taken_at DESC LIMIT 1"
            )
            found = cursor.fetchone()
    except Exception:  # noqa: BLE001
        return {"why": "store"}
    row = None
    if found:
        hour, taken, held_days, complete, held_fingerprint, payload = found
        row = {
            "tick_hour": hour,
            "taken_at": taken.replace(tzinfo=UTC) if taken.tzinfo is None else taken,
            "days": int(held_days),
            "complete": bool(complete),
        }
    why = judge(row, tick_started, now, days)
    if why != "used":
        return {"why": why}
    try:
        body = json.loads(zlib.decompress(bytes(payload)))
        records = body["records"]
        if not isinstance(records, list) or fingerprint(records) != held_fingerprint:
            return {"why": "corrupt"}
        return {
            "why": "used",
            "records": records,
            "down": int(body["down"]),
            "detail": dict(body["detail"]),
            "seconds": dict(body["seconds"]),
            "taken_at": row["taken_at"],
        }
    except Exception:  # noqa: BLE001
        return {"why": "corrupt"}


FULL_EVERY_HOURS = 6  # at hours 0, 6, 12 and 18 Central the whole window is read, so a message whose folder changed is corrected within six hours
REUSE_MAX_AGE = timedelta(hours=3)  # an earlier complete read older than this is not trusted as a checkpoint


def previous(connect, now, days):
    """{Gmail message id: record} from the latest complete snapshot of this window within REUSE_MAX_AGE, else {}.
    Never raises."""
    try:
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT taken_at, days, fingerprint, payload FROM {TABLE} WHERE complete = 1 ORDER BY taken_at DESC LIMIT 1"
            )
            found = cursor.fetchone()
        if not found:
            return {}
        taken, held_days, held_fingerprint, payload = found
        taken = taken.replace(tzinfo=UTC) if taken.tzinfo is None else taken
        if int(held_days) != days or not now - REUSE_MAX_AGE <= taken <= now:
            return {}
        records = json.loads(zlib.decompress(bytes(payload)))["records"]
        if not isinstance(records, list) or fingerprint(records) != held_fingerprint:
            return {}
        return {
            r["id"]: r
            for r in records
            if isinstance(r, dict) and r.get("provider") == "gmail" and isinstance(r.get("id"), str)
        }
    except Exception:  # noqa: BLE001
        return {}


def take(environ, now, days, connect=None):
    """A usable snapshot for the tick that just finished, or why not. Without TICK_STARTED it answers 'no_tick'."""
    started = (environ.get("TICK_STARTED") or "").strip()
    if not started:
        return {"why": "no_tick"}
    try:
        tick = parse(started)
    except ValueError:
        return {"why": "no_tick"}
    return load(connect or store.connect, tick, now.astimezone(UTC), days)


def _sources(records):
    out = {}
    for record in records:
        out[record["provider"]] = out.get(record["provider"], 0) + 1
    return out


def stage(limit, live, environ=os.environ, now=None, connect=None, read=None):
    from lifeos.sources.interview import interview_evidence as evidence  # noqa: PLC0415 - that module imports this one

    now = (now or datetime.now(evidence.TZ)).astimezone(evidence.TZ)
    days, seconds, detail = evidence.window(environ, live), {}, {}
    reuse = (
        previous(connect or store.connect, now.astimezone(UTC), days)
        if live and read is None and now.hour % FULL_EVERY_HOURS
        else {}
    )
    try:
        records, down = (
            (read or evidence.read_mail)(environ, now, None, None, days, seconds, detail, reuse=reuse)
            if reuse
            else (read or evidence.read_mail)(environ, now, None, None, days, seconds, detail)
        )
    except Exception as error:  # noqa: BLE001
        return {"snapshot": "not_saved", "why": "read_" + type(error).__name__.lower()[:40]}
    counts = {
        "records": len(records),
        "complete": down == 0,
        "down": down,
        "days": days,
        "sources": _sources(records),
        "seconds": seconds,
        "gmail": {k: (detail.get("gmail") or {}).get(k) for k in ("listed", "read", "reused", "retries")},
    }
    if not live:
        return {"snapshot": "dry_run", **counts}
    try:
        filed = save(connect or store.connect, now, days, down == 0, records, down, detail, seconds)
    except Exception:  # noqa: BLE001
        return {"snapshot": "not_saved", "why": "store", **counts}
    return {"snapshot": "saved" if filed else "saved_incomplete", **counts}
