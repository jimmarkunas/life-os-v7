"""LIFE-894: Interview OS's mail, read early beside the tick and handed to the late Interview job through one table of the private store (v7_interview_mail).

The early domains run of a tick (phase tick) reads the same Gmail and Outlook window the late job reads today (interview_evidence.read_mail) and files the records, the per-source status and a fingerprint under the tick's hour;
it writes nothing to Notion, the Ledger or anywhere else, and a failed read files nothing. The late Interview job (the one writer of the opportunity pages) uses the snapshot only when it is complete, for the same window
and taken after THIS tick started (the same two-minute grace lifeos.platform.phase gives a dispatch run); a stale, wrong-hour, incomplete, corrupt or missing snapshot, or a store that cannot be read, means it reads mail itself exactly as before.
The proof-week shadow that compared a fresh read with the snapshot is removed (Jim accepted it, LIFE-894 comment 14099). Output is counts and fixed codes only: never an address, subject, id or body."""
import json
import os
import zlib
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from lifeos.jobs import store

UTC = timezone.utc
TABLE = "v7_interview_mail"
GRACE = timedelta(minutes=2)             # a snapshot taken this long before the tick started still belongs to it (the grace lifeos.platform.phase gives the early run)
KEEP = timedelta(hours=6)                # older rows are removed whenever a new one is filed
MAX_BYTES = 12_000_000                   # a compressed payload above this is not filed (the late job then reads for itself); MySQL's MEDIUMBLOB holds 16 MB


def hour_key(moment):
    """The UTC hour a snapshot is filed under, e.g. 2026-11-22T12."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H")


def parse(value):
    """An ISO 8601 time (a trailing Z allowed) as an aware UTC datetime; a naive one is read as UTC."""
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _canon(records):
    return json.dumps(sorted(records, key=lambda r: (r["provider"], r["id"])), sort_keys=True, separators=(",", ":"))


def fingerprint(records):
    """A hash of the records, independent of their order: it covers every field, so equal fingerprints mean byte-identical mail input."""
    return sha256(_canon(records).encode()).hexdigest()


def judge(row, tick_started, now, days):
    """Pure: 'used' when the row is complete, for the same window and taken between this tick's start (less the grace) and now; else a fixed code saying why not."""
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
    """File the snapshot under its hour and remove the rows older than KEEP. An incomplete read (or one too large) files its status only: its records would never be used. Raises on a store error."""
    payload = zlib.compress(json.dumps({"records": records, "down": down, "detail": detail, "seconds": seconds}, separators=(",", ":")).encode(), 6) if complete else b""
    if len(payload) > MAX_BYTES:
        complete = False
    if complete:
        fingerprint_ = fingerprint(records)
    else:
        payload, fingerprint_ = zlib.compress(b"{}"), "0" * 64
    taken = taken.astimezone(UTC).replace(tzinfo=None)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(_create())
        cursor.execute(f"REPLACE INTO {TABLE} (tick_hour, taken_at, days, complete, fingerprint, payload) VALUES (%s,%s,%s,%s,%s,%s)",
                       (hour_key(taken.replace(tzinfo=UTC)), taken, int(days), 1 if complete else 0, fingerprint_, payload))
        cursor.execute(f"DELETE FROM {TABLE} WHERE taken_at < %s", (taken - KEEP,))
        connection.commit()
    return complete


def load(connect, tick_started, now, days):
    """-> {'why': 'used', 'records', 'down', 'detail', 'seconds', 'taken_at'} for a snapshot the late job may use, else {'why': <fixed code>}: none, wrong_hour, days, incomplete, stale, future, corrupt or store.
    Never raises: a store that cannot be read, a missing table or a payload that does not match its fingerprint all mean 'read the mail yourself'."""
    try:
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT tick_hour, taken_at, days, complete, fingerprint, payload FROM {TABLE} ORDER BY taken_at DESC LIMIT 1")
            found = cursor.fetchone()
    except Exception:                                                                            # noqa: BLE001 - any store problem: the full read, never an error
        return {"why": "store"}
    row = None
    if found:
        hour, taken, held_days, complete, held_fingerprint, payload = found
        row = {"tick_hour": hour, "taken_at": taken.replace(tzinfo=UTC) if taken.tzinfo is None else taken, "days": int(held_days), "complete": bool(complete)}
    why = judge(row, tick_started, now, days)
    if why != "used":
        return {"why": why}
    try:
        body = json.loads(zlib.decompress(bytes(payload)))
        records = body["records"]
        if not isinstance(records, list) or fingerprint(records) != held_fingerprint:
            return {"why": "corrupt"}
        return {"why": "used", "records": records, "down": int(body["down"]), "detail": dict(body["detail"]), "seconds": dict(body["seconds"]), "taken_at": row["taken_at"]}
    except Exception:                                                                            # noqa: BLE001 - an unreadable payload is never used
        return {"why": "corrupt"}


FULL_EVERY_HOURS = 6                     # LIFE-906: at hours 0, 6, 12 and 18 Central the whole window is read, so a message whose folder changed after it was reused is corrected within six hours
REUSE_MAX_AGE = timedelta(hours=3)       # LIFE-906: an earlier complete read older than this is not trusted as a checkpoint (the next read is the full window)


def previous(connect, now, days):
    """LIFE-906: the checkpoint for an incremental read: {Gmail message id: record} from the LATEST COMPLETE snapshot of this window taken within REUSE_MAX_AGE, else {}. A missing, stale, incomplete, other-window or
    corrupt snapshot, or any store problem, is {} (the full window is read); a partial read never became a checkpoint because only complete rows are chosen. Never raises."""
    try:
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT taken_at, days, fingerprint, payload FROM {TABLE} WHERE complete = 1 ORDER BY taken_at DESC LIMIT 1")
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
        return {r["id"]: r for r in records if isinstance(r, dict) and r.get("provider") == "gmail" and isinstance(r.get("id"), str)}
    except Exception:                                                                            # noqa: BLE001 - any problem: read the whole window
        return {}


def take(environ, now, days, connect=None):
    """What the late job asks: a usable snapshot for the tick that just finished, or why not. Without TICK_STARTED (a manual run, `all`) it answers 'no_tick' and never touches the store."""
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
    """The early step: read the mail window and, on a live run, file it. Read only: nothing is written to Notion or the Ledger, and any failure files nothing and does not fail the run (the late job reads for itself)."""
    from lifeos.sources.interview import interview_evidence as evidence             # noqa: PLC0415 - that module imports this one
    now = (now or datetime.now(evidence.TZ)).astimezone(evidence.TZ)
    days, seconds, detail = evidence.window(environ, live), {}, {}
    reuse = previous(connect or store.connect, now.astimezone(UTC), days) if live and read is None and now.hour % FULL_EVERY_HOURS else {}   # LIFE-906: read only the mail the last complete snapshot does not hold, except in the full-read hours
    try:
        records, down = (read or evidence.read_mail)(environ, now, None, None, days, seconds, detail, reuse=reuse) if reuse else (read or evidence.read_mail)(environ, now, None, None, days, seconds, detail)
    except Exception as error:                                                       # noqa: BLE001 - a fixed code, never the error's text
        return {"snapshot": "not_saved", "why": "read_" + type(error).__name__.lower()[:40]}
    counts = {"records": len(records), "complete": down == 0, "down": down, "days": days, "sources": _sources(records), "seconds": seconds,
              "gmail": {k: (detail.get("gmail") or {}).get(k) for k in ("listed", "read", "reused", "retries")}}      # LIFE-906: listed, read, skipped (reused) and retries, counts only
    if not live:
        return {"snapshot": "dry_run", **counts}
    try:
        filed = save(connect or store.connect, now, days, down == 0, records, down, detail, seconds)
    except Exception:                                                                # noqa: BLE001 - a fixed code
        return {"snapshot": "not_saved", "why": "store", **counts}
    return {"snapshot": "saved" if filed else "saved_incomplete", **counts}

