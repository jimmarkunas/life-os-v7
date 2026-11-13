"""RECR-1.2: configured Gmail and Outlook mail (Inbox and Junk, last 14 days, read in memory) -> deterministic classification -> the existing Recruiters data source.

Dry run unless live; counts and fixed codes only (no name, address, subject, id or URL ever leaves this function). Mail is never changed. If every source listing is incomplete the run is
DEGRADED: classification counts are reported, nothing is written, and the run fails instead of reporting a trustworthy zero. If only some sources are incomplete (LIFE-646: one Outlook
account returning server errors) the run is PARTIAL: what the readable sources hold is written (every write rests on a message that was read), nothing is concluded from the unread
source, the heading and the counts name it (`unread`), and the ledger records only what was read, so the next run reads the unread source's mail as new.

A LinkedIn notification with no recruiting evidence is UNRESOLVED (counted, no row; D193). Rows already made from such a notification are never changed here: a sweep over past rows is a separate decision."""
import json
import os
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lifeos.platform import mail
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform import mail_seen as seen
from . import classify, notion as store, qualify, scan

CHICAGO = ZoneInfo("America/Chicago")
AUTO = object()                       # LIFE-610: "build the production ledger if the database is configured"
REASONS = ("AUTOREPLY", "BULK", "AUTOMATION_ADDRESS", "HIRING_MANAGER", "NO_RECRUITER_EVIDENCE", "RELAY_NOT_A_MESSAGE")


def _client(environ):
    return Client(environ, token_name="NOTION_RECRUITERS_TOKEN", source_name="NOTION_RECRUITERS_DATA_SOURCE_ID")


def _announce(environ, kind, moment, unread=()):
    """The kind and Central time of the mail read the Recruiter Outreach heading reports, passed to the next steps as step outputs (a skipped read reports the last one).
    A partial read names the unread sources in the kind, e.g. `14-day scan (Outlook personal unread)`."""
    path = environ.get("GITHUB_OUTPUT")
    if path and kind and moment:
        shown = f"{kind} ({', '.join(unread)} unread)" if unread else kind
        with open(path, "a") as handle:
            handle.write(f"scan_kind={shown}\nscan_at={scan.clock(moment)}\n")


def _source(label):
    """A fixed, lower-case name for an Outlook account label, safe to print."""
    return "Outlook " + (re.sub(r"[^a-z0-9_-]", "", str(label).lower())[:24] or "account")


def run(limit, live, environ=os.environ, gmail=None, outlook_accounts=None, notion=None, now=None, ledger=AUTO):
    now = now or datetime.now(timezone.utc)
    began = time.monotonic()
    ledger = seen.production(environ) if ledger is AUTO else ledger
    known_keys, state, ledger_status = None, None, "off"
    if ledger is not None:
        try:
            keys, last_read, last_kind, last_full = ledger.read()
            known_keys, state, ledger_status = keys, (last_read, last_kind, last_full), ("ok" if keys else "empty")
        except seen.LedgerError:
            ledger_status = "unavailable"
    action, kind, why = scan.plan(now, scan.step_row(), state, environ.get("GITHUB_EVENT_NAME") == "workflow_run")
    stats = {"scanned": 0, "new": 0}
    if action == "skip":
        _announce(environ, state[1], state[0])
        return {"scan": "skipped", "why": why, "ledger": ledger_status}
    incremental = kind == seen.NEW_MAIL
    known = (lambda provider, ident: seen.key(provider, ident) in known_keys) if incremental else None
    counts = {"messages": 0, "unreadable": 0, "sources_failed": 0, "outlook_accounts": 0, "direct_human": 0, "human_relay": 0, "excluded": 0, "unresolved": 0, "needs_review": 0,
              "would_create": 0, "would_update": 0, "existing": 0, "done_kept": 0, "ambiguous": 0, "created": 0, "updated": 0, "failed": 0, "excluded_why": {}, "unresolved_why": {},
              "waiting": 0, "would_schedule": 0, "scheduled": 0, "would_reopen_due": 0, "reopened_due": 0, "notion": "ok",
              "scan": kind, "scan_why": why, "ledger": ledger_status}
    records, unread = [], []

    def take(read, name):
        try:
            found, bad = read()
        except (mail.MailError, GmailError):
            counts["sources_failed"] += 1
            unread.append(name)
            return
        records.extend(found)
        counts["unreadable"] += bad

    try:
        gm = gmail or Gmail.from_env()
        take(lambda: mail.gmail_records(gm, now, known=known, stats=stats), "Gmail")
    except GmailError:
        counts["sources_failed"] += 1
        unread.append("Gmail")
    accounts = list(outlook_accounts if outlook_accounts is not None else mail.outlook_accounts(environ))
    counts["outlook_accounts"] = len(accounts)
    for label, client in accounts:
        if client is None:
            counts["sources_failed"] += 1
            unread.append(_source(label))
            continue
        take(lambda c=client, l=label: mail.outlook_records(c, l, now, known=known, stats=stats), _source(label))
    counts["messages"] = len(records)
    counts["scanned"], counts["new"] = (stats["scanned"], stats["new"]) if incremental else (len(records), len(records))      # a 14-day scan reads everything it lists
    candidates = []
    for message in records:
        shape, reason, evidence = classify.classify(message)
        verdict, detail = qualify.qualify(message, shape, reason, evidence)
        if verdict == "EXCLUDED":
            counts["excluded"] += 1
            counts["excluded_why"][detail] = counts["excluded_why"].get(detail, 0) + 1
        elif verdict == "UNRESOLVED":
            counts["unresolved"] += 1
            counts["unresolved_why"][detail] = counts["unresolved_why"].get(detail, 0) + 1
        else:
            counts["direct_human" if shape == classify.DIRECT_HUMAN else "human_relay"] += 1
            counts["needs_review"] += 1 if detail["needs_review"] else 0
            candidates.append(detail)
    degraded = counts["sources_failed"] > 0
    partial = degraded and len(unread) < 1 + len(accounts)                # LIFE-646: some source was read, some was not
    write = live and (not degraded or partial)
    try:
        notion = notion or _client(environ)
        store.check_schema(notion)
    except NotionError as error:
        if live:
            raise store.RecruitersError(str(error)[:60]) from None
        counts["notion"] = "unavailable"
        notion = None
    if notion is not None:
        _apply(candidates, notion, write, counts)                          # every candidate is a message that was read; an unread source adds none
        _follow_ups(notion, write, counts, now.astimezone(CHICAGO).date())
    if degraded:
        counts["unread"] = unread
    if degraded and not partial:
        if live:
            counts["notion"] = "not_written_degraded"
        print("recruiters:", json.dumps(counts, sort_keys=True))        # counts only: so a degraded run still says what it saw
        raise store.RecruitersError("RECRUITERS_DEGRADED_SOURCE_INCOMPLETE")
    if live and counts["failed"]:                                     # a write or its read-back failed: not a success, whatever the other rows did (stage-local: D182's publish-only exit is untouched)
        print("recruiters:", json.dumps(counts, sort_keys=True))
        raise store.RecruitersError("RECRUITERS_RECORDS_FAILED")
    if live and ledger is not None and counts["notion"] == "ok":                     # recorded only after a complete live run: a failed write is read again next time
        try:
            ledger.commit({seen.key(m["provider"], m["id"]) for m in records}, kind, now)
        except seen.LedgerError:
            counts["ledger"] = "not_saved"
    counts["seconds"] = int(time.monotonic() - began)
    _announce(environ, kind, now, unread)
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


def _follow_ups(notion, write, counts, today):
    """RECR-1.4: a Waiting row gets a follow-up date two business days out, and returns to Needs Action when that date comes (D220)."""
    rows = store.waiting_rows(notion)
    counts["waiting"] = len(rows)
    for row in rows:
        kind, props = store.follow_up_plan(row, today)
        if kind is None:
            continue
        counts["would_schedule" if kind == "schedule" else "would_reopen_due"] += 1
        if write:
            try:
                store.set_follow_up(notion, row, props)
                counts["scheduled" if kind == "schedule" else "reopened_due"] += 1
            except NotionError:
                counts["failed"] += 1
