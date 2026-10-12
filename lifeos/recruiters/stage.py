"""RECR-1.2: configured Gmail and Outlook mail (Inbox and Junk, last 14 days, read in memory) -> deterministic classification -> the existing Recruiters data source.

Dry run unless live; counts and fixed codes only (no name, address, subject, id or URL ever leaves this function). Mail is never changed. If any source listing is incomplete the run is
DEGRADED: classification counts are reported, nothing is written, and the run fails instead of reporting a trustworthy zero.

A LinkedIn notification with no recruiting evidence is UNRESOLVED (counted, no row; D185). An existing row made from such a notification is changed only when its exact message
link is passed in `quarantine_sources` (nothing passes it today: a sweep over past rows is a separate decision); it is then set inactive only if nobody else has touched it."""
import json
import os
from datetime import datetime, timezone

from lifeos.platform import mail
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.notion_client import Client, NotionError
from . import classify, notion as store, qualify
from .identity import clean_name, week_ending

REASONS = ("AUTOREPLY", "BULK", "AUTOMATION_ADDRESS", "HIRING_MANAGER", "NO_RECRUITER_EVIDENCE", "RELAY_NOT_A_MESSAGE")


def _client(environ):
    return Client(environ, token_name="NOTION_RECRUITERS_TOKEN", source_name="NOTION_RECRUITERS_DATA_SOURCE_ID")


def run(limit, live, environ=os.environ, gmail=None, outlook_accounts=None, notion=None, now=None, quarantine_sources=()):
    now = now or datetime.now(timezone.utc)
    counts = {"messages": 0, "unreadable": 0, "sources_failed": 0, "outlook_accounts": 0, "direct_human": 0, "human_relay": 0, "excluded": 0, "unresolved": 0, "needs_review": 0,
              "would_create": 0, "would_update": 0, "existing": 0, "done_kept": 0, "ambiguous": 0, "created": 0, "updated": 0, "failed": 0, "excluded_why": {}, "unresolved_why": {}, "would_quarantine": 0, "quarantined": 0, "quarantine_skipped": 0, "notion": "ok"}
    records = []

    def take(read):
        try:
            found, bad = read()
        except (mail.MailError, GmailError) as error:
            counts["sources_failed"] += 1
            return
        records.extend(found)
        counts["unreadable"] += bad

    try:
        gm = gmail or Gmail.from_env()
        take(lambda: mail.gmail_records(gm, now))
    except GmailError:
        counts["sources_failed"] += 1
    accounts = list(outlook_accounts if outlook_accounts is not None else mail.outlook_accounts(environ))
    counts["outlook_accounts"] = len(accounts)
    for label, client in accounts:
        if client is None:
            counts["sources_failed"] += 1
            continue
        take(lambda c=client, l=label: mail.outlook_records(c, l, now))
    counts["messages"] = len(records)
    candidates, unverified, quarantine_sources = [], [], set(quarantine_sources)
    for message in records:
        shape, reason, evidence = classify.classify(message)
        verdict, detail = qualify.qualify(message, shape, reason, evidence)
        if verdict == "EXCLUDED":
            counts["excluded"] += 1
            counts["excluded_why"][detail] = counts["excluded_why"].get(detail, 0) + 1
        elif verdict == "UNRESOLVED":
            counts["unresolved"] += 1
            counts["unresolved_why"][detail] = counts["unresolved_why"].get(detail, 0) + 1
            if detail == classify.RELAY_UNVERIFIED and message.get("url") in quarantine_sources:
                unverified.append((clean_name(evidence.get("name", ""), allow_single=True), week_ending(message["received"]), message["url"]))
        else:
            counts["direct_human" if shape == classify.DIRECT_HUMAN else "human_relay"] += 1
            counts["needs_review"] += 1 if detail["needs_review"] else 0
            candidates.append(detail)
    degraded = counts["sources_failed"] > 0
    try:
        notion = notion or _client(environ)
        store.check_schema(notion)
    except NotionError as error:
        if live:
            raise store.RecruitersError(str(error)[:60]) from None
        counts["notion"] = "unavailable"
        notion = None
    if notion is not None:
        _apply(candidates, notion, live and not degraded, counts)
        _quarantine(unverified, notion, live and not degraded, counts)
    if degraded and live:
        counts["notion"] = "not_written_degraded"
    if degraded:
        print("recruiters:", json.dumps(counts, sort_keys=True))        # counts only: so a degraded run still says what it saw
        raise store.RecruitersError("RECRUITERS_DEGRADED_SOURCE_INCOMPLETE")
    return counts


def _apply(candidates, notion, write, counts):
    cache, newest = {}, {}
    for c in candidates:                                              # one proposal per natural key: the newest message wins, so a replay and a double mail agree
        k = (c["week_ending"],) + tuple(" ".join(str(c[f]).split()).casefold() for f in ("person", "company", "role"))
        if k not in newest or c["last_contact"] > newest[k]["last_contact"]:
            newest[k] = c
    for c in newest.values():
        if c["week_ending"] not in cache:
            cache[c["week_ending"]] = store.week_rows(notion, c["week_ending"])
        action, row, props = store.plan(cache[c["week_ending"]], c)
        if action == "ambiguous":
            counts["ambiguous"] += 1
        elif action == "done":
            counts["done_kept"] += 1
        elif action == "existing":
            counts["existing"] += 1
        elif action == "update":
            counts["would_update"] += 1
            if write:
                try:
                    store.update(notion, row, props, c)
                    counts["updated"] += 1
                except NotionError:
                    counts["failed"] += 1
                cache.pop(c["week_ending"], None)
        else:
            counts["would_create"] += 1
            if write:
                try:
                    store.create(notion, c)
                    counts["created"] += 1
                except NotionError:
                    counts["failed"] += 1
                cache.pop(c["week_ending"], None)


def _quarantine(unverified, notion, write, counts):
    """Only rows whose exact message link was passed in: the page must be the automation's own, untouched since it created it. Anything else is counted and left alone."""
    for person, week, url in unverified:
        rows = [r for r in store.week_rows(notion, week) if r["source_url"] == url and person and store.norm(r["person"]) == store.norm(person)]
        if len(rows) != 1:
            counts["ambiguous" if rows else "quarantine_skipped"] += 1
        elif not store.quarantine_eligible(rows[0], url):
            counts["quarantine_skipped"] += 1
        else:
            counts["would_quarantine"] += 1
            if write:
                try:
                    store.quarantine(notion, rows[0], week)
                    counts["quarantined"] += 1
                except NotionError:
                    counts["failed"] += 1
