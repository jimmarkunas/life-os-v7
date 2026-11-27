"""The one machine-owned "Network Leads" block on a Job Ledger page. Platform only: Notion calls go through the
caller's client.

Ownership is the marker, never position: a callout (or older toggle) carrying a marker and a hash of the lead
set. Nothing else on the page is read for change or written. Reasons come from fixed templates; no outreach text
is drafted."""
import hashlib
import re
import time
from urllib.parse import quote

from lifeos.platform import ledger_body as body
from lifeos.network import match
from lifeos.platform import hidden
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.platform.notion_pages import children as notion_children

# older block (a toggle showing its marker and ids): still read, retired once
MARK = "v7-net:1"
# current block (a callout): marker and tick ids ride in a hidden link
MARK2 = "net:2"
DESCRIPTION_MARK = "v7-jd:1"
TICK = re.compile(r"^(Dismiss|Same company) · (\d+):([0-9a-f]{12})$")
TICK2 = re.compile(r"^(Dismiss|Same company|Contacted|Referral|Closed) (\d+):([0-9a-f]{12})$")
BASE_URL = "https://www.linkedin.com/in/"
TITLE = "LinkedIn connections"
REACH = "Reach Out ↗"
DISMISS_TEXT, SAME_TEXT = "Not relevant · dismiss", "Same company"
STATUS = ("Contacted", "Referral", "Closed")
# hash carried until every card is written, so a block cut short never reads as the wanted one
PENDING = "building"
PROFILE = "LinkedIn profile ↗"
ICON = {"type": "external", "external": {"url": "https://upload.wikimedia.org/wikipedia/commons/c/ca/LinkedIn_logo_initials.png"}}


class SurfaceError(NotionError):
    """Raised with a fixed code."""


def evidence_hash(lead, company_key, title, verified):
    return hashlib.sha256(f"{lead['person_id']}|{lead['tier']}|{company_key}|{title or ''}|{verified}".encode()).hexdigest()[:12]


def set_hash(leads):
    return hashlib.sha256(("|".join(f"{l['person_id']}:{l['evidence_hash']}" for l in leads) + "|ui5").encode()).hexdigest()[:12] if leads else ""


def _day(moment):
    return moment.strftime("%b ") + str(moment.day)


def reason(lead):
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


def _todo(ref, text):
    return {"object": "block", "type": "to_do", "to_do": {"rich_text": [hidden.run(ref)] + rich_text(text), "checked": False}}


def _bold(text):
    return [{"type": "text", "text": {"content": text}, "annotations": {"bold": True}}]


def build_block(leads):
    """leads: display dicts {person_id, tier, freshness, age_days, name, company, title, verified, url_key,
    evidence_hash}. -> ONE callout: a paragraph per lead, a dismiss tick, and a "Same company" tick on a
    possible match."""
    kids = []
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
    title = [hidden.run(f"{MARK2} {set_hash(leads)}")] + _bold(TITLE)
    return {"object": "block", "type": "callout", "callout": {"rich_text": title, "icon": {"type": "emoji", "emoji": "🔗"}, "color": "gray_background", "children": kids}}


def _employee(lead):
    if lead["tier"] == match.CURRENT:
        text = (f"{lead['title']} · " if lead["title"] else "") + "Current employee"
    elif lead["tier"] == match.PREVIOUS:
        text = "Former employee"
    else:
        text = (f"{lead['title']} · " if lead["title"] else "") + "Same company?"
    return text + (" · verify first" if lead["freshness"] == "STALE" else "")


def card_columns(lead, carried=None):
    """One contact card's column_list. Its "Referral status" ticks go in by a second request (Notion's two-level
    limit); carried: {kind: checked} keeps existing ticks across a rewrite."""
    carried = carried or {}
    profile = ([{"type": "text", "text": {"content": PROFILE, "link": {"url": BASE_URL + lead["url_key"]}}}] if lead.get("url_key") else rich_text("Profile unavailable"))
    left = [_para(_bold(lead["name"])), _para(rich_text(_employee(lead))), _para(profile + rich_text(" · " + ("Contacted" if carried.get("Contacted") else "Not contacted")))]
    right = [{"object": "block", "type": "toggle", "toggle": {"rich_text": rich_text("Referral status")}}]
    return {"object": "block", "type": "column_list", "column_list": {"children": [
        {"object": "block", "type": "column", "column": {"width_ratio": 0.65, "children": left}},
        {"object": "block", "type": "column", "column": {"width_ratio": 0.35, "children": right}}]}}


def status_ticks(lead, carried=None):
    carried = carried or {}
    ref = f"{lead['person_id']}:{lead['evidence_hash']}"
    kinds = STATUS + ((SAME_TEXT,) if lead["tier"] == match.POSSIBLE else ())
    return [{"object": "block", "type": "to_do", "to_do": {"rich_text": [hidden.run(f"{kind} {ref}")] + rich_text(kind), "checked": bool(carried.get(kind))}} for kind in kinds]


def write_cards(client, parent_id, leads, want, carried=None):
    """Writes the block into parent_id; the outer title carries the PENDING hash until the last card is in. ->
    outer block id."""
    carried = carried or {}
    shell = {"object": "block", "type": "callout", "callout": {"rich_text": [hidden.run(f"{MARK2} {PENDING}")] + _bold(TITLE), "icon": ICON, "children": [
        {"object": "block", "type": "callout", "callout": {"rich_text": [], "icon": ICON}} for _ in leads]}}
    outer_id = _append_last(client, parent_id, shell)
    cards = [b for b in children(client, outer_id) if b.get("type") == "callout"]
    if len(cards) != len(leads):
        raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")
    for card, lead in zip(cards, leads):
        mine = carried.get(lead["person_id"]) or {}
        columns = [b for b in children(client, _append_last(client, card["id"], card_columns(lead, mine))) if b.get("type") == "column"]
        toggles = [b for b in children(client, columns[1]["id"]) if b.get("type") == "toggle"] if len(columns) == 2 else []
        if len(toggles) != 1:
            raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")
        _write(client, f"/blocks/{quote(toggles[0]['id'], safe='')}/children", {"children": status_ticks(lead, mine)})
    client.call("PATCH", f"/blocks/{quote(outer_id, safe='')}", {"callout": {"rich_text": [hidden.run(f"{MARK2} {want}")] + _bold(TITLE)}})
    return outer_id


def _write(client, path, body, sleep=time.sleep):
    for attempt in range(5):
        try:
            return client.call_once("PATCH", path, body)
        except NotionError as error:
            if str(error) != "NOTION_HTTP_429" or attempt == 4:
                raise
            sleep(2 + 2 * attempt)


def _append_last(client, parent_id, block):
    """Appends ONE block and returns its id: from Notion's reply, else proven by reading the parent back."""
    made = _write(client, f"/blocks/{quote(parent_id, safe='')}/children", {"children": [block]})
    results = (made or {}).get("results") if isinstance(made, dict) else None
    if isinstance(results, list) and results:
        last = results[-1]
        if last.get("type") != block["type"] or not last.get("id"):
            raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")
        return last["id"]
    after = children(client, parent_id)
    if not after or after[-1].get("type") != block["type"]:
        raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")
    return after[-1]["id"]


def _cards(client, inner, names=True):
    """-> [{"name", "reason", "ticks": [(kind, person_id, evidence_hash, checked)]}]; names=False skips the name
    reads."""
    out = []
    for card in inner:
        if card.get("type") != "callout":
            continue
        lists = [b for b in children(client, card["id"]) if b.get("type") == "column_list"]
        columns = [b for b in children(client, lists[0]["id"]) if b.get("type") == "column"] if len(lists) == 1 else []
        if len(columns) != 2:
            continue
        lines = [_plain(b) for b in children(client, columns[0]["id"]) if b.get("type") == "paragraph"] if names else []
        ticks = []
        for toggle in [b for b in children(client, columns[1]["id"]) if b.get("type") == "toggle"]:
            ticks += [t for t in (_tick(c) for c in children(client, toggle["id"])) if t]
        out.append({"name": lines[0].strip() if lines else "", "reason": lines[1].strip() if len(lines) > 1 else "", "ticks": ticks})
    return out


def _plain(block):
    inner = block.get(block.get("type")) or {}
    return "".join((p.get("plain_text") or (p.get("text") or {}).get("content") or "") for p in inner.get("rich_text") or [] if isinstance(p, dict))


def children(client, block_id):
    return [c for page in notion_children(client, block_id, SurfaceError, incomplete="NETWORK_SURFACE_PAGE_INCOMPLETE", limit=50) for c in page["results"]]


def _tick(child):
    if child.get("type") != "to_do":
        return None
    checked = bool((child.get("to_do") or {}).get("checked"))
    for ref in hidden.refs(child):
        found = TICK2.match(ref)
        if found:
            return found.group(1), int(found.group(2)), found.group(3), checked
    found = TICK.match(_plain(child))
    return (found.group(1), int(found.group(2)), found.group(3), checked) if found else None


def _owned_head(client, block):
    kind = block.get("type")
    if kind == "callout":
        for ref in hidden.refs(block):
            if ref.startswith(MARK2 + " "):
                return ref[len(MARK2):].strip(), children(client, block["id"]) if block.get("has_children", True) else []
        return None
    if kind == "toggle" and block.get("has_children", True):
        inner = children(client, block["id"])
        if inner and inner[0].get("type") == "paragraph" and _plain(inner[0]).startswith(MARK):
            return _plain(inner[0])[len(MARK):].strip(), inner[1:]
    return None


class Top(list):
    """The page's top-level blocks, plus `right`: the right column's id on a two-column Ledger page, else None."""
    right = None


def _right(client, top, key=None):
    found = body.description_list(top, key)
    if found is None:
        return None
    columns = [b for b in children(client, found["id"]) if b.get("type") == "column"]
    return columns[1]["id"] if len(columns) == 2 else None


def row_key(client, page_id, top):
    if sum(1 for b in top if b.get("type") == "column_list") < 2:
        return None
    page = client.get_page(page_id) if hasattr(client, "get_page") else client.call("GET", f"/pages/{page_id}")
    props = (page or {}).get("properties") or {}
    return "".join(t.get("plain_text", "") for t in (props.get("Stable Job Key") or {}).get("rich_text") or []) or None


def read_page(client, page_id, deep=True, key=None):
    """-> (top-level blocks, owned blocks oldest first). An interrupted replacement can leave two owned blocks."""
    top = Top(children(client, page_id))
    top.right = _right(client, top, key or row_key(client, page_id, top))
    owned = []
    for parent, blocks in ((page_id, top), *(((top.right, children(client, top.right)),) if top.right else ())):
        for block in blocks:
            head = _owned_head(client, block)
            if head:
                cards = [c for c in head[1] if c.get("type") == "callout"]
                owned.append({"id": block["id"], "parent": parent, "hash": head[0], "ticks": [t for t in (_tick(c) for c in head[1]) if t] + ([t for card in _cards(client, cards, names=False) for t in card["ticks"]] if deep else [])})
    return top, owned


def leads_on_page(client, page_id, limit=10):
    """The people the owned block lists that nobody has dismissed, in page order. A lead without its Dismiss tick
    raises."""
    inner, top = None, children(client, page_id)
    right = _right(client, top, row_key(client, page_id, top))
    for block in [*top, *(children(client, right) if right else ())]:
        head = _owned_head(client, block)
        if head:
            inner = head[1]
    if any(c.get("type") == "callout" for c in inner or []):
        cards = _cards(client, inner)
        found = []
        for card in cards:
            ids = [(t[1], t[2]) for t in card["ticks"]]
            if not card["name"] or not ids:
                raise SurfaceError("NETWORK_SURFACE_PAGE_INCOMPLETE")
            if not any(t[0] == "Closed" and t[3] for t in card["ticks"]):
                found.append({"person_id": ids[0][0], "evidence_hash": ids[0][1], "name": card["name"], "reason": card["reason"]})
        return found[:limit]
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
    """None when the page is the canonical Ledger page for this job, else a fixed reason."""
    if page.get("archived") or page.get("in_trash"):
        return "page_gone"
    parent = page.get("parent") or {}
    parent_source = str(parent.get("data_source_id") or "").replace("-", "").lower()
    if not parent_source or parent_source != str(source_id or "").replace("-", "").lower().replace("collection://", ""):
        return "wrong_data_source"
    key = "".join(t.get("plain_text", "") for t in ((page.get("properties") or {}).get("Stable Job Key") or {}).get("rich_text") or [])
    if key != job_key:
        return "key_mismatch"
    if not any(body.is_marker(b, job_key) for b in top or []):
        return "no_description_marker"
    return None


def _blank(block):
    return block.get("type") == "paragraph" and not _plain(block).strip() and not hidden.refs(block)


def _description_parts(client, list_id):
    columns = [b for b in children(client, list_id) if b.get("type") == "column"]
    if len(columns) != 2:
        return None
    left = children(client, columns[0]["id"])
    text = [(b.get("type"), _plain(b)) for b in left]
    for toggle in (b for b in left if b.get("type") == "toggle"):
        text += [(b.get("type"), _plain(b)) for b in children(client, toggle["id"])]
    return text, children(client, columns[1]["id"])


def remove_duplicate_descriptions(client, page_id, top, key, live):
    """Remove V7's own repeated job description from a Ledger row: the top-most one is kept; an extra goes ONLY
    when provably V7's identical write, otherwise it is left and counted. Live only. -> "removed" |
    "would_remove" | "left" | "none"."""
    pairs = [(b, top[i + 1]) for i, b in enumerate(top[:-1]) if body.is_marker(b, key) and top[i + 1].get("type") == "column_list"]
    if len(pairs) < 2:
        return "none"
    kept = _description_parts(client, pairs[0][1]["id"])
    if kept is None:
        return "left"
    gone = []
    for marker, layout in pairs[1:]:
        parts = _description_parts(client, layout["id"])
        if parts is None or parts[0] != kept[0] or not all(_blank(b) for b in parts[1]):
            return "left"
        gone += [marker["id"], layout["id"]]
    if not live:
        return "would_remove"
    for block_id in reversed(gone):
        client.call("DELETE", f"/blocks/{quote(block_id, safe='')}")
    after = children(client, page_id)
    if [b["id"] for b in after] != [b["id"] for b in top if b["id"] not in gone]:
        raise SurfaceError("NETWORK_DESCRIPTION_READBACK_MISMATCH")
    return "removed"


def _keep_nonempty(client, column_id, owned_ids):
    if all(k["id"] in owned_ids for k in children(client, column_id)):
        client.call_once("PATCH", f"/blocks/{quote(column_id, safe='')}/children", {"children": [_para([])]})


def _drop_blanks(client, column_id):
    kids = children(client, column_id)
    if any(not _blank(k) for k in kids):
        for k in kids:
            if _blank(k):
                client.call("DELETE", f"/blocks/{quote(k['id'], safe='')}")


def _others(top, owned):
    ids = {o["id"] for o in owned}
    return [b["id"] for b in top if b["id"] not in ids]


def _verify(client, page_id, others, want, count):
    """Proves the page holds exactly `count` owned blocks with the wanted hash and no other top-level block
    changed."""
    top, owned = read_page(client, page_id, deep=False)
    if _others(top, owned) != others or len(owned) != count or any(o["hash"] != want for o in owned):
        raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")
    return top, owned


def apply(client, page_id, top, owned, desired_leads, live):
    """-> "unchanged" | "created" | "replaced" | "converged" | "removed" | "none" (prefixed "would_" when not
    live). A replacement appends and PROVES the new block first, then retires the old one."""
    want = set_hash(desired_leads)
    others = _others(top, owned)
    home = getattr(top, "right", None) or page_id
    matching = [o for o in owned if o["hash"] == want and o.get("parent", home) == home] if desired_leads else []
    if not desired_leads:
        action = "removed" if owned else "none"
    elif matching and len(owned) == 1:
        action = "unchanged"
    elif matching:
        action = "converged"
    else:
        action = "replaced" if owned else "created"
    if action in ("none", "unchanged"):
        return action
    if not live:
        return "would_" + action
    keep = matching[-1] if matching else None
    column = getattr(top, "right", None)
    if not desired_leads:
        retire = list(owned)
        if column and owned:
            _keep_nonempty(client, column, {o["id"] for o in owned})
    elif keep:
        retire = [o for o in owned if o is not keep]
    else:
        carried = {}
        for old in owned:
            for kind, person_id, _evidence, checked in old["ticks"]:
                if checked and kind in STATUS + (SAME_TEXT,):
                    carried.setdefault(person_id, {})[kind] = True
        write_cards(client, column or page_id, desired_leads, want, carried)
        _, now_owned = read_page(client, page_id, deep=False)
        fresh = [o for o in now_owned if o["id"] not in {x["id"] for x in owned}]
        if len(fresh) != 1 or fresh[0]["hash"] != want or [o["id"] for o in now_owned if o["id"] in {x["id"] for x in owned}] != [o["id"] for o in owned]:
            # the old block is still there: nothing known-good was retired
            raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")
        retire = list(owned)
        if column:
            _drop_blanks(client, column)
    for old in retire:
        client.call("DELETE", f"/blocks/{quote(old['id'], safe='')}")
    _verify(client, page_id, others, want, 1 if desired_leads else 0)
    return action
