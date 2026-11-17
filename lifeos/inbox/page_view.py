"""LIFE-711: the Action Inbox page text, a pure function of the stored Followup payloads. No Notion call, no store, no clock of its own."""
# The page is Jim's approved status dashboard with the list under it (QA9 look): header, five tiles, a source health table (one row per account and source), the list (an inline Notion database, rows owned
# by page_rows.py) and the Followup section. This module decides every word the live page shows; page_writer.py puts those words into the blocks that already exist.
#
# The six sources are Jira, Calendar, Todoist, Asana (view only), Concord and Followup, never accounts or credentials. Honesty rules: a count is a number only when every connected source of both workspaces
# read OK this run, otherwise a dash; a source that failed shows DEGRADED with its last accepted time, never an ungrounded zero; Asana and Concord show "not connected" until they are; Review Needed
# suggestions are never counted; TGP and any unknown workspace are never a row; Approvals is a dash until an approvals source is wired.
from datetime import datetime

from lifeos.inbox import page_items
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


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def due_text(text):
    """A due moment as the page shows it: `Nov 20 · 9:00 AM CT` (Central, 12-hour); text that is not a time is shown as given, empty stays empty."""
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError:
        return str(text or "")
    moment = moment.replace(tzinfo=ct_stamp.CENTRAL) if moment.tzinfo is None else moment.astimezone(ct_stamp.CENTRAL)
    return f"{MONTHS[moment.month - 1]} {moment.day} · {moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'} CT"


SYNTHETIC_HOSTS = (".test", "example.com", "example.org", "example.net", "localhost")


def is_synthetic(payload):
    """A fixture or test payload: it says so, or a link points at a reserved example host. Production never shows one (the writer refuses the whole run)."""
    if payload.get("synthetic") or (payload.get("provenance") or {}).get("synthetic"):
        return True
    links = [str(t.get("url") or "") for t in payload.get("threads") or ()] + [str(r.get("url") or "") for r in payload.get("review") or ()]
    return any(host in link.split("/")[2].lower() for link in links if "://" in link for host in SYNTHETIC_HOSTS) if links else False


def _status(entry):
    if entry.get("status") == "ok":
        return "ok"
    if entry.get("status") == "degraded":
        return "DEGRADED, last accepted " + (_ct(entry["last_accepted"]) if entry.get("last_accepted") else "never")
    return f"UNAVAILABLE ({entry.get('reason') or 'not read'})"


def _allowed(payloads):
    return [p for p in payloads if p.get("workspace") in WORKSPACES and p.get("workspace") not in UNRELEASED_WORKSPACES and "tgp" not in str(p.get("account", "")).lower()]


def six_sources(payloads, entries=()):
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
    for entry in entries:                                                                 # the list's own item reads: the same worst-state rule
        name = READ_BY.get(entry.get("source"))
        if name:
            text = _status(entry)
            if name not in worst or (worst[name] == "ok" and text != "ok"):
                worst[name] = text
    return [(name, NOT_CONNECTED[name] if name in NOT_CONNECTED else worst.get(name, "not read yet")) for name in SIX]


FIXED_HEALTH = (("Personal", "Jira", "jira"), ("Personal", "Calendar", "calendar"), ("Personal", "Gmail", "gmail"), ("Personal", "Notion", "notion"), ("Personal", "Interview OS", "interview_os"),
                ("Personal", "Todoist", "todoist"), ("Personal", "Outlook (personal)", "outlook:personal"), ("Personal", "Outlook (work)", "outlook:work"), ("Bytalos", "Jira", "jira"),
                ("Bytalos", "Calendar", "calendar"), ("Bytalos", "Gmail", "gmail"))
NOT_CONNECTED_ROWS = (("All", "Concord"), ("Bytalos", "Asana (view only)"), ("Personal", "School Outlook (admin approval required)"), ("TGP", "Everything (tracked, by design)"))


def _light(entry):
    """The health select option for one source read."""
    if entry.get("status") == "ok":
        return "🟢 ok"
    return "🟡 degraded" if entry.get("status") == "degraded" else "🔴 unavailable"


def _key(entry, account):
    source = entry.get("source")
    return f"outlook:{str(account).split()[0].lower()}" if source == "outlook" else source


def health_rows(payloads, loops, entries=(), counts=None, stamp="—"):
    """-> one row dict per account and source Jim named (the System health database): Workspace, Source, Health (a fixed select option), Last OK, Items. A source with no payload is not read yet; never a guess."""
    seen = {}
    for payload in payloads:
        for entry in payload.get("sources") or ():
            key = (payload["workspace"].title(), _key(entry, payload.get("account")))
            light = _light(entry)
            ok_at = _ct(payload.get("generated_at")) if entry.get("status") == "ok" else (_ct(entry["last_accepted"]) if entry.get("last_accepted") else "—")
            if key not in seen or (seen[key][0] == "🟢 ok" and light != "🟢 ok"):
                seen[key] = (light, ok_at)
    read = {}
    for entry in entries:                                                                 # the list's own item reads for Jira, Calendar and Todoist: the same worst-state rule, and they carry the item count
        key = (entry["workspace"].title(), entry["source"])
        light = _light(entry)
        read[key] = str((counts or {}).get((entry["workspace"], entry["source"]), 0)) if entry.get("status") == "ok" else "—"
        old = seen.get(key)
        if old is None or old[0] == "🟢 ok":
            seen[key] = (light, stamp if light == "🟢 ok" else (old[1] if old else "—"))
    rows = []
    for workspace, label, key in FIXED_HEALTH:
        light, ok_at = seen.get((workspace, key), ("⚪ not read yet", "—"))
        items = read.get((workspace, key), "—")
        rows.append({"id": f"health:{workspace.lower()}:{key}", "Source": label, "Workspace": workspace, "Health": light, "Last OK": ok_at, "Items": items})
    ok = [p for p in payloads if p.get("state") == "PASS"]
    follow = ("⚪ not read yet", "—") if not payloads else (("🟢 ok", _ct(max(p.get("generated_at", "") for p in ok))) if len(ok) == len(payloads) else ("🟡 degraded", "—"))
    rows.append({"id": "health:all:followup", "Source": "Followup", "Workspace": "All", "Health": follow[0], "Last OK": follow[1], "Items": str(len(loops)) if payloads else "—"})
    rows += [{"id": f"health:{w.lower()}:{name.split(' (')[0].lower().replace(' ', '_')}", "Source": name, "Workspace": w, "Health": "⚪ not connected", "Last OK": "—", "Items": "—"} for w, name in NOT_CONNECTED_ROWS]
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
            iso = due
            rows.append({"id": f"followup:{payload['workspace']}:{str(payload.get('account'))[:40]}:{thread.get('id')}", "due": due_text(iso), "iso": iso, "link": str(thread.get("url") or ""), "workspace": payload["workspace"].title(), "bucket": bucket, "title": str(thread.get("title") or "")[:200], "owner": owner,
                         "move": f"Check back {due_text(iso)}" if iso else "Open the source record", "checkpoint": _checkpoint(thread), "fresh": stamp})
    return sorted(rows, key=lambda r: (RANK[r["bucket"]], r["iso"] or "9", r["workspace"], r["title"]))


def _rank(bucket, iso):
    """The sort number of a Followup row: its bucket, then its checkpoint (same scale as the item rows); no checkpoint sorts last in its bucket."""
    try:
        when = datetime.fromisoformat(str(iso)) if iso else None
    except ValueError:
        when = None
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=ct_stamp.CENTRAL)
    return page_items.rank(bucket, when)


def render(payloads, now, items=None):
    """-> {'refreshed', 'tiles': [number or a dash] x4, 'healthy': 'n of 6', 'health': 16 System health rows, 'rows': [list rows], 'sources': [(name, text)], 'state': None | (title, text)}.
    `items`: page_items.collect's answer (the real Jira, Calendar and Todoist items), or None while those are not read: then the list is the Followup rows and Approvals stays a dash."""
    payloads = _allowed(payloads)
    entries = [e for e in (items or {}).get("sources", ()) if e.get("workspace") in WORKSPACES]
    sources = six_sources(payloads, entries)
    seen = {p["workspace"] for p in payloads}
    connected = [(n, t) for n, t in sources if n not in NOT_CONNECTED]
    complete = bool(payloads) and seen == set(WORKSPACES) and all(t == "ok" for _, t in connected)
    loops = _loops(payloads, now)
    records = list((items or {}).get("records", ()))
    item_rows = [page_items.row_of(r) for r in records]
    bucket_count = {b: sum(1 for r in loops if r["bucket"] == b) + sum(1 for r in item_rows if r["Bucket"] == b) for b in TILES}
    tiles = []
    for bucket in TILES:
        if not complete or (bucket == "Approvals" and not any(e["source"] == "jira" for e in entries)):
            tiles.append("—")                                                         # no approvals source read, or a source was not read: a dash, never a zero
        else:
            tiles.append(str(bucket_count[bucket]))
    healthy = sum(1 for _, t in sources if t == "ok")
    counts = {}
    for record in records:
        counts[(record.workspace, record.source)] = counts.get((record.workspace, record.source), 0) + 1
    main = [{"id": r["id"], "Outcome": r["title"], "Workspace": r["workspace"], "Source": "Followup", "Bucket": r["bucket"], "Rank": _rank(r["bucket"], r["iso"]), "Owner": r["owner"], "Next move": r["move"],
             "Due": r["due"], "Freshness": "fresh", "Link": r["link"]} for r in loops] + item_rows
    state = None if not payloads else EMPTY if complete and not main else CANNOT if not complete else None
    bad = {e["workspace"] for e in entries if e.get("status") != "ok"}                    # a workspace whose item read failed keeps its item rows: only a clean read may archive
    clean = sorted(p["workspace"] for p in payloads if p.get("state") == "PASS" and p["workspace"] not in bad and all(e.get("status") == "ok" for e in p.get("sources") or ()))
    return {"clean": clean, "refreshed": f"Last refreshed: {ct_stamp.when(now) if payloads else '—'}",
            "tiles": tiles, "healthy": f"{healthy} of 6", "health": health_rows(payloads, loops, entries, counts, _ct(now.isoformat())), "rows": main, "sources": sources, "state": state}


def source_pieces(view):
    """The Source health line as (text, bold, gray) pieces: `**Jira** ok · **Calendar** ok · ...`."""
    out = []
    for i, (name, text) in enumerate(view["sources"]):
        out += [(("" if i == 0 else " · "), False, False), (name, True, False), (" " + text, False, False)]
    return out


__all__ = ["CANNOT", "EMPTY", "NOT_CONNECTED", "RANK", "SIX", "TILES", "due_text", "health_rows", "is_synthetic", "render", "six_sources", "source_pieces"]
