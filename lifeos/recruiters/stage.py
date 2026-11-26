"""RECR-1.2: configured Gmail and Outlook mail (Inbox and Junk, last 14 days, read in memory) -> deterministic classification -> the existing Recruiters data source.

Dry run unless live; counts and fixed codes only (no name, address, subject, id or URL ever leaves this function). Mail is never changed. If every source listing is incomplete the run is
DEGRADED: classification counts are reported, nothing is written, and the run fails instead of reporting a trustworthy zero. If only some sources are incomplete (LIFE-646: one Outlook
account returning server errors) the run is PARTIAL: what the readable sources hold is written (every write rests on a message that was read), nothing is concluded from the unread
source, the heading and the counts name it (`unread`), and the ledger records only what was read, so the next run reads the unread source's mail as new.

A LinkedIn notification with no recruiting evidence is UNRESOLVED (counted, no row; D193). Rows already made from such a notification are never changed here: a sweep over past rows is a separate decision."""
import json
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lifeos.platform import gate, mail
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform import mail_seen as seen
from . import backfill, classify, fill, notion as store, qualify, scan

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


def run(limit, live, environ=os.environ, gmail=None, outlook_accounts=None, notion=None, now=None, ledger=AUTO, chores=True):
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
    action, kind, why = scan.plan(now, scan.step_row(), state, gate.is_tick_event(environ))
    if action == "read" and environ.get("RECRUITERS_FULL_SCAN") == "on":
        kind, why = seen.FULL_SCAN, "manual full scan"                    # LIFE-715: the manual workflow's "full rescan" box: read all 14 days this run, ignoring the seen list
    stats = {"scanned": 0, "new": 0}
    if action == "skip":
        _announce(environ, state[1], state[0])
        skipped = {"scan": "skipped", "why": why, "ledger": ledger_status}
        if chores:
            skipped.update(_chores(environ, live, now, notion))                       # LIFE-973: the status sync and reply-by dates need no mail, so a skipped read still runs them
        return skipped
    incremental = kind == seen.NEW_MAIL
    known = (lambda provider, ident: seen.key(provider, ident) in known_keys) if incremental else None
    counts = {"messages": 0, "unreadable": 0, "sources_failed": 0, "outlook_accounts": 0, "direct_human": 0, "human_relay": 0, "excluded": 0, "unresolved": 0, "needs_review": 0,
              "would_create": 0, "would_update": 0, "existing": 0, "done_kept": 0, "ambiguous": 0, "created": 0, "updated": 0, "failed": 0, "excluded_why": {}, "unresolved_why": {},
              "waiting": 0, "would_schedule": 0, "scheduled": 0, "would_reopen_due": 0, "reopened_due": 0, "notion": "ok",
              "scan": kind, "scan_why": why, "ledger": ledger_status}
    records, unread = [], []
    gm = None

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
            unread.append(mail.source_name(label))
            continue
        take(lambda c=client, l=label: mail.outlook_records(c, l, now, known=known, stats=stats), mail.source_name(label))
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
        if chores:
            _status_and_dates(notion, write, counts, now.astimezone(CHICAGO).date())
            _fill(notion, write, counts, records, gm, accounts)
            _asks(notion, write, counts, records, gm, accounts)
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
        if action == "create" or (action == "update" and not str(row["what"] or "").strip()):         # LIFE-989: What they want is never written blank
            c["what"], c["what_fallback"] = store.what_or_fallback(c)
            if c["what_fallback"]:
                c["needs_review"] = True
            if action == "update":
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


def _chores(environ, live, now, notion):
    """The Notion-only work of a run that reads no mail: Done / Active / Needs review follow Action Status, and every open row gets its reply-by date. -> counts."""
    counts = {"status_synced": 0, "would_sync": 0, "dates_set": 0, "would_set_date": 0, "failed": 0}
    try:
        notion = notion or _client(environ)
        store.check_schema(notion)
        _status_and_dates(notion, bool(live), counts, now.astimezone(CHICAGO).date())
    except NotionError:
        counts["chores"] = "unavailable"
    return counts


def _status_and_dates(notion, write, counts, today):
    """LIFE-973: Action Status is the only source of truth. A row whose Done / Active / Needs review disagree with it is corrected (Completed, Dismissed, Declined: Done on, Active and Needs review off;
    Needs Action: Done off, Active on). A Needs Action row with no Follow-up Date gets one (a set date is never replaced). Each write is read back."""
    for key in ("status_synced", "would_sync", "dates_set", "would_set_date", "failed"):
        counts.setdefault(key, 0)
    for row in store.disagreeing_rows(notion):
        props = store.sync_plan(row)
        if not props:
            continue
        counts["would_sync"] += 1
        if write:
            try:
                store.set_props(notion, row, props, {"done": props["Done"]["checkbox"], "active": props["Active"]["checkbox"], **({"needs_review": False} if "Needs review" in props else {})})
                counts["status_synced"] += 1
            except NotionError:
                counts["failed"] += 1
    for row in store.rows_where(notion, {"and": [{"property": "Action Status", "select": {"equals": store.NEEDS_ACTION}}, {"property": "Follow-up Date", "date": {"is_empty": True}}]}):
        day = store.default_follow_up(row)
        if day is None:
            continue
        counts["would_set_date"] += 1
        if write:
            try:
                store.set_props(notion, row, {"Follow-up Date": {"date": {"start": day.isoformat()}}}, {"follow_up": day.isoformat()})
                counts["dates_set"] += 1
            except NotionError:
                counts["failed"] += 1


def _asks(notion, write, counts, records, gm, accounts):
    """LIFE-989: What they want is never empty. Every open row (Needs Action, Waiting, Replied) with a blank one gets the ask from the message behind it, or the fallback sentence with Needs review ticked.
    Older rows' messages are read once more (backfill.MAX_PER_RUN a run, read-only); a row that already has text is skipped. The log carries the row's page id and `ask` or `fallback`, never a name or text."""
    counts.update(asks_filled=0, asks_would_fill=0, asks_fallback=0, asks_unread=0)
    by_url = {m["url"]: m for m in records if m.get("url")}
    older = 0
    for row in fill.blank_ask_rows(notion):
        if row["source_url"] not in by_url:
            if older >= backfill.MAX_PER_RUN:
                continue
            older += 1
        message = backfill.message_for(row, by_url, gm, accounts)
        if message is None:
            counts["asks_unread"] += 1
            continue
        try:
            kind = fill.apply_ask(notion, row, message, write)
        except NotionError:
            counts["failed"] += 1
            continue
        if kind:
            counts["asks_would_fill"] += 1
            counts["asks_filled"] += 1 if write else 0
            counts["asks_fallback"] += 1 if kind == "fallback" else 0
            print("recruiters-ask:", row["page_id"].replace("-", "")[:8], kind)


def _fill(notion, write, counts, records, gm, accounts):
    """LIFE-973: Recruiter Company, Role and Client of every open row that has a blank one, from the one message behind it (lifeos.recruiters.fill). Messages this run read are used as read; older rows' messages
    are read once more (backfill.MAX_PER_RUN a run). A set value is never replaced. The log carries the row's page id and where each value came from, never a name or a company."""
    counts.update(filled=0, would_fill=0, fill_unread=0)
    by_url = {m["url"]: m for m in records if m.get("url")}
    older = 0
    for row in fill.blank_rows(notion):
        if row["source_url"] not in by_url:
            if older >= backfill.MAX_PER_RUN:
                continue
            older += 1
        message = backfill.message_for(row, by_url, gm, accounts)
        if message is None:
            counts["fill_unread"] += 1
            continue
        try:
            sources = fill.apply(notion, row, message, write)
        except NotionError:
            counts["failed"] += 1
            continue
        if sources:
            counts["would_fill"] += 1
            counts["filled"] += 1 if write else 0
            print("recruiters-fill:", row["page_id"].replace("-", "")[:8], ", ".join(f"{f}={src}" for f, src in sorted(sources.items())))
