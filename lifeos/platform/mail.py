"""One small provider-neutral mail record for read-only classification; classifier code sees only this shape.

A record: provider, id, received (ISO 8601 UTC), sender_name, sender_address, subject, body, folder ("INBOX" or
"JUNK"), markers (bulk, auto, noreply), url. Content stays in memory: nothing here writes, labels, moves, deletes or
sends mail."""

import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from lifeos.platform.fixed_code import fixed
from lifeos.platform.gmail import GmailError, error_code
from lifeos.platform.outlook import Outlook, OutlookError

WINDOW_DAYS = 14
MAX_MESSAGES = 3000
OUTLOOK_FIELDS = "id,receivedDateTime,from,subject,body,webLink,internetMessageHeaders"
NOREPLY = re.compile(
    r"(?:^|[._-])(?:no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer[-_.]?daemon|postmaster|bounce[s]?|notifications?|alerts?|newsletter)(?:$|[._-]|\d)",
    re.I,
)


class MailError(RuntimeError):
    """Fixed codes only."""


def _sender(raw):
    name, address = parseaddr(raw or "")
    return name.strip().strip("\"'"), address.strip().lower()


def _markers(headers, address):
    get = lambda key: str(headers.get(key, "") or "").strip()
    return {
        "bulk": bool(get("list-id") or get("list-unsubscribe"))
        or get("precedence").lower() in ("bulk", "list", "junk"),
        "auto": get("auto-submitted").lower() not in ("", "no") or bool(get("x-auto-response-suppress")),
        "noreply": bool(NOREPLY.search(address.split("@")[0] if address else "")),
    }


def _old_enough(record, now, fresh_hours):
    """True when a held record may be reused: it was received more than `fresh_hours` ago (a newer message can still
    change folder)."""
    try:
        return (now - datetime.fromisoformat(record["received"])).total_seconds() > fresh_hours * 3600
    except (KeyError, TypeError, ValueError):
        return False


def gmail_records(
    gmail,
    now,
    limit=MAX_MESSAGES,
    days=WINDOW_DAYS,
    terms="",
    known=None,
    stats=None,
    workers=1,
    timing=None,
    heal=False,
    reuse=None,
    fresh_hours=2,
):
    """Gmail Inbox and Spam, last `days` days (default WINDOW_DAYS), optionally only messages matching Gmail search
    `terms`."""
    query = f"{{in:inbox in:spam}} newer_than:{days}d" + (f" {terms}" if terms else "")
    began = time.monotonic()
    with fixed(GmailError, MailError, "MAIL_GMAIL_LISTING_INCOMPLETE"):
        ids = gmail.list_ids_complete(query, limit, include_spam=True)
    listed = len(ids)
    if timing is not None:
        timing["listed"], timing["list_s"] = listed, round(time.monotonic() - began, 1)
    if known is not None:
        ids = [i for i in ids if not known("gmail", i)]
    if stats is not None:
        stats["scanned"] = stats.get("scanned", 0) + listed
        stats["new"] = stats.get("new", 0) + len(ids)
    out, unreadable = [], 0
    listed_ids = ids
    kept = {i for i in ids if reuse and i in reuse and _old_enough(reuse[i], now, fresh_hours)}
    ids = [i for i in ids if i not in kept]
    read_began = time.monotonic()

    failures, missing = {}, set()

    def read(message_id):
        try:
            return gmail.mail_record(message_id)
        except GmailError as error:
            failures[message_id] = error_code(error)
            if re.search(r"\bHTTP 404\b", str(error)):
                missing.add(message_id)
            return None

    if workers > 1 and len(ids) > 1:
        first = [read(ids[0])]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            raws = first + list(pool.map(read, ids[1:]))
    else:
        raws = [read(message_id) for message_id in ids]
    gone = set()
    if heal:
        for position, message_id in enumerate(ids):
            if raws[position] is None:
                failures.pop(message_id, None)
                missing.discard(message_id)
                raws[position] = read(message_id)
        gone = {m for m in failures if m in missing}
    if timing is not None:
        timing["read"], timing["read_s"] = len(ids), round(time.monotonic() - read_began, 1)
        retries = getattr(gmail, "retries", None)
        if retries is not None:
            timing["retries"] = retries
        if heal:
            timing["gone"] = len(gone)
            timing["unreadable"] = {
                m: code for m, code in failures.items() if m not in gone and raws[ids.index(m)] is None
            }
    raw_of = dict(zip(ids, raws))
    if timing is not None:
        timing["reused"] = len(kept)
    for message_id in listed_ids:
        if message_id in kept:
            out.append(dict(reuse[message_id]))
            continue
        raw = raw_of[message_id]
        if raw is None:
            if message_id not in gone:
                unreadable += 1
            continue
        name, address = _sender(raw["headers"].get("from", ""))
        labels = raw.get("label_ids") or []
        out.append(
            {
                "provider": "gmail",
                "id": raw["id"],
                "received": raw["received_at"],
                "sender_name": name,
                "sender_address": address,
                "subject": raw["headers"].get("subject", ""),
                "body": raw.get("body_text", ""),
                "folder": "JUNK" if "SPAM" in labels else "INBOX",
                "markers": _markers(raw["headers"], address),
                "url": f"https://mail.google.com/mail/u/0/#all/{raw.get('thread') or raw['id']}",
            }
        )
    return out, unreadable


NEW_READ_LIMIT = 100


def _outlook_record(raw, account, label):
    """One Graph message -> the record, or None when it has no id or time (counted as unreadable by the caller)."""
    sender = (raw.get("from") or {}).get("emailAddress") or {}
    if not raw.get("id") or not raw.get("receivedDateTime"):
        return None
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in raw.get("internetMessageHeaders") or []
        if isinstance(h, dict)
    }
    address = str(sender.get("address") or "").strip().lower()
    body = raw.get("body") or {}
    return {
        "provider": "outlook",
        "id": f"{account}:{raw['id']}",
        "received": raw["receivedDateTime"],
        "sender_name": str(sender.get("name") or "").strip(),
        "sender_address": address,
        "subject": raw.get("subject") or "",
        "body": body.get("content") or "",
        "folder": label,
        "markers": _markers(headers, address),
        "url": raw.get("webLink") or "",
    }


def outlook_records(client, account, now, limit=MAX_MESSAGES, days=WINDOW_DAYS, known=None, stats=None):
    """Outlook Inbox and Junk Email of one account, last `days` days (default WINDOW_DAYS)."""
    since = (now - timedelta(days=days)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, unreadable = [], 0
    text = 'outlook.body-content-type="text"'
    for folder, label in (("inbox", "INBOX"), ("junkemail", "JUNK")):
        with fixed(OutlookError, MailError, "MAIL_OUTLOOK_LISTING_INCOMPLETE"):
            found = client.messages(
                folder, since, limit, OUTLOOK_FIELDS if known is None else "id,receivedDateTime", prefer=text
            )
            if known is not None:
                fresh = [m for m in found if m.get("id") and not known("outlook", f"{account}:{m['id']}")]
                if stats is not None:
                    stats["scanned"] = stats.get("scanned", 0) + len(found)
                    stats["new"] = stats.get("new", 0) + len(fresh)
                if len(fresh) > NEW_READ_LIMIT:
                    ids = {m["id"] for m in fresh}
                    found = [
                        m
                        for m in client.messages(folder, since, limit, OUTLOOK_FIELDS, prefer=text)
                        if m.get("id") in ids
                    ]
                else:
                    found = []
                    for m in fresh:
                        try:
                            found.append(
                                client.get(
                                    f"/me/messages/{urllib.parse.quote(m['id'], safe='')}",
                                    {"$select": OUTLOOK_FIELDS},
                                    text,
                                )
                            )
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
    return "Outlook " + (re.sub(r"[^a-z0-9_-]", "", str(label).lower())[:24] or "account")


def outlook_accounts(environ=os.environ):
    """[(label, client or None)] for every signed-in Outlook account (read-only; no schema is created)."""
    client_id = (environ.get("OUTLOOK_CLIENT_ID") or "").strip()
    if not client_id:
        return []
    from lifeos.platform import db, outlook_tokens as store  # noqa: PLC0415

    out = []
    with db.connect() as connection:
        for label in store.accounts(connection):
            try:
                out.append(
                    (
                        label,
                        Outlook(client_id, store.load(connection, label), lambda new, label=label: _save(label, new)),
                    )
                )
            except OutlookError:
                out.append((label, None))
    return out


def _save(label, token):
    from lifeos.platform import db, outlook_tokens as store  # noqa: PLC0415

    with db.connect() as connection:
        store.save(connection, label, token)
