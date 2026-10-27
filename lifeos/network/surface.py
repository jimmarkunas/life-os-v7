"""NET-2.3 (D160): the one machine-owned "Network Leads (n)" block on a Job Ledger page. Platform only: Notion calls arrive through the caller's client.

Ownership is the marker, never position: a top-level toggle whose first child paragraph starts with `v7-net:1` and a short hash of the lead set. Nothing else on the page is read for
change or written. Each lead is a paragraph (name, company, title, observation date, a profile link, a fixed reason) plus a Dismiss tick, and a possible match also a Same company tick.
The ticks carry `<person id>:<evidence hash>`, so a tick can only ever confirm the exact evidence that was shown. Fixed reason templates only; nothing about closeness, willingness to
refer or hiring authority is ever generated, and no outreach text is drafted."""
import hashlib
import re
from datetime import date
from urllib.parse import quote, unquote

from lifeos.network import match
from lifeos.platform.notion_client import NotionError, rich_text

MARK = "v7-net:1"                                                       # the older block (a toggle that showed its marker and ids); still read, and retired once, so a replaced page loses nothing
MARK2 = "net:2"                                                         # the current block (a callout): its marker and every tick id ride in a hidden link, never in visible text
DESCRIPTION_MARK = "v7-jd:1"
TICK = re.compile(r"^(Dismiss|Same company) · (\d+):([0-9a-f]{12})$")
TICK2 = re.compile(r"^(Dismiss|Same company) (\d+):([0-9a-f]{12})$")
BASE_URL = "https://www.linkedin.com/in/"
HIDDEN = "https://example.com/lifeos/"                                  # an invisible zero-width character linked here carries the ids (Jim, Oct 27: no marker, hash or id may show on a page)
ZW = "\u200b"
TITLE = "LinkedIn connections"
REACH = "Reach Out ↗"
DISMISS_TEXT, SAME_TEXT = "Not relevant · dismiss", "Same company"


class SurfaceError(NotionError):
    """Fixed codes only: no name, company, title, link or page id ever enters one."""


def evidence_hash(lead, company_key, title, verified):
    return hashlib.sha256(f"{lead['person_id']}|{lead['tier']}|{company_key}|{title or ''}|{verified}".encode()).hexdigest()[:12]


SENT_TEXT = "Mark as Sent ↗"


def sent_url(base, page_id):
    """LIFE-78: the private Close Mark as Sent screen for THIS page's job, or None when no https origin is configured (then the block is exactly what it was before)."""
    base = (base or "").strip().rstrip("/")
    page = str(page_id or "").replace("-", "").lower()
    return f"{base}/sent?j={page}" if base.startswith("https://") and len(page) == 32 and page.isalnum() else None


def set_hash(leads, link=None):
    """The block's identity. The link's presence is part of it, so a block written before the link existed is replaced exactly once (Dismiss and Same company ticks are persisted first, as always)."""
    return hashlib.sha256(("|".join(f"{l['person_id']}:{l['evidence_hash']}" for l in leads) + ("|ms1" if link else "") + "|ui2").encode()).hexdigest()[:12] if leads else ""


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


def _hidden(ref):
    """A zero-width character that links to the ref: invisible on the page, read back from the link."""
    return {"type": "text", "text": {"content": ZW, "link": {"url": HIDDEN + quote(ref, safe="")}}}


def _todo(ref, text):
    return {"object": "block", "type": "to_do", "to_do": {"rich_text": [_hidden(ref)] + rich_text(text), "checked": False}}


def _bold(text):
    return [{"type": "text", "text": {"content": text}, "annotations": {"bold": True}}]


def build_block(leads, link=None):
    """leads: display dicts {person_id, tier, freshness, age_days, name, company, title, verified, url_key, evidence_hash}. -> ONE callout "LinkedIn connections" (the approved look: Jim, Oct 27) with children.
    Each lead is a paragraph (bold name, the fixed reason, a Reach Out link that only opens the verified profile) and a tick to dismiss it, plus a "Same company" tick on a possible match. The block's marker and
    hash ride in a hidden link on the title and each tick's `<person id>:<evidence hash>` in a hidden link on the tick, so nothing machine-made shows. `link` (sent_url) adds ONE quote block at the top that opens
    the signed Mark as Sent form for this job. A quote, not a paragraph: the leads reader (leads_on_page, also in the deployed Close service) only reads paragraphs and to_dos, so it ignores the link."""
    kids = []
    if link:
        kids.append({"object": "block", "type": "quote", "quote": {"rich_text": [{"type": "text", "text": {"content": SENT_TEXT, "link": {"url": link}}},
                                                                                   {"type": "text", "text": {"content": " · after you have messaged someone about this job. LIFE OS sends nothing."}}]}})
    for lead in leads:
        runs = _bold(lead["name"]) + rich_text(f" · {reason(lead)}")
        if lead.get("url_key"):
            runs.append({"type": "text", "text": {"content": " · "}})
            runs.append({"type": "text", "text": {"content": REACH, "link": {"url": BASE_URL + lead["url_key"]}}})
        kids.append(_para(runs))
        ref = f"{lead['person_id']}:{lead['evidence_hash']}"
        kids.append(_todo(f"Dismiss {ref}", DISMISS_TEXT))
        if lead["tier"] == match.POSSIBLE:
            kids.append(_todo(f"Same company {ref}", SAME_TEXT))
    title = [_hidden(f"{MARK2} {set_hash(leads, link)}")] + _bold(TITLE)
    return {"object": "block", "type": "callout", "callout": {"rich_text": title, "icon": {"type": "emoji", "emoji": "🔗"}, "color": "gray_background", "children": kids}}


def _refs(block):
    """The hidden refs carried by a block's own text (a zero-width character linked under HIDDEN)."""
    inner = block.get(block.get("type")) or {}
    out = []
    for part in inner.get("rich_text") or []:
        url = (part.get("href") if isinstance(part, dict) else None) or (((part.get("text") or {}).get("link") or {}).get("url") if isinstance(part, dict) else None) or ""
        if url.startswith(HIDDEN):
            out.append(unquote(url[len(HIDDEN):]))
    return out


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


def _tick(child):
    """(kind, person id, evidence hash, checked) for a Dismiss / Same company tick of either block generation, else None."""
    if child.get("type") != "to_do":
        return None
    checked = bool((child.get("to_do") or {}).get("checked"))
    for ref in _refs(child):
        found = TICK2.match(ref)
        if found:
            return found.group(1), int(found.group(2)), found.group(3), checked
    found = TICK.match(_plain(child))
    return (found.group(1), int(found.group(2)), found.group(3), checked) if found else None


def _owned_head(client, block):
    """(hash, inner children) when the block is a Network Leads block of either generation, else None. A callout or toggle is only opened when its own title (new) or, for a toggle, its first child (old) says so."""
    kind = block.get("type")
    if kind == "callout":
        for ref in _refs(block):
            if ref.startswith(MARK2 + " "):
                return ref[len(MARK2):].strip(), children(client, block["id"]) if block.get("has_children", True) else []
        return None
    if kind == "toggle" and block.get("has_children", True):
        inner = children(client, block["id"])
        if inner and inner[0].get("type") == "paragraph" and _plain(inner[0]).startswith(MARK):
            return _plain(inner[0])[len(MARK):].strip(), inner[1:]
    return None


def read_page(client, page_id):
    """-> (top-level blocks, owned blocks oldest first). owned item = {"id", "hash", "ticks": [(kind, person_id, evidence_hash, checked)]}. Normally one; an interrupted replacement can leave two,
    and the next run converges them (see apply). A page whose owned block cannot be read raises."""
    top = children(client, page_id)
    owned = []
    for block in top:
        head = _owned_head(client, block)
        if head:
            owned.append({"id": block["id"], "hash": head[0], "ticks": [t for t in (_tick(c) for c in head[1]) if t]})
    return top, owned


def leads_on_page(client, page_id, limit=10):
    """LIFE-21 (Mark as Sent): the people the page's owned block lists and Jim has not dismissed, in page order -> [{person_id, evidence_hash, name, reason}].
    Read only. No owned block -> []; two (an interrupted replacement) -> the newest, as apply converges; a lead without its Dismiss tick or an unreadable page raises."""
    inner = None
    for block in children(client, page_id):
        head = _owned_head(client, block)
        if head:
            inner = head[1]
    leads = []
    for child in inner or []:
        tick = _tick(child)
        if child.get("type") == "paragraph":
            text = _plain(child)
            for tail in (REACH, "open profile"):
                text = text.removesuffix(tail)
            name, _, reason = text.strip().removesuffix("·").strip().partition(" · ")
            leads.append({"name": name, "reason": reason, "person_id": None})
        elif tick and tick[0] == "Dismiss":
            if not leads or leads[-1]["person_id"] is not None:
                raise SurfaceError("NETWORK_SURFACE_PAGE_INCOMPLETE")
            leads[-1].update(person_id=tick[1], evidence_hash=tick[2], dismissed=tick[3])
    if any(lead["person_id"] is None or not lead["name"] for lead in leads):
        raise SurfaceError("NETWORK_SURFACE_PAGE_INCOMPLETE")
    return [{k: lead[k] for k in ("person_id", "evidence_hash", "name", "reason")} for lead in leads if not lead["dismissed"]][:limit]


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


def _others(top, owned):
    ids = {o["id"] for o in owned}
    return [b["id"] for b in top if b["id"] not in ids]


def _verify(client, page_id, others, want, count):
    """Proves the page holds exactly `count` owned blocks, all with the wanted hash, and that no other top-level block changed."""
    top, owned = read_page(client, page_id)
    if _others(top, owned) != others or len(owned) != count or any(o["hash"] != want for o in owned):
        raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")
    return top, owned


def apply(client, page_id, top, owned, desired_leads, live, link=None):
    """-> "unchanged" | "created" | "replaced" | "converged" | "removed" | "none" (prefixed "would_" when not live). Only owned blocks are ever touched; the others are proven unchanged.

    A replacement is recoverable: the new block is appended and PROVEN first, and only then is the previously accepted block retired. If the run is interrupted anywhere in between, the page
    holds the old and the new block, the old one is still visible, and the next run (the new block's hash is the wanted one) retires the extras and converges. Duplicates never accumulate."""
    want = set_hash(desired_leads, link)
    others = _others(top, owned)
    matching = [o for o in owned if o["hash"] == want] if desired_leads else []
    if not desired_leads:
        action = "removed" if owned else "none"
    elif matching and len(owned) == 1:
        action = "unchanged"
    elif matching:
        action = "converged"                                            # the wanted block is already there; extra (older) owned blocks are retired
    else:
        action = "replaced" if owned else "created"
    if action in ("none", "unchanged"):
        return action
    if not live:
        return "would_" + action
    keep = matching[-1] if matching else None
    if not desired_leads:
        retire = list(owned)
    elif keep:
        retire = [o for o in owned if o is not keep]
    else:
        client.call_once("PATCH", f"/blocks/{quote(page_id, safe='')}/children", {"children": [build_block(desired_leads, link)]})
        _, now_owned = read_page(client, page_id)
        fresh = [o for o in now_owned if o["id"] not in {x["id"] for x in owned}]
        if len(fresh) != 1 or fresh[0]["hash"] != want or [o["id"] for o in now_owned if o["id"] in {x["id"] for x in owned}] != [o["id"] for o in owned]:
            raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")      # the old block is still there and still visible: nothing known-good was retired
        retire = list(owned)
    for old in retire:
        client.call("DELETE", f"/blocks/{quote(old['id'], safe='')}")
    _verify(client, page_id, others, want, 1 if desired_leads else 0)
    return action
