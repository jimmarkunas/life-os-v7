"""Attention admission policy (D127). Attention is a small weekly exception queue for genuine unresolved human judgment, never a second task list.

A message Jim labelled `LifeOS/Attention` (Gmail) or tagged with the `LifeOS Attention` category (Outlook) is a CANDIDATE. It is admitted unless another canonical
surface owns the action; a risk signal picks its category (Security, Account, Deadline, Admin); otherwise what Jim owes decides it (Reply needed, Review / decide, FYI), from the sender and subject only. Pure; fixed patterns; subjects are matched, never logged."""
import re

OWNED = (                                              # (owner, pattern over "sender | subject"): the action belongs to another surface, so Attention never duplicates it
    ("amazon", r"@[\w.-]*amazon\.[a-z.]+"),
    ("mail_alerts", r"anytime mailbox|physical mail|scanned (mail|letter|item)|you have (new )?mail|mail(box)? (notice|item)"),
    ("bills", r"payment (due|reminder|received|scheduled|processed|failed)|amount due|past due|autopay|minimum payment|(bill|invoice|statement) (is )?(ready|available|due)"),
    ("hiring", r"\binterview\b|recruiter|your application|application (received|status)|job alert|offer letter|\bhiring\b"),
    ("calendar", r"^[^|]*\|\s*(updated )?invitation\b|\|\s*(accepted|declined|canceled event|cancelled event):|calendar invite|meeting (request|invite)"),
    ("jira", r"@[\w.-]*atlassian\.[a-z]+|(?-i:\b[A-Z]{2,10}-\d+\b)"),
)
RISK = (                                               # (category, pattern): first match wins, Security first
    ("Security", r"security alert|suspicious|unusual (sign-?in|activity|login)|new (sign-?in|login|device)|unrecogni[sz]ed|someone (tried|signed|logged)|"
                 r"password (was )?(changed|reset)|verify (that )?(it'?s|it was) you|compromis|breach|fraud(ulent)? (alert|activity)|two-?factor|2-?step"),
    ("Account", r"account (locked|suspended|restricted|disabled|closed|change)|card\b[^|]{0,40}\b(added|removed)\b|\b(added|removed)\b[^|]{0,20}\bcard\b|"
                r"payment method (added|removed|changed)|(email|phone)( address| number)? (changed|updated)|unexpected|login attempt|verify your (account|identity)|new (payee|recipient)|beneficiary"),
    ("Deadline", r"action required|final notice|expires? (on|in|soon)|expiring|deadline|respond by|within \d+ (days|hours)"),
    ("Admin", r"\birs\b|\btax\b|\bdmv\b|insurance (claim|renewal|cancel)|renewal notice|legal notice|jury|licen[sc]e (expir|renew)"),
)
OWES = (                                               # (category, pattern over "sender | subject"): what the message asks of Jim; first match wins, FYI when none
    ("Reply needed", r"\?|@bytalos\.com|\b(can|could|would) you\b|please (send|confirm|let me know|reply|respond|advise)|let me know|need (you|your)|your (input|thoughts|feedback|answer)|waiting (on|for) you|following up|follow-up"),
    ("Review / decide", r"attach(ed|ment)|for your review|please review|approv(e|al)|\bsign(ature)?\b|proposal|contract|agreement|\bdraft\b|\bdecid(e|ion)\b|\bsow\b|\bnda\b|statement of work"),
)
_PREFIX = re.compile(r"^\s*((re|fwd?)\s*:\s*)+", re.I)


def item_text(subject):
    return re.sub(r"\s+", " ", _PREFIX.sub("", subject or "")).strip()[:140]


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def decide(sender, subject):
    """-> ("ADMIT", category) | ("OWNED", owner). Owned elsewhere always wins; otherwise the message is admitted (FYI when nothing says what it asks of Jim)."""
    haystack = f"{sender or ''} | {item_text(subject)}"
    for owner, pattern in OWNED:
        if re.search(pattern, haystack, re.I):
            return "OWNED", owner
    for category, pattern in RISK:
        if re.search(pattern, haystack, re.I):
            return "ADMIT", category
    for category, pattern in OWES:
        if re.search(pattern, haystack, re.I):
            return "ADMIT", category
    return "ADMIT", "FYI"                                     # Jim's label or category IS the signal: mail he routes here and nothing else owns stays visible
