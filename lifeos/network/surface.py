"""NET-2.3 (D160): the one machine-owned "Network Leads (n)" block on a Job Ledger page. Platform only: Notion calls arrive through the caller's client.

Ownership is the marker, never position: a top-level toggle whose first child paragraph starts with `v7-net:1` and a short hash of the lead set. Nothing else on the page is read for
change or written. Each lead is a paragraph (name, company, title, observation date, a profile link, a fixed reason) plus a Dismiss tick, and a possible match also a Same company tick.
The ticks carry `<person id>:<evidence hash>`, so a tick can only ever confirm the exact evidence that was shown. Fixed reason templates only; nothing about closeness, willingness to
refer or hiring authority is ever generated, and no outreach text is drafted."""
import hashlib
import re
import time
from datetime import date
from urllib.parse import quote, unquote

from lifeos.platform import ledger_body as body
from lifeos.network import match
from lifeos.platform import hidden
from lifeos.platform.notion_client import NotionError, rich_text

MARK = "v7-net:1"                                                       # the older block (a toggle that showed its marker and ids); still read, and retired once, so a replaced page loses nothing
MARK2 = "net:2"                                                         # the current block (a callout): its marker and every tick id ride in a hidden link, never in visible text
DESCRIPTION_MARK = "v7-jd:1"
TICK = re.compile(r"^(Dismiss|Same company) · (\d+):([0-9a-f]{12})$")
TICK2 = re.compile(r"^(Dismiss|Same company|Contacted|Referral|Closed) (\d+):([0-9a-f]{12})$")
BASE_URL = "https://www.linkedin.com/in/"
TITLE = "LinkedIn connections"
REACH = "Reach Out ↗"
DISMISS_TEXT, SAME_TEXT = "Not relevant · dismiss", "Same company"
STATUS = ("Contacted", "Referral", "Closed")                              # the approved card's "Referral status" ticks (Jim, Oct 28: the UI PROOF "LinkedIn Contacts (3 of 5)" page)
PENDING = "building"                                                    # the hash a card block carries until every card is written: a block cut short never reads as the wanted one
PROFILE = "LinkedIn profile ↗"
ICON = {"type": "external", "external": {"url": "https://upload.wikimedia.org/wikipedia/commons/c/ca/LinkedIn_logo_initials.png"}}   # Jim, Oct 28: the LinkedIn logo, not Notion's default bulb (a callout must carry an icon)


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
    return hashlib.sha256(("|".join(f"{l['person_id']}:{l['evidence_hash']}" for l in leads) + ("|ms1" if link else "") + "|ui5").encode()).hexdigest()[:12] if leads else ""


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


def _todo(ref, text):
    return {"object": "block", "type": "to_do", "to_do": {"rich_text": [hidden.run(ref)] + rich_text(text), "checked": False}}


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
    title = [hidden.run(f"{MARK2} {set_hash(leads, link)}")] + _bold(TITLE)
    return {"object": "block", "type": "callout", "callout": {"rich_text": title, "icon": {"type": "emoji", "emoji": "🔗"}, "color": "gray_background", "children": kids}}


def _employee(lead):
    """The card's second line: role and relation, fixed words only."""
    if lead["tier"] == match.CURRENT:
        text = (f"{lead['title']} · " if lead["title"] else "") + "Current employee"
    elif lead["tier"] == match.PREVIOUS:
        text = "Former employee"
    else:
        text = (f"{lead['title']} · " if lead["title"] else "") + "Same company?"
    return text + (" · verify first" if lead["freshness"] == "STALE" else "")


def card_columns(lead, carried=None):
    """One contact card's column_list (the approved card: Jim, Oct 28): left 65% bold name, role and relation, profile link and contact state; right 35% an empty "Referral status" toggle (its
    ticks go in by a second request, Notion's two-level limit). carried: {kind: checked} from the block being replaced, so Jim's ticks survive a rewrite."""
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
    """Writes the approved "LinkedIn connections" block into parent_id: an outer callout, one inner callout per contact, each holding its card. The outer title carries the PENDING hash until the
    last card is in, then the wanted hash, so an interrupted write is replaced next run and never accepted. carried: {person_id: {kind: checked}}. -> the outer block id."""
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
    """One append. A 429 means Notion did not apply it, so only that refusal is retried (8 slices share one integration's rate limit)."""
    for attempt in range(5):
        try:
            return client.call_once("PATCH", path, body)
        except NotionError as error:
            if str(error) != "NOTION_HTTP_429" or attempt == 4:
                raise
            sleep(2 + 2 * attempt)


def _append_last(client, parent_id, block):
    """Appends ONE block to parent_id and returns its id: from Notion's own reply (the appended block), else proven by reading the parent back (its last child must be of that type)."""
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


def _cards(client, inner):
    """The approved block's cards -> [{"name", "reason", "ticks": [(kind, person_id, evidence_hash, checked)]}] in page order. Reads each card's columns and its Referral status toggle."""
    out = []
    for card in inner:
        if card.get("type") != "callout":
            continue
        lists = [b for b in children(client, card["id"]) if b.get("type") == "column_list"]
        columns = [b for b in children(client, lists[0]["id"]) if b.get("type") == "column"] if len(lists) == 1 else []
        if len(columns) != 2:
            continue                                                    # a card a cut-short write left unfinished carries no lead and no tick; its block is replaced (its hash is PENDING)
        lines = [_plain(b) for b in children(client, columns[0]["id"]) if b.get("type") == "paragraph"]
        ticks = []
        for toggle in [b for b in children(client, columns[1]["id"]) if b.get("type") == "toggle"]:
            ticks += [t for t in (_tick(c) for c in children(client, toggle["id"])) if t]
        out.append({"name": lines[0].strip() if lines else "", "reason": lines[1].strip() if len(lines) > 1 else "", "ticks": ticks})
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
    for ref in hidden.refs(child):
        found = TICK2.match(ref)
        if found:
            return found.group(1), int(found.group(2)), found.group(3), checked
    found = TICK.match(_plain(child))
    return (found.group(1), int(found.group(2)), found.group(3), checked) if found else None


def _owned_head(client, block):
    """(hash, inner children) when the block is a Network Leads block of either generation, else None. A callout or toggle is only opened when its own title (new) or, for a toggle, its first child (old) says so."""
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
    """The page's top-level blocks, plus `right`: the id of the right-hand column of the two-column Ledger page (lifeos.platform.ledger_body), None for the older flat page."""
    right = None


def _right(client, top):
    """The right column's id on a two-column page, else None. Reads the column_list's children only when the page has exactly one."""
    lists = [b for b in top if b.get("type") == "column_list"]
    if len(lists) != 1:
        return None
    columns = [b for b in children(client, lists[0]["id"]) if b.get("type") == "column"]
    return columns[1]["id"] if len(columns) == 2 else None


def read_page(client, page_id, deep=True):
    """-> (top-level blocks, owned blocks oldest first). owned item = {"id", "parent", "hash", "ticks": [(kind, person_id, evidence_hash, checked)]}; `parent` is the page, or the right column of the two-column page.
    Normally one; an interrupted replacement can leave two, and the next run converges them (see apply). A page whose owned block cannot be read raises."""
    top = Top(children(client, page_id))
    top.right = _right(client, top)
    owned = []
    for parent, blocks in ((page_id, top), *(((top.right, children(client, top.right)),) if top.right else ())):
        for block in blocks:
            head = _owned_head(client, block)
            if head:
                cards = [c for c in head[1] if c.get("type") == "callout"]         # the approved card block: its ticks sit inside each card's Referral status toggle
                owned.append({"id": block["id"], "parent": parent, "hash": head[0], "ticks": [t for t in (_tick(c) for c in head[1]) if t] + ([t for card in _cards(client, cards) for t in card["ticks"]] if deep else [])})
    return top, owned


def leads_on_page(client, page_id, limit=10):
    """LIFE-21 (Mark as Sent): the people the page's owned block lists and Jim has not dismissed, in page order -> [{person_id, evidence_hash, name, reason}].
    Read only. No owned block -> []; two (an interrupted replacement) -> the newest, as apply converges; a lead without its Dismiss tick or an unreadable page raises."""
    inner, top = None, children(client, page_id)
    right = _right(client, top)
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
    if not top or not body.is_marker(top[0], job_key):
        return "no_description_marker"
    return None


def _blank(block):
    return block.get("type") == "paragraph" and not _plain(block).strip() and not hidden.refs(block)


def _keep_nonempty(client, column_id, owned_ids):
    """A column cannot be empty: before the last owned block leaves the right column, one blank paragraph is put there."""
    if all(k["id"] in owned_ids for k in children(client, column_id)):
        client.call_once("PATCH", f"/blocks/{quote(column_id, safe='')}/children", {"children": [_para([])]})


def _drop_blanks(client, column_id):
    """With a real block in the right column, its placeholder blank paragraph(s) go, so the block starts at the top of the column."""
    kids = children(client, column_id)
    if any(not _blank(k) for k in kids):
        for k in kids:
            if _blank(k):
                client.call("DELETE", f"/blocks/{quote(k['id'], safe='')}")


def _others(top, owned):
    ids = {o["id"] for o in owned}
    return [b["id"] for b in top if b["id"] not in ids]


def _verify(client, page_id, others, want, count):
    """Proves the page holds exactly `count` owned blocks, all with the wanted hash, and that no other top-level block changed."""
    top, owned = read_page(client, page_id, deep=False)                  # the hash proves the block; its cards' ticks are not needed here
    if _others(top, owned) != others or len(owned) != count or any(o["hash"] != want for o in owned):
        raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")
    return top, owned


def apply(client, page_id, top, owned, desired_leads, live, link=None):
    """-> "unchanged" | "created" | "replaced" | "converged" | "removed" | "none" (prefixed "would_" when not live). Only owned blocks are ever touched; the others are proven unchanged.

    A replacement is recoverable: the new block is appended and PROVEN first, and only then is the previously accepted block retired. If the run is interrupted anywhere in between, the page
    holds the old and the new block, the old one is still visible, and the next run (the new block's hash is the wanted one) retires the extras and converges. Duplicates never accumulate."""
    want = set_hash(desired_leads, link)
    others = _others(top, owned)
    home = getattr(top, "right", None) or page_id                          # where the block belongs: the right column of the two-column page, else the page
    matching = [o for o in owned if o["hash"] == want and o.get("parent", home) == home] if desired_leads else []      # a block with the right content in the wrong place is replaced, once
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
    column = getattr(top, "right", None)                                # the two-column page keeps its block in the right column
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
            raise SurfaceError("NETWORK_SURFACE_READBACK_MISMATCH")      # the old block is still there and still visible: nothing known-good was retired
        retire = list(owned)
        if column:
            _drop_blanks(client, column)
    for old in retire:
        client.call("DELETE", f"/blocks/{quote(old['id'], safe='')}")
    _verify(client, page_id, others, want, 1 if desired_leads else 0)
    return action
