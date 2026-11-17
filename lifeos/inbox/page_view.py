"""LIFE-711: the Action Inbox page text, a pure function of the stored Followup payloads. No Notion call, no store, no clock of its own."""
# The page is Jim's approved QA9 mockup (a full-width page of callouts, columns and plain tables, no database). This module decides every word the live page shows: the header line, five tiles, the main
# table, the six-source health line, the Followup table and the Review Needed line. page_writer.py puts these words into the blocks that already exist on the page.
#
# The six sources are Jira, Calendar, Todoist, Asana (view only), Concord and Followup, never accounts or credentials. Honesty rules: a count is a number only when every connected source of both workspaces
# read OK this run, otherwise a dash; a source that failed shows DEGRADED with its last accepted time, never an ungrounded zero; Asana and Concord show "not connected" until they are; Review Needed
# suggestions are never counted; TGP and any unknown workspace are never a row; Approvals is a dash until an approvals source is wired.
from datetime import datetime

from lifeos.inbox.model import UNRELEASED_WORKSPACES, WORKSPACES
from lifeos.platform import ct_stamp

SIX = ("Jira", "Calendar", "Todoist", "Asana", "Concord", "Followup")
NOT_CONNECTED = {"Asana": "not connected (view only)", "Concord": "not connected"}
READ_BY = {"jira": "Jira", "calendar": "Calendar", "todoist": "Todoist"}              # the payload's source readers that stand for an Inbox source; Gmail, Outlook, Notion are inputs, not page elements
BUCKET_OF = {"needs_action_today": "Needs me", "waiting_aging": "Waiting", "upcoming": "Upcoming"}
TILES = ("Needs me", "Waiting", "Approvals", "Upcoming")
EMPTY = ("Truly empty", "Nothing needs you right now. Every connected source was read and returned nothing.")
CANNOT = ("Cannot say", "Unavailable, not zero. At least one source could not be read, so no list or count is shown for it.")


def _ct(text):
    try:
        return ct_stamp.when(datetime.fromisoformat(str(text)))
    except ValueError:
        return str(text)


def _status(entry):
    if entry.get("status") == "ok":
        return "ok"
    if entry.get("status") == "degraded":
        return "DEGRADED, last accepted " + (_ct(entry["last_accepted"]) if entry.get("last_accepted") else "never")
    return f"UNAVAILABLE ({entry.get('reason') or 'not read'})"


def _allowed(payloads):
    return [p for p in payloads if p.get("workspace") in WORKSPACES and p.get("workspace") not in UNRELEASED_WORKSPACES and "tgp" not in str(p.get("account", "")).lower()]


def six_sources(payloads):
    """-> [(name, text)] in the fixed order: each source's worst state across scopes; Followup is DEGRADED when any payload is; Asana and Concord are not connected; none read yet says so."""
    worst = {}
    for payload in payloads:
        for entry in payload.get("sources") or ():
            name = READ_BY.get(entry.get("source"))
            if name:
                text = _status(entry)
                if name not in worst or (worst[name] == "ok" and text != "ok"):
                    worst[name] = text
        mine = "ok" if payload.get("state") == "PASS" else "DEGRADED, last accepted " + (_ct(payload["last_accepted"]) if payload.get("last_accepted") else "never")
        if "Followup" not in worst or (worst["Followup"] == "ok" and mine != "ok"):
            worst["Followup"] = mine
    return [(name, NOT_CONNECTED[name] if name in NOT_CONNECTED else worst.get(name, "not read yet")) for name in SIX]


def _checkpoint(thread):
    text = str(thread.get("notes") or "")
    for key, label in (("jim_override", "Jim override"), ("source-backed", "Source-backed"), ("suggested", "Suggested")):
        if f"checkpoint {key}" in text:
            return label
    return "Suggested"


def _loops(payloads, now):
    rows = []
    for payload in payloads:
        stamp = _ct(payload.get("generated_at") or now.isoformat())
        for thread in payload.get("threads") or ():
            bucket = BUCKET_OF.get(thread.get("state"))
            if bucket is None:
                continue
            owner = "Them" if bucket == "Waiting" or thread.get("owner") in ("external", "them") else "You"
            due = thread.get("next_checkpoint") or ""
            rows.append({"workspace": payload["workspace"].title(), "bucket": bucket, "title": str(thread.get("title") or "")[:200], "owner": owner,
                         "move": ("Check back " + _ct(due)) if due else "Open the source record", "checkpoint": _checkpoint(thread) + (f" · {_ct(due)}" if due else ""), "fresh": stamp})
    order = {b: i for i, b in enumerate(TILES)}
    return sorted(rows, key=lambda r: (r["workspace"], order[r["bucket"]], r["title"]))


def render(payloads, now):
    """-> {'refreshed', 'tiles': [(number, caption)] x4, 'healthy': (n of 6, caption), 'main': [7-tuples], 'followup': [6-tuples], 'sources': [(name, text)], 'review': text, 'state': None | (title, text)}."""
    payloads = _allowed(payloads)
    sources = six_sources(payloads)
    seen = {p["workspace"] for p in payloads}
    connected = [(n, t) for n, t in sources if n not in NOT_CONNECTED]
    complete = bool(payloads) and seen == set(WORKSPACES) and all(t == "ok" for _, t in connected)
    loops = _loops(payloads, now)
    reviews = sum(len(p.get("review") or ()) for p in payloads)
    tiles = []
    for bucket in TILES:
        if bucket == "Approvals":
            tiles.append(("—", "Not wired yet"))
        elif complete:
            tiles.append((str(sum(1 for r in loops if r["bucket"] == bucket)), "Followup loops only"))
        else:
            tiles.append(("—", "Cannot say: a source is down" if payloads else "Not read yet"))
    healthy = sum(1 for _, t in sources if t == "ok")
    down = [n for n, t in connected if t != "ok"]
    caption = "Asana, Concord not connected" + (f" · down: {', '.join(down)}" if down else "")
    main = [(r["workspace"], "Followup", r["bucket"], r["title"], r["owner"], r["move"], r["fresh"]) for r in loops] or [("—", "—", "—", "No rows" if complete else "Cannot say: not every source was read", "—", "—", "—")]
    followup = [(r["workspace"], r["title"], r["owner"], r["move"], r["checkpoint"], r["fresh"]) for r in loops] or [("—", "No Followup loops" if complete else "Cannot say: not every source was read", "—", "—", "—", "—")]
    state = None if not payloads else EMPTY if complete and not loops else CANNOT if not complete else None
    return {"refreshed": f"Last refreshed: {ct_stamp.when(now)} · TGP is never shown" if payloads else "Last refreshed: waiting for the first live read · TGP is never shown",
            "tiles": tiles, "healthy": (f"{healthy} of 6", caption), "main": main, "followup": followup, "sources": sources,
            "review": f"Review Needed (not counted): {reviews}" if payloads else "Review Needed (not counted): —", "state": state}


def source_pieces(view):
    """The Source health line as (text, bold, gray) pieces: `**Jira** ok · **Calendar** ok · ...`."""
    out = []
    for i, (name, text) in enumerate(view["sources"]):
        out += [(("" if i == 0 else " · "), False, False), (name, True, False), (" " + text, False, False)]
    return out


__all__ = ["CANNOT", "EMPTY", "NOT_CONNECTED", "SIX", "TILES", "render", "six_sources", "source_pieces"]
