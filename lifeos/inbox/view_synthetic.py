"""LIFE-711: synthetic Inbox rows for the QA proof of the view writer. Fictional titles and example.com links only; never registered as a source, so it can never stand in for a live feed."""
# Two scenarios on the same keys. A: every source clean except Concord Personal (degraded, last accepted shown) and Followup (not provisioned). B: the next hour: one title changed, one record gone from a clean
# source (its row is archived), one source degraded (its rows stay), so one run exercises create, update, archive and keep. TGP is the fixed "tracked, not connected" row (`tgp_row`), never a source.
from datetime import timedelta

from lifeos.inbox import view_rows
from lifeos.inbox.inbox import Inbox, SourceHealth
from lifeos.inbox.model import Record, record_id

TGP_ROW_ID = "health:tgp:not_connected"


def _record(source, workspace, native, title, bucket, status, now, due_hours=None, kind="task"):
    account = f"jim-{workspace}"
    return Record(record_id(source, workspace, account, native), source, workspace, account, native, f"https://example.com/synthetic/{source}/{workspace}/{native}", title, "", "jim", status, bucket,
                  now + timedelta(hours=due_hours) if due_hours is not None else None, "America/Chicago" if due_hours is not None else None, now - timedelta(minutes=30), now - timedelta(minutes=2), "ok", kind)


def _inbox(now, scenario):
    b = scenario == "b"
    records = [
        _record("jira", "personal", "LIFE-1", "SYNTHETIC human UAT: " + ("check the receipt" if b else "review the receipt"), "needs_me", "Human UAT", now, 2),
        _record("jira", "bytalos", "BAD-1", "SYNTHETIC waiting on a vendor reply", "waiting", "Waiting", now),
        _record("calendar", "personal", "e1", "SYNTHETIC dentist, tomorrow", "upcoming", "confirmed", now, 20, "event"),
        _record("calendar", "bytalos", "e2", "SYNTHETIC client call", "upcoming", "confirmed", now, 5, "event"),
        _record("todoist", "personal", "t1", "SYNTHETIC renew the passport", "needs_me", "open", now, 30),
        _record("concord", "bytalos", "c1", "SYNTHETIC approve the draft plan", "approvals", "pending_approval", now, None, "approval_request"),
        _record("asana", "bytalos", "a1", "SYNTHETIC send the quarterly deck", "needs_me", "open", now, 48),
    ]
    if b:
        records = [r for r in records if r.native_id not in ("t1", "a1")]            # a1 is gone from a clean source (archived); t1's source is degraded, so its row is kept
    stale = [_record("concord", "personal", "c0", "SYNTHETIC last accepted Concord item", "approvals", "pending_approval", now, None, "approval_request")]
    health = [SourceHealth("jira", "personal", "jim-personal", "ok", "ok"), SourceHealth("jira", "bytalos", "jim-bytalos", "ok", "ok"),
              SourceHealth("calendar", "personal", "jim-personal", "ok", "ok"), SourceHealth("calendar", "bytalos", "jim-bytalos", "ok", "ok"),
              SourceHealth("todoist", "personal", "jim-personal", "degraded" if b else "ok", "records_rejected" if b else "ok"),
              SourceHealth("concord", "bytalos", "jim-bytalos", "ok", "ok"), SourceHealth("asana", "bytalos", "jim-bytalos", "ok", "ok"),
              SourceHealth("concord", "personal", "jim-personal", "degraded", "evidence_degraded")]
    return Inbox(tuple(records + stale), tuple(health))


def tgp_row():
    """The fixed row for TGP: tracked, not connected, by design (Jim, Nov 16 2026). It carries no record and no source read."""
    return {"Record ID": TGP_ROW_ID, "Kind": "health", "Title": "TGP", "Workspace": "", "Source": "TGP", "Bucket": "", "Source status": "", "Due": "", "Link": "", "Owner cue": "", "Next move": "",
            "Checkpoint": "", "Freshness": "", "Source updated": "", "Source health": "Tracked, not connected", "Health state": "unavailable", "Last refreshed": "", "Last OK": "", "Counted": False}


def rows(now, scenario="a"):
    """The desired rows of one scenario: `view_rows.project_rows` over a fictional Inbox, plus the TGP row."""
    last_ok = {("concord", "personal"): "Nov 16 2026, 1:00 PM CT"}
    return (*view_rows.project_rows(_inbox(now, scenario), now, last_ok=last_ok), tgp_row())


__all__ = ["TGP_ROW_ID", "rows", "tgp_row"]
