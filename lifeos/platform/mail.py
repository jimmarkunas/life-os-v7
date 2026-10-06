"""One small provider-neutral mail record for read-only classification. Gmail and Outlook are read differently inside; classifier code sees only this shape.

A record: provider, id, received (ISO 8601 UTC), sender_name, sender_address, subject, body, folder ("INBOX" or "JUNK"), markers (bulk, auto, noreply: booleans), url (a link to the
message when it is deterministic, else ""). Content stays in memory: nothing here writes, logs, labels, moves, deletes, sends or forwards mail, and errors are fixed codes only."""
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.outlook import Outlook, OutlookError

WINDOW_DAYS = 14
MAX_MESSAGES = 3000
OUTLOOK_FIELDS = "id,receivedDateTime,from,subject,body,webLink,internetMessageHeaders"
NOREPLY = re.compile(r"(?:^|[._-])(?:no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer[-_.]?daemon|postmaster|bounce[s]?|notifications?|alerts?|newsletter)(?:$|[._-]|\d)", re.I)


class MailError(RuntimeError):
    """Fixed codes only."""


def _sender(raw):
    name, address = parseaddr(raw or "")
    return name.strip().strip("\"'"), address.strip().lower()


def _markers(headers, address):
    get = lambda key: str(headers.get(key, "") or "").strip()
    return {"bulk": bool(get("list-id") or get("list-unsubscribe")) or get("precedence").lower() in ("bulk", "list", "junk"),
            "auto": get("auto-submitted").lower() not in ("", "no") or bool(get("x-auto-response-suppress")),
            "noreply": bool(NOREPLY.search(address.split("@")[0] if address else ""))}


def gmail_records(gmail, now, limit=MAX_MESSAGES):
    """Gmail Inbox and Spam, last WINDOW_DAYS days. Returns (records, unreadable). A listing that cannot be proven complete raises MailError."""
    query = f"{{in:inbox in:spam}} newer_than:{WINDOW_DAYS}d"
    try:
        ids = gmail.list_ids_complete(query, limit, include_spam=True)
    except GmailError:
        raise MailError("MAIL_GMAIL_LISTING_INCOMPLETE") from None
    out, unreadable = [], 0
    for message_id in ids:
        try:
            raw = gmail.mail_record(message_id)
        except GmailError:
            unreadable += 1
            continue
        name, address = _sender(raw["headers"].get("from", ""))
        labels = raw.get("label_ids") or []
        out.append({"provider": "gmail", "id": raw["id"], "received": raw["received_at"], "sender_name": name, "sender_address": address,
                    "subject": raw["headers"].get("subject", ""), "body": raw.get("body_text", ""), "folder": "JUNK" if "SPAM" in labels else "INBOX",
                    "markers": _markers(raw["headers"], address), "url": f"https://mail.google.com/mail/u/0/#all/{raw.get('thread') or raw['id']}"})
    return out, unreadable


def outlook_records(client, account, now, limit=MAX_MESSAGES):
    """Outlook Inbox and Junk Email of one account, last WINDOW_DAYS days. Returns (records, unreadable). An incomplete listing raises MailError."""
    since = (now - timedelta(days=WINDOW_DAYS)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, unreadable = [], 0
    for folder, label in (("inbox", "INBOX"), ("junkemail", "JUNK")):
        try:
            found = client.messages(folder, since, limit, OUTLOOK_FIELDS, prefer='outlook.body-content-type="text"')
        except OutlookError:
            raise MailError("MAIL_OUTLOOK_LISTING_INCOMPLETE") from None
        for raw in found:
            sender = ((raw.get("from") or {}).get("emailAddress") or {})
            if not raw.get("id") or not raw.get("receivedDateTime"):
                unreadable += 1
                continue
            headers = {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in raw.get("internetMessageHeaders") or [] if isinstance(h, dict)}
            address = str(sender.get("address") or "").strip().lower()
            body = raw.get("body") or {}
            out.append({"provider": "outlook", "id": f"{account}:{raw['id']}", "received": raw["receivedDateTime"], "sender_name": str(sender.get("name") or "").strip(),
                        "sender_address": address, "subject": raw.get("subject") or "", "body": body.get("content") or "", "folder": label,
                        "markers": _markers(headers, address), "url": raw.get("webLink") or ""})
    return out, unreadable


def outlook_accounts(environ=os.environ):
    """[(label, client or None)] for every signed-in Outlook account (tokens live in the private database; reading only, no schema is created)."""
    client_id = (environ.get("OUTLOOK_CLIENT_ID") or "").strip()
    if not client_id:
        return []
    from lifeos.platform import db, outlook_tokens as store              # noqa: PLC0415
    out = []
    with db.connect() as connection:
        for label in store.accounts(connection):
            try:
                out.append((label, Outlook(client_id, store.load(connection, label), lambda new, label=label: _save(label, new))))
            except OutlookError:
                out.append((label, None))
    return out


def _save(label, token):
    from lifeos.platform import db, outlook_tokens as store              # noqa: PLC0415
    with db.connect() as connection:
        store.save(connection, label, token)
