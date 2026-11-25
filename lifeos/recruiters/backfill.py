"""LIFE-973: the one message behind an open recruiter row (its Source URL), so Company / Role / Client can be filled from it (lifeos.recruiters.fill).

A message the run just read is taken from that read. Any other row's message is read once more, read-only: a Gmail link names the thread, and the first message in it that Jim did not send is the recruiter's;
an Outlook link names the item, tried on each signed-in account. A message that cannot be read leaves the row as it was (it is tried again next run). Nothing here writes; no name, subject or body is returned
beyond the record the caller keeps in memory."""
import re
import urllib.parse

from lifeos.platform import mail
from lifeos.platform import gmail as G
from lifeos.platform.gmail import GmailError
from lifeos.platform.outlook import OutlookError

MAX_PER_RUN = 15                                   # older rows read per run, so one run never turns into a mailbox sweep
THREAD = re.compile(r"mail\.google\.com/.*#[a-z]+/(?P<id>[A-Za-z0-9]{8,64})\b")
ITEM = re.compile(r"[?&]ItemID=(?P<id>[^&#]+)", re.I)


def _gmail_message(gm, thread_id):
    result = gm._request("GET", f"{G.API}/threads/{urllib.parse.quote(thread_id, safe='')}?format=minimal")
    messages = result.get("messages") if isinstance(result, dict) else None
    if not isinstance(messages, list):
        return None
    for m in messages:
        if isinstance(m, dict) and "SENT" not in (m.get("labelIds") or []) and m.get("id"):
            raw = gm.mail_record(m["id"])
            name, address = mail._sender(raw["headers"].get("from", ""))
            return {"provider": "gmail", "id": raw["id"], "received": raw["received_at"], "sender_name": name, "sender_address": address, "subject": raw["headers"].get("subject", ""),
                    "body": raw.get("body_text", ""), "folder": "INBOX", "markers": mail._markers(raw["headers"], address), "url": ""}
    return None


def _outlook_message(accounts, item_id):
    for label, client in accounts:
        if client is None:
            continue
        try:
            raw = client.get(f"/me/messages/{urllib.parse.quote(item_id, safe='')}", {"$select": mail.OUTLOOK_FIELDS}, 'outlook.body-content-type="text"')
        except OutlookError:
            continue
        record = mail._outlook_record(raw, label, "INBOX")
        if record:
            return record
    return None


def message_for(row, by_url, gm, accounts):
    """-> the message record behind `row`, or None (no link, not found, or unreadable)."""
    url = row.get("source_url") or ""
    if url in by_url:
        return by_url[url]
    try:
        found = THREAD.search(url)
        if found and gm is not None:
            return _gmail_message(gm, found.group("id"))
        item = ITEM.search(url)
        if item:
            return _outlook_message(accounts, urllib.parse.unquote(item.group("id")))
    except (GmailError, mail.MailError, OutlookError):
        return None
    return None
