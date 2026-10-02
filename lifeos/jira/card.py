"""The Jira card in the Daily Report (Phase B). V7 owns exactly one Notion block - the "JIRA Execution" callout named by
JIRA_CARD_BLOCK_ID - and nothing else on the page. Content comes only from the private database snapshot (never live Jira, so a
report refresh cannot be broken by Jira being down) and is rendered with V1's buckets: Overdue / Blocked, Today (capacity 3),
This Week, Done. A snapshot older than STALE_HOURS is never shown as current: only the status line changes, to STALE.
Output is counts and fixed codes only."""
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from lifeos.platform.jira import JiraError
from lifeos.platform.notion_client import Client, NotionError, rich_text
from .boards import boards
from . import store

LOCAL_TZ = "America/Chicago"
STALE_HOURS = 3
TODAY_CAPACITY = 3
WEEK_OPEN_LIMIT = 5                       # V1: up to 5 This Week rows stay expanded, more go behind a toggle
CARD_TITLE = "JIRA Execution"
STATUS_PREFIX = "V7 · "
HIGH = {"high", "highest"}


class CardError(JiraError):
    """Fixed codes only."""


def _day(value):
    return datetime.fromisoformat(value).date() if value else None


def _blocked(issue):
    return issue["status"].lower() == "blocked"


def buckets(snap, today):
    """V1's precedence (Overdue/Blocked -> Today -> This Week), over actionable leaves only: a parent that still has unfinished
    children is suppressed so work is never double counted. Anything V1 would have dropped (e.g. a review status) lands in This Week."""
    seen, open_items = set(), []
    for issue in snap["current_tasks"] + snap["overdue"] + snap["blocked"]:
        if issue["key"] not in seen and issue["category"] != "done":
            seen.add(issue["key"])
            open_items.append(issue)
    parents = {i["parent"] for i in open_items if i.get("parent")}
    leaves = [i for i in open_items if i["key"] not in parents]
    out = {"overdue_blocked": [], "today": [], "this_week": [], "done": list(snap["done"])}
    for issue in leaves:
        due = _day(issue.get("due"))
        if _blocked(issue) or (due and due < today):
            out["overdue_blocked"].append(issue)
        elif issue["priority"].lower() in HIGH or due == today:
            out["today"].append(issue)
        else:
            out["this_week"].append(issue)
    out["overdue_blocked"].sort(key=lambda i: (i.get("due") or "9999", i["key"]))
    return out


def _text(content, url=None, bold=False):
    node = {"type": "text", "text": {"content": content[:2000]}}
    if url:
        node["text"]["link"] = {"url": url}
    if bold:
        node["annotations"] = {"bold": True}
    return node


def _line(issue, site, show_due=True, show_status=True):
    label = f"{issue['key']} — {issue['summary']}"
    parts = [_text(label, f"{site}/browse/{issue['key']}" if site else None)]
    tail = []
    if show_due and issue.get("due"):
        tail.append("due " + _day(issue["due"]).strftime("%b %-d"))
    if _blocked(issue):
        tail.append("blocked")
    elif show_status:
        tail.append(issue["status"])
    if tail:
        parts.append(_text(" · " + " · ".join(tail)))
    return parts


def _bullet(parts, children=None):
    block = {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": parts}}
    if children:
        block["bulleted_list_item"]["children"] = children
    return block


def _rows(issues, site):
    """Flat leaves in stable order, except leaves sharing a parent: that parent is rendered once, as a non-counting header."""
    rows, groups = [], {}
    for issue in issues:
        parent = issue.get("parent")
        if parent and parent not in groups:
            groups[parent] = {"summary": issue.get("parent_summary") or parent, "kids": []}
            rows.append(("group", parent))
        if parent:
            groups[parent]["kids"].append(issue)
        else:
            rows.append(("leaf", issue))
    out = []
    for kind, value in rows:
        if kind == "leaf":
            out.append(_bullet(_line(value, site)))
        else:
            group = groups[value]
            out.append(_bullet([_text(f"{value} — {group['summary']}", f"{site}/browse/{value}" if site else None, bold=True)],
                               [_bullet(_line(kid, site)) for kid in group["kids"]]))
    return out


def _heading(text):
    return {"object": "block", "type": "heading_4", "heading_4": {"rich_text": [_text(text)]}}


def _toggle(summary, children):
    return {"object": "block", "type": "toggle", "toggle": {"rich_text": [_text(summary)], "children": children}}


def _none():
    return [_bullet([_text("None")])]


def status_line(now, stale_at=None):
    stamp = now.strftime("%-I:%M %p CT")
    if stale_at:
        return f"{STATUS_PREFIX}STALE · last accepted {stale_at.strftime('%b %-d %-I:%M %p CT')}"
    return f"{STATUS_PREFIX}updated {stamp}"


def render(snaps, today, now, site):
    """Notion blocks for the card body (the 'JIRA Execution' heading is not part of it) and the per-section counts."""
    counts = {"overdue_blocked": 0, "today": 0, "this_week": 0, "done": 0}
    body = []
    for snap in snaps:
        b = buckets(snap, today)
        for name in counts:
            counts[name] += len(b[name])
        if len(snaps) > 1:
            body.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": [_text(snap["project"])]}})
        sprint = (snap.get("current_sprint") or {}).get("name") or "no active sprint"
        body.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_text(
            f"{sprint} · {len(b['overdue_blocked'])} Overdue/Blocked · {len(b['today'])} Today · "
            f"{len(b['this_week'])} This Week · {len(b['done'])} Done", bold=True)]}})
        body.append(_heading(f"Overdue / Blocked — {len(b['overdue_blocked'])}"))
        body += _rows(b["overdue_blocked"], site) or _none()
        flag = " · RE-PRIORITIZE" if len(b["today"]) > TODAY_CAPACITY else ""
        body.append(_heading(f"Today — {len(b['today'])} · Capacity {len(b['today'])}/{TODAY_CAPACITY}{flag}"))
        body += _rows(b["today"], site) or _none()
        body.append(_heading(f"This Week — {len(b['this_week'])}"))
        week = _rows(b["this_week"], site)
        if len(b["this_week"]) > WEEK_OPEN_LIMIT:
            body.append(_toggle("Show This Week items", week))
        else:
            body += week or _none()
        body.append(_toggle(f"Done — {len(b['done'])}", [_bullet(_line(i, site, show_due=False, show_status=False)) for i in b["done"]]
                            or _none()))
    return body, counts


def _depth(block):
    kids = (block.get(block.get("type")) or {}).get("children") or []
    return 1 + max((_depth(k) for k in kids), default=0)


def _strip(block):
    shell = {k: v for k, v in block.items() if k != block["type"]}
    inner = {k: v for k, v in block[block["type"]].items() if k != "children"}
    shell[block["type"]] = inner
    return shell, (block[block["type"]].get("children") or [])


def append_tree(client, parent_id, blocks):
    """Append blocks under parent. Notion accepts two nesting levels per request, so deeper subtrees are added in a second call.
    One attempt per POST: a retried append could duplicate rows (stale duplicates are removed on the next run)."""
    for start in range(0, len(blocks), 100):
        chunk = blocks[start:start + 100]
        deep = [b for b in chunk if _depth(b) > 2]
        payload = [(_strip(b)[0] if b in deep else b) for b in chunk]
        results = client.call_once("PATCH", f"/blocks/{parent_id}/children", {"children": payload}).get("results") or []
        if len(results) != len(chunk):
            raise CardError("JIRA_CARD_APPEND_MISMATCH")
        for block, made in zip(chunk, results):
            if block in deep:
                append_tree(client, made["id"], _strip(block)[1])


def children(client, block_id):
    out, cursor = [], None
    for _ in range(50):
        page = client.call("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        out += page.get("results") or []
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")
    raise CardError("JIRA_CARD_TOO_MANY_BLOCKS")


def _plain(block):
    inner = block.get(block.get("type")) or {}
    return "".join((r.get("plain_text") or (r.get("text") or {}).get("content") or "") for r in inner.get("rich_text") or [])


def _owned(kids):
    """The card is ours only while it opens with the 'JIRA Execution' heading: anything else is somebody else's block."""
    return bool(kids) and kids[0].get("type") == "heading_4" and _plain(kids[0]).strip() == CARD_TITLE


def site_url(environ):
    """Browse links need the site address; a gateway-style base URL (host starting "api.") has none."""
    explicit = (environ.get("JIRA_SITE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    base = (environ.get("JIRA_BASE_URL") or "").strip().rstrip("/")
    return base if base and not (urlparse(base).hostname or "").startswith("api.") else None


def card_projects(environ):
    configured = [entry[0] for entry in boards(environ)]
    wanted = [p.strip() for p in (environ.get("JIRA_CARD_PROJECTS") or "").split(",") if p.strip()] or configured[:1]
    if any(p not in configured for p in wanted):
        raise CardError("JIRA_CARD_CONFIG_INVALID")
    return wanted


def _client(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def run(limit, live, environ=os.environ, client=None, now=None, connect=None):
    block_id = (environ.get("JIRA_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        raise CardError("JIRA_CARD_CONFIG_MISSING")
    tz = ZoneInfo(LOCAL_TZ)
    now = now or datetime.now(tz)
    projects = card_projects(environ)
    with (connect or __import__("lifeos.platform.db", fromlist=["connect"]).connect)() as connection:
        snaps = [store.load(connection, p) for p in projects]
    if any(s is None for s in snaps):
        raise CardError("JIRA_CARD_NO_SNAPSHOT")
    taken = min(datetime.fromisoformat(s["taken_at"]) for s in snaps)
    stale = now - taken > timedelta(hours=STALE_HOURS)
    client = client or _client(environ)
    existing = children(client, block_id)
    if not _owned(existing):
        raise CardError("JIRA_CARD_NOT_OWNED")
    status = existing[1] if len(existing) > 1 and existing[1].get("type") == "paragraph" and _plain(existing[1]).startswith(STATUS_PREFIX) else None
    result = {"projects": len(projects), "stale": int(stale), "written": 0, "blocks": 0, "removed": 0}
    if stale:
        if status is None:
            raise CardError("JIRA_CARD_STALE_NO_BASELINE")          # nothing of ours to mark; the old card is left untouched
        if live:
            client.call("PATCH", f"/blocks/{status['id']}", {"paragraph": {"rich_text": rich_text(status_line(now, taken))}})
            result["written"] = 1
        return result
    body, counts = render(snaps, now.date(), now, site_url(environ))
    result.update({k: v for k, v in counts.items()}, blocks=len(body))
    if not live:
        return result
    top = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_text(status_line(now))]}}] + body
    old = existing[1:]
    append_tree(client, block_id, top)                              # new content first: a failure here removes nothing
    for block in old:
        try:
            client.call("DELETE", f"/blocks/{block['id']}")
            result["removed"] += 1
        except NotionError as error:
            if str(error) != "NOTION_HTTP_404":
                raise
    after = children(client, block_id)
    if not _owned(after) or len(after) != 1 + len(top) or not _plain(after[1]).startswith(STATUS_PREFIX):
        raise CardError("JIRA_CARD_VERIFY_FAILED")
    result["written"] = 1
    return result
