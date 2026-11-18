"""One small provider-neutral mail record for read-only classification. Gmail and Outlook are read differently inside; classifier code sees only this shape.

A record: provider, id, received (ISO 8601 UTC), sender_name, sender_address, subject, body, folder ("INBOX" or "JUNK"), markers (bulk, auto, noreply: booleans), url (a link to the
message when it is deterministic, else ""). Content stays in memory: nothing here writes, logs, labels, moves, deletes, sends or forwards mail, and errors are fixed codes only."""
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from email.utils import parseaddr

from lifeos.platform.fixed_code import fixed
from lifeos.platform.gmail import GmailError, error_code
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


def gmail_records(gmail, now, limit=MAX_MESSAGES, days=WINDOW_DAYS, terms="", known=None, stats=None, workers=1, timing=None, heal=False):
    """Gmail Inbox and Spam, last `days` days (default WINDOW_DAYS), optionally only messages matching Gmail search `terms`. Returns (records, unreadable). A listing that cannot be proven complete raises MailError."""
    query = f"{{in:inbox in:spam}} newer_than:{days}d" + (f" {terms}" if terms else "")
    began = time.monotonic()
    with fixed(GmailError, MailError, "MAIL_GMAIL_LISTING_INCOMPLETE"):
        ids = gmail.list_ids_complete(query, limit, include_spam=True)
    listed = len(ids)
    if timing is not None:                                                   # LIFE-793: where a Gmail read spends its time, counts and seconds only
        timing["listed"], timing["list_s"] = listed, round(time.monotonic() - began, 1)
    if known is not None:                                                    # LIFE-610: only ids new to the system are read; `known(provider, id)` says which are not
        ids = [i for i in ids if not known("gmail", i)]
    if stats is not None:
        stats["scanned"] = stats.get("scanned", 0) + listed
        stats["new"] = stats.get("new", 0) + len(ids)
    out, unreadable = [], 0
    read_began = time.monotonic()

    failures = {}                                                            # message id -> the fixed cause of its last failed read

    def read(message_id):
        try:
            return gmail.mail_record(message_id)
        except GmailError as error:
            failures[message_id] = error_code(error)
            return None
    if workers > 1 and len(ids) > 1:                                         # LIFE-793: the same reads, the same order and the same unreadable count, at most `workers` at a time; the first read goes alone so the access token is fetched once
        first = [read(ids[0])]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            raws = first + list(pool.map(read, ids[1:]))
    else:
        raws = [read(message_id) for message_id in ids]
    gone = set()
    if heal:                                                                 # LIFE-793: a read that failed gets one more try; a listed message Gmail then says does not exist was deleted after the listing (the
        for position, message_id in enumerate(ids):                          # read takes minutes) and can hold no pending request: dropped, not "unreadable". Anything else still failing is counted and named by its fixed code
            if raws[position] is None:
                failures.pop(message_id, None)
                raws[position] = read(message_id)
        gone = {m for m, code in failures.items() if code.startswith("HTTP_404")}
    if timing is not None:
        timing["read"], timing["read_s"] = len(ids), round(time.monotonic() - read_began, 1)
        retries = getattr(gmail, "retries", None)
        if retries is not None:
            timing["retries"] = retries
        if heal:
            timing["gone"] = len(gone)
            timing["unreadable"] = {m: code for m, code in failures.items() if m not in gone and raws[ids.index(m)] is None}
    for message_id, raw in zip(ids, raws):
        if raw is None:
            if message_id not in gone:
                unreadable += 1
            continue
        name, address = _sender(raw["headers"].get("from", ""))
        labels = raw.get("label_ids") or []
        out.append({"provider": "gmail", "id": raw["id"], "received": raw["received_at"], "sender_name": name, "sender_address": address,
                    "subject": raw["headers"].get("subject", ""), "body": raw.get("body_text", ""), "folder": "JUNK" if "SPAM" in labels else "INBOX",
                    "markers": _markers(raw["headers"], address), "url": f"https://mail.google.com/mail/u/0/#all/{raw.get('thread') or raw['id']}"})
    return out, unreadable


NEW_READ_LIMIT = 100          # LIFE-610: more new messages than this are cheaper to read through one full listing than one by one


def _outlook_record(raw, account, label):
    """One Graph message -> the record, or None when it has no id or time (counted as unreadable by the caller)."""
    sender = ((raw.get("from") or {}).get("emailAddress") or {})
    if not raw.get("id") or not raw.get("receivedDateTime"):
        return None
    headers = {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in raw.get("internetMessageHeaders") or [] if isinstance(h, dict)}
    address = str(sender.get("address") or "").strip().lower()
    body = raw.get("body") or {}
    return {"provider": "outlook", "id": f"{account}:{raw['id']}", "received": raw["receivedDateTime"], "sender_name": str(sender.get("name") or "").strip(),
            "sender_address": address, "subject": raw.get("subject") or "", "body": body.get("content") or "", "folder": label,
            "markers": _markers(headers, address), "url": raw.get("webLink") or ""}


def outlook_records(client, account, now, limit=MAX_MESSAGES, days=WINDOW_DAYS, known=None, stats=None):
    """Outlook Inbox and Junk Email of one account, last `days` days (default WINDOW_DAYS). Returns (records, unreadable). An incomplete listing raises MailError.
    With `known` (LIFE-610) only messages new to the system are read: the listing carries ids and times, and each new message is then read on its own."""
    since = (now - timedelta(days=days)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, unreadable = [], 0
    text = 'outlook.body-content-type="text"'
    for folder, label in (("inbox", "INBOX"), ("junkemail", "JUNK")):
        with fixed(OutlookError, MailError, "MAIL_OUTLOOK_LISTING_INCOMPLETE"):
            found = client.messages(folder, since, limit, OUTLOOK_FIELDS if known is None else "id,receivedDateTime", prefer=text)
            if known is not None:
                fresh = [m for m in found if m.get("id") and not known("outlook", f"{account}:{m['id']}")]
                if stats is not None:
                    stats["scanned"] = stats.get("scanned", 0) + len(found)
                    stats["new"] = stats.get("new", 0) + len(fresh)
                if len(fresh) > NEW_READ_LIMIT:
                    ids = {m["id"] for m in fresh}
                    found = [m for m in client.messages(folder, since, limit, OUTLOOK_FIELDS, prefer=text) if m.get("id") in ids]
                else:
                    found = []
                    for m in fresh:
                        try:
                            found.append(client.get(f"/me/messages/{urllib.parse.quote(m['id'], safe='')}", {"$select": OUTLOOK_FIELDS}, text))
                        except OutlookError:
                            unreadable += 1
        for raw in found:
            record = _outlook_record(raw, account, label)
            if record is None:
                unreadable += 1
            else:
                out.append(record)
    return out, unreadable


def source_name(label):
    """A fixed, lower-case name for an Outlook account label, safe to print (LIFE-646, LIFE-648)."""
    return "Outlook " + (re.sub(r"[^a-z0-9_-]", "", str(label).lower())[:24] or "account")


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
