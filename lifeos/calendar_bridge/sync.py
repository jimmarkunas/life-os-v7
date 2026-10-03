"""Outlook calendar -> a dedicated Google calendar (one-way mirror). Every Google event V7 writes carries a private marker and a
deterministic id derived from the Outlook event (iCalUId + occurrence start), so re-runs never duplicate; events V7 did not write are
never touched; a cancelled or declined Outlook event removes its mirror. Output is counts and fixed codes only: titles, times, places
and addresses are private."""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

from lifeos.platform.gcal import GcalError, GoogleCalendar
from lifeos.platform.outlook import Outlook
from lifeos.platform import outlook_tokens

SRC = "v7_src"
HASH = "v7_hash"
PAST_DAYS = 1
AHEAD_DAYS = 90
DEFAULT_ACCOUNTS = "personal"
MASS_DELETE_FLOOR = 5


class BridgeError(RuntimeError):
    """The message is always a fixed code."""


def _z(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def google_id(label, ical_uid, start):
    return "v7" + hashlib.sha256(f"{label}|{ical_uid}|{start}".encode()).hexdigest()[:40]       # hex digits are valid Google event ids


def mirrors(event):
    """False for events that must not appear on Google: cancelled, declined, or without a stable identity."""
    if event.get("isCancelled") or not event.get("iCalUId"):
        return False
    return (event.get("responseStatus") or {}).get("response") != "declined"


def to_google(label, event):
    """The Google event body for one Outlook occurrence (times arrive in UTC)."""
    start, end = event["start"]["dateTime"][:19], event["end"]["dateTime"][:19]
    if event.get("isAllDay"):
        when = {"start": {"date": start[:10]}, "end": {"date": end[:10]}}
    else:
        when = {"start": {"dateTime": start, "timeZone": "UTC"}, "end": {"dateTime": end, "timeZone": "UTC"}}
    shown = event.get("showAs") or "busy"
    body = {"summary": event.get("subject") or "(no title)", **when,
            "location": (event.get("location") or {}).get("displayName") or "",
            "description": f"From Outlook ({label})" + (f"\n{event['webLink']}" if event.get("webLink") else ""),
            "transparency": "transparent" if shown == "free" else "opaque",
            "status": "tentative" if shown == "tentative" else "confirmed",
            "reminders": {"useDefault": True}}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]
    body["extendedProperties"] = {"private": {SRC: "outlook", HASH: digest}}
    return google_id(label, event["iCalUId"], start), body


def plan(wanted, existing):
    """wanted {id: body}; existing {id: google event}. -> (create, update, delete, same) lists of ids."""
    create = [i for i in wanted if i not in existing]
    update = [i for i in wanted if i in existing and
              ((existing[i].get("extendedProperties") or {}).get("private") or {}).get(HASH) !=
              wanted[i]["extendedProperties"]["private"][HASH]]
    delete = [i for i in existing if i not in wanted]
    same = [i for i in wanted if i in existing and i not in update]
    return create, update, delete, same


def run(limit, live, environ=os.environ, connect=None, outlook_factory=None, gcal=None, now=None):
    from lifeos.platform import db
    connect = connect or db.connect
    now = now or datetime.now(timezone.utc)
    start, end = _z(now - timedelta(days=PAST_DAYS)), _z(now + timedelta(days=AHEAD_DAYS))
    gcal = gcal or GoogleCalendar.from_env(environ)
    client_id = (environ.get("OUTLOOK_CLIENT_ID") or "").strip()
    labels = [x.strip() for x in (environ.get("CALENDAR_ACCOUNTS") or DEFAULT_ACCOUNTS).split(",") if x.strip()]
    if not client_id or not labels:
        raise BridgeError("CALENDAR_CONFIG_MISSING")
    wanted, seen, skipped = {}, 0, 0
    with connect() as connection:                          # tokens are read and any rotated token saved while the connection is open
        outlook_tokens.ensure_schema(connection)
        for label in labels:
            token = outlook_tokens.load(connection, label)
            if not token:
                raise BridgeError("CALENDAR_ACCOUNT_NOT_SIGNED_IN")
            client = (outlook_factory or Outlook)(client_id, token, lambda new, label=label: outlook_tokens.save(connection, label, new))
            for event in client.events(start, end):
                seen += 1
                if not mirrors(event):
                    skipped += 1
                    continue
                gid, body = to_google(label, event)
                wanted[gid] = body
    existing = {e["id"]: e for e in gcal.list_events(start, end, f"{SRC}=outlook")}
    create, update, delete, same = plan(wanted, existing)
    if len(delete) > max(MASS_DELETE_FLOOR, len(existing) // 2) and not environ.get("CALENDAR_ALLOW_MASS_DELETE"):
        raise BridgeError("CALENDAR_MASS_DELETE_GUARD")        # a half-empty Outlook answer must never wipe the mirror
    out = {"outlook": seen, "skipped": skipped, "google": len(existing), "create": len(create), "update": len(update),
           "delete": len(delete), "same": len(same), "applied": 0, "failed": 0}
    if not live:
        return out
    for gid in create:
        try:
            try:
                gcal.insert({"id": gid, **wanted[gid]})
            except GcalError as error:
                if str(error) != "GCAL_HTTP_409":              # already there (a retried insert, or a previously deleted id): update it
                    raise
                gcal.update(gid, wanted[gid])
            out["applied"] += 1
        except GcalError:
            out["failed"] += 1
    for gid in update:
        try:
            gcal.update(gid, wanted[gid])
            out["applied"] += 1
        except GcalError:
            out["failed"] += 1
    for gid in delete:
        try:
            gcal.delete(gid)
            out["applied"] += 1
        except GcalError:
            out["failed"] += 1
    if out["failed"]:
        raise BridgeError(f"CALENDAR_SYNC_FAILED:{out['failed']}")
    return out
