"""Gmail Physical Mail evidence -> deterministic parse -> private derivative state (MAIL-1.2). Counts only; fixed error codes.

Source access is READ-ONLY: this package lists and reads messages and never modifies a label, read state or mail. The Gmail listing is complete or it fails closed
(`list_ids_complete`), every listed message is read before anything is saved, and the state row is replaced and read back. The first run needs Jim's starting pile
(`PHYSICAL_MAIL_ANCHOR`, the number of items waiting on October 4, 2026, from the workflow input, never from code); every later run continues from the stored row,
re-reading the last OVERLAP days, which changes nothing because events are keyed by message id."""
import os
from datetime import datetime, timedelta, timezone

from lifeos.platform import db
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.snapshot_store import Store
from . import events, state as state_mod

STORE = Store("v7_physical_mail")
KEY = 1
OVERLAP = timedelta(days=3)


class PhysMailError(RuntimeError):
    """Fixed codes only: never a subject, sender, mail number or link."""


def _now():
    return datetime.now(timezone.utc)


def _anchor_count(environ):
    raw = (environ.get("PHYSICAL_MAIL_ANCHOR") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise PhysMailError("PHYSMAIL_ANCHOR_INVALID") from None


def window_query(since):
    return f"{events.SENDER_QUERY} after:{int(since.timestamp())}"


def run(limit, live, environ=os.environ, gmail=None, connection=None, now=None):
    counts = {"listed": 0, "arrivals_new": 0, "actions_new": 0, "review_new": 0, "ignored": 0, "before_anchor": 0, "saved": 0, "status": "ok"}
    now = now or _now()
    owns = connection is None
    manager = db.connect() if owns else None
    try:
        connection = manager.__enter__() if owns else connection
        stored = STORE.load(connection, KEY) if _exists(connection) else None
        if stored is None:
            anchor = _anchor_count(environ)
            if anchor is None:
                counts["status"] = "no_state"                                  # starting state unknown: nothing is saved and the card says DEGRADED, never "none"
                raise PhysMailError("PHYSMAIL_NO_STATE")
            stored = state_mod.new_state(anchor, now)
        elif stored.get("schema") != state_mod.SCHEMA:
            raise PhysMailError("PHYSMAIL_STATE_SCHEMA")
        gmail = gmail or Gmail.from_env()
        since = state_mod._at(stored["through"]) - OVERLAP if stored.get("accepted_at") else state_mod.ANCHOR_AT
        since = max(since, state_mod.ANCHOR_AT)
        try:
            ids = gmail.list_ids_complete(window_query(since), limit)
        except GmailError as error:
            raise PhysMailError("PHYSMAIL_" + str(error)[:40].upper().replace(" ", "_")) from None
        counts["listed"] = len(ids)
        extracted = []
        for message_id in ids:                                                 # the whole census is read before any write
            try:
                extracted.append(events.extract(gmail.message_record(message_id)))
            except GmailError:
                raise PhysMailError("PHYSMAIL_GMAIL_READ_FAILED") from None
        merged, delta = state_mod.apply(stored, extracted, now)
        for key in ("arrivals_new", "actions_new", "review_new", "ignored", "before_anchor"):
            counts[key] = delta[key]
        merged["through"], merged["accepted_at"], merged["taken_at"] = now.isoformat(), now.isoformat(), now.isoformat()
        counts.update({f"state_{k}": v for k, v in state_mod.summary(merged, now).items() if k != "degraded_reason"})
        if live:
            STORE.save_verified(connection, KEY, merged, lambda code: PhysMailError("PHYSMAIL_" + code))
            counts["saved"] = 1
            new_ids = set(merged["arrivals"]) - set((stored or {}).get("arrivals", {}))
            if new_ids:                                                        # only after the state is saved, so a failed save never pushes twice; counts only, no mail content
                from lifeos.platform import alerts                             # noqa: PLC0415
                items = sum(int(merged["arrivals"][i].get("count") or 0) for i in new_ids)
                counts["notified"] = alerts.ntfy((environ.get("NTFY_TOPIC") or "").strip(), "LIFE OS Physical Mail",
                                                 f"{items} new mail item{'s' if items != 1 else ''} received" if items else "New mail received", alerts.PAGE)
        return counts
    finally:
        if owns and manager is not None:
            manager.__exit__(None, None, None)


def _exists(connection):
    STORE.ensure(connection)
    return True
