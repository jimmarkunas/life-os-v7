"""LIFE-711: the real Jira, Calendar and Todoist items for the Action Inbox list. Read-only, per (workspace, source), each source fails alone; fixed words and counts only."""
# The list had only Followup rows. This reads the current open items of the other three sources with the followup job's own grants, receipts and read-only clients (followup_fetchers.build_item_fetchers),
# normalizes each native dict with the governed adapters (lifeos.inbox.adapters: TGP and unknown workspaces refused, https links only, the presentation word comes from the source's own state), and turns
# the records into list rows. Order of every read: the grant is checked, the receipt is written first (no receipt, no read), then the read. A refused, failed, truncated or partly invalid read is shown as
# DEGRADED for that source and workspace and never as a zero; the rows that were good are kept. Nothing here writes to a provider or to Notion.
import re
from datetime import datetime

from lifeos.inbox import adapters
from lifeos.inbox.model import UNRELEASED_WORKSPACES, WORKSPACES, InboxError
from lifeos.platform import ct_stamp

SOURCES = ("jira", "calendar", "todoist")
NAME = {"jira": "Jira", "calendar": "Calendar", "todoist": "Todoist"}
BUCKET = {"needs_me": "Needs me", "waiting": "Waiting", "approvals": "Approvals", "upcoming": "Upcoming"}
RANK = {"Needs me": 1, "Approvals": 2, "Waiting": 3, "Upcoming": 4}                  # most urgent first
MOVE = {"human_uat": "Accept or reject in Jira", "task": "Open it and decide", "event": "Review the agenda"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
EPOCH = datetime(2026, 1, 1, tzinfo=ct_stamp.CENTRAL)


def rank(bucket, when):
    """One number that sorts the list: the bucket first, then the earliest due (minutes since Jan 1 2026, scaled below 1); no due sorts last in its bucket."""
    if when is None:
        return RANK[bucket] + 0.9999
    return RANK[bucket] + min(max((when.astimezone(ct_stamp.CENTRAL) - EPOCH).total_seconds() / 60, 0) / 1e7, 0.9998)


def due_text(when):
    """`Nov 20 · 9:00 AM CT` in Central; a date with no time of day (midnight) is just `Nov 20`; no due is empty."""
    if when is None:
        return ""
    when = when.astimezone(ct_stamp.CENTRAL)
    day = f"{MONTHS[when.month - 1]} {when.day}"
    return day if (when.hour, when.minute) == (0, 0) else f"{day} · {when.hour % 12 or 12}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'} CT"


def row_of(record):
    """One validated Record -> one list row (the page_rows.LIST shape)."""
    bucket = BUCKET[record.presentation]
    return {"id": record.record_id, "Outcome": record.title[:200], "Workspace": record.workspace.title(), "Source": NAME[record.source], "Bucket": bucket, "Rank": rank(bucket, record.due),
            "Owner": "Them" if bucket == "Waiting" else "You", "Next move": MOVE.get(record.kind, "Open it and decide") if record.source != "todoist" else "Mark done in Todoist",
            "Due": due_text(record.due), "Freshness": "fresh", "Link": record.url}


def slug(account):
    """An account LABEL as a record-id part (letters, digits and . _ @ : - only)."""
    return re.sub(r"[^A-Za-z0-9_.@:-]+", "-", str(account))[:80] or "account"


def _receipt(source, tenant, account, request_id, outcome, reason, now):
    return {"t": now.isoformat(), "source": source, "tenant": tenant, "account": account, "request_id": request_id, "outcome": outcome, "reason": reason}


def _read(wiring, scope, source, request_id, now):
    """-> (entry, records) for one source of one scope. Never raises for a refused or failed read; an audit failure raises before any read."""
    entry = {"workspace": scope.workspace, "source": source, "status": "ok", "reason": "ok", "last_accepted": None}
    mine = [g for g in wiring.grants if g.source == source and g.tenant == scope.workspace and g.account == scope.account]
    fetch = (wiring.items or {}).get(source)
    if not mine or fetch is None:
        wiring.audit(_receipt(source, scope.workspace, scope.account, request_id, "deny", "no_grant" if not mine else "no_reader", now))
        return {**entry, "status": "degraded", "reason": "no_grant" if not mine else "no_reader"}, []
    grant = mine[0]
    if grant.revoked or grant.expires <= now:
        reason = "grant_revoked" if grant.revoked else "grant_expired"
        wiring.audit(_receipt(source, scope.workspace, scope.account, request_id, "deny", reason, now))
        return {**entry, "status": "degraded", "reason": reason}, []
    wiring.audit(_receipt(source, scope.workspace, scope.account, request_id, "allow", "ok", now))             # the receipt first: no receipt, no read
    try:
        natives = fetch(grant.credential_ref)
    except Exception:                                                                                            # noqa: BLE001 - a connector failure is recorded by outcome only, never its message
        wiring.audit(_receipt(source, scope.workspace, scope.account, request_id, "degraded", "fetch_failed", now))
        return {**entry, "status": "degraded", "reason": "fetch_failed"}, []
    records, rejected = [], 0
    for native in natives:
        try:
            records.append(adapters.normalize(source, scope.workspace, slug(scope.account), native, scope.account, now, now))
        except (InboxError, KeyError, ValueError, TypeError):
            rejected += 1
    outcome, reason = ("degraded", "records_rejected") if rejected else ("ok", "ok")
    wiring.audit(_receipt(source, scope.workspace, scope.account, request_id, outcome, reason, now))
    return {**entry, "status": outcome, "reason": reason}, records


def collect(wiring, now):
    """-> {'sources': [entry per (workspace, source)], 'records': [Record, ...]}, or None when the wiring has no item readers. TGP and any unreleased workspace are never read."""
    if not getattr(wiring, "items", None):
        return None
    entries, records, n = [], [], 0
    for scope in wiring.scopes:
        if scope.workspace not in WORKSPACES or scope.workspace in UNRELEASED_WORKSPACES or "tgp" in str(scope.account).lower():
            continue
        for source in SOURCES:
            if source not in scope.sources or (source == "todoist" and scope.workspace != "personal"):
                continue
            n += 1
            entry, got = _read(wiring, scope, source, f"items-{n}", now)
            entries.append(entry)
            records += got
    return {"sources": entries, "records": records}


def lines(items):
    """The log lines: `inbox-items <workspace> <source> <status> <reason> n=<rows>`: fixed words and a count, never a title, a link or an account."""
    counts = {}
    for record in items["records"]:
        counts[(record.workspace, record.source)] = counts.get((record.workspace, record.source), 0) + 1
    return [f"inbox-items {e['workspace']} {e['source']} {e['status']} {e['reason']} n={counts.get((e['workspace'], e['source']), 0)}" for e in items["sources"]]


__all__ = ["BUCKET", "RANK", "SOURCES", "collect", "due_text", "lines", "rank", "row_of"]
