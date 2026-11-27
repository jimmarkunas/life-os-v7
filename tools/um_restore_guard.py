"""Proposed restore guard for Honcho memory (offline design, fabricated data only; NOT wired to any restore, purge or
service). Stdlib only, no network.

The rule it encodes (a proposal, not a guarantee):
1. Inventory: from dump metadata (stamp) and revoke events (stored and revoked times), say which dumps may still hold
   the text of a revoked memory. Metadata only; no dump is opened.
2. Before a restored copy serves recall, the LIVE tombstone export is re-applied to it: every id revoked or superseded
   live is deleted from the copy, even when the copy predates the revoke and holds no tombstone of its own.
3. Lineage: everything derived from a deleted id is deleted too, transitively (message -> conclusion through
   conclusion_source_messages, source -> derived through document_sources, dream "created" changes through their
   source_ids); the text of dream "removed" changes that touch deleted lineage is scrubbed; a peer card edited by a dream
   run that touched deleted lineage is quarantined (a card has no per-line source).
4. Fail closed: no live tombstone export, an incomplete export, or a derived document with no lineage record in a
   workspace that has deletions -> that workspace is quarantined (it serves no recall) instead of being trusted.

Snapshot shape (lists of dicts, as read from the restored copy):
  documents: {id, workspace, level ("explicit"|"deductive"|"inductive"), envelope: {kind, of?}}
  conclusion_source_messages: {conclusion_id, message_id}
  document_sources: {derived_id, source_id}
  dream_run_changes: {id, run_id, workspace, change ("created"|"removed"), document_id, source_ids: [...], has_text}
  peer_cards: {peer, workspace, run_ids: [...]}
Live export: {"complete": bool, "tombstones": {workspace: [revoked or superseded ids]}} or None."""

DERIVED = ("deductive", "inductive")


def straddling(dumps, events):
    """dumps: [{stamp: epoch}], events: [{stored: epoch, revoked: epoch}] -> {stamp: n events whose text the dump may
    hold} (a dump taken after a store and before its revoke can hold the revoked text)."""
    return {d["stamp"]: sum(1 for e in events if e["stored"] <= d["stamp"] < e["revoked"]) for d in dumps}


def _descendants(start, snapshot):
    """Every id derived from `start`, transitively; cycles end because each id is visited once."""
    edges = {}
    for row in snapshot.get("conclusion_source_messages", []):
        edges.setdefault(row["message_id"], set()).add(row["conclusion_id"])
    for row in snapshot.get("document_sources", []):
        edges.setdefault(row["source_id"], set()).add(row["derived_id"])
    for row in snapshot.get("dream_run_changes", []):
        if row.get("change") == "created" and row.get("document_id"):
            for source in row.get("source_ids") or []:
                edges.setdefault(source, set()).add(row["document_id"])
    seen, todo = set(start), list(start)
    while todo:
        for child in edges.get(todo.pop(), ()):
            if child not in seen:
                seen.add(child)
                todo.append(child)
    return seen


def _has_lineage(doc_id, snapshot):
    return (
        any(r["conclusion_id"] == doc_id for r in snapshot.get("conclusion_source_messages", []))
        or any(r["derived_id"] == doc_id for r in snapshot.get("document_sources", []))
        or any(
            r.get("change") == "created" and r.get("document_id") == doc_id and r.get("source_ids")
            for r in snapshot.get("dream_run_changes", [])
        )
    )


def plan(snapshot, live_export):
    """-> {"delete_documents", "scrub_dream_changes", "quarantine_peer_cards", "quarantine_workspaces", "reasons",
    "servable_workspaces"}: what must happen to the restored copy before any of it serves recall."""
    docs = snapshot.get("documents", [])
    workspaces = sorted({d["workspace"] for d in docs})
    out = {
        "delete_documents": [],
        "scrub_dream_changes": [],
        "quarantine_peer_cards": [],
        "quarantine_workspaces": [],
        "reasons": {},
        "servable_workspaces": [],
    }
    if not isinstance(live_export, dict) or live_export.get("complete") is not True:
        out["quarantine_workspaces"] = workspaces
        out["reasons"] = {ws: "tombstone_export_missing" for ws in workspaces}
        return out
    live = live_export.get("tombstones") or {}
    by_ws = {ws: [d for d in docs if d["workspace"] == ws] for ws in workspaces}
    deleted = set()
    for ws, rows in by_ws.items():
        dead = set(live.get(ws, []))
        dead |= {
            (d.get("envelope") or {}).get("of")
            for d in rows
            if (d.get("envelope") or {}).get("kind") == "tombstone" and (d.get("envelope") or {}).get("of")
        }
        ids = {d["id"] for d in rows}
        doomed = {i for i in _descendants(dead, snapshot) if i in ids or i in dead}
        tombstone_ids = {d["id"] for d in rows if (d.get("envelope") or {}).get("kind") == "tombstone"}
        doomed -= tombstone_ids  # the audit record of a revoke stays; only the revoked text and what it fed go
        if dead and any(
            d["level"] in DERIVED and not _has_lineage(d["id"], snapshot) for d in rows if d["id"] not in doomed
        ):
            out["quarantine_workspaces"].append(ws)
            out["reasons"][ws] = "lineage_missing"
        deleted |= {i for i in doomed if i in ids}
    out["delete_documents"] = sorted(deleted)
    touched_runs = set()
    for change in snapshot.get("dream_run_changes", []):
        hit = change.get("document_id") in deleted or set(change.get("source_ids") or []) & deleted
        if hit:
            touched_runs.add(change.get("run_id"))
            if change.get("change") == "removed" and change.get("has_text"):
                out["scrub_dream_changes"].append(change["id"])
    out["scrub_dream_changes"].sort()
    out["quarantine_peer_cards"] = sorted(
        (card["workspace"], card["peer"])
        for card in snapshot.get("peer_cards", [])
        if set(card.get("run_ids") or []) & touched_runs
    )
    out["servable_workspaces"] = [ws for ws in workspaces if ws not in out["quarantine_workspaces"]]
    return out


def servable(snapshot_after, live_export):
    """-> True only if, after the plan was applied, no live-revoked id or its lineage is left in a workspace that serves
    recall (the check run last, before the restored copy is put in front of any client)."""
    again = plan(snapshot_after, live_export)
    return not again["delete_documents"] and not again["scrub_dream_changes"] and not again["quarantine_workspaces"]
