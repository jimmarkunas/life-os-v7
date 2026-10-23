"""Outcome mail for the Hiring Pipeline (D190): a rejection or an offer that reached Gmail or Outlook after the pipeline last heard from an opportunity.

The pipeline's stages come from the Job Ledger, the Notes pages and the Calendar, so an interview that ended in a rejection email stayed "Awaiting outcome" forever. This stage reads mail
(read only: nothing is marked, moved, labelled, deleted, sent or forwarded) for the companies already in the pipeline, decides by fixed wording whether a message is a rejection or an offer, and
keeps one private row of what it found. The Hiring Pipeline reads that row and says "Rejection email Sep 12 · confirm, then move the Notes page to Retired". It never closes anything itself: a
wrong match costs a line of text, never an opportunity. Counts and fixed codes only; no company, sender, subject, address or message text is logged or stored, only the pipeline's own row key, the kind,
the received time and whether the role matched as well as the company.

Matching is deterministic. A message belongs to an opportunity only if the company's words appear in order in the sender's name, the subject, the sender's domain or the first part of the body, it arrived
after the opportunity's last evidence (its latest event, else its Applied date), and it contains outcome wording. A message that names two pipeline companies, or two roles of one company that the text
cannot tell apart, is ambiguous and is counted, never guessed."""
import json
import os
import re
from datetime import datetime, timedelta, timezone

from lifeos.platform import mail, names
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.notion_client import NotionError
from lifeos.platform.snapshot_store import Store
from . import snapshot as hiring_snapshot

STORE, KEY, SCHEMA_V = Store("v7_hiring_outcomes"), 1, 1
SCAN_EVERY_HOURS = 12                  # outcome mail is not urgent; the stage decides when it is due, so a tick that is not due reads no mail
STALE_HOURS = 18                       # E13: a scan not saved for this long (one missed cycle plus a tick) makes the Hiring card DEGRADED; the last accepted outcomes stay shown
MIN_DAYS, MAX_DAYS = 14, 60            # how far back it reads: from the oldest evidence in the pipeline, between these bounds
BODY_CHARS = 3000
REJECTED, OFFER = "REJECTED", "OFFER"

REJECT = re.compile("|".join((
    r"\bnot (?:be )?moving forward\b", r"\bwill not be (?:proceeding|continuing|advancing|moving)\b", r"\bwon'?t be (?:moving|proceeding|continuing)\b",
    r"\b(?:move|moving|proceed|proceeding|go|going)\s+forward with (?:other|another|different)\b", r"\bpursu(?:e|ing) (?:other|another) (?:candidate|applicant|individual)s?\b",
    r"\bdecided not to (?:move|proceed|go) forward\b", r"\bregret to inform\b", r"\bnot (?:been )?selected\b", r"\bposition (?:has been|was) filled\b", r"\bunable to (?:offer|move forward|proceed)\b",
    r"\bdecided (?:not )?to (?:move|proceed|go) (?:forward )?with (?:other|another|different)\b", r"\bother candidates? (?:whose|who|that) (?:experience|background|qualifications|skills)\b")), re.I)
AUTOMATED = re.compile(r"(?i)no-?reply|do-?not-?reply|auto|confirm|notif|alert|news|digest|market|ship|order|store|update|mailer|bounce|jobs?\b|careers?|talent"
                       r"|recruit|\bhr\b|info|support|team|hello|help|account|security|billing|service|apply|applicant|candidate|receipt|deal|offer|promo|reward|email|mail"
                       r"|deliver|notice|payment|message|return|reply|event|survey|calendar|invite|refund|order")     # LIFE-26: a mailbox, not a person
OFFERED = re.compile("|".join((r"\bpleased to (?:offer|extend)\b", r"\boffer of employment\b", r"\bextend(?:ing)? (?:you )?an? (?:formal )?offer\b", r"\boffer letter\b")), re.I)


class OutcomeError(NotionError):
    """Fixed codes only."""


def fail(code):
    return OutcomeError("OUTCOMES_" + code)


def _tokens(text):
    return names.tokens(text)


def _contains(haystack, needle):
    n = len(needle)
    return n > 0 and any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def kind_of(text):
    """REJECTED | OFFER | None from the fixed wording. An offer wins when both appear (a rejection of one role and an offer for another is not told apart here, so the person looks)."""
    if OFFERED.search(text):
        return OFFER
    if REJECT.search(text):
        return REJECTED
    return None


def _domain_labels(address):
    host = (address or "").rsplit("@", 1)[-1].lower()
    return [label for label in re.split(r"[.\-]", host) if label]


def mentions(message, company):
    """True when the company's words appear in order in the sender's name, the subject, the sender's domain or the opening of the body."""
    want = _tokens(company)
    if not want or len("".join(want)) < 3:
        return False
    for text in (message.get("sender_name", ""), message.get("subject", ""), (message.get("body") or "")[:BODY_CHARS]):
        if _contains(_tokens(text), want):
            return True
    return len(want) == 1 and want[0] in _domain_labels(message.get("sender_address"))


def role_in(message, role):
    words = set(_tokens(role))
    return bool(words) and words <= set(_tokens(f"{message.get('subject', '')} {(message.get('body') or '')[:BODY_CHARS]}"))


def _since(row):
    """The moment the pipeline last had evidence for this row: its latest event, else its Applied date (start of that day, Central), else None."""
    value = row.get("event_start") or row.get("applied_on")
    if not value:
        return None
    moment = datetime.fromisoformat(value) if "T" in value else datetime.fromisoformat(value + "T00:00:00-05:00")
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _received(message):
    moment = datetime.fromisoformat(str(message["received"]).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def find(messages, roster):
    """-> ({row key: {"kind", "received", "match"}}, {"ambiguous": n, "outcome_messages": n, "unmatched": n}). Pure.
    roster: [{"key", "company", "role", "event_start", "applied_on"}]. One outcome per row: the newest qualifying message."""
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
        found[row["key"]] = {"kind": kind, "received": at.astimezone(timezone.utc).isoformat(), "match": why}
    return found, stats


def attribute(message, roster, at):
    """-> (the one roster row this message belongs to, "role" | "company"), or (None, "unmatched" | "ambiguous"). It must name the company and arrive
    after the row's last evidence; two pipeline companies, or two roles at one company the text cannot tell apart, are ambiguous and never guessed."""
    named = [row for row in roster if mentions(message, row["company"]) and (_since(row) is None or at > _since(row))]
    if not named:
        return None, "unmatched"
    if len({tuple(_tokens(row["company"])) for row in named}) > 1:
        return None, "ambiguous"
    by_role = [row for row in named if role_in(message, row["role"])]
    pick = by_role if by_role else named
    return (pick[0], "role" if by_role else "company") if len(pick) == 1 else (None, "ambiguous")


SUFFIXES = {"co", "com", "org", "net", "ac", "gov"}                # the second-level labels of a two-label public suffix (acme.co.uk, acme.com.au)


def _own_domain(domain, company):
    """LIFE-26: the employer's own registered domain and nothing under it: acme.com or acme.co.uk, never a tenant such as acme.myworkday.com or jira.acme.net."""
    labels = [label for label in domain.lower().split(".") if label]
    base = labels[:-2] if len(labels) > 2 and labels[-2] in SUFFIXES else labels[:-1]
    return len(base) == 1 and base[0].replace("-", "") == "".join(_tokens(company))


def _person(message, company):
    """LIFE-26: a person writing, from their own mailbox: a display name of 2-4 words, none a company word or a mailbox word, one of which also names the mailbox
    (Maria Lopez <maria.lopez@...> or <mlopez@...>). Brand, team and transactional senders have no such name."""
    words = re.findall(r"[a-z]+", (message.get("sender_name") or "").lower())
    local = (message.get("sender_address") or "").partition("@")[0].lower()
    return (2 <= len(words) <= 4 and not set(words) & set(_tokens(company)) and not any(AUTOMATED.search(w) for w in words)
            and any(len(w) >= 3 and w in local for w in words))


def contacts(messages, roster):
    """LIFE-26: {row key: the newest received time of a direct message from that row's hiring team}. Pure. Only positive evidence counts: an inbox
    message (never junk, list, bulk or auto-submitted mail) from a named person's own mailbox on the employer's own registered domain, attributed to
    exactly one row. A brand, team or transactional sender, any other domain (an agency, an ATS or SaaS tenant, a marketplace), or mail that cannot be
    attributed never counts: an unknown is never contact, so a row only goes Stale sooner."""
    found = {}
    for message in sorted(messages, key=_received):
        local, _, domain = (message.get("sender_address") or "").partition("@")
        marked = message.get("markers") or {}
        if (message.get("folder") != "INBOX" or not local or not domain or "@" in domain or AUTOMATED.search(local)
                or marked.get("bulk") or marked.get("auto") or marked.get("noreply")):         # list, newsletter or auto-submitted mail is never a person writing
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
    """True when the outcomes table exists (a read before the first live save must not create it)."""
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE %s", (STORE.table,))
        return cursor.fetchone() is not None


def load(connection):
    """The stored outcomes ({row key: {...}}), when they were last fully read, and LIFE-26's last direct contact per row ({row key: time}), or ({}, None, {})."""
    if not _exists(connection):
        return {}, None, {}
    try:
        stored = STORE.load(connection, KEY)
    except (TypeError, ValueError):
        raise fail("STORE_INVALID") from None
    if stored is None:
        return {}, None, {}
    if stored.get("schema") != SCHEMA_V or not isinstance(stored.get("outcomes"), dict) or not isinstance(stored.get("contacts", {}), dict):
        raise fail("STORE_INVALID")
    return stored["outcomes"], stored.get("scanned_at"), stored.get("contacts", {})


def _roster(prior):
    return [{"key": r["key"], "company": r["company"], "role": r["role"], "event_start": r.get("event_start"), "applied_on": r.get("applied_on")}
            for r in (prior or {}).get("rows") or [] if r.get("key") and r.get("company")]


def _terms(roster):
    quoted = sorted({'"' + re.sub(r'["\\]', "", row["company"]).strip() + '"' for row in roster if len(row["company"].strip()) >= 3})
    return "(" + " OR ".join(quoted) + ")" if quoted else ""


def run(limit, live, environ=os.environ, gmail=None, outlook_accounts=None, connection=None, now=None):
    import contextlib                                              # noqa: PLC0415
    from lifeos.platform import db                                 # noqa: PLC0415
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    counts = {"status": "not_due", "roster": 0, "window_days": 0, "messages": 0, "unreadable": 0, "sources_failed": 0, "outcome_messages": 0, "matched": 0,
              "rejected": 0, "offers": 0, "ambiguous": 0, "unmatched": 0, "contacts": 0, "saved": 0}
    with contextlib.ExitStack() as stack:
        conn = connection if connection is not None else stack.enter_context(db.connect())
        prior = hiring_snapshot.load(conn)
        roster = _roster(prior)
        counts["roster"] = len(roster)
        _, scanned, _ = load(conn)
        if scanned and datetime.fromisoformat(scanned) > now - timedelta(hours=SCAN_EVERY_HOURS):
            return counts
        if not roster:
            counts["status"] = "empty"
            return counts
        days = window_days(roster, now)
        counts["window_days"] = days
        records, failed = [], 0

        def take(read):
            nonlocal failed
            try:
                found, bad = read()
            except (mail.MailError, GmailError):
                failed += 1
                return
            records.extend(found)
            counts["unreadable"] += bad

        try:
            gm = gmail or Gmail.from_env()
            take(lambda: mail.gmail_records(gm, now, days=days, terms=_terms(roster), limit=500))
        except GmailError:
            failed += 1
        for label, client in (outlook_accounts if outlook_accounts is not None else mail.outlook_accounts(environ)):
            if client is None:
                failed += 1
                continue
            take(lambda c=client, l=label: mail.outlook_records(c, l, now, days=days))
        counts["messages"], counts["sources_failed"] = len(records), failed
        outcomes, stats = find(records, roster)
        heard = contacts(records, roster)
        counts.update(stats, matched=len(outcomes), rejected=sum(1 for o in outcomes.values() if o["kind"] == REJECTED), offers=sum(1 for o in outcomes.values() if o["kind"] == OFFER), contacts=len(heard))
        if failed or counts["unreadable"]:                       # a listing that is complete but had a message that could not be read is just as incomplete
            counts["status"] = "degraded"                         # a source that could not be read in full is never a trustworthy "no outcome": nothing is saved, the last accepted outcomes stay
            print("hiring-outcomes:", json.dumps(counts, sort_keys=True))
            raise fail("DEGRADED_SOURCE_INCOMPLETE")
        counts["status"] = "fresh"
        if live:
            STORE.save_verified(conn, KEY, {"schema": SCHEMA_V, "taken_at": now.isoformat(), "scanned_at": now.isoformat(), "outcomes": outcomes, "contacts": heard}, fail)
            counts["saved"] = 1
    return counts
