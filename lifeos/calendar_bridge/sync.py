"""Outlook to Google calendar: one-way, idempotent, never touching events V7 did not write.

Written events carry a private marker and an id derived from iCalUId plus occurrence start, so re-runs never
duplicate. An event already on Google by another route is left alone; a cancelled or declined one removes only V7's
copy. Output is counts and fixed codes only."""

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
    """Fixed codes only."""


def _z(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def google_id(label, ical_uid, start):
    return "v7" + hashlib.sha256(f"{label}|{ical_uid}|{start}".encode()).hexdigest()[:40]


def mirrors(event):
    if event.get("isCancelled") or not event.get("iCalUId"):
        return False
    return (event.get("responseStatus") or {}).get("response") != "declined"


def to_google(label, event):
    """Google event body for one Outlook occurrence (times arrive in UTC)."""
    start, end = event["start"]["dateTime"][:19], event["end"]["dateTime"][:19]
    if event.get("isAllDay"):
        when = {"start": {"date": start[:10]}, "end": {"date": end[:10]}}
    else:
        when = {"start": {"dateTime": start, "timeZone": "UTC"}, "end": {"dateTime": end, "timeZone": "UTC"}}
    shown = event.get("showAs") or "busy"
    body = {
        "summary": event.get("subject") or "(no title)",
        **when,
        "location": (event.get("location") or {}).get("displayName") or "",
        "description": f"From Outlook ({label})" + (f"\n{event['webLink']}" if event.get("webLink") else ""),
        "transparency": "transparent" if shown == "free" else "opaque",
        "status": "tentative" if shown == "tentative" else "confirmed",
        "reminders": {"useDefault": True},
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]
    body["extendedProperties"] = {"private": {SRC: "outlook", HASH: digest}}
    return google_id(label, event["iCalUId"], start), body


def _utc(when):
    if not when:
        return None
    if "date" in when and "dateTime" not in when:
        return when["date"]
    return (
        datetime.fromisoformat(when["dateTime"].replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S")
    )


def already_there(event):
    """The (iCalUID, start) of an event V7 did not write."""
    return (event.get("iCalUID"), _utc(event.get("start")))


def plan(wanted, existing):
    """Returns (create, update, delete) id lists for wanted {id: body} against existing {id: event}."""
    create = [i for i in wanted if i not in existing]
    update = [
        i
        for i in wanted
        if i in existing
        and ((existing[i].get("extendedProperties") or {}).get("private") or {}).get(HASH)
        != wanted[i]["extendedProperties"]["private"][HASH]
    ]
    return create, update, [i for i in existing if i not in wanted]


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
    wanted, ident, seen, skipped = {}, {}, 0, 0
    with connect() as connection:  # rotated tokens are saved while open
        outlook_tokens.ensure_schema(connection)
        for label in labels:
            token = outlook_tokens.load(connection, label)
            if not token:
                raise BridgeError("CALENDAR_ACCOUNT_NOT_SIGNED_IN")
            client = (outlook_factory or Outlook)(
                client_id, token, lambda new, label=label: outlook_tokens.save(connection, label, new)
            )
            for event in client.events(start, end):
                seen += 1
                if not mirrors(event):
                    skipped += 1
                    continue
                gid, body = to_google(label, event)
                wanted[gid] = body
                ident[gid] = (event["iCalUId"], body["start"].get("date") or body["start"]["dateTime"])
    listed = gcal.list_events(start, end)
    existing = {
        e["id"]: e for e in listed if ((e.get("extendedProperties") or {}).get("private") or {}).get(SRC) == "outlook"
    }
    elsewhere = {already_there(e) for e in listed if e["id"] not in existing}
    covered = [g for g in wanted if g not in existing and ident[g] in elsewhere]
    for gid in covered:
        del wanted[gid]
    create, update, delete = plan(wanted, existing)
    out = {
        "outlook": seen,
        "skipped": skipped,
        "google": len(existing),
        "create": len(create),
        "update": len(update),
        "already_in_google": len(covered),
        "delete": len(delete),
        "same": len(wanted) - len(create) - len(update),
        "applied": 0,
        "failed": 0,
    }
    floor = max(MASS_DELETE_FLOOR, len(existing) // 2)
    answered = len(wanted) + len(covered)  # events belonging in the mirror
    if len(delete) > floor and answered <= floor and not environ.get("CALENDAR_ALLOW_MASS_DELETE"):
        print("calendar-sync guard:", json.dumps(out))
        # A half-empty answer must never wipe the mirror; a full answer with all-new ids is replaced.
        raise BridgeError("CALENDAR_MASS_DELETE_GUARD")
    if not live:
        return out

    def attempt(call, *args):
        try:
            call(*args)
            out["applied"] += 1
        except GcalError:
            out["failed"] += 1

    def create_one(gid):
        try:
            gcal.insert({"id": gid, **wanted[gid]})
        except GcalError as error:
            if str(error) != "GCAL_HTTP_409":  # id already exists: update it
                raise
            gcal.update(gid, wanted[gid])

    for gid in create:
        attempt(create_one, gid)
    for gid in update:
        attempt(gcal.update, gid, wanted[gid])
    for gid in delete:
        attempt(gcal.delete, gid)
    if out["failed"]:
        raise BridgeError(f"CALENDAR_SYNC_FAILED:{out['failed']}")
    return out
