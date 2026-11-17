"""LIFE-711: the Action Inbox page text, a pure function of the stored Followup payloads. No Notion call, no store, no clock of its own."""
# The page is Jim's approved status dashboard with the list under it (QA9 look): header, five tiles, a source health table (one row per account and source), the list (an inline Notion database, rows owned
# by page_rows.py) and the Followup section. This module decides every word the live page shows; page_writer.py puts those words into the blocks that already exist.
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
RANK = {"Needs me": 1, "Approvals": 2, "Waiting": 3, "Upcoming": 4}                    # most urgent first; the page sorts by this number, then by the earliest due
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


FIXED_HEALTH = (("Personal", "Jira", "jira"), ("Personal", "Calendar", "calendar"), ("Personal", "Gmail", "gmail"), ("Personal", "Notion", "notion"), ("Personal", "Interview OS", "interview_os"),
                ("Personal", "Todoist", "todoist"), ("Personal", "Outlook (personal)", "outlook:personal"), ("Personal", "Outlook (work)", "outlook:work"), ("Bytalos", "Jira", "jira"),
                ("Bytalos", "Calendar", "calendar"), ("Bytalos", "Gmail", "gmail"))
NOT_CONNECTED_ROWS = (("All", "Concord", "⚪ not connected"), ("Bytalos", "Asana (view only)", "⚪ not connected"), ("Personal", "School Outlook", "⚪ not connected (admin approval required)"),
                      ("TGP", "Everything", "⚪ tracked, not connected (by design)"))
LIGHT = {"ok": "🟢 ok"}


def _light(entry):
    if entry.get("status") == "ok":
        return "🟢 ok"
    if entry.get("status") == "degraded":
        return "🟡 DEGRADED (stale)"
    return f"🔴 UNAVAILABLE ({entry.get('reason') or 'not read'})"


def _key(entry, account):
    source = entry.get("source")
    return f"outlook:{str(account).split()[0].lower()}" if source == "outlook" else source


def health_table(payloads, loops):
    """-> 16 fixed rows (workspace, source/account, health, last OK, items): one per account and source Jim named, a traffic-light word each, never a guess; sources with no payload say not read yet."""
    seen = {}
    for payload in payloads:
        for entry in payload.get("sources") or ():
            key = (payload["workspace"].title(), _key(entry, payload.get("account")))
            text = _light(entry)
            ok_at = _ct(payload.get("generated_at")) if entry.get("status") == "ok" else (_ct(entry["last_accepted"]) if entry.get("last_accepted") else "—")
            if key not in seen or (seen[key][0].startswith("🟢") and not text.startswith("🟢")):
                seen[key] = (text, ok_at)
    rows = []
    for workspace, label, key in FIXED_HEALTH:
        text, ok_at = seen.get((workspace, key), ("⚪ not read yet", "—"))
        rows.append((workspace, label, text, ok_at, "—"))
    state = [(p, "ok" if p.get("state") == "PASS" else "degraded") for p in payloads]
    follow = ("⚪ not read yet", "—") if not payloads else (("🟢 ok", _ct(max(p.get("generated_at", "") for p in payloads))) if all(s == "ok" for _, s in state)
                                                          else ("🟡 DEGRADED (stale)", "—"))
    rows.append(("All", "Followup", follow[0], follow[1], str(len(loops)) if payloads else "—"))
    rows += [(w, name, text, "—", "—") for w, name, text in NOT_CONNECTED_ROWS]
    return rows


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
            rows.append({"id": f"followup:{payload['workspace']}:{str(payload.get('account'))[:40]}:{thread.get('id')}", "due": due[:25], "link": str(thread.get("url") or ""), "workspace": payload["workspace"].title(), "bucket": bucket, "title": str(thread.get("title") or "")[:200], "owner": owner,
                         "move": ("Check back " + _ct(due)) if due else "Open the source record", "checkpoint": _checkpoint(thread) + (f" · {_ct(due)}" if due else ""), "fresh": stamp})
    return sorted(rows, key=lambda r: (RANK[r["bucket"]], r["due"] or "9", r["workspace"], r["title"]))


def render(payloads, now):
    """-> {'refreshed', 'tiles': [number or a dash] x4, 'healthy': 'n of 6', 'health': 16 rows, 'rows': [list rows], 'followup': [6-tuples], 'sources': [(name, text)], 'review': text, 'state': None | (title, text)}."""
    payloads = _allowed(payloads)
    sources = six_sources(payloads)
    seen = {p["workspace"] for p in payloads}
    connected = [(n, t) for n, t in sources if n not in NOT_CONNECTED]
    complete = bool(payloads) and seen == set(WORKSPACES) and all(t == "ok" for _, t in connected)
    loops = _loops(payloads, now)
    reviews = sum(len(p.get("review") or ()) for p in payloads)
    tiles = []
    for bucket in TILES:
        if bucket == "Approvals" or not complete:
            tiles.append("—")                                                         # no approvals source yet, or a source was not read: a dash, never a zero
        else:
            tiles.append(str(sum(1 for r in loops if r["bucket"] == bucket)))
    healthy = sum(1 for _, t in sources if t == "ok")
    main = [{"id": r["id"], "Outcome": r["title"], "Workspace": r["workspace"], "Source": "Followup", "Bucket": r["bucket"], "Rank": RANK[r["bucket"]], "Owner": r["owner"], "Next move": r["move"],
             "Due": r["due"], "Freshness": "fresh", "Link": r["link"]} for r in loops]
    followup = [(r["workspace"], r["title"], r["owner"], r["move"], r["checkpoint"], r["fresh"]) for r in loops] or [("—", "No Followup loops" if complete else "Cannot say: not every source was read", "—", "—", "—", "—")]
    state = None if not payloads else EMPTY if complete and not loops else CANNOT if not complete else None
    clean = sorted(p["workspace"] for p in payloads if p.get("state") == "PASS" and all(e.get("status") == "ok" for e in p.get("sources") or ()))
    return {"clean": clean, "refreshed": f"Last refreshed: {ct_stamp.when(now) if payloads else '—'}",
            "tiles": tiles, "healthy": f"{healthy} of 6", "health": health_table(payloads, loops), "rows": main, "followup": followup, "sources": sources,
            "review": f"Review Needed (not counted): {reviews}" if payloads else "Review Needed (not counted): —", "state": state}


def source_pieces(view):
    """The Source health line as (text, bold, gray) pieces: `**Jira** ok · **Calendar** ok · ...`."""
    out = []
    for i, (name, text) in enumerate(view["sources"]):
        out += [(("" if i == 0 else " · "), False, False), (name, True, False), (" " + text, False, False)]
    return out


__all__ = ["CANNOT", "EMPTY", "NOT_CONNECTED", "RANK", "SIX", "TILES", "health_table", "render", "six_sources", "source_pieces"]
