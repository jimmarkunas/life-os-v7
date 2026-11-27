"""The one gray count line above the Recruiter Outreach view on the Daily Report: `N need action · M overdue`.

V7 writes ONE thing: the count line's text, in the callout named by RECRUITER_COUNT_BLOCK_ID. The callout's
title, link and view are untouched. Live writes need --live AND RECRUITER_OUTREACH=on. If the Recruiters stage
failed or the source is unreadable the line is left as it was and the step is a warning."""

import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.platform import report_region as R
from lifeos.platform.notion_client import Client, NotionError
from . import notion as store

CHICAGO = ZoneInfo("America/Chicago")
SETTING = "RECRUITER_COUNT_BLOCK_ID"


class OutreachError(NotionError):
    """Raised with a fixed code."""


def fail(code):
    return OutreachError(f"RECRUITER_OUTREACH_{code}")


def writer(environ):
    return Client(
        {
            "NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(),
            "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused",
        }
    )


def source(environ):
    return Client(environ, token_name="NOTION_RECRUITERS_TOKEN", source_name="NOTION_RECRUITERS_DATA_SOURCE_ID")


def line(needs_action, overdue):
    return f"{needs_action} need action · {overdue} overdue"


def _gray(part):
    return (part.get("annotations") or {}).get("color") == "gray"


def _is_link(part):
    return part.get("type") == "text" and bool((part.get("text") or {}).get("link"))


def retext(parts, text):
    """The callout's rich text with the gray text before the link replaced by `text`; None when there is no gray
    text or link."""
    parts = [p for p in parts if isinstance(p, dict)]
    link_at = next((i for i, p in enumerate(parts) if _is_link(p)), None)
    head = parts if link_at is None else parts[:link_at]
    gray = [i for i, p in enumerate(head) if p.get("type") == "text" and _gray(p)]
    if not gray and link_at is None:
        return None
    new = {
        "type": "text",
        "text": {"content": text + (" · " if link_at is not None else "")},
        "annotations": {"color": "gray"},
    }
    if gray:
        out = [p for i, p in enumerate(parts) if i not in gray[1:]]
        out[gray[0]] = new
        return out
    return parts[:link_at] + [new] + parts[link_at:]


def _plain(parts):
    return "".join(p.get("plain_text") or (p.get("text") or {}).get("content") or "" for p in parts)


def _clean(parts):
    out = []
    for p in parts:
        if p.get("type") != "text":
            out.append({k: v for k, v in p.items() if k != "href"})
            continue
        item = {"type": "text", "text": {"content": (p.get("text") or {}).get("content", "")}}
        link = (p.get("text") or {}).get("link")
        if link:
            item["text"]["link"] = {"url": link.get("url")}
        if p.get("annotations"):
            item["annotations"] = {k: v for k, v in p["annotations"].items() if v not in (False, "default")}
        out.append(item)
    return out


def region(client, environ):
    block_id = R.clean_id(environ.get("RECRUITER_COUNT_BLOCK_ID"))
    if not block_id:
        raise fail("NOT_CONFIGURED")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", block_id.replace("-", "")):
        raise fail(f"ID_INVALID:{SETTING}:len{len(block_id.replace('-', ''))}")
    try:
        block = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    except NotionError as error:
        raise fail(f"ANCHOR_GET:{error}") from None
    if (
        not isinstance(block, dict)
        or block.get("type") != "callout"
        or not R.same_id(block.get("id"), block_id)
        or block.get("archived")
        or block.get("in_trash")
    ):
        raise fail("ANCHOR_INVALID")
    return block


def present(client, environ, text, live):
    """Dry run: prove the callout and the new text only. Live: one write, read back. -> {"would_change",
    "verified"}"""
    block = region(client, environ)
    parts = (block.get("callout") or {}).get("rich_text") or []
    new = retext(parts, text)
    if new is None:
        raise fail("SHAPE_INVALID")
    counts = {"would_change": int(_plain(parts) != _plain(new)), "verified": False}
    if not live:
        return counts
    if counts["would_change"]:
        try:
            client.call("PATCH", f"/blocks/{quote(block['id'], safe='')}", {"callout": {"rich_text": _clean(new)}})
        except NotionError:
            raise fail("WRITE_FAILED") from None
    back = (region(client, environ).get("callout") or {}).get("rich_text") or []
    if _plain(back) != _plain(new):
        raise fail("READBACK_MISMATCH")
    counts["verified"] = True
    return counts


def run(limit, live, environ=os.environ, notion=None, client=None, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(CHICAGO)
    write = bool(live) and (environ.get("RECRUITER_OUTREACH") or "").strip().lower() == "on"
    counts = {"needs_action": 0, "overdue": 0, "live": write, "status": "ok"}
    stage = (environ.get("RECRUITERS_STAGE_OUTCOME") or "success").strip()
    reason, rows = None, []
    if stage not in ("success", "skipped"):
        reason = "RECRUITERS_STAGE_FAILED"
    else:
        try:
            notion = notion or source(environ)
            store.check_schema(notion)
            rows = store.rows_where(notion, {"property": "Action Status", "select": {"equals": store.NEEDS_ACTION}})
        except NotionError:
            reason = "SOURCE_UNREADABLE"
    if reason:
        counts["status"] = "degraded"
        print("recruiter-outreach:", json.dumps(counts, sort_keys=True))
        raise fail(f"DEGRADED:{reason}")
    open_rows = [r for r in rows if r["active"] and not r["done"]]
    counts.update(needs_action=len(open_rows), overdue=len(store.overdue_rows(open_rows, now.date())))
    counts.update(present(client or writer(environ), environ, line(counts["needs_action"], counts["overdue"]), write))
    return counts
