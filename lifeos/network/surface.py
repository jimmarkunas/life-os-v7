"""NET-2.3 (D160): the one machine-owned "Network Leads (n)" block on a Job Ledger page. Platform only: Notion calls arrive through the caller's client.

Ownership is the marker, never position: a top-level toggle whose first child paragraph starts with `v7-net:1` and a short hash of the lead set. Nothing else on the page is read for
change or written. Each lead is a paragraph (name, company, title, observation date, a profile link, a fixed reason) plus a Dismiss tick, and a possible match also a Same company tick.
The ticks carry `<person id>:<evidence hash>`, so a tick can only ever confirm the exact evidence that was shown. Fixed reason templates only; nothing about closeness, willingness to
refer or hiring authority is ever generated, and no outreach text is drafted."""
import hashlib
import re
from datetime import date
from urllib.parse import quote

from lifeos.network import match
from lifeos.platform.notion_client import NotionError, rich_text

MARK = "v7-net:1"
DESCRIPTION_MARK = "v7-jd:1"
TICK = re.compile(r"^(Dismiss|Same company) · (\d+):([0-9a-f]{12})$")
BASE_URL = "https://www.linkedin.com/in/"


class SurfaceError(NotionError):
    """Fixed codes only: no name, company, title, link or page id ever enters one."""


def evidence_hash(lead, company_key, title, verified):
    return hashlib.sha256(f"{lead['person_id']}|{lead['tier']}|{company_key}|{title or ''}|{verified}".encode()).hexdigest()[:12]


def set_hash(leads):
    return hashlib.sha256("|".join(f"{l['person_id']}:{l['evidence_hash']}" for l in leads).encode()).hexdigest()[:12] if leads else ""


def _day(moment):
    return moment.strftime("%b ") + str(moment.day)


def reason(lead):
    """A fixed template built from stored facts."""
    when = f"as of {_day(lead['verified'])} ({lead['age_days']} days)"
    where = lead["company"] or "this company"
    if lead["tier"] == match.CURRENT:
        text = f"Listed at {where}" + (f" as {lead['title']}" if lead["title"] else "") + f", {when}"
    elif lead["tier"] == match.PREVIOUS:
        text = f"Previously listed at {where}, {when}"
    else:
        text = f"Name differs: listed at {where}, {when}. Same company?"
    return text + (" · verify first" if lead["freshness"] == "STALE" else "")


def _para(runs):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": runs}}


def _todo(text):
    return {"object": "block", "type": "to_do", "to_do": {"rich_text": rich_text(text), "checked": False}}


def build_block(leads):
    """leads: display dicts {person_id, tier, freshness, age_days, name, company, title, verified, url_key, evidence_hash}. -> one toggle block with children."""
    kids = [_para(rich_text(f"{MARK} {set_hash(leads)}"))]
    for lead in leads:
        runs = rich_text(f"{lead['name']} · {reason(lead)} · ")
        if lead.get("url_key"):
            runs.append({"type": "text", "text": {"content": "open profile", "link": {"url": BASE_URL + lead["url_key"]}}})
        kids.append(_para(runs))
        ref = f"{lead['person_id']}:{lead['evidence_hash']}"
        kids.append(_todo(f"Dismiss · {ref}"))
        if lead["tier"] == match.POSSIBLE:
            kids.append(_todo(f"Same company · {ref}"))
    return {"object": "block", "type": "toggle", "toggle": {"rich_text": rich_text(f"Network Leads ({len(leads)})"), "children": kids}}


def _plain(block):
    inner = block.get(block.get("type")) or {}
    return "".join((p.get("plain_text") or (p.get("text") or {}).get("content") or "") for p in inner.get("rich_text") or [] if isinstance(p, dict))


def children(client, block_id):
    """Every child of a block, following every page; anything short of a provably complete read raises."""
    out, cursor, seen = [], None, set()
    for _ in range(50):
        path = f"/blocks/{quote(block_id, safe='')}/children?page_size=100" + (f"&start_cursor={quote(cursor, safe='')}" if cursor else "")
        page = client.call("GET", path)
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise SurfaceError("NETWORK_SURFACE_PAGE_INCOMPLETE")
        out.extend(page["results"])
        if not page["has_more"]:
            return out
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise SurfaceError("NETWORK_SURFACE_PAGE_INCOMPLETE")
        seen.add(cursor)
    raise SurfaceError("NETWORK_SURFACE_PAGE_INCOMPLETE")


def read_page(client, page_id):
    """-> (top-level blocks, owned block or None). owned = {"id", "hash", "ticks": [(kind, person_id, evidence_hash, checked)]}. A page whose owned block cannot be read raises."""
    top = children(client, page_id)
    owned = None
    for block in top:
        if block.get("type") != "toggle" or not block.get("has_children", True):
            continue
        inner = children(client, block["id"])
        if inner and inner[0].get("type") == "paragraph" and _plain(inner[0]).startswith(MARK):
            if owned is not None:
                raise SurfaceError("NETWORK_SURFACE_DUPLICATE_BLOCK")
            ticks = []
            for child in inner[1:]:
                found = TICK.match(_plain(child)) if child.get("type") == "to_do" else None
                if found:
                    ticks.append((found.group(1), int(found.group(2)), found.group(3), bool((child.get("to_do") or {}).get("checked"))))
            owned = {"id": block["id"], "hash": _plain(inner[0])[len(MARK):].strip(), "ticks": ticks}
    return top, owned


def guard(page, top, job_key, source_id):
    """None when the page is the canonical Ledger page for this job, else a fixed reason. Nothing is written when this fails."""
    if page.get("archived") or page.get("in_trash"):
        return "page_gone"
    parent = page.get("parent") or {}
    parent_source = str(parent.get("data_source_id") or "").replace("-", "").lower()
    if not parent_source or parent_source != str(source_id or "").replace("-", "").lower().replace("collection://", ""):
        return "wrong_data_source"
    key = "".join(t.get("plain_text", "") for t in ((page.get("properties") or {}).get("Stable Job Key") or {}).get("rich_text") or [])
    if key != job_key:
        return "key_mismatch"
    if not top or top[0].get("type") != "paragraph" or not _plain(top[0]).startswith(DESCRIPTION_MARK) or job_key not in _plain(top[0]):
        return "no_description_marker"
    return None


def apply(client, page_id, top, owned, desired_leads, live):
    """-> "unchanged" | "created" | "replaced" | "removed" | "none" (and the same prefixed "would_" when not live). Only the owned block is ever touched; the others are proven unchanged."""
    want = set_hash(desired_leads)
    if not desired_leads:
        action = "removed" if owned else "none"
    elif owned and owned["hash"] == want:
        action = "unchanged"
    else:
        action = "replaced" if owned else "created"
    if action in ("none", "unchanged"):
        return action
    if not live:
        return "would_" + action
    others = [b["id"] for b in top if not (owned and b["id"] == owned["id"])]
    if owned:
        client.call("DELETE", f"/blocks/{quote(owned['id'], safe='')}")
    if desired_leads:
        client.call_once("PATCH", f"/blocks/{quote(page_id, safe='')}/children", {"children": [build_block(desired_leads)]})
    after_top, after_owned = read_page(client, page_id)
    if [b["id"] for b in after_top if not (after_owned and b["id"] == after_owned["id"])] != others:
        raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")
    if (after_owned["hash"] if after_owned else "") != want or (not desired_leads) != (after_owned is None):
        raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")
    return action
