"""MegIBOW evidence readers (D134): Gmail and Outlook sent mail, Google and Outlook calendar events -> the plain dicts classify expects. Read only, in memory only.

A bridged copy on Google (it carries the V7 bridge's private marker) is skipped: its creation time is the bridge's and it has no attendees; the Outlook original is read instead.
A listing that cannot be proven complete raises (the platform clients do), so a partial read is never counted."""
from datetime import datetime, timezone
from email.utils import getaddresses
import re

from lifeos.megibow.windows import iso
from lifeos.platform.outlook import OutlookError

BRIDGE_MARKER = "v7_src"
SENT_FIELDS = "id,sentDateTime,toRecipients,ccRecipients,subject,bodyPreview,isDraft,internetMessageHeaders"
EVENT_FIELDS = ("id,iCalUId,subject,bodyPreview,start,end,isAllDay,isCancelled,responseStatus,attendees,isOrganizer,createdDateTime,type,seriesMasterId")
GMAIL_LIMIT = 1500


def _when(text):
    """An ISO timestamp (Z, offset, or a bare UTC string from Graph) -> aware datetime, or None."""
    if not text:
        return None
    text = str(text).replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def addresses(header):
    return [a.lower() for _, a in getaddresses([header or ""]) if "@" in a]


def _own(client):
    """The mailbox's own addresses, so a self-send or Jim's own attendee entry is never an external person. A sign-in without the profile permission returns none (the other sources still name Jim)."""
    try:
        me = client.get("/me", {"$select": "mail,userPrincipalName"})
    except OutlookError:
        return set()
    return {str(me.get(k)).lower() for k in ("mail", "userPrincipalName") if isinstance(me, dict) and me.get(k)}


def gmail_sent(gmail, start, end):
    """Sent messages in [start, end): {id, sent_at, to, cc, subject, snippet, bulk}. Also returns the mailbox's own address."""
    query = f"in:sent after:{int(start.timestamp())} before:{int(end.timestamp())}"
    out = []
    for message_id in gmail.list_ids_complete(query, GMAIL_LIMIT):
        r = gmail.sent_record(message_id)
        out.append({"id": "g:" + r["id"], "sent_at": r["sent_at"], "to": addresses(r["to"]), "cc": addresses(r["cc"]), "subject": r["subject"], "snippet": r["snippet"],
                    "bulk": r["bulk"]})
    return out, gmail.profile_address()


def outlook_sent(client, account, start, end):
    """Sent Items in [start, end) for one Outlook account. -> (messages, own address)."""
    own = _own(client)
    out = []
    for m in client.messages("sentitems", iso(start), 3000, fields=SENT_FIELDS, time_field="sentDateTime"):
        if m.get("isDraft"):
            continue
        sent = _when(m.get("sentDateTime"))
        if sent is None or not (start <= sent < end):
            continue
        headers = {h.get("name", "").lower(): h.get("value", "") for h in m.get("internetMessageHeaders") or [] if isinstance(h, dict)}
        bulk = bool(headers.get("list-id") or headers.get("list-unsubscribe")) or headers.get("precedence", "").lower() in ("bulk", "list", "junk") or headers.get("auto-submitted", "no").lower() not in ("", "no")
        pick = lambda key: [((r.get("emailAddress") or {}).get("address") or "").lower() for r in m.get(key) or []]
        out.append({"id": f"o:{account}:{m['id']}", "sent_at": sent, "to": pick("toRecipients"), "cc": pick("ccRecipients"), "subject": m.get("subject") or "",
                    "snippet": m.get("bodyPreview") or "", "bulk": bulk})
    return out, own


def gcal_events(gcal, start, end):
    """Native Google events (never bridged copies). -> (events, own address if the calendar shows one)."""
    out, own = [], set()
    for e in gcal.list_events(iso(start), iso(end)):
        if ((e.get("extendedProperties") or {}).get("private") or {}).get(BRIDGE_MARKER):
            continue
        s, f = e.get("start") or {}, e.get("end") or {}
        begin, finish = _when(s.get("dateTime") or s.get("date")), _when(f.get("dateTime") or f.get("date"))
        if begin is None or finish is None:
            continue
        attendees, mine = [], "accepted" if (e.get("organizer") or {}).get("self") else "needsAction"
        for a in e.get("attendees") or []:
            if a.get("self"):
                own.add((a.get("email") or "").lower())
                mine = "organizer" if a.get("organizer") else (a.get("responseStatus") or mine)
            elif not a.get("resource"):
                attendees.append({"email": (a.get("email") or "").lower(), "response": a.get("responseStatus") or ""})
        uid = e.get("iCalUID") or e.get("id")
        out.append({"uid": uid, "occ": f"{uid}@{iso(begin)}", "created": _when(e.get("created")), "start": begin, "end": finish, "title": e.get("summary") or "",
                    "preview": e.get("description") or "", "cancelled": e.get("status") == "cancelled", "all_day": "date" in s and "dateTime" not in s,
                    "my_response": mine, "attendees": attendees, "bridged": False})
    return out, own


def outlook_events(client, start, end):
    own = _own(client)
    out = []
    for e in client.events(iso(start), iso(end), 3000, fields=EVENT_FIELDS):
        begin, finish = _when((e.get("start") or {}).get("dateTime")), _when((e.get("end") or {}).get("dateTime"))
        if begin is None or finish is None:
            continue
        uid = e.get("iCalUId") or e.get("id")
        status = ((e.get("responseStatus") or {}).get("response") or "").lower()
        mine = "organizer" if e.get("isOrganizer") else {"accepted": "accepted", "declined": "declined", "tentativelyaccepted": "tentative"}.get(status, "needsAction")
        attendees = [{"email": ((a.get("emailAddress") or {}).get("address") or "").lower(), "response": ((a.get("status") or {}).get("response") or "").lower()} for a in e.get("attendees") or []]
        out.append({"uid": uid, "occ": f"{uid}@{iso(begin)}", "created": _when(e.get("createdDateTime")), "start": begin, "end": finish, "title": e.get("subject") or "",
                    "preview": e.get("bodyPreview") or "", "cancelled": bool(e.get("isCancelled")), "all_day": bool(e.get("isAllDay")), "my_response": mine, "attendees": attendees,
                    "bridged": False})
    return out, own
