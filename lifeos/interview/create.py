"""Creates the Interview opportunity and round pages: one page per evidence pass, no write retries."""

import hashlib
import json
import re
from collections import Counter
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
from lifeos.platform import ledger_claim
from lifeos.platform.names import core, norm, same_company, same_role, split_title
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.platform.runtime import DeadlineExceeded
from . import notion, view as views
from .identity import creation_eligibility, resolve_child, resolve_parent, valid_date
from .models import Ownership, RoundQuery, Scan, State
from .template import (
    DECISION_LINE,
    DECISION_MAX,
    DONE_LINE,
    DONE_MAX,
    HAND_MAX,
    HOLD_LINE,
    NEXT_LINE,
    PAGE_ICONS,
    REPAIRED_LINE,
    SENT_LINE,
    SENT_MAX,
    UNSENT_LINE,
    machine_part,
    opportunity_page,
    render_line,
    repaired_line,
    round_page,
    transcript_lines,
)

IDENTITY_PREFIX = "v7-interview-identity:1;"


def identity_text(query):
    data = {
        "interview_date": query.interview_date,
        "interviewer": query.interviewer.strip() if query.interviewer else None,
        "ordinal": query.ordinal,
    }
    return IDENTITY_PREFIX + json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def parse_identity(value):
    try:
        if not value.startswith(IDENTITY_PREFIX):
            return None
        data = json.loads(value[len(IDENTITY_PREFIX) :])
        if list(data) != ["interview_date", "interviewer", "ordinal"]:
            return None
        if not valid_date(data["interview_date"]) and not (
            data["interview_date"] is None and data["ordinal"] == 1 and type(data["ordinal"]) is int
        ):
            return None
        who, ordinal = data["interviewer"], data["ordinal"]
        if who is not None and (not isinstance(who, str) or not who.strip()):
            return None
        if ordinal is not None and (type(ordinal) is not int or ordinal < 0):
            return None
        if who is None and ordinal is None:
            return None
        if value != IDENTITY_PREFIX + json.dumps(data, separators=(",", ":"), ensure_ascii=False):
            return None
        return data
    except (ValueError, TypeError, KeyError):
        return None


def adopt(client, environ, parent, parent_query, query, live, context, out):
    """Adopt a hand-made page whose first line is `LIFE OS: adopt`: insert the sections, then remove that line."""
    if query.confirmed_round is not True:
        return "pursuit_unconfirmed"
    blocks = notion.children(client, parent.page_id, [notion.MAX_CALLS], context)
    labels = [notion.text(b) for b in blocks]
    if (
        labels.count(notion.ADOPT) != 1
        or notion.JIM_NOTES in labels
        or notion.FOLD in labels
        or any("v7-interview" in label for label in labels)
    ):
        return "adopt_conflict"
    before = notion.human_tail(client, parent.page_id, blocks)
    out["writes_planned"] = 1
    if not live:
        return "adopt_allowed"
    context.require_time()
    kids = notion.child_scan(
        client, parent.page_id, context, legacy=notion.is_legacy_company(environ, parent_query.company)
    )
    if not kids.complete:
        return "child_scan_incomplete"
    current = _view(client, environ, parent_query, query, environ["HIRING_PIPELINE_PAGE_ID"].strip(), kids, today())
    sections = rendered(current, adopted=today(), tail=2)[0]
    try:
        notion.write_tree(client, parent.page_id, sections, after=blocks[0]["id"])
    except NotionError:
        return "write_failed"
    out["writes"] = out["readback_failed"] = 1
    try:
        notion.delete_block(client, blocks[0]["id"])
    except NotionError:
        return "write_failed"
    return _sections_readback(client, parent.page_id, before, out, "parent_adopted", current, sections)


def begin_adopt(client, page_id, live, out, context=None):
    """Put one adopt line at the top of a hand-made page and prove every existing block is unchanged."""
    blocks = notion.children(client, page_id, [notion.MAX_CALLS], context)
    labels = [notion.text(b) for b in blocks]
    if (
        not blocks
        or notion.ADOPT in labels
        or notion.FOLD in labels
        or notion.JIM_NOTES in labels
        or any("v7-interview" in label for label in labels)
    ):
        return "adopt_line_conflict"
    before = notion.human_tail(client, page_id, [{}] + blocks)
    out["writes_planned"] = 1
    if not live:
        return "adopt_line_allowed"
    if context:
        context.require_time()
    try:
        client.call(
            "PATCH",
            f"/blocks/{page_id}/children",
            {"children": [notion.paragraph(notion.ADOPT)], "position": {"type": "start"}},
        )
    except NotionError:
        return "write_failed"
    out["writes"] = out["readback_failed"] = 1
    after = notion.children(client, page_id, [notion.MAX_CALLS], context)
    if (
        len(after) != len(blocks) + 1
        or notion.text(after[0]) != notion.ADOPT
        or [b.get("id") for b in after[1:]] != [b.get("id") for b in blocks]
    ):
        return "readback_layout_mismatch"
    if notion.human_tail(client, page_id, after) != before:
        return "readback_protected_changed"
    out["readback_failed"], out["readback_ok"] = 0, 1
    return "adopt_line_added"


def cause_of(error):
    text = str(error)
    if isinstance(error, NotionError) and re.fullmatch(r"[A-Z0-9_]{3,60}", text):
        return text.lower()
    return type(error).__name__.lower()


def today():
    return datetime.now(ZoneInfo("America/Chicago")).date().isoformat()


def rendered(current, adopted=None, tail=0):
    """(blocks, source): the machine region plus the first `tail` notes blocks (None: all), and its digest."""
    draft = opportunity_page(current, today(), adopted=adopted)
    source = hashlib.sha256(json.dumps(machine_part(draft), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[
        :16
    ]
    blocks = opportunity_page(current, today(), source, adopted=adopted)
    machine = machine_part(blocks)
    return (blocks if tail is None else machine + blocks[len(machine) :][:tail]), source


RENDER = re.compile(r"v7-render:1;job=(.+);source=([0-9a-f]{16});page=([0-9a-f]{16}|pending)")
ORIGIN = re.compile(r"https://[A-Za-z0-9.-]+(:\d{1,5})?/?")


def _view(client, environ, *args, **kwargs):
    base = (environ.get("V7_CLOSE_BASE_URL") or "").strip()
    return replace(
        views.opportunity(client, *args, **kwargs), close_base=base.rstrip("/") if ORIGIN.fullmatch(base) else None
    )


def done_lines(lines):
    found = {}
    for block in lines:
        m = DONE_LINE.fullmatch(notion.text(block)) if block.get("type") == "paragraph" else None
        if m and m.group(1) not in found:
            found[m.group(1)] = m.group(2)
    return tuple(found.items())[-DONE_MAX:]


def decision_lines(lines):
    return tuple(
        t for t in (notion.text(b) for b in lines if b.get("type") == "paragraph") if DECISION_LINE.fullmatch(t)
    )[-DECISION_MAX:]


def hand_lines(lines):
    found = [
        t
        for t in (notion.text(b) for b in lines if b.get("type") == "paragraph")
        if HOLD_LINE.fullmatch(t) or NEXT_LINE.fullmatch(t) or SENT_LINE.fullmatch(t) or UNSENT_LINE.fullmatch(t)
    ]
    hand = [i for i, t in enumerate(found) if not (SENT_LINE.fullmatch(t) or UNSENT_LINE.fullmatch(t))][-HAND_MAX:]
    sent = [i for i, t in enumerate(found) if SENT_LINE.fullmatch(t)][-SENT_MAX:]
    unsent = [i for i, t in enumerate(found) if UNSENT_LINE.fullmatch(t)][-SENT_MAX:]
    return tuple(found[i] for i in sorted(hand + sent + unsent))


def _undone(nodes):
    return [
        {**n, "children": _undone(n["children"])} if "children" in n else n
        for n in nodes
        if not any(
            p.fullmatch(n.get("text", ""))
            for p in (DONE_LINE, DECISION_LINE, HOLD_LINE, NEXT_LINE, SENT_LINE, UNSENT_LINE)
        )
    ]


def _clock():
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%I:%M %p").lstrip("0")


NOTHING = "No text to copy: the edit emptied, added, removed or moved a block."


def _lines(nodes):
    out = []
    for node in nodes:
        out += [t for t in [node.get("text", ""), *node.get("cells", [])] if t.strip()]
        out += _lines(node.get("children", []))
    return out


def _differing(shown, fresh):
    left, out = Counter(_lines(fresh)), []
    for line in _lines(shown):
        if left[line] > 0:
            left[line] -= 1
        elif not (line.startswith("v7-render:") or REPAIRED_LINE.fullmatch(line)):
            out.append(line)
    return out


def machine_region(client, page_id, context=None):
    """(machine blocks, fold lines, top blocks): everything before the notes heading, minus a trailing Preserved
    toggle."""
    tops = notion.children(client, page_id, [notion.MAX_CALLS], context)
    heads = [
        i for i, b in enumerate(tops) if b.get("type", "").startswith("heading") and notion.text(b) in notion.NOTES
    ]
    if len(heads) != 1 or not notion.is_fold(tops[0]):
        return None, [], tops
    body = tops[: heads[0]]
    if len(body) > 1 and notion.is_preserved(body[-1]):
        body = body[:-1]
    return body, notion.children(client, tops[0]["id"], [notion.MAX_CALLS], context), tops


def prefold_region(client, page_id, context=None):
    """(machine blocks, [marker], top blocks) of a page with the plain, unfolded marker; else machine blocks None."""
    tops = notion.children(client, page_id, [notion.MAX_CALLS], context)
    heads = [
        i for i, b in enumerate(tops) if b.get("type", "").startswith("heading") and notion.text(b) in notion.NOTES
    ]
    if (
        len(heads) != 1
        or not tops
        or tops[0].get("type") != "paragraph"
        or not notion.is_marker(notion.text(tops[0]), "opportunity")
        or notion.is_fold(tops[0])
    ):
        return None, [], tops
    return tops[: heads[0]], [tops[0]], tops


def _pages(tops):
    return [b["id"] for b in tops if b.get("type") == "child_page"]


def _fields(nodes):
    return [
        {**{k: v for k, v in n.items() if k not in ("icon", "children")}, "children": _fields(n.get("children", []))}
        for n in nodes
        if not n.get("text", "").startswith("v7-render:")
    ]


def finalize(client, page_id, current, sections, before=None):
    machine, lines, tops = machine_region(client, page_id)
    renders = [b for b in lines if RENDER.fullmatch(notion.text(b))]
    if machine is None or len(renders) != 1 or not lines or not notion.is_marker(notion.text(lines[0]), "opportunity"):
        return "readback_marker_missing"
    budget = [notion.TREE_MAX_CALLS]
    nodes = notion.region_nodes(client, machine, budget, skip={renders[0]["id"]})
    want = notion.as_nodes(machine_part(sections))
    if notion.outline(nodes, icons=False) != notion.outline(want, icons=False):
        return "readback_layout_mismatch"
    if _fields(nodes) != _fields(want):
        return "readback_content_mismatch"
    job, source, _ = RENDER.fullmatch(notion.text(renders[0])).groups()
    notion.set_text(
        client, renders[0]["id"], "paragraph", rich_text(render_line(job, source, notion.tree_digest(_undone(nodes))))
    )
    if before is not None and (notion.human_tail(client, page_id) != before[0] or _pages(tops) != before[1]):
        return "readback_protected_changed"
    return "readback_ok"


def refresh(client, page_id, current, live, out, context=None, migrate=False):
    """Bring a page's machine region up to date with its view. Only a region this writer wrote is replaced; a human
    edit is preserved, then repaired; anything unrecognized stops with a code."""
    machine, lines, tops = machine_region(client, page_id, context)
    prefold = False
    if machine is None and migrate:
        machine, lines, tops = prefold_region(client, page_id, context)
        prefold = machine is not None
    if (
        machine is None
        or not lines
        or not notion.is_marker(notion.text(lines[0]), "opportunity")
        or "child_page" in [b.get("type") for b in machine]
    ):
        return "parent_unrecognized"
    current = replace(
        current, done=done_lines(lines), decisions=decision_lines(lines), hand=hand_lines(lines), page_id=page_id
    )
    found = [m for m in (RENDER.fullmatch(notion.text(b)) for b in lines) if m]
    shown, edited = [], False
    if found:
        job, previous, page = found[0].groups()
        if len(found) != 1 or job != (current.stable_job_key or "none"):
            return "identity_mismatch"
        skip = {b["id"] for b in lines if RENDER.fullmatch(notion.text(b))}
        shown = notion.region_nodes(client, machine, [notion.TREE_MAX_CALLS], skip)
        edited = page != notion.tree_digest(_undone(shown))
        current = replace(current, repaired=next((t for t in _lines(shown) if REPAIRED_LINE.fullmatch(t)), ""))
    elif not (
        migrate
        and (prefold or (len(machine) > 1 and notion.text(machine[1]).startswith("Canonical opportunity parent")))
    ):
        return "parent_legacy_layout"
    if edited:
        current = replace(current, repaired=repaired_line(_clock()))
    sections, source = rendered(current)
    if found and previous == source and not edited:
        return "parent_unchanged"
    before = (notion.human_tail(client, page_id), _pages(tops))
    out["writes_planned"] = 1
    if not live:
        return "refresh_allowed"
    if context:
        context.require_time()
    if edited:
        toggle = tops[len(machine)] if len(tops) > len(machine) and notion.is_preserved(tops[len(machine)]) else None
        try:
            notion.preserve(
                client,
                page_id,
                toggle,
                _differing(shown, notion.as_nodes(machine_part(sections))) or [NOTHING],
                f"Preserved {_clock()} CT",
                after=machine[-1]["id"],
            )
        except NotionError:
            return "preserve_failed"
        out["writes"] = out["readback_failed"] = 1
    try:
        notion.write_tree(client, page_id, sections, after=machine[-1]["id"])
        out["writes"] = out["readback_failed"] = 1
        client.call("PATCH", f"/pages/{page_id}", {"icon": PAGE_ICONS["opportunity"]})
        for block in machine:
            notion.delete_block(client, block["id"])
        heading = next(
            b
            for b in tops[len(machine) :]
            if b.get("type", "").startswith("heading") and notion.text(b) in notion.NOTES
        )
        if notion.text(heading) != notion.JIM_NOTES:
            notion.set_text(client, heading["id"], heading["type"], rich_text(notion.JIM_NOTES))
    except NotionError:
        return "write_failed"
    check = finalize(client, page_id, current, sections, before)
    if check != "readback_ok":
        return check
    out["readback_failed"], out["readback_ok"] = 0, 1
    if edited:
        out["repaired_edits"] = 1
    return "parent_refreshed"


CARRIED = "from the old notes page"


def carried(block):
    return CARRIED in notion.text(block)


TRANSCRIPT, ACCEPTED = "Full Transcript", "v7-transcript:1;accepted="
TRANSCRIPT_MARKER = "v7-transcript:1;"


def transcript_state(client, round_id):
    """(state, page id) of the round's transcript page: absent, unverified, accepted or ambiguous."""
    found = []
    for b in notion.children(client, round_id, [notion.MAX_CALLS]):
        if b.get("type") != "child_page":
            continue
        first = notion.children(client, b["id"], [notion.MAX_CALLS])[:1]
        if first and notion.text(first[0]).startswith(TRANSCRIPT_MARKER):
            found.append((b["id"], notion.text(first[0])))
    if len(found) != 1:
        return ("absent" if not found else "ambiguous"), None
    return ("accepted" if found[0][1].startswith(ACCEPTED) else "unverified"), found[0][0]


def link_transcript(client, round_id, live, out):
    state, page = transcript_state(client, round_id)
    if state == "ambiguous":
        return "transcript_ambiguous"
    tops = notion.children(client, round_id, [notion.MAX_CALLS])
    lists = [b["id"] for b in tops if b.get("type") == "column_list"]
    if not tops or notion.text(tops[0]) != notion.ROUND_FOLD or len(lists) != 1:
        return "transcript_unrecognized"
    boxes = [b for b in notion.children(client, tops[0]["id"], [notion.MAX_CALLS]) if b.get("type") == "callout"]
    for column in notion.children(client, lists[0], [notion.MAX_CALLS])[1:]:
        boxes += [b for b in notion.children(client, column["id"], [notion.MAX_CALLS]) if b.get("type") == "callout"]
    want = {notion.text(line).split(":")[0]: line["paragraph"]["rich_text"] for line in transcript_lines(state, page)}
    found = [
        (b, want[notion.text(b).split(":")[0]])
        for box in boxes
        for b in notion.children(client, box["id"], [notion.MAX_CALLS])
        if b.get("type") == "paragraph" and notion.text(b).split(":")[0] in want
    ]
    if len(found) != 2:
        return "transcript_unrecognized"
    stale = [
        (b, rich)
        for b, rich in found
        if not carried(b) and notion._plain(b["paragraph"].get("rich_text")) != notion._plain(rich)
    ]
    if not stale:
        return "transcript_unchanged"
    out["writes_planned"] = 1
    if not live:
        return "transcript_link_allowed"
    for block, rich in stale:
        notion.set_text(client, block["id"], "paragraph", rich)
    out["writes"] = 1
    if link_transcript(client, round_id, False, {}) != "transcript_unchanged":
        return "readback_content_mismatch"
    out["readback_ok"] = 1
    return "transcript_linked"


def _sections_readback(client, page_id, before, out, done, current=None, sections=None):
    if notion.ownership(client, page_id, "opportunity") != Ownership.MACHINE:
        return "readback_marker_missing"
    if current is not None and finalize(client, page_id, current, sections) != "readback_ok":
        return "readback_layout_mismatch"
    if notion.protected_snapshot(client, page_id, "opportunity") is None:
        return "readback_protected_missing"
    if notion.human_tail(client, page_id) != before:
        return "readback_protected_changed"
    (
        out["readback_failed"],
        out["readback_ok"],
        out["parents_adopted" if done == "parent_adopted" else "parents_upgraded"],
    ) = 0, 1, 1
    return done


def upgrade(client, environ, parent, parent_query, query, live, context, out, blocks):
    if query.confirmed_round is not True:
        return "pursuit_unconfirmed"
    labels = [notion.text(b) for b in blocks]
    heads = [notion.text(b) for b in blocks if b.get("type", "").startswith("heading")]
    if (
        labels.count(notion.FOLD) > 1
        or notion.ADOPT in labels
        or any(label in notion.MARKERS.values() for label in labels[1:])
        or not all(label in heads for label in notion.PROTECTED)
    ):
        return "upgrade_unrecognized"
    before = notion.human_tail(client, parent.page_id, blocks)
    out["writes_planned"] = 1
    if not live:
        return "upgrade_allowed"
    context.require_time()
    kids = notion.child_scan(
        client, parent.page_id, context, legacy=notion.is_legacy_company(environ, parent_query.company)
    )
    if not kids.complete:
        return "child_scan_incomplete"
    current = _view(client, environ, parent_query, query, environ["HIRING_PIPELINE_PAGE_ID"].strip(), kids, today())
    sections = rendered(current, tail=2)[0][1:]
    try:
        notion.write_tree(client, parent.page_id, sections, after=blocks[0]["id"])
    except NotionError:
        return "write_failed"
    out["writes"] = out["readback_failed"] = 1
    return _sections_readback(client, parent.page_id, before, out, "parent_upgraded")


CALENDAR_REF = re.compile(r"^Calendar event: (Accepted invite|Cancelled) · ref ([0-9a-f]{12})$")
MEETING = "Meeting link / location: "


def round_calendar(client, page_id, budget=None):
    """(ref, cancelled) from a round page's Logistics callout, (None, False) when none."""
    return round_logistics(client, page_id, budget)[:2]


def round_logistics(client, page_id, budget=None):
    """(ref, cancelled, meeting): meeting is (join link, location) or None."""
    budget = budget or [notion.MAX_CALLS]
    lists = [b for b in notion.children(client, page_id, budget) if b.get("type") == "column_list"]
    columns = notion.children(client, lists[0]["id"], budget) if len(lists) == 1 else []
    right = notion.children(client, columns[1]["id"], budget) if len(columns) == 2 else []
    calls = [b for b in right if b.get("type") == "callout"]
    nodes = notion.children(client, calls[0]["id"], budget) if calls else []
    lines = [notion.text(b) for b in nodes]
    if not lines or lines[0] != "Logistics":
        raise NotionError("INTERVIEW_ROUND_LAYOUT")
    found = [m for m in (CALENDAR_REF.match(line) for line in lines) if m]
    ref, cancelled = (found[0].group(2), found[0].group(1) == "Cancelled") if len(found) == 1 else (None, False)
    held = [b for b, line in zip(nodes, lines) if line.startswith(MEETING)]
    value = notion.text(held[0])[len(MEETING) :] if len(held) == 1 and not carried(held[0]) else "Unavailable"
    join = next(iter(_links(held[0])), None) if len(held) == 1 else None
    place = value.removeprefix("Join link ↗").removeprefix(" · ") if join else value
    return ref, cancelled, (join, place or None) if join or (place and place != "Unavailable") else None


def _links(block):
    return [
        ((p.get("text") or {}).get("link") or {}).get("url") or p.get("href")
        for p in (block.get(block.get("type")) or {}).get("rich_text") or []
        if ((p.get("text") or {}).get("link") or {}).get("url") or p.get("href")
    ]


def _changed(old, new, path=()):
    if len(old) != len(new):
        raise NotionError("INTERVIEW_RENDER_SHAPE")
    out = []
    for index, (a, b) in enumerate(zip(old, new)):
        if a["type"] != b["type"]:
            raise NotionError("INTERVIEW_RENDER_SHAPE")
        if (notion.text(a), _links(a)) != (notion.text(b), _links(b)):
            out.append((path + (index,), a, b))
        out += _changed(notion.kids(a), notion.kids(b), path + (index,))
    return out


def _round_targets(client, page_id, edits, budget, tops=None):
    """[(block, old, new)] on the live page per changed render path, or None when the structure is unrecognisable."""
    tops = notion.children(client, page_id, budget) if tops is None else tops
    lists = [b for b in tops if b.get("type") == "column_list"]
    calls = [b for b in tops[1:] if b.get("type") == "callout"]
    if not tops or not notion.is_fold(tops[0]) or len(lists) != 1 or not calls:
        return None
    anchors, found = {0: tops[0], 1: calls[0], 2: lists[0]}, []
    for path, old, new in edits:
        if path[:2] == (2, 0) or path[0] not in anchors:
            return None
        block = anchors[path[0]]
        for index in path[1:]:
            below = notion.children(client, block["id"], budget)
            if index >= len(below):
                return None
            block = below[index]
        if block.get("type") != old["type"]:
            return None
        found.append((block, old, new))
    return found


def transition(client, parent_id, child_id, current, want, live, out, context=None):
    """Move one round page in place to the state a Calendar event gives it (`want`): dated, re-dated or cancelled.

    The page's event reference must equal the event's. Only blocks whose text differs change, the identity line
    last (the commit point); a human edit is preserved, then repaired. Live and Raw Notes never change."""
    fresh = notion.explicit_child(client, child_id)
    if (
        fresh is None
        or fresh.identity_valid is not True
        or not notion.same_notion_id(fresh.parent_id, parent_id)
        or not valid_date(want.interview_date)
        or (fresh.interviewer, fresh.ordinal) != (want.interviewer, want.ordinal)
    ):
        return "round_unconfirmable"
    budget = [notion.MAX_CALLS]
    ref, cancelled, joining = round_logistics(client, child_id, budget)
    if fresh.interview_date is None:
        if fresh.ordinal != 1 or want.cancelled or not want.calendar_ref:
            return "round_unconfirmable"
        source, kind = RoundQuery(True, None, fresh.interviewer, 1), "confirm"
    else:
        if not ref or ref != want.calendar_ref:
            return "round_unlinked"
        source = RoundQuery(
            True,
            fresh.interview_date,
            fresh.interviewer,
            fresh.ordinal,
            calendar_ref=ref,
            cancelled=cancelled,
            meeting=joining,
        )
        if want.cancelled and want.interview_date != fresh.interview_date:
            return "round_unconfirmable"
        kind = (
            "cancel"
            if want.cancelled
            else "reschedule"
            if want.interview_date != fresh.interview_date or cancelled
            else "details"
        )
    if source == want:
        return "round_unchanged"
    edits = _changed(
        round_page(current, source, parent_id, identity_text(source)),
        round_page(current, want, parent_id, identity_text(want)),
    )
    tops = notion.children(client, child_id, budget)
    targets = _round_targets(client, child_id, edits, budget, tops)
    lines = notion.machine_lines(client, child_id, 2)[0]
    if (
        targets is None
        or len(lines) < 2
        or lines[1].get("type") != "paragraph"
        or not notion.text(lines[1]).startswith(IDENTITY_PREFIX)
    ):
        return "machine_region_edited"
    if all(t[0]["id"] != lines[1]["id"] for t in targets) and notion.text(lines[1]) != identity_text(want):
        targets.append((lines[1], notion.paragraph(identity_text(source)), notion.paragraph(identity_text(want))))
    snapshot = next(b for b in tops[1:] if b.get("type") == "callout")
    kept = [t for t in targets if carried(t[0])]
    targets = [t for t in targets if not carried(t[0])]
    meeting_kept = any(notion.text(t[1]).startswith(MEETING) for t in kept)
    edited = [t for t in targets if notion.text(t[0]) not in (notion.text(t[1]), notion.text(t[2]))]
    before = notion.protected_snapshot(client, child_id, "round")
    if before is None:
        return "readback_protected_missing"
    out["writes_planned"] = 1
    if not live:
        return f"{kind}_allowed"
    if context:
        context.require_time()
    todo = sorted(
        [t for t in targets if (notion.text(t[0]), _links(t[0])) != (notion.text(t[2]), _links(t[2]))],
        key=lambda t: notion.text(t[1]).startswith(IDENTITY_PREFIX),
    )
    undo = []
    try:
        if edited:
            try:
                notion.preserve(
                    client,
                    child_id,
                    next((b for b in reversed(tops) if notion.is_preserved(b)), None),
                    [t for t in (notion.text(e[0]) for e in edited) if t.strip()] or [NOTHING],
                    f"Preserved {_clock()} CT",
                )
            except NotionError:
                return "preserve_failed"
            out["writes"] = out["readback_failed"] = 1
            marker = next(
                (b for b in notion.children(client, snapshot["id"], budget) if REPAIRED_LINE.fullmatch(notion.text(b))),
                None,
            )
            if marker:
                was = notion.writable(marker[marker["type"]]["rich_text"])
                notion.set_text(client, marker["id"], marker["type"], rich_text(repaired_line(_clock())))
                undo.append(lambda m=marker, w=was: notion.set_text(client, m["id"], m["type"], w))
            else:
                made = client.call(
                    "PATCH",
                    f"/blocks/{snapshot['id']}/children",
                    {"children": [notion.paragraph(repaired_line(_clock()))]},
                )["results"][0]["id"]
                undo.append(lambda i=made: notion.delete_block(client, i))
        for block, _, new in todo:
            was = notion.writable(block[block["type"]]["rich_text"])
            notion.set_text(client, block["id"], block["type"], new[new["type"]]["rich_text"])
            undo.append(lambda b=block, w=was: notion.set_text(client, b["id"], b["type"], w))
            out["writes"] = out["readback_failed"] = 1
    except NotionError:
        try:
            for step in reversed(undo):
                step()
        except NotionError:
            return "write_failed_partial"
        return "write_failed"
    after = notion.explicit_child(client, child_id)
    check = _round_targets(client, child_id, edits, [notion.MAX_CALLS])
    if (
        after is None
        or after.identity_valid is not True
        or (after.interview_date, after.interviewer, after.ordinal)
        != (want.interview_date, want.interviewer, want.ordinal)
        or check is None
        or any(notion.text(b) != notion.text(new) for b, _, new in check if not carried(b))
        or (
            edited
            and not any(
                REPAIRED_LINE.fullmatch(notion.text(b))
                for b in notion.children(client, snapshot["id"], [notion.MAX_CALLS])
            )
        )
        or round_logistics(client, child_id)
        != (want.calendar_ref, want.cancelled, None if want.cancelled or meeting_kept else want.meeting)
    ):
        return "readback_content_mismatch"
    if notion.protected_snapshot(client, child_id, "round") != before:
        return "readback_protected_changed"
    out["readback_failed"], out["readback_ok"] = 0, 1
    if edited:
        out["repaired_edits"] = 1
    return {
        "confirm": "round_confirmed",
        "reschedule": "round_rescheduled",
        "cancel": "round_cancelled",
        "details": "round_details",
    }[kind]


def _create_in_row(client, environ, source, row, parent_query, query, current, blocks, out, context, deferred):
    """Write a new pursuit's sections onto its Ledger row page, creating a Manual row under a claim if none. Stage is
    set last, so an interrupted run is finished by the next run."""
    company, role = parent_query.company.strip(), parent_query.role.strip()
    try:
        if row is None:
            claim_key, claimant = (
                query.stable_job_key or ledger_claim.role_key(company, role),
                ledger_claim.who("opportunity"),
            )
            try:
                state, _ = ledger_claim.claim(claim_key, claimant)
            except Exception:  # noqa: BLE001 - no claim means no create; a fixed code, never the exception text
                out["claim"] = "unavailable"
                return "write_failed"
            code, found = notion.ledger_row(client, source, company, role, query.stable_job_key)
            if code == "ledger_row_found":
                row_id = found["id"]
                if state == ledger_claim.WON:
                    ledger_claim.release(claim_key, claimant)
            elif code == "ledger_row_missing" and state == ledger_claim.WON:
                row_id = notion.create_ledger_row(client, source, company, role, query.stable_job_key)
                out["rows_created"] = 1
                ledger_claim.release(claim_key, claimant)
            else:
                out["claim"] = "held" if code == "ledger_row_missing" else "unreadable"
                return "write_failed"
        else:
            row_id = row["id"]
    except NotionError:
        return "write_failed"
    deferred.add(row_id)
    out["writes"] = out["readback_failed"] = 1
    context.require_time()
    original = notion.children(client, row_id, [notion.MAX_CALLS], context)
    try:
        notion.write_tree(client, row_id, blocks, start=bool(original))
    except NotionError:
        return "write_failed"
    check = notion.readback(client, row_id, row_id, "opportunity")
    if check == "readback_ok":
        check = finalize(client, row_id, current, blocks)
    if check != "readback_ok":
        return check
    if original:
        after = notion.children(client, row_id, [notion.MAX_CALLS], context)
        if [b.get("id") for b in after[-len(original) :]] != [b.get("id") for b in original] or [
            notion.text(b) for b in after[-len(original) :]
        ] != [notion.text(b) for b in original]:
            return "readback_protected_changed"
    if not row or row["stage"] in (None, "Found"):
        try:
            notion.set_stage(client, row_id, "Applied")
        except NotionError:
            return "write_failed"
    refreshed = resolve_parent(parent_query, notion.parents(client, environ, context))
    if refreshed.state != State.MATCH or not notion.same_notion_id(refreshed.page_id, row_id):
        return "readback_identity_mismatch"
    out["readback_failed"], out["readback_ok"] = 0, 1
    out["parents_created"] = 1
    return "parent_created"


def apply(client, environ, parent_query, query, live, context, deferred):
    out = {
        "writes_planned": 0,
        "writes": 0,
        "readback_ok": 0,
        "readback_failed": 0,
        "parents_created": 0,
        "parents_adopted": 0,
        "parents_upgraded": 0,
        "rounds_created": 0,
        "code": "pipeline_incomplete",
    }

    def finish(code):
        out["code"] = code
        return out

    try:
        context.require_time()
        target = notion.target_check(client, environ)
        if target != "target_ok":
            return finish(target)
        scan = notion.parents(client, environ, context)
        parent = resolve_parent(parent_query, scan)
        if parent.state == State.BLOCKED:
            return finish(parent.code)
        before, pipeline = None, environ["HIRING_PIPELINE_PAGE_ID"].strip()
        if query.refresh_only:
            if (
                parent.state != State.MATCH
                or notion.ownership(client, parent.page_id, "opportunity") != Ownership.MACHINE
            ):
                return finish("refresh_not_machine")
            if any(notion.same_notion_id(parent.page_id, key) for key in deferred):
                return finish("parent_exists")
            children = notion.child_scan(client, parent.page_id, context)
            if not children.complete or children.items:
                return finish("child_scan_incomplete" if not children.complete else "refresh_has_rounds")
            done = refresh(
                client,
                parent.page_id,
                _view(
                    client, environ, parent_query, query, environ["HIRING_PIPELINE_PAGE_ID"].strip(), children, today()
                ),
                live,
                out,
                context,
            )
            if done in ("parent_refreshed", "refresh_allowed"):
                deferred.add(parent.page_id)
            return finish(
                "parent_exists" if done in ("parent_unchanged", "parent_legacy_layout", "parent_unrecognized") else done
            )
        if parent.state == State.NOT_FOUND:
            company, role = parent_query.company.strip(), parent_query.role.strip()
            title = f"{company} — {role}"
            if not core(company) or not norm(role).strip() or split_title(title) != (company, role):
                return finish("identity_invalid")
            if query.confirmed_round is not True:
                return finish("pursuit_unconfirmed")
            source = (environ.get("NOTION_JOB_LEDGER_DATA_SOURCE_ID") or "").strip()
            if not source:
                return finish("ledger_config_missing")
            found, row = notion.ledger_row(client, source, company, role, query.stable_job_key, context)
            if found not in ("ledger_row_found", "ledger_row_missing"):
                return finish(found)
            if row and any(notion.same_notion_id(row["id"], key) for key in deferred):
                return finish("parent_exists")
            if row and not (same_company(row["company"], company) and same_role(row["role"], role)):
                return finish("ledger_row_name_mismatch")
            if row and (row["decision"] or (row["stage"] and row["stage"] not in ("Found",) + notion.LEDGER_ACTIVE)):
                return finish("ledger_row_closed")
            if row:
                labels = [notion.text(b) for b in notion.children(client, row["id"], [notion.MAX_CALLS], context)]
                if (
                    notion.FOLD in labels
                    or notion.ADOPT in labels
                    or notion.JIM_NOTES in labels
                    or any("v7-interview" in label for label in labels)
                ):
                    return finish("ledger_row_conflict")
            insertion = row["id"] if row else None
            kind, code = "opportunity", "parent_create_allowed"
            current = _view(client, environ, parent_query, query, pipeline, today=today())
            blocks = rendered(current, tail=None)[0]
        else:
            if any(notion.same_notion_id(parent.page_id, key) for key in deferred):
                return finish("parent_exists")
            owner = notion.ownership(client, parent.page_id, "opportunity")
            if owner == Ownership.ADOPT:
                code = adopt(client, environ, parent, parent_query, query, live, context, out)
                if code == "parent_adopted":
                    deferred.add(parent.page_id)
                return finish(code)
            if owner != Ownership.MACHINE:
                if owner == Ownership.HUMAN and query.explicit_child_page_id:
                    children = notion.child_scan(client, parent.page_id, context)
                    explicit = notion.explicit_child(client, query.explicit_child_page_id)
                    reference = resolve_child(parent, query, children, explicit)
                    if reference.state == State.MATCH:
                        out["matched"] = 1
                return finish("human_page" if owner == Ownership.HUMAN else "ownership_unknown")
            tops = notion.children(client, parent.page_id, [notion.MAX_CALLS], context)
            if not any(notion.text(b) in notion.NOTES and b.get("type", "").startswith("heading") for b in tops):
                code = upgrade(client, environ, parent, parent_query, query, live, context, out, tops)
                if code == "parent_upgraded":
                    deferred.add(parent.page_id)
                return finish(code)
            children = notion.child_scan(
                client, parent.page_id, context, legacy=notion.is_legacy_company(environ, parent_query.company)
            )
            explicit = (
                notion.explicit_child(client, query.explicit_child_page_id) if query.explicit_child_page_id else None
            )
            child = resolve_child(parent, query, children, explicit)
            code = creation_eligibility(target, parent, owner, child, children, query)
            current = _view(client, environ, parent_query, query, pipeline, children, today())
            held = [
                c
                for c in children.items
                if notion.same_notion_id(c.page_id, child.page_id or "") and c.identity_valid is True
            ]
            if (
                code == "child_match"
                and query.explicit_child_page_id
                and query.interview_date
                and query.calendar_ref
                and held
            ):
                deferred.add(parent.page_id)
                want = RoundQuery(
                    True,
                    query.interview_date,
                    held[0].interviewer,
                    held[0].ordinal,
                    calendar_ref=query.calendar_ref,
                    cancelled=query.cancelled,
                    meeting=query.meeting,
                )
                return finish(transition(client, parent.page_id, held[0].page_id, current, want, live, out, context))
            if code == "child_match":
                done = refresh(
                    client,
                    parent.page_id,
                    current,
                    live,
                    out,
                    context,
                    migrate=notion.is_legacy_company(environ, parent_query.company),
                )
                if done in ("parent_unchanged", "parent_legacy_layout", "parent_unrecognized"):
                    done = link_transcript(client, child.page_id, live, out)
                    done = (
                        done
                        if done in ("transcript_linked", "transcript_link_allowed", "readback_content_mismatch")
                        else "parent_unchanged"
                    )
                if done in ("parent_refreshed", "refresh_allowed", "transcript_linked", "transcript_link_allowed"):
                    deferred.add(parent.page_id)
                return finish(
                    code if done in ("parent_unchanged", "parent_legacy_layout", "parent_unrecognized") else done
                )
            if code != "create_allowed":
                return finish(code)
            deferred.add(parent.page_id)
            if parse_identity(identity_text(query)) is None:
                return finish("round_identity_incomplete")
            kind, insertion = "round", parent.page_id
            title = f"Interview {query.ordinal}" if query.ordinal else f"Interview — {query.interview_date}"
            blocks = round_page(current, query, insertion, identity_text(query))
        out["writes_planned"] = 1
        if not live:
            return finish(code)
        context.require_time()
        if kind == "round":
            before = notion.protected_snapshot(client, insertion, "opportunity")
            if before is None:
                return finish("readback_protected_missing")
        context.require_time()
        if kind == "opportunity":
            return finish(
                _create_in_row(
                    client, environ, source, row, parent_query, query, current, blocks, out, context, deferred
                )
            )
        try:
            page_id = notion.write_tree(
                client, insertion, blocks, title=title, icon=PAGE_ICONS[kind], template=notion.FULL_WIDTH
            )
        except NotionError:
            return finish("write_failed")
        out["writes"] = 1
        out["readback_failed"] = 1
        check = notion.readback(client, page_id, insertion, kind)
        if check != "readback_ok":
            return finish(check)
        scan = notion.child_scan(client, insertion, context)
        if scan.complete and not any(notion.same_notion_id(c.page_id, page_id) for c in scan.items):
            created = notion.explicit_child(client, page_id)
            scan = Scan(scan.items + ((created,) if created else ()), True)
        refreshed = resolve_child(parent, query, scan)
        if notion.protected_snapshot(client, insertion, "opportunity") != before:
            return finish("readback_protected_changed")
        if refreshed.state != State.MATCH or not notion.same_notion_id(refreshed.page_id, page_id):
            return finish("readback_identity_mismatch")
        out["readback_failed"], out["readback_ok"] = 0, 1
        out["rounds_created"] = 1
        return finish("round_created")
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError, AttributeError) as error:
        out["cause"] = cause_of(error)
        return finish("readback_unreadable" if out["writes"] else "pipeline_incomplete")
