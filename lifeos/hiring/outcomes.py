"""Outcome mail for the Hiring Pipeline: rejections and offers found in Gmail and Outlook since the last evidence.

Reads mail only (nothing marked, moved or sent) for companies already in the pipeline and saves one private row for
the pipeline to read; it never closes anything itself, and stores only the row key, kind, received time and role-match
flag. Matching is deterministic (company words in order in sender name, subject, domain or body start, after the
opportunity's last evidence); ambiguous messages are counted, never guessed. Failure is per source: an `unread` source
keeps what the other sources hold and any outcome already accepted."""

import json
import os
import re
from datetime import datetime, timedelta, timezone

from lifeos.platform import mail, names
from lifeos.platform.fixed_code import fixed
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.notion_client import NotionError
from lifeos.platform.snapshot_store import Store
from . import models as M, snapshot as hiring_snapshot

STORE, KEY, SCHEMA_V = Store("v7_hiring_outcomes"), 1, 1
SCAN_EVERY_HOURS = 12
STALE_HOURS = 18
MIN_DAYS, MAX_DAYS = 14, 60
BODY_CHARS = 3000
REJECTED, OFFER = "REJECTED", "OFFER"
UNREAD_NAME = re.compile(r"Gmail|Outlook [a-z0-9_-]{1,24}")

REJECT = re.compile(
    "|".join(
        (
            r"\bnot (?:be )?moving forward\b",
            r"\bwill not be (?:proceeding|continuing|advancing|moving)\b",
            r"\bwon'?t be (?:moving|proceeding|continuing)\b",
            r"\b(?:move|moving|proceed|proceeding|go|going)\s+forward with (?:other|another|different)\b",
            r"\bpursu(?:e|ing) (?:other|another) (?:candidate|applicant|individual)s?\b",
            r"\bdecided not to (?:move|proceed|go) forward\b",
            r"\bregret to inform\b",
            r"\bnot (?:been )?selected\b",
            r"\bposition (?:has been|was) filled\b",
            r"\bunable to (?:offer|move forward|proceed)\b",
            r"\bdecided (?:not )?to (?:move|proceed|go) (?:forward )?with (?:other|another|different)\b",
            r"\bother candidates? (?:whose|who|that) (?:experience|background|qualifications|skills)\b",
        )
    ),
    re.I,
)
AUTOMATED = re.compile(
    r"(?i)no-?reply|do-?not-?reply|auto|confirm|notif|alert|news|digest|market|ship|order|store|update|mailer|bounce|jobs?\b|careers?|talent"
    r"|recruit|\bhr\b|info|support|team|hello|help|account|security|billing|service|apply|applicant|candidate|receipt|deal|offer|promo|reward|email|mail"
    r"|deliver|notice|payment|message|return|reply|event|survey|calendar|invite|refund|order"
)
OFFERED = re.compile(
    "|".join(
        (
            r"\bpleased to (?:offer|extend)\b",
            r"\boffer of employment\b",
            r"\bextend(?:ing)? (?:you )?an? (?:formal )?offer\b",
            r"\boffer letter\b",
        )
    ),
    re.I,
)


class OutcomeError(NotionError):
    """Fixed codes only."""


def fail(code):
    return OutcomeError("OUTCOMES_" + code)


def kind_of(text):
    if OFFERED.search(text):
        return OFFER
    if REJECT.search(text):
        return REJECTED
    return None


def _domain_labels(address):
    host = (address or "").rsplit("@", 1)[-1].lower()
    return [label for label in re.split(r"[.\-]", host) if label]


def mentions(message, company):
    want = names.tokens(company)
    if not want or len("".join(want)) < 3:
        return False
    for text in (message.get("sender_name", ""), message.get("subject", ""), (message.get("body") or "")[:BODY_CHARS]):
        if M.contains(names.tokens(text), want):
            return True
    return len(want) == 1 and want[0] in _domain_labels(message.get("sender_address"))


def role_in(message, role):
    words = set(names.tokens(role))
    return bool(words) and words <= set(
        names.tokens(f"{message.get('subject', '')} {(message.get('body') or '')[:BODY_CHARS]}")
    )


MODIFIERS = {
    "senior",
    "sr",
    "junior",
    "jr",
    "lead",
    "principal",
    "staff",
    "associate",
    "head",
    "chief",
    "group",
    "director",
    "vp",
    "assistant",
    "deputy",
    "executive",
    "global",
    "regional",
    "i",
    "ii",
    "iii",
    "iv",
    "1",
    "2",
    "3",
}


def exact_role(message, role):
    """The role's own words appear together, in order, with no seniority word or level number touching them."""
    want, text = (
        names.tokens(role),
        names.tokens(f"{message.get('subject', '')} {(message.get('body') or '')[:BODY_CHARS]}"),
    )
    n = len(want)
    return n > 0 and any(
        text[i : i + n] == want
        and (i == 0 or text[i - 1] not in MODIFIERS)
        and (i + n == len(text) or text[i + n] not in MODIFIERS)
        for i in range(len(text) - n + 1)
    )


def _since(row):
    value = row.get("event_start") or row.get("applied_on")
    return M.moment(value) if value else None


def _received(message):
    moment = datetime.fromisoformat(str(message["received"]).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def find(messages, roster):
    """-> ({row key: {"kind", "received", "match", "exact"}}, {"ambiguous": n, "outcome_messages": n, "unmatched": n}).

    Pure. One outcome per row: the newest qualifying message."""
    found, stats = {}, {"ambiguous": 0, "outcome_messages": 0, "unmatched": 0}
    for message in sorted(messages, key=_received):
        kind = kind_of(f"{message.get('subject', '')}\n{(message.get('body') or '')[:BODY_CHARS]}")
        if kind is None:
            continue
        stats["outcome_messages"] += 1
        at = _received(message)
        row, why = attribute(message, roster, at)
        if row is None:
            stats[why] += 1
            continue
        found[row["key"]] = {
            "kind": kind,
            "received": at.astimezone(timezone.utc).isoformat(),
            "match": why,
            "exact": why == "role" and exact_role(message, row["role"]),
        }
    return found, stats


def attribute(message, roster, at):
    named = [row for row in roster if mentions(message, row["company"]) and (_since(row) is None or at > _since(row))]
    if not named:
        return None, "unmatched"
    if len({tuple(names.tokens(row["company"])) for row in named}) > 1:
        return None, "ambiguous"
    by_role = [row for row in named if role_in(message, row["role"])]
    pick = by_role if by_role else named
    return (pick[0], "role" if by_role else "company") if len(pick) == 1 else (None, "ambiguous")


SUFFIXES = {"co", "com", "org", "net", "ac", "gov"}


def _own_domain(domain, company):
    """The employer's own registered domain and nothing under it: acme.com or acme.co.uk, never acme.myworkday.com."""
    labels = [label for label in domain.lower().split(".") if label]
    base = labels[:-2] if len(labels) > 2 and labels[-2] in SUFFIXES else labels[:-1]
    return len(base) == 1 and base[0].replace("-", "") == "".join(names.tokens(company))


def _person(message, company):
    words = re.findall(r"[a-z]+", (message.get("sender_name") or "").lower())
    local = (message.get("sender_address") or "").partition("@")[0].lower()
    return (
        2 <= len(words) <= 4
        and not set(words) & set(names.tokens(company))
        and not any(AUTOMATED.search(w) for w in words)
        and any(len(w) >= 3 and w in local for w in words)
    )


def contacts(messages, roster):
    """{row key: the newest received time of a direct message from that row's hiring team}. Pure.

    Only positive evidence counts: an inbox message (never junk, list, bulk or auto-submitted) from a named person on
    the employer's own registered domain, attributed to exactly one row."""
    found = {}
    for message in sorted(messages, key=_received):
        local, _, domain = (message.get("sender_address") or "").partition("@")
        marked = message.get("markers") or {}
        if (
            message.get("folder") != "INBOX"
            or not local
            or not domain
            or "@" in domain
            or AUTOMATED.search(local)
            or marked.get("bulk")
            or marked.get("auto")
            or marked.get("noreply")
        ):
            continue
        at = _received(message)
        row, _ = attribute(message, roster, at)
        if row is not None and _own_domain(domain, row["company"]) and _person(message, row["company"]):
            found[row["key"]] = at.astimezone(timezone.utc).isoformat()
    return found


def window_days(roster, now):
    oldest = [s for s in (_since(row) for row in roster) if s]
    days = (now - min(oldest)).days + 2 if oldest else MIN_DAYS
    return max(MIN_DAYS, min(MAX_DAYS, days))


def _exists(connection):
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE %s", (STORE.table,))
        return cursor.fetchone() is not None


def load(connection):
    """The stored outcomes ({row key: {...}}), when last scanned, direct contact per row, and unread sources."""
    if not _exists(connection):
        return {}, None, {}, []
    with fixed((TypeError, ValueError), fail, "STORE_INVALID"):
        stored = STORE.load(connection, KEY)
    if stored is None:
        return {}, None, {}, []
    if (
        stored.get("schema") != SCHEMA_V
        or not isinstance(stored.get("outcomes"), dict)
        or not isinstance(stored.get("contacts", {}), dict)
        or not isinstance(stored.get("unread", []), list)
    ):
        raise fail("STORE_INVALID")
    return (
        stored["outcomes"],
        stored.get("scanned_at"),
        stored.get("contacts", {}),
        [n for n in stored.get("unread", []) if isinstance(n, str) and UNREAD_NAME.fullmatch(n)],
    )


def _roster(prior):
    return [
        {
            "key": r["key"],
            "company": r["company"],
            "role": r["role"],
            "event_start": r.get("event_start"),
            "applied_on": r.get("applied_on"),
        }
        for r in (prior or {}).get("rows") or []
        if r.get("key") and r.get("company")
    ]


def _terms(roster):
    quoted = sorted(
        {'"' + re.sub(r'["\\]', "", row["company"]).strip() + '"' for row in roster if len(row["company"].strip()) >= 3}
    )
    return "(" + " OR ".join(quoted) + ")" if quoted else ""


def _merge(prior, new, newest):
    return {
        **prior,
        **{key: value for key, value in new.items() if key not in prior or newest(value) >= newest(prior[key])},
    }


def run(limit, live, environ=os.environ, gmail=None, outlook_accounts=None, connection=None, now=None):
    import contextlib  # noqa: PLC0415
    from lifeos.platform import db  # noqa: PLC0415

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    counts = {
        "status": "not_due",
        "roster": 0,
        "window_days": 0,
        "messages": 0,
        "unreadable": 0,
        "sources_failed": 0,
        "outcome_messages": 0,
        "matched": 0,
        "rejected": 0,
        "offers": 0,
        "ambiguous": 0,
        "unmatched": 0,
        "contacts": 0,
        "saved": 0,
    }
    with contextlib.ExitStack() as stack:
        conn = connection if connection is not None else stack.enter_context(db.connect())
        prior = hiring_snapshot.load(conn)
        roster = _roster(prior)
        counts["roster"] = len(roster)
        accepted, scanned, accepted_contacts, _ = load(conn)
        if scanned and datetime.fromisoformat(scanned) > now - timedelta(hours=SCAN_EVERY_HOURS):
            return counts
        if not roster:
            counts["status"] = "empty"
            return counts
        days = window_days(roster, now)
        counts["window_days"] = days
        records, unread, failed = [], [], 0

        def take(read, name):
            nonlocal failed
            try:
                found, bad = read()
            except (mail.MailError, GmailError):
                failed += 1
                unread.append(name)
                return
            records.extend(found)
            counts["unreadable"] += bad
            if bad:
                unread.append(name)

        try:
            gm = gmail or Gmail.from_env()
            take(lambda: mail.gmail_records(gm, now, days=days, terms=_terms(roster), limit=500), "Gmail")
        except GmailError:
            failed += 1
            unread.append("Gmail")
        accounts = list(outlook_accounts if outlook_accounts is not None else mail.outlook_accounts(environ))
        for label, client in accounts:
            if client is None:
                failed += 1
                unread.append(mail.source_name(label))
                continue
            take(lambda c=client, l=label: mail.outlook_records(c, l, now, days=days), mail.source_name(label))
        counts["messages"], counts["sources_failed"] = len(records), failed
        outcomes, stats = find(records, roster)
        heard = contacts(records, roster)
        counts.update(
            stats,
            matched=len(outcomes),
            rejected=sum(1 for o in outcomes.values() if o["kind"] == REJECTED),
            offers=sum(1 for o in outcomes.values() if o["kind"] == OFFER),
            contacts=len(heard),
        )
        if failed == 1 + len(accounts):  # no source could be listed: save nothing, keep the last accepted outcomes
            counts["status"] = "degraded"
            print("hiring-outcomes:", json.dumps(counts, sort_keys=True))
            raise fail("DEGRADED_SOURCE_INCOMPLETE")
        counts["status"] = "partial" if unread else "fresh"
        if unread:  # a source not read in full never yields a trustworthy "no outcome": keep what was accepted, add what was found
            counts["unread"] = list(dict.fromkeys(unread))
            outcomes, heard = (
                _merge(accepted, outcomes, lambda o: o.get("received", "")),
                _merge(accepted_contacts, heard, str),
            )
        if live:
            STORE.save_verified(
                conn,
                KEY,
                {
                    "schema": SCHEMA_V,
                    "taken_at": now.isoformat(),
                    "scanned_at": now.isoformat(),
                    "outcomes": outcomes,
                    "contacts": heard,
                    "unread": counts.get("unread", []),
                },
                fail,
            )
            counts["saved"] = 1
    return counts
