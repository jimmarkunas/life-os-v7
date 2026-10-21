"""Gmail Physical Mail evidence -> deterministic parse -> private derivative state (MAIL-1.2). Counts only; fixed error codes.

Source access is READ-ONLY: this package lists and reads messages and never modifies a label, read state or mail. The Gmail listing is complete or it fails closed
(`list_ids_complete`), every listed message is read before anything is saved, and the state row is replaced and read back. The first run needs Jim's starting pile
(`PHYSICAL_MAIL_ANCHOR`, the number of items waiting on October 4, 2026, from the workflow input, never from code); every later run continues from the stored row,
re-reading the last OVERLAP days, which changes nothing because events are keyed by message id."""
import os
from datetime import datetime, timedelta, timezone

from lifeos.platform import db
from lifeos.platform.gmail import Gmail, GmailError, error_code
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
    return f"({events.SENDER_QUERY} OR from:{events.FEDEX_SENDER}) after:{int(since.timestamp())}"


def run(limit, live, environ=os.environ, gmail=None, connection=None, now=None):
    counts = {"listed": 0, "arrivals_new": 0, "actions_new": 0, "review_new": 0, "review_resolved": 0, "ignored": 0, "before_anchor": 0, "saved": 0, "status": "ok", "rebuild": 0}
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
        previous, rebuild = stored, False
        if stored.get("accepted_at") and stored.get("replay") != state_mod.REPLAY:
            rebuild = True                                                      # a row an older, flatter parser built: replay the whole census from the anchor into a candidate, keep the row until the candidate is proven
            stored = state_mod.new_state(previous["anchor_count"], now)
        counts["rebuild"] = int(rebuild)
        gmail = gmail or Gmail.from_env()
        since = state_mod._at(stored["through"]) - OVERLAP if stored.get("accepted_at") else state_mod.ANCHOR_AT
        since = max(since, state_mod.ANCHOR_AT)
        try:
            ids = gmail.list_ids_complete(window_query(since), limit)
        except GmailError as error:
            raise PhysMailError("PHYSMAIL_" + error_code(error)) from None
        counts["listed"] = len(ids)
        extracted = []
        for message_id in ids:                                                 # the whole census is read before any write
            try:
                extracted.append(events.extract(gmail.message_record(message_id, keep_lines=True)))
            except GmailError as error:
                raise PhysMailError("PHYSMAIL_GMAIL_READ_FAILED_" + error_code(error)) from None
        merged, delta = state_mod.apply(stored, extracted, now)
        for key in ("arrivals_new", "actions_new", "review_new", "review_resolved", "ignored", "before_anchor"):
            counts[key] = delta[key]
        if rebuild:
            for number, chain in previous["chains"].items():                    # tracking belongs to its piece: a rebuild from the anchor carries it over, never drops it
                if chain.get("tracking") and number in merged["chains"]:
                    merged["chains"][number]["tracking"] = chain["tracking"]
        counts["tracking_recorded"] = 0
        entered = _entered_tracking(environ)
        if entered:
            merged, recorded = state_mod.record_tracking(merged, entered[0], entered[1], "operator", now)
            counts["tracking_recorded"] = int(recorded)
        merged["through"], merged["accepted_at"], merged["taken_at"] = now.isoformat(), now.isoformat(), now.isoformat()
        counts.update({f"state_{k}": v for k, v in state_mod.summary(merged, now).items() if k != "degraded_reason"})
        if rebuild:
            _prove_rebuild(previous, merged, environ, now)                     # fail closed: the known-good row stays; the candidate carries it as `prior` for a rollback
            merged["prior"] = {k: v for k, v in previous.items() if k != "prior"}
        if live:
            if owns:                                                           # the connection sat idle through the Gmail census and the store tunnel drops it (runs 39659585455 and 39668591536 lost it at this first statement): save on a fresh one
                manager.__exit__(None, None, None)
                manager = None
                manager = db.connect()
                connection = manager.__enter__()
            STORE.save_verified(connection, KEY, merged, lambda code: PhysMailError("PHYSMAIL_" + code))
            counts["saved"] = 1
            new_ids = set(merged["arrivals"]) - set((stored or {}).get("arrivals", {}))
            if new_ids and not rebuild:                                                        # only after the state is saved, so a failed save never pushes twice; counts only, no mail content
                from lifeos.platform import alerts                             # noqa: PLC0415
                items = sum(int(merged["arrivals"][i].get("count") or 0) for i in new_ids)
                counts["notified"] = alerts.ntfy((environ.get("NTFY_TOPIC") or "").strip(), "LIFE OS Physical Mail",
                                                 f"{items} new mail item{'s' if items != 1 else ''} received" if items else "New mail received", alerts.PAGE)
        return counts
    finally:
        if owns and manager is not None:
            manager.__exit__(None, None, None)


def _entered_tracking(environ):
    """PHYSICAL_MAIL_TRACKING_ENTRY, a one-off workflow input for the ONE piece the vendor's notices never describe: `mail number | carrier | service | tracking number | bundle`. -> (number, entry) or None.
    The temporary stand-in for an authoritative tracking reader; stored with the piece, never a standing configuration."""
    parts = [part.strip() for part in (environ.get("PHYSICAL_MAIL_TRACKING_ENTRY") or "").split("|")]
    if len(parts) < 4 or not parts[0].isdigit():
        return None
    entry = {"carrier": parts[1], "service": parts[2], "number": parts[3], "bundle": parts[4] if len(parts) > 4 else ""}
    if entry["carrier"].lower() == "fedex" and entry["number"].isdigit():
        entry["url"] = "https://www.fedex.com/fedextrack/?trknbr=" + entry["number"]
    return parts[0], entry


def _expected(environ):
    """PHYSICAL_MAIL_EXPECT: optional counts only, `identified,scanned,disposed,awaiting_tracking,unidentified,review` (for example 21,9,11,1,0,0): Jim's own tally of what the notices say."""
    raw = (environ.get("PHYSICAL_MAIL_EXPECT") or "").strip()
    if not raw:
        return None
    try:
        values = tuple(int(part) for part in raw.split(","))
    except ValueError:
        raise PhysMailError("PHYSMAIL_EXPECT_INVALID") from None
    if len(values) != 6 or any(v < 0 for v in values):
        raise PhysMailError("PHYSMAIL_EXPECT_INVALID")
    return values


def _prove_rebuild(previous, candidate, environ, now):
    """The candidate replaces the known-good row only if it loses no mail number the row knew, is not worse on review, is not degraded, and (when Jim states his tally) matches it exactly."""
    old, new = state_mod.summary(previous, now), state_mod.summary(candidate, now)
    lost = set(previous["chains"]) - set(candidate["chains"])
    got = (new["identified"], new["opened"], new["done"], new["waiting_tracking"], new["unidentified"], new["review"])
    expected = _expected(environ)
    if lost or new["review"] > old["review"] or new["degraded_reason"] or (expected is not None and got != expected):
        raise PhysMailError("PHYSMAIL_REBUILD_REJECTED")


def _exists(connection):
    STORE.ensure(connection)
    return True
