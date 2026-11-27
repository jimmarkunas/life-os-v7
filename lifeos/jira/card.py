"""The Jira card in the Daily Report: the "JIRA Execution" Notion block (JIRA_CARD_BLOCK_ID), rendered from the private snapshot, never live Jira.

Tickets under the Global Talent Visa epic (JIRA_GTV_EPIC) are excluded; their own callout (GTV_CARD_BLOCK_ID) is
filled in place. A snapshot older than STALE_HOURS changes only the status line, to STALE. Output: counts and fixed
codes only.
"""

import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from lifeos.platform import ct_stamp, report_region
from lifeos.platform.jira import JiraError
from lifeos.platform.notion_client import Client, NotionError, rich_text
from .boards import boards
from . import reconcile, store

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
    month = ((today.month - 1) // 3 + 1) * 3
    return date(today.year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)


def visa_keys(snap, epic):
    keys = {i["key"] for i in snap.get("gtv") or []} | ({epic} if epic else set())
    return keys


def _visa(issue, keys):
    return issue["key"] in keys or issue.get("parent") in keys


def buckets(snap, today, epic=None):
    """Classify actionable leaves only: Blocked, Overdue (oldest first), Today (quarter-end goals left out), else This
    Week. Visa-epic tickets are excluded."""
    gtv = visa_keys(snap, epic)
    seen, open_items = set(), []
    for issue in snap["current_tasks"] + snap["overdue"] + snap["blocked"]:
        if issue["key"] not in seen and issue["category"] != "done" and not _visa(issue, gtv):
            seen.add(issue["key"])
            open_items.append(issue)
    parents = {i["parent"] for i in open_items if i.get("parent")}
    leaves = [i for i in open_items if i["key"] not in parents]
    last = quarter_end(today).isoformat()
    out = {
        "overdue": [],
        "blocked": [],
        "today": [],
        "this_week": [],
        "done": [i for i in snap["done"] if not _visa(i, gtv)],
    }
    for issue in leaves:
        due = _day(issue.get("due"))
        if _blocked(issue):
            out["blocked"].append(issue)
        elif due and due < today:
            out["overdue"].append(issue)
        elif issue.get("due") == last:
            continue  # a quarter goal is not this week's work; it shows under Overdue once past its date
        elif issue["priority"].lower() in HIGH or due == today:
            out["today"].append(issue)
        else:
            out["this_week"].append(issue)
    out["overdue"].sort(key=lambda i: (i.get("due") or "9999", i["key"]))
    out["blocked"].sort(key=lambda i: (i.get("due") or "9999", i["key"]))
    return out


def week_count(items):
    """The This Week count: children of one non-epic parent count once."""
    return len(
        {
            ("parent", i["parent"]) if i.get("parent") and i.get("parent_type") != "Epic" else ("leaf", i["key"])
            for i in items
        }
    )


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
    rows, groups = [], {}
    for issue in issues:
        parent = issue.get("parent")
        if parent and parent not in groups:
            groups[parent] = {
                "summary": issue.get("parent_summary") or parent,
                "kids": [],
                "label": "Epic \u00b7 " if issue.get("parent_type") == "Epic" else "",
            }
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
            out.append(
                _bullet(
                    [
                        _text(
                            f"{group['label']}{value} — {group['summary']}",
                            f"{site}/browse/{value}" if site else None,
                            bold=True,
                        )
                    ],
                    [_bullet(_line(kid, site, **line)) for kid in group["kids"]],
                )
            )
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
    """The one unfinished, unblocked GTV action (leaves only), sticky until done or blocked."""
    work = [i for i in snap.get("gtv") or [] if i["category"] != "done"]
    parents = {i["parent"] for i in work if i.get("parent")}
    leaves = [i for i in work if i["key"] not in parents and not _blocked(i)]
    for issue in leaves:
        if issue["key"] == sticky:
            return issue
    leaves.sort(key=lambda i: (PRIORITY_ORDER.get(i["priority"].lower(), 2), i.get("due") or "9999"))
    return leaves[0] if leaves else None


def gtv_parts(snap, sticky, site, context_url):
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
    if stale_at:
        return f"{CARD_TITLE} (STALE · last accepted {ct_stamp.when(stale_at)})"
    return f"{CARD_TITLE} ({ct_stamp.when(now)})"


def render(snaps, today, now, site, epic=None):
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
        body.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        _text(
                            f"{sprint} \u00b7 {len(b['overdue'])} Overdue \u00b7 {len(b['blocked'])} Blocked \u00b7 {len(b['today'])} Today \u00b7 {week} This Week \u00b7 {len(b['done'])} Done",
                            bold=True,
                        )
                    ]
                },
            }
        )
        body.append(_heading(f"Overdue \u2014 {len(b['overdue'])}"))
        body += _rows(b["overdue"], site) or _none()
        body.append(_heading(f"Blocked \u2014 {len(b['blocked'])}"))
        body += _rows(b["blocked"], site, show_status=False, tag_blocked=False) or _none()
        flag = " \u00b7 RE-PRIORITIZE" if len(b["today"]) > TODAY_CAPACITY else ""
        body.append(
            _heading(f"Today \u2014 {len(b['today'])} \u00b7 Capacity {len(b['today'])}/{TODAY_CAPACITY}{flag}")
        )
        body += _rows(b["today"], site) or _none()
        body.append(
            _toggle(
                f"Done \u2014 {len(b['done'])}",
                [_bullet(_line(i, site, show_due=False, show_status=False)) for i in b["done"]] or _none(),
            )
        )
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
    """Append blocks under parent; deeper subtrees go in a second call (two nesting levels per request). One attempt
    per POST: a retry could duplicate rows."""
    for start in range(0, len(blocks), 100):
        chunk = blocks[start : start + 100]
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
        page = client.call(
            "GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        )
        out += page.get("results") or []
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")
    raise CardError("JIRA_CARD_TOO_MANY_BLOCKS")


def _plain(block):
    inner = block.get(block.get("type")) or {}
    return "".join(
        (r.get("plain_text") or (r.get("text") or {}).get("content") or "") for r in inner.get("rich_text") or []
    )


def _owned(kids):
    if not (kids and kids[0].get("type") == "heading_4"):
        return False
    heading = _plain(kids[0]).strip()
    return heading == CARD_TITLE or (heading.startswith(CARD_TITLE + " (") and heading.endswith(")"))


def site_url(environ):
    """Browse links need the site address; a gateway-style base URL (host "api.") has none."""
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
    return Client(
        {
            "NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(),
            "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused",
        }
    )


GTV_TITLE = "Global Talent Visa"


def _gtv_line(kids):
    lines = [k for k in kids if k.get("type") == "paragraph" and _plain(k).startswith("GTV Action")]
    return lines[0] if len(lines) == 1 else None


def write_gtv(client, block_id, snaps, live, site, context_url):
    """Rewrite only the `GTV Action` paragraph of the visa callout through a scoped client. -> counts."""
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


def reconcile_line(recon, now):
    """The reconcile workload's Daily Report line, or DEGRADED without a good summary from the last 36 hours."""
    if (
        recon
        and recon.get("ok")
        and recon.get("line")
        and now - datetime.fromisoformat(recon["taken_at"]) <= timedelta(hours=36)
    ):
        return recon["line"]
    return "Jira sprints: DEGRADED \u00b7 " + (
        (recon or {}).get("line", "").replace("Jira sprints: DEGRADED \u00b7 ", "")
        if recon and not recon.get("ok")
        else "no good run in the last 36 hours"
    )


def run(limit, live, environ=os.environ, client=None, now=None, connect=None, gtv_client=None):
    block_id = (environ.get("JIRA_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        raise CardError("JIRA_CARD_CONFIG_MISSING")
    tz = ZoneInfo(LOCAL_TZ)
    now = now or datetime.now(tz)
    projects = card_projects(environ)
    with (connect or __import__("lifeos.platform.db", fromlist=["connect"]).connect)() as connection:
        snaps = [store.load(connection, p) for p in projects]
        recon = store.load(connection, "_recon") if reconcile.enabled(environ) else None
    if any(s is None for s in snaps):
        raise CardError("JIRA_CARD_NO_SNAPSHOT")
    taken = min(datetime.fromisoformat(s["taken_at"]) for s in snaps)
    stale = now - taken > timedelta(hours=STALE_HOURS)
    raw = client or _client(environ)
    client = report_region.ScopedClient(raw, block_id, lambda code: CardError("JIRA_CARD_" + code))
    existing = children(client, block_id)
    if not _owned(existing):
        raise CardError("JIRA_CARD_NOT_OWNED")
    result = {"projects": len(projects), "stale": int(stale), "written": 0, "blocks": 0, "removed": 0}
    heading_block = existing[0]
    if stale:
        if len(existing) < 2 or not _plain(heading_block).strip().startswith(CARD_TITLE + " ("):
            raise CardError(
                "JIRA_CARD_STALE_NO_BASELINE"
            )  # V7 has not written this card yet: nothing of ours to mark, the old card is left untouched
        if live:
            client.call(
                "PATCH",
                f"/blocks/{heading_block['id']}",
                {"heading_4": {"rich_text": rich_text(heading_text(now, taken))}},
            )
            result["written"] = 1
        return result
    site, epic = site_url(environ), (environ.get("JIRA_GTV_EPIC") or "").strip() or None
    body, counts = render(snaps, now.date(), now, site, epic)
    if reconcile.enabled(environ):
        body.insert(
            0,
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [_text(reconcile_line(recon, now), bold=False)]},
            },
        )
        result["reconcile_line"] = 1
    result.update({k: v for k, v in counts.items()}, blocks=len(body))
    gtv_block, gtv_error = (environ.get("GTV_CARD_BLOCK_ID") or "").strip(), None
    if gtv_block:
        try:
            result.update(
                write_gtv(
                    gtv_client or raw,
                    report_region.clean_id(gtv_block),
                    snaps,
                    live,
                    site,
                    (environ.get("JIRA_GTV_CONTEXT_URL") or "").strip() or None,
                )
            )
        except CardError as error:
            gtv_error = error
    if not live:
        if gtv_error:
            raise gtv_error
        return result
    old = existing[1:]
    append_tree(client, block_id, body)
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
    if (
        not _owned(after)
        or len(after) != 1 + len(body)
        or after[0].get("id") != heading_block["id"]
        or _plain(after[0]).strip() != heading
    ):
        raise CardError("JIRA_CARD_VERIFY_FAILED")
    result["written"] = 1
    if gtv_error:
        raise gtv_error
    return result
