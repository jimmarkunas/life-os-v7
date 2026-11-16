"""Pure projection of the Action Inbox into Notion row dicts (LIFE-711): items, review rows and a health row per source and workspace. No Notion call, no store."""
# What a row is. Every dict below is plain data: property name -> string or bool. Nothing here reads a provider, a credential or Notion, and no description or body text is copied (a title, a source link
# and a few derived labels only). The Notion writer is a separate step behind an injected callable (view_plan.py); this module is a pure function of the Inbox it is given.
#
# Honesty rules (the ticket's Followup display and trust contract): a source that failed, is stale or is not provisioned is shown as such, never as an empty list; a count is only shown as a number
# when every expected source of that workspace read cleanly, otherwise it is "at least n" or unknown; Followup's Review Needed rows are their own kind and are never counted.
import re

from lifeos.inbox.model import FRESH_FOR, PRESENTATION, SOURCES, UNRELEASED_WORKSPACES, WORKSPACES, InboxError
from lifeos.platform import ct_stamp

BUCKETS = {"needs_me": "Needs me", "waiting": "Waiting", "approvals": "Approvals", "upcoming": "Upcoming"}
REVIEW_BUCKET = "Review needed"
EXPECTED = (("jira", "personal"), ("jira", "bytalos"), ("calendar", "personal"), ("calendar", "bytalos"), ("todoist", "personal"), ("asana", "bytalos"),
            ("concord", "personal"), ("concord", "bytalos"), ("followup", "personal"), ("followup", "bytalos"))          # the six sources as released: Todoist Personal only, Asana Bytalos only
NOT_PROVISIONED = frozenset({"live_reads_not_authorized", "live_reader_missing", "followup_projection_missing", "not_provisioned"})
_RANK = {"ok": 0, "degraded": 1, "unavailable": 2, "no_grant": 2, "refused": 2}
_CHECKPOINT = {"source-backed": "Source-backed", "suggested": "Suggested", "jim_override": "Jim override"}
_INDICATOR = re.compile(r"checkpoint (source-backed|suggested|jim_override)")
_ACCEPTED = re.compile(r"DEGRADED, last accepted (.+)$")


def _pair(source, workspace):
    return f"{source}:{workspace}"


def health_label(state, reason, last_ok=""):
    """The words the view shows for one source: OK, DEGRADED with the last accepted time, or Unavailable (not provisioned, or the fixed reason word)."""
    if state == "ok":
        return "OK"
    if state == "degraded":
        return "DEGRADED, last accepted " + (last_ok or "never")                # last_ok is already a ct_stamp.when text
    return "Unavailable: not provisioned" if reason in NOT_PROVISIONED else f"Unavailable: {reason}"


def _row_accepted(record):
    """Followup threads carry '; DEGRADED, last accepted <CT>' in their notes when the stage's payload was degraded: the matching text, or None."""
    found = _ACCEPTED.search(record.description or "")
    return found.group(1) if found else None


def _checkpoint(record):
    found = _INDICATOR.search(record.description or "")
    return _CHECKPOINT[found.group(1)] if found else ""


def _item(record, now, health_text):
    if record.workspace in UNRELEASED_WORKSPACES:
        raise InboxError("workspace_not_released")
    if record.workspace not in WORKSPACES or record.source not in SOURCES or record.presentation not in PRESENTATION:
        raise InboxError("record_not_presentable")
    followup = record.source == "followup"
    return {
        "Record ID": record.record_id, "Kind": "item", "Title": record.title, "Workspace": record.label, "Source": record.source.title(), "Bucket": BUCKETS[record.presentation],
        "Source status": record.source_status, "Due": record.due.isoformat() if record.due else "", "Link": record.url,
        "Owner cue": ("Them" if record.source_status == "waiting" else "You") if followup else "", "Checkpoint": _checkpoint(record) if followup else "",
        "Next move": "", "Freshness": "stale" if now - record.retrieved_at > FRESH_FOR else "fresh", "Source updated": record.source_updated_at.isoformat(),
        "Source health": health_text, "Counted": True,
    }


def _review_row(workspace, account, item):
    if workspace in UNRELEASED_WORKSPACES:
        raise InboxError("workspace_not_released")
    if workspace not in WORKSPACES:
        raise InboxError("workspace_unknown")
    notes = item.get("notes", "")
    found = _INDICATOR.search(notes)
    return {
        "Record ID": f"review:followup:{workspace}:{account}:{item['id']}", "Kind": "review", "Title": item.get("title", ""), "Workspace": workspace.title(), "Source": "Followup",
        "Bucket": REVIEW_BUCKET, "Source status": "review_needed", "Due": item.get("next_checkpoint") or "", "Link": item["url"], "Owner cue": "", "Next move": "",
        "Checkpoint": _CHECKPOINT[found.group(1)] if found else "Suggested", "Freshness": "", "Source updated": item.get("updated", ""), "Source health": "", "Counted": False,
    }


def project_rows(inbox, now, last_ok=None, review=(), expected=EXPECTED):
    """-> a tuple of row dicts sorted by Record ID. `last_ok`: {(source, workspace): CT text} from the previous run, shown on a source that is not OK now. `review`: (workspace, account, review dict) for Followup's
    separate Review Needed list. Every expected source with no health entry at all is shown as Unavailable: not provisioned."""
    last_ok = last_ok or {}
    per = {}                                                                        # (source, workspace) -> worst state, reason, newest retrieval
    for h in inbox.health:
        key = (h.source, h.workspace)
        worst = per.get(key)
        if worst is None or _RANK[h.state] > _RANK[worst[0]]:
            per[key] = (h.state, h.reason, worst[2] if worst else None)
    newest = {}
    degraded_note = {}
    for r in inbox.records:
        key = (r.source, r.workspace)
        newest[key] = max(newest.get(key, r.retrieved_at), r.retrieved_at)
        accepted = _row_accepted(r)
        if accepted:
            degraded_note[key] = accepted
    for key in tuple(degraded_note):                                                # a degraded Followup payload degrades its source even though the read itself succeeded
        state, reason, _ = per.get(key, ("ok", "ok", None))
        per[key] = ("degraded", "evidence_degraded", None) if state == "ok" else (state, reason, None)
    for key in expected:
        per.setdefault(key, ("unavailable", "not_provisioned", None))
    rows = []
    for (source, workspace), (state, reason, _) in sorted(per.items()):
        if workspace in UNRELEASED_WORKSPACES or workspace not in WORKSPACES:
            continue
        accepted = degraded_note.get((source, workspace)) or last_ok.get((source, workspace), "")
        text = health_label(state, reason, accepted)
        stamp = ct_stamp.when(newest.get((source, workspace), now)) if state == "ok" else (accepted or "never")
        rows.append({"Record ID": f"health:{source}:{workspace}", "Kind": "health", "Title": f"{source.title()} ({workspace.title()})", "Workspace": workspace.title(), "Source": source.title(),
                     "Bucket": "", "Source status": "", "Due": "", "Link": "", "Owner cue": "", "Next move": "", "Checkpoint": "", "Freshness": "", "Source updated": "",
                     "Source health": text, "Health state": state, "Last refreshed": stamp, "Last OK": stamp if state == "ok" else accepted, "Counted": False})
    health_text = {key: health_label(state, reason, degraded_note.get(key) or last_ok.get(key, "")) for key, (state, reason, _) in per.items()}
    for r in inbox.records:
        rows.append(_item(r, now, health_text.get((r.source, r.workspace), "")))
    for workspace, account, item in review:
        rows.append(_review_row(workspace, account, item))
    return tuple(sorted(rows, key=lambda row: row["Record ID"]))


def summary(rows, expected=EXPECTED):
    """{workspace: {bucket: {'count': n or None, 'complete': bool}}}. A count is complete only when every expected source of that workspace is OK; an incomplete zero is None (unknown), never 0."""
    state = {r["Record ID"]: r.get("Health state") for r in rows if r["Kind"] == "health"}
    out = {}
    for workspace in WORKSPACES:
        complete = all(state.get(f"health:{source}:{workspace}") == "ok" for source, ws in expected if ws == workspace)
        out[workspace] = {}
        for bucket in (*BUCKETS.values(),):
            n = sum(1 for r in rows if r["Kind"] == "item" and r["Workspace"] == workspace.title() and r["Bucket"] == bucket and r["Counted"])
            out[workspace][bucket] = {"count": n if (n or complete) else None, "complete": complete}
    return out


def last_ok_from(current_rows):
    """The previous run's last-OK text per (source, workspace), read from the health rows already in Notion."""
    out = {}
    for row in current_rows:
        if row.get("Kind") == "health" and row.get("Last OK"):
            _, source, workspace = row["Record ID"].split(":")
            out[(source, workspace)] = row["Last OK"]
    return out


__all__ = ["BUCKETS", "EXPECTED", "health_label", "last_ok_from", "project_rows", "summary"]
