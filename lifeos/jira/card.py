"""The Jira card in the Daily Report (Phase B): the one Notion block V7 owns, rendered from the private database snapshot.

Block: the "JIRA Execution" callout named by JIRA_CARD_BLOCK_ID, and nothing else on the page. Input: the snapshot only, never live Jira, so a report refresh cannot break when Jira is down.
LIFE-975 (Jim's wireframe, Nov 25 2026): Overdue (oldest due first) and Blocked (a ticket that is both is Blocked only) are two sections, Today (capacity 3, quarter-end goals left out), the Done toggle; This Week is a COUNT in the
header line only (children of one non-epic parent count once), and every ticket under the Global Talent Visa epic (JIRA_GTV_EPIC) is left out of every list and count: that epic has its own callout (GTV_CARD_BLOCK_ID), which this stage
fills in place. A snapshot older than STALE_HOURS is never shown as current: only the status line changes, to STALE.
Output: counts and fixed codes only."""
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from lifeos.platform import ct_stamp, report_region
from lifeos.platform.jira import JiraError
from lifeos.platform.notion_client import Client, NotionError, rich_text
from .boards import boards
from . import store

LOCAL_TZ = "America/Chicago"
STALE_HOURS = 3
TODAY_CAPACITY = 3
CARD_TITLE = "JIRA Execution"

HIGH = {"high", "highest"}


class CardError(JiraError):
    """Fixed codes only."""


def _day(value):
    return datetime.fromisoformat(value).date() if value else None


def _blocked(issue):
    return issue["status"].lower() == "blocked"


def quarter_end(today):
    """The last day of the calendar quarter `today` is in (Dec 31 in Q4)."""
    month = ((today.month - 1) // 3 + 1) * 3
    return (date(today.year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))


def visa_keys(snap, epic):
    """The tickets under the Global Talent Visa epic: the snapshot's own `gtv` work (its Tasks and their Sub-tasks), anything whose parent is the epic or one of those Tasks, and the epic itself."""
    keys = {i["key"] for i in snap.get("gtv") or []} | ({epic} if epic else set())
    return keys


def _visa(issue, keys):
    return issue["key"] in keys or issue.get("parent") in keys


def buckets(snap, today, epic=None):
    """Over actionable leaves only: a parent that still has unfinished children is suppressed so work is never double counted.
    LIFE-975 precedence: Blocked (status blocked), Overdue (due before today), then Today (high priority or due today; a goal due on the last day of the quarter is left out), else This Week.
    Every ticket under the visa epic `epic` is left out. Overdue is oldest due first. `this_week` is the list; `week_count` is the number shown (children of one non-epic parent count once)."""
    gtv = visa_keys(snap, epic)
    seen, open_items = set(), []
    for issue in snap["current_tasks"] + snap["overdue"] + snap["blocked"]:
        if issue["key"] not in seen and issue["category"] != "done" and not _visa(issue, gtv):
            seen.add(issue["key"])
            open_items.append(issue)
    parents = {i["parent"] for i in open_items if i.get("parent")}
    leaves = [i for i in open_items if i["key"] not in parents]
    last = quarter_end(today).isoformat()
    out = {"overdue": [], "blocked": [], "today": [], "this_week": [], "done": [i for i in snap["done"] if not _visa(i, gtv)]}
    for issue in leaves:
        due = _day(issue.get("due"))
        if _blocked(issue):
            out["blocked"].append(issue)
        elif due and due < today:
            out["overdue"].append(issue)
        elif issue.get("due") == last:
            continue                                                  # a quarter goal is not this week's or today's work; it shows under Overdue if it ever passes its date
        elif issue["priority"].lower() in HIGH or due == today:
            out["today"].append(issue)
        else:
            out["this_week"].append(issue)
    out["overdue"].sort(key=lambda i: (i.get("due") or "9999", i["key"]))
    out["blocked"].sort(key=lambda i: (i.get("due") or "9999", i["key"]))
    return out


def week_count(items):
    """The This Week number: all children of one non-epic parent count as 1; an epic's children and parentless tickets count one each."""
    return len({("parent", i["parent"]) if i.get("parent") and i.get("parent_type") != "Epic" else ("leaf", i["key"]) for i in items})


def _text(content, url=None, bold=False):
    node = {"type": "text", "text": {"content": content[:2000]}}
    if url:
        node["text"]["link"] = {"url": url}
    if bold:
        node["annotations"] = {"bold": True}
    return node


def _line(issue, site, show_due=True, show_status=True, tag_blocked=True):
    label = f"{issue['key']} — {issue['summary']}"
    parts = [_text(label, f"{site}/browse/{issue['key']}" if site else None)]
    tail = []
    if show_due and issue.get("due"):
        tail.append("due " + _day(issue["due"]).strftime("%b %-d"))
    if _blocked(issue) and tag_blocked:
        tail.append("blocked")
    elif show_status and not _blocked(issue):
        tail.append(issue["status"])
    if tail:
        parts.append(_text(" · " + " · ".join(tail)))
    return parts


def _bullet(parts, children=None):
    block = {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": parts}}
    if children:
        block["bulleted_list_item"]["children"] = children
    return block


def _rows(issues, site, **line):
    """Flat leaves in stable order, except leaves sharing a parent: that parent is rendered once, as a non-counting header."""
    rows, groups = [], {}
    for issue in issues:
        parent = issue.get("parent")
        if parent and parent not in groups:
            groups[parent] = {"summary": issue.get("parent_summary") or parent, "kids": [],
                              "label": "Epic \u00b7 " if issue.get("parent_type") == "Epic" else ""}
            rows.append(("group", parent))
        if parent:
            groups[parent]["kids"].append(issue)
        else:
            rows.append(("leaf", issue))
    out = []
    for kind, value in rows:
        if kind == "leaf":
            out.append(_bullet(_line(value, site, **line)))
        else:
            group = groups[value]
            out.append(_bullet([_text(f"{group['label']}{value} — {group['summary']}", f"{site}/browse/{value}" if site else None, bold=True)],
                               [_bullet(_line(kid, site, **line)) for kid in group["kids"]]))
    return out


def _heading(text):
    return {"object": "block", "type": "heading_4", "heading_4": {"rich_text": [_text(text)]}}


def _toggle(summary, children):
    return {"object": "block", "type": "toggle", "toggle": {"rich_text": [_text(summary)], "children": children}}


def _none():
    return [_bullet([_text("None")])]


KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")
PRIORITY_ORDER = {"highest": 0, "high": 1}


def gtv_action(snap, sticky):
    """Exactly one unfinished, unblocked GTV action, sticky until it is done or blocked. Leaves only (a Task with open Sub-tasks
    yields its Sub-tasks). Otherwise: Jira priority, then earliest due date, then Jira rank order."""
    work = [i for i in snap.get("gtv") or [] if i["category"] != "done"]
    parents = {i["parent"] for i in work if i.get("parent")}
    leaves = [i for i in work if i["key"] not in parents and not _blocked(i)]
    for issue in leaves:
        if issue["key"] == sticky:
            return issue
    leaves.sort(key=lambda i: (PRIORITY_ORDER.get(i["priority"].lower(), 2), i.get("due") or "9999"))
    return leaves[0] if leaves else None


def gtv_parts(snap, sticky, site, context_url):
    """-> (rich text of the `GTV Action` line, chosen key). Only for a snapshot that has a GTV epic configured (gtv is a list, possibly empty)."""
    action = gtv_action(snap, sticky)
    parts = [_text("GTV Action \u00b7 ", bold=True)]
    if action:
        parts += _line(action, site, show_due=True)
    else:
        parts.append(_text("Create Jira \u2014 define the next GTV action \u00b7 Needs Jira"))
    if context_url:
        parts += [_text(" \u00b7 "), _text("GTV Notion Context", context_url)]
    return parts, (action or {}).get("key")


def heading_text(now, stale_at=None):
    """D121: the update time shares the heading, like Calendar: "JIRA Execution (11/13 · 2:10 PM CT)"."""
    if stale_at:
        return f"{CARD_TITLE} (STALE · last accepted {ct_stamp.when(stale_at)})"
    return f"{CARD_TITLE} ({ct_stamp.when(now)})"


def render(snaps, today, now, site, epic=None):
    """Notion blocks for the card body (the 'JIRA Execution' heading is not part of it) and the per-section counts. The visa epic is never in it."""
    counts = {"overdue": 0, "blocked": 0, "today": 0, "this_week": 0, "done": 0}
    body = []
    for snap in snaps:
        b = buckets(snap, today, epic)
        week = week_count(b["this_week"])
        for name in ("overdue", "blocked", "today", "done"):
            counts[name] += len(b[name])
        counts["this_week"] += week
        if len(snaps) > 1:
            body.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": [_text(snap["project"])]}})
        sprint = (snap.get("current_sprint") or {}).get("name") or "no active sprint"
        body.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_text(
            f"{sprint} \u00b7 {len(b['overdue'])} Overdue \u00b7 {len(b['blocked'])} Blocked \u00b7 {len(b['today'])} Today \u00b7 {week} This Week \u00b7 {len(b['done'])} Done", bold=True)]}})
        body.append(_heading(f"Overdue \u2014 {len(b['overdue'])}"))
        body += _rows(b["overdue"], site) or _none()
        body.append(_heading(f"Blocked \u2014 {len(b['blocked'])}"))
        body += _rows(b["blocked"], site, show_status=False, tag_blocked=False) or _none()
        flag = " \u00b7 RE-PRIORITIZE" if len(b["today"]) > TODAY_CAPACITY else ""
        body.append(_heading(f"Today \u2014 {len(b['today'])} \u00b7 Capacity {len(b['today'])}/{TODAY_CAPACITY}{flag}"))
        body += _rows(b["today"], site) or _none()
        body.append(_toggle(f"Done \u2014 {len(b['done'])}", [_bullet(_line(i, site, show_due=False, show_status=False)) for i in b["done"]]
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
    """The card is ours only while it opens with the 'JIRA Execution' heading (exactly, or 'JIRA Execution (...)' once the update time shares it):
    anything else is somebody else's block."""
    if not (kids and kids[0].get("type") == "heading_4"):
        return False
    heading = _plain(kids[0]).strip()
    return heading == CARD_TITLE or (heading.startswith(CARD_TITLE + " (") and heading.endswith(")"))


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


GTV_TITLE = "Global Talent Visa"


def _gtv_line(kids):
    """The one `GTV Action` paragraph of the visa callout, or None."""
    lines = [k for k in kids if k.get("type") == "paragraph" and _plain(k).startswith("GTV Action")]
    return lines[0] if len(lines) == 1 else None


def write_gtv(client, block_id, snaps, live, site, context_url):
    """LIFE-975 step 3: the Global Talent Visa callout (Jim moved the GTV line out of Jira Execution into its own callout, GTV_CARD_BLOCK_ID). Only its `GTV Action` paragraph is rewritten, in place, through a client scoped to that
    callout; the heading, every other block and the page are never touched, and a callout that is not the visa callout or holds no such line is never written or filled. -> counts."""
    client = report_region.ScopedClient(client, block_id, lambda code: CardError("JIRA_CARD_" + code))
    kids = children(client, block_id)
    if not (kids and kids[0].get("type") in report_region.HEADINGS and GTV_TITLE in _plain(kids[0])):
        raise CardError("JIRA_CARD_GTV_NOT_OWNED")
    line = _gtv_line(kids)
    if line is None:
        raise CardError("JIRA_CARD_GTV_NO_LINE")
    snap = next((s for s in snaps if s.get("gtv") is not None), None)
    if snap is None:
        return {"gtv": 0, "gtv_written": 0}
    sticky = next((m.group(0) for m in [KEY_RE.search(_plain(line))] if m), None)
    parts, chosen = gtv_parts(snap, sticky, site, context_url)
    want = "".join(p["text"]["content"] for p in parts)
    result = {"gtv": int(chosen is not None), "gtv_written": 0}
    if _plain(line) == want or not live:
        return result
    client.call("PATCH", f"/blocks/{line['id']}", {"paragraph": {"rich_text": parts}})
    after = _gtv_line(children(client, block_id))
    if after is None or _plain(after) != want or after["id"] != line["id"]:
        raise CardError("JIRA_CARD_GTV_VERIFY_FAILED")
    result["gtv_written"] = 1
    return result


def run(limit, live, environ=os.environ, client=None, now=None, connect=None, gtv_client=None):
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
    raw = client or _client(environ)
    client = report_region.ScopedClient(raw, block_id, lambda code: CardError("JIRA_CARD_" + code))      # LIFE-688: like every card, a write to any block outside this callout is refused before it is sent
    existing = children(client, block_id)
    if not _owned(existing):
        raise CardError("JIRA_CARD_NOT_OWNED")
    result = {"projects": len(projects), "stale": int(stale), "written": 0, "blocks": 0, "removed": 0}
    heading_block = existing[0]
    if stale:
        if len(existing) < 2 or not _plain(heading_block).strip().startswith(CARD_TITLE + " ("):
            raise CardError("JIRA_CARD_STALE_NO_BASELINE")          # V7 has not written this card yet: nothing of ours to mark, the old card is left untouched
        if live:
            client.call("PATCH", f"/blocks/{heading_block['id']}", {"heading_4": {"rich_text": rich_text(heading_text(now, taken))}})   # only the heading text changes
            result["written"] = 1
        return result
    site, epic = site_url(environ), (environ.get("JIRA_GTV_EPIC") or "").strip() or None
    body, counts = render(snaps, now.date(), now, site, epic)
    result.update({k: v for k, v in counts.items()}, blocks=len(body))
    gtv_block, gtv_error = (environ.get("GTV_CARD_BLOCK_ID") or "").strip(), None
    if gtv_block:                                                  # LIFE-975: the visa callout is its own card; a failure here is reported after the Jira card itself is written, never instead of it
        try:
            result.update(write_gtv(gtv_client or raw, report_region.clean_id(gtv_block), snaps, live, site, (environ.get("JIRA_GTV_CONTEXT_URL") or "").strip() or None))
        except CardError as error:
            gtv_error = error
    if not live:
        if gtv_error:
            raise gtv_error
        return result
    old = existing[1:]
    append_tree(client, block_id, body)                             # new content first: a failure here removes nothing
    for block in old:
        try:
            client.call("DELETE", f"/blocks/{block['id']}")
            result["removed"] += 1
        except NotionError as error:
            if str(error) != "NOTION_HTTP_404":
                raise
    heading = heading_text(now)
    client.call("PATCH", f"/blocks/{heading_block['id']}", {"heading_4": {"rich_text": rich_text(heading)}})
    after = children(client, block_id)
    if not _owned(after) or len(after) != 1 + len(body) or after[0].get("id") != heading_block["id"] or _plain(after[0]).strip() != heading:
        raise CardError("JIRA_CARD_VERIFY_FAILED")
    result["written"] = 1
    if gtv_error:
        raise gtv_error                                            # the Jira card is written and read back; the visa callout's own failure still fails the stage with its fixed code
    return result
