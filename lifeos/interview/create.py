"""B2 vertical slice; one page per evidence pass, no write retries. LIFE-88: pages are the approved UI 3c / UI 4 trees, and a
UI 3c opportunity page's machine region is reconciled from its view when the sources change."""
import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from lifeos.platform.names import core, norm, split_title
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.platform.runtime import DeadlineExceeded
from . import notion, view as views
from .identity import creation_eligibility, resolve_child, resolve_parent, valid_date
from .models import Ownership, RoundQuery, State
from .template import PAGE_ICONS, machine_part, opportunity_page, render_line, round_page, transcript_lines

IDENTITY_PREFIX = "v7-interview-identity:1;"


def identity_text(query):
    data = {"interview_date": query.interview_date, "interviewer": query.interviewer.strip() if query.interviewer else None,
            "ordinal": query.ordinal}
    return IDENTITY_PREFIX + json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def parse_identity(value):
    try:
        if not value.startswith(IDENTITY_PREFIX):
            return None
        data = json.loads(value[len(IDENTITY_PREFIX):])
        if list(data) != ["interview_date", "interviewer", "ordinal"]:
            return None
        if not valid_date(data["interview_date"]) and not (data["interview_date"] is None and data["ordinal"] == 1 and type(data["ordinal"]) is int):
            return None                                                     # no date only for Interview 1 in Scheduling Pending
        who, ordinal = data["interviewer"], data["ordinal"]
        if who is not None and (not isinstance(who, str) or not who.strip()):
            return None
        if ordinal is not None and (type(ordinal) is not int or ordinal <= 0):
            return None
        if who is None and ordinal is None:
            return None
        if value != IDENTITY_PREFIX + json.dumps(data, separators=(",", ":"), ensure_ascii=False):
            return None
        return data
    except (ValueError, TypeError, KeyError):
        return None


def adopt(client, environ, parent, parent_query, query, live, context, out):
    """LIFE-46: one adoption of Jim's own page whose exact first line is `LIFE OS: adopt` (accepted UI 3b). The folded adopted
    marker (D236), the UI 3b sections and the Jim's notes heading go in right after that line, then the line itself is removed;
    nothing Jim wrote is edited, moved or deleted. Rounds come on a later run. A page that already has LIFE OS sections or a
    second marker is never adopted."""
    if query.confirmed_round is not True:
        return "pursuit_unconfirmed"
    blocks = notion.children(client, parent.page_id, [notion.MAX_CALLS], context)
    labels = [notion.text(b) for b in blocks]
    if (labels.count(notion.ADOPT) != 1 or notion.JIM_NOTES in labels or notion.FOLD in labels
            or any("v7-interview" in label for label in labels)):
        return "adopt_conflict"
    before = notion.human_tail(client, parent.page_id, blocks)
    out["writes_planned"] = 1
    if not live:
        return "adopt_allowed"
    context.require_time()
    kids = notion.child_scan(client, parent.page_id, context, legacy=notion.is_legacy_company(environ, parent_query.company))
    if not kids.complete:
        return "child_scan_incomplete"
    current = views.opportunity(client, parent_query, query, environ["HIRING_PIPELINE_PAGE_ID"].strip(), kids, today())
    sections = rendered(current, adopted=today(), tail=2)[0]
    try:
        notion.write_tree(client, parent.page_id, sections, after=blocks[0]["id"])
    except NotionError:
        return "write_failed"
    out["writes"] = out["readback_failed"] = 1
    try:
        notion.delete_block(client, blocks[0]["id"])                    # the adopt line Jim typed; the fold is now first
    except NotionError:
        return "write_failed"                                           # the adopt line stays; the next run reports adopt_conflict
    return _sections_readback(client, parent.page_id, before, out, "parent_adopted", current, sections)


def begin_adopt(client, page_id, live, out, context=None):
    """The batch conversion's first step for a hand-made opportunity page (Jim: convert all of them): put the one `LIFE OS: adopt` line Jim would have typed at the very top, then prove every block that was
    there is still there, in order, unchanged. Nothing is edited, moved or deleted; the normal adopt then converts the page. A page that already has any LIFE OS line, fold or notes heading is never touched."""
    blocks = notion.children(client, page_id, [notion.MAX_CALLS], context)
    labels = [notion.text(b) for b in blocks]
    if not blocks or notion.ADOPT in labels or notion.FOLD in labels or notion.JIM_NOTES in labels or any("v7-interview" in label for label in labels):
        return "adopt_line_conflict"
    before = notion.human_tail(client, page_id, [{}] + blocks)          # every block already on the page, child pages left out
    out["writes_planned"] = 1
    if not live:
        return "adopt_line_allowed"
    if context:
        context.require_time()
    try:
        client.call("PATCH", f"/blocks/{page_id}/children", {"children": [notion.paragraph(notion.ADOPT)], "position": {"type": "start"}})
    except NotionError:
        return "write_failed"
    out["writes"] = out["readback_failed"] = 1
    after = notion.children(client, page_id, [notion.MAX_CALLS], context)
    if len(after) != len(blocks) + 1 or notion.text(after[0]) != notion.ADOPT or [b.get("id") for b in after[1:]] != [b.get("id") for b in blocks]:
        return "readback_layout_mismatch"
    if notion.human_tail(client, page_id, after) != before:
        return "readback_protected_changed"
    out["readback_failed"], out["readback_ok"] = 0, 1
    return "adopt_line_added"


def cause_of(error):
    """Why a page's pass ended as pipeline_incomplete, as a fixed code: the Notion client's own code (upper-case letters, digits and underscores) or the exception's class name."""
    text = str(error)
    if isinstance(error, NotionError) and re.fullmatch(r"[A-Z0-9_]{3,60}", text):
        return text.lower()
    return type(error).__name__.lower()


def today():
    return datetime.now(ZoneInfo("America/Chicago")).date().isoformat()


def rendered(current, adopted=None, tail=0):
    """(blocks, source): the UI 3c machine region followed by the first `tail` blocks of the Jim's Notes region (None: all of
    it), and the digest of what the machine region says."""
    draft = opportunity_page(current, today(), adopted=adopted)
    source = hashlib.sha256(json.dumps(machine_part(draft), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    blocks = opportunity_page(current, today(), source, adopted=adopted)
    machine = machine_part(blocks)
    return (blocks if tail is None else machine + blocks[len(machine):][:tail]), source


RENDER = re.compile(r"v7-render:1;job=(.+);source=([0-9a-f]{16});page=([0-9a-f]{16}|pending)")


def machine_region(client, page_id, context=None):
    """(machine blocks, fold lines, top blocks): everything before the one Jim's Notes heading, which starts with the fold."""
    tops = notion.children(client, page_id, [notion.MAX_CALLS], context)
    heads = [i for i, b in enumerate(tops) if b.get("type", "").startswith("heading") and notion.text(b) in notion.NOTES]
    if len(heads) != 1 or not notion.is_fold(tops[0]):
        return None, [], tops
    return tops[:heads[0]], notion.children(client, tops[0]["id"], [notion.MAX_CALLS], context), tops


def prefold_region(client, page_id, context=None):
    """(machine blocks, [marker], top blocks) of a page V7 wrote before the marker was folded: its first block is the plain opportunity marker line (D235), everything before the one Jim's Notes heading is V7's.
    None when the page is not that shape."""
    tops = notion.children(client, page_id, [notion.MAX_CALLS], context)
    heads = [i for i, b in enumerate(tops) if b.get("type", "").startswith("heading") and notion.text(b) in notion.NOTES]
    if len(heads) != 1 or not tops or tops[0].get("type") != "paragraph" or not notion.is_marker(notion.text(tops[0]), "opportunity") or notion.is_fold(tops[0]):
        return None, [], tops
    return tops[:heads[0]], [tops[0]], tops


def _pages(tops):
    return [b["id"] for b in tops if b.get("type") == "child_page"]


def _fields(nodes):
    """What a read-back must reproduce: everything written except icons (Notion converts icon URLs to its own) and the render line."""
    return [{**{k: v for k, v in n.items() if k not in ("icon", "children")}, "children": _fields(n.get("children", []))}
            for n in nodes if not n.get("text", "").startswith("v7-render:")]


def finalize(client, page_id, current, sections, before=None):
    """Read the written machine region back: the approved structure, then its digest into the fold's render line, then Jim's
    region and the child pages unchanged."""
    machine, lines, tops = machine_region(client, page_id)
    renders = [b for b in lines if RENDER.fullmatch(notion.text(b))]
    if machine is None or len(renders) != 1 or not lines or not notion.is_marker(notion.text(lines[0]), "opportunity"):
        return "readback_marker_missing"
    budget = [notion.TREE_MAX_CALLS]
    nodes = notion.region_nodes(client, machine, budget, skip={renders[0]["id"]})
    want = notion.as_nodes(machine_part(sections))
    if notion.outline(nodes, icons=False) != notion.outline(want, icons=False):
        return "readback_layout_mismatch"
    if _fields(nodes) != _fields(want):                                  # every written value too: text, links by target, table cells
        return "readback_content_mismatch"
    job, source, _ = RENDER.fullmatch(notion.text(renders[0])).groups()
    notion.set_text(client, renders[0]["id"], "paragraph", rich_text(render_line(job, source, notion.tree_digest(nodes))))
    if before is not None and (notion.human_tail(client, page_id) != before[0] or _pages(tops) != before[1]):
        return "readback_protected_changed"
    return "readback_ok"


def refresh(client, page_id, current, live, out, context=None, migrate=False):
    """LIFE-88: bring a UI 3c page's machine region up to date with its view. Only a region this writer wrote (the fold's render
    line names this job and still matches a fresh read of the region) is replaced; a human edit there, another job's page or an
    unrecognized layout stops with a code and writes nothing. Jim's Notes and the child pages are digest-checked."""
    machine, lines, tops = machine_region(client, page_id, context)
    prefold = False
    if machine is None and migrate:                                     # a page V7 wrote with the plain marker line (no fold, no render line): its region is V7's, replaced once like a UI 3b page
        machine, lines, tops = prefold_region(client, page_id, context)
        prefold = machine is not None
    if machine is None or not lines or not notion.is_marker(notion.text(lines[0]), "opportunity") or "child_page" in [b.get("type") for b in machine]:
        return "parent_unrecognized"
    sections, source = rendered(current)
    found = [m for m in (RENDER.fullmatch(notion.text(b)) for b in lines) if m]
    if found:
        job, previous, page = found[0].groups()
        if len(found) != 1 or job != (current.stable_job_key or "none"):
            return "identity_mismatch"
        skip = {b["id"] for b in lines if RENDER.fullmatch(notion.text(b))}
        if page != notion.tree_digest(notion.region_nodes(client, machine, [notion.TREE_MAX_CALLS], skip)):
            return "machine_region_edited"
        if previous == source:
            return "parent_unchanged"
    elif not (migrate and (prefold or (len(machine) > 1 and notion.text(machine[1]).startswith("Canonical opportunity parent")))):
        return "parent_legacy_layout"                                   # UI 3b (D235-D237): migrated only when authorized
    before = (notion.human_tail(client, page_id), _pages(tops))
    out["writes_planned"] = 1
    if not live:
        return "refresh_allowed"
    if context:
        context.require_time()
    try:
        notion.write_tree(client, page_id, sections, after=machine[-1]["id"])
        out["writes"] = out["readback_failed"] = 1
        client.call("PATCH", f"/pages/{page_id}", {"icon": PAGE_ICONS["opportunity"]})
        for block in machine:
            notion.delete_block(client, block["id"])
        heading = tops[len(machine)]
        if notion.text(heading) != notion.JIM_NOTES:                    # a migrated UI 3b page takes the approved label
            notion.set_text(client, heading["id"], heading["type"], rich_text(notion.JIM_NOTES))
    except NotionError:
        return "write_failed"
    check = finalize(client, page_id, current, sections, before)
    if check != "readback_ok":
        return check
    out["readback_failed"], out["readback_ok"] = 0, 1
    return "parent_refreshed"


TRANSCRIPT, ACCEPTED = "Full Transcript", "v7-transcript:1;accepted="


def transcript_state(client, round_id):
    """(state, page id) of the round's one exact "Full Transcript" child page: absent, unverified, or accepted only when its first block is the
    accepted-import marker that a verified Granola import writes (not built yet: until then every existing page is unverified); two are ambiguous."""
    pages = [b["id"] for b in notion.children(client, round_id, [notion.MAX_CALLS])
             if b.get("type") == "child_page" and (b.get("child_page") or {}).get("title") == TRANSCRIPT]
    if len(pages) != 1:
        return ("absent" if not pages else "ambiguous"), None
    first = notion.children(client, pages[0], [notion.MAX_CALLS])[:1]
    return ("accepted" if first and notion.text(first[0]).startswith(ACCEPTED) else "unverified"), pages[0]


def link_transcript(client, round_id, live, out):
    """UI 4: bring the Source Health "Transcript" line and the Transcript & Recording "Full Transcript page" line up to date in place, then read back."""
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
    found = [(b, want[notion.text(b).split(":")[0]]) for box in boxes for b in notion.children(client, box["id"], [notion.MAX_CALLS])
             if b.get("type") == "paragraph" and notion.text(b).split(":")[0] in want]
    if len(found) != 2:
        return "transcript_unrecognized"
    stale = [(b, rich) for b, rich in found if notion._plain(b["paragraph"].get("rich_text")) != notion._plain(rich)]
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
    out["readback_failed"], out["readback_ok"], out["parents_adopted" if done == "parent_adopted" else "parents_upgraded"] = 0, 1, 1
    return done


def upgrade(client, environ, parent, parent_query, query, live, context, out, blocks):
    """LIFE-46 (Jim, Oct 20, option B): a LIFE OS opportunity page written before D235 (marker + Live Notes / Raw Notes /
    Interview Rounds) gets the approved UI 3c sections (LIFE-88; UI 3b before it) and the Jim's Notes heading once, inserted
    right after its marker.
    The old headings, anything written under them and the round pages stay where they are, now inside Jim's notes; nothing
    is edited, moved or deleted. Rounds come on a later run."""
    if query.confirmed_round is not True:
        return "pursuit_unconfirmed"
    labels = [notion.text(b) for b in blocks]
    heads = [notion.text(b) for b in blocks if b.get("type", "").startswith("heading")]
    if (labels.count(notion.FOLD) > 1 or notion.ADOPT in labels or any(label in notion.MARKERS.values() for label in labels[1:])
            or not all(label in heads for label in notion.PROTECTED)):
        return "upgrade_unrecognized"
    before = notion.human_tail(client, parent.page_id, blocks)
    out["writes_planned"] = 1
    if not live:
        return "upgrade_allowed"
    context.require_time()
    kids = notion.child_scan(client, parent.page_id, context, legacy=notion.is_legacy_company(environ, parent_query.company))
    if not kids.complete:
        return "child_scan_incomplete"
    current = views.opportunity(client, parent_query, query, environ["HIRING_PIPELINE_PAGE_ID"].strip(), kids, today())
    sections = rendered(current, tail=2)[0][1:]                        # the page keeps its own marker; no second fold
    try:
        notion.write_tree(client, parent.page_id, sections, after=blocks[0]["id"])
    except NotionError:
        return "write_failed"
    out["writes"] = out["readback_failed"] = 1
    return _sections_readback(client, parent.page_id, before, out, "parent_upgraded")


CALENDAR_REF = re.compile(r"^Calendar event: (Accepted invite|Cancelled) · ref ([0-9a-f]{12})$")


def round_calendar(client, page_id, budget=None):
    """LIFE-46 E07: (ref, cancelled) a UI 4 round page's Logistics records for its Calendar event, (None, False) when it records none.
    The Logistics callout is the first callout of the right column; an unknown layout raises a fixed code (nothing is guessed)."""
    budget = budget or [notion.MAX_CALLS]
    lists = [b for b in notion.children(client, page_id, budget) if b.get("type") == "column_list"]
    columns = notion.children(client, lists[0]["id"], budget) if len(lists) == 1 else []
    right = notion.children(client, columns[1]["id"], budget) if len(columns) == 2 else []
    calls = [b for b in right if b.get("type") == "callout"]
    lines = [notion.text(b) for b in notion.children(client, calls[0]["id"], budget)] if calls else []
    if not lines or lines[0] != "Logistics":
        raise NotionError("INTERVIEW_ROUND_LAYOUT")
    found = [m for m in (CALENDAR_REF.match(line) for line in lines) if m]
    return (found[0].group(2), found[0].group(1) == "Cancelled") if len(found) == 1 else (None, False)


def _changed(old, new, path=()):
    """[(path, old block, new block)] for every block whose text differs between two renders of one round page; the shapes must match."""
    if len(old) != len(new):
        raise NotionError("INTERVIEW_RENDER_SHAPE")
    out = []
    for index, (a, b) in enumerate(zip(old, new)):
        if a["type"] != b["type"]:
            raise NotionError("INTERVIEW_RENDER_SHAPE")
        if notion.text(a) != notion.text(b):
            out.append((path + (index,), a, b))
        out += _changed(notion.kids(a), notion.kids(b), path + (index,))
    return out


def _round_targets(client, page_id, edits, budget):
    """[(block, old block, new block)] on the live page for each changed render path, or None when any target is not machine-owned or reads as
    neither render (a human edit or an unknown layout). Top level: the fold, the Interview snapshot callout and the one column list; the left
    column (tabs, Live Notes, Raw Notes) is never a target."""
    tops = notion.children(client, page_id, budget)
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
        if block.get("type") != old["type"] or notion.text(block) not in (notion.text(old), notion.text(new)):
            return None
        found.append((block, old, new))
    return found


def transition(client, parent_id, child_id, current, want, live, out, context=None):
    """LIFE-46 E07: one round page moved, in place, to the state an accepted or cancelled Calendar event gives it (`want`): Interview 1 in
    Scheduling Pending dated, a dated round re-dated (rescheduled) or marked cancelled. Re-dating and cancelling need the page's own recorded
    event reference to equal the event's; nothing is matched by date or title alone. The identity line keeps its v1 format (only the date
    changes; interviewer and ordinal never do) and is written last, so a page is re-identified only when every machine block already says
    so; a cancelled round keeps its date and identity. Only the blocks whose text differs between the two renders change, in the fold, the
    snapshot or the right column, and each must read as either render; the Live and Raw Notes digest must not change. Nothing is created,
    renamed or moved."""
    fresh = notion.explicit_child(client, child_id)
    if (fresh is None or fresh.identity_valid is not True or not notion.same_notion_id(fresh.parent_id, parent_id) or not valid_date(want.interview_date)
            or (fresh.interviewer, fresh.ordinal) != (want.interviewer, want.ordinal)):
        return "round_unconfirmable"
    budget = [notion.MAX_CALLS]
    ref, cancelled = round_calendar(client, child_id, budget)
    if fresh.interview_date is None:
        if fresh.ordinal != 1 or want.cancelled or not want.calendar_ref:
            return "round_unconfirmable"
        source, kind = RoundQuery(True, None, fresh.interviewer, 1), "confirm"
    else:
        if not ref or ref != want.calendar_ref:
            return "round_unlinked"                                     # no recorded event, or another one: a move or cancel is never guessed
        source = RoundQuery(True, fresh.interview_date, fresh.interviewer, fresh.ordinal, calendar_ref=ref, cancelled=cancelled)
        if want.cancelled and want.interview_date != fresh.interview_date:
            return "round_unconfirmable"
        kind = "cancel" if want.cancelled else "reschedule"
    if source == want:
        return "round_unchanged"
    edits = _changed(round_page(current, source, parent_id, identity_text(source)), round_page(current, want, parent_id, identity_text(want)))
    targets = _round_targets(client, child_id, edits, budget)
    lines = [notion.text(b) for b in notion.machine_lines(client, child_id, 2)[0]]
    if targets is None or len(lines) < 2 or lines[1] != identity_text(source):
        return "machine_region_edited"
    before = notion.protected_snapshot(client, child_id, "round")
    if before is None:
        return "readback_protected_missing"
    out["writes_planned"] = 1
    if not live:
        return f"{kind}_allowed"
    if context:
        context.require_time()
    todo = sorted([t for t in targets if notion.text(t[0]) != notion.text(t[2])], key=lambda t: notion.text(t[1]).startswith(IDENTITY_PREFIX))
    done = []
    try:
        for block, old, new in todo:                                    # the identity line last: it is the commit point
            notion.set_text(client, block["id"], block["type"], new[new["type"]]["rich_text"])
            done.append((block, old))
            out["writes"] = out["readback_failed"] = 1
    except NotionError:
        try:
            for block, old in reversed(done):                           # roll the machine blocks back to the page as it was
                notion.set_text(client, block["id"], block["type"], old[old["type"]]["rich_text"])
        except NotionError:
            return "write_failed_partial"
        return "write_failed"
    after = notion.explicit_child(client, child_id)
    check = _round_targets(client, child_id, edits, [notion.MAX_CALLS])
    if (after is None or after.identity_valid is not True or (after.interview_date, after.interviewer, after.ordinal) != (want.interview_date, want.interviewer, want.ordinal)
            or check is None or any(notion.text(b) != notion.text(new) for b, _, new in check)
            or round_calendar(client, child_id) != (want.calendar_ref, want.cancelled)):
        return "readback_content_mismatch"
    if notion.protected_snapshot(client, child_id, "round") != before:
        return "readback_protected_changed"
    out["readback_failed"], out["readback_ok"] = 0, 1
    return {"confirm": "round_confirmed", "reschedule": "round_rescheduled", "cancel": "round_cancelled"}[kind]


def apply(client, environ, parent_query, query, live, context, deferred):
    """Private evidence in, counts and fixed code out. deferred forbids parent+round in one run."""
    out = {"writes_planned": 0, "writes": 0, "readback_ok": 0, "readback_failed": 0,
           "parents_created": 0, "parents_adopted": 0, "parents_upgraded": 0, "rounds_created": 0, "code": "pipeline_incomplete"}
    def finish(code):
        out["code"] = code
        return out
    try:
        context.require_time()
        target = notion.target_check(client, environ)
        if target != "target_ok":
            return finish(target)
        scan = notion.parent_scan(client, environ["HIRING_PIPELINE_PAGE_ID"], context)
        parent = resolve_parent(parent_query, scan)
        if parent.state == State.BLOCKED:
            return finish(parent.code)
        before, pipeline = None, environ["HIRING_PIPELINE_PAGE_ID"].strip()
        if parent.state == State.NOT_FOUND:
            company, role = parent_query.company.strip(), parent_query.role.strip()
            title = f"{company} — {role}"
            if not core(company) or not norm(role).strip() or split_title(title) != (company, role):
                return finish("identity_invalid")
            if query.confirmed_round is not True:
                return finish("pursuit_unconfirmed")
            insertion = notion.active_target(client, environ["HIRING_PIPELINE_PAGE_ID"], context)
            if insertion is None:
                return finish("active_target_incomplete")
            kind, code = "opportunity", "parent_create_allowed"
            current = views.opportunity(client, parent_query, query, pipeline, today=today())
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
            children = notion.child_scan(client, parent.page_id, context, legacy=notion.is_legacy_company(environ, parent_query.company))
            explicit = notion.explicit_child(client, query.explicit_child_page_id) if query.explicit_child_page_id else None
            child = resolve_child(parent, query, children, explicit)
            code = creation_eligibility(target, parent, owner, child, children, query)
            current = views.opportunity(client, parent_query, query, pipeline, children, today())
            held = [c for c in children.items if notion.same_notion_id(c.page_id, child.page_id or "") and c.identity_valid is True]
            if code == "child_match" and query.explicit_child_page_id and query.interview_date and query.calendar_ref and held:
                deferred.add(parent.page_id)                            # one write per opportunity per run (E07: the round moves in place)
                want = RoundQuery(True, query.interview_date, held[0].interviewer, held[0].ordinal, calendar_ref=query.calendar_ref, cancelled=query.cancelled)
                return finish(transition(client, parent.page_id, held[0].page_id, current, want, live, out, context))
            if code == "child_match":                                   # LIFE-88: the round exists; bring the page up to date
                done = refresh(client, parent.page_id, current, live, out, context, migrate=notion.is_legacy_company(environ, parent_query.company))     # a UI 3b page migrates only for a pursuit Jim named
                if done in ("parent_unchanged", "parent_legacy_layout", "parent_unrecognized"):
                    done = link_transcript(client, child.page_id, live, out)           # the round page links its Full Transcript page
                    done = done if done in ("transcript_linked", "transcript_link_allowed", "readback_content_mismatch") else "parent_unchanged"
                if done in ("parent_refreshed", "refresh_allowed", "transcript_linked", "transcript_link_allowed"):
                    deferred.add(parent.page_id)
                return finish(code if done in ("parent_unchanged", "parent_legacy_layout", "parent_unrecognized") else done)
            if code != "create_allowed":
                return finish(code)
            deferred.add(parent.page_id)                                # one write per opportunity per run
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
        try:
            page_id = notion.write_tree(client, insertion, blocks, title=title, icon=PAGE_ICONS[kind], template=notion.FULL_WIDTH)
        except NotionError:
            return finish("write_failed")
        out["writes"] = 1
        out["readback_failed"] = 1
        check = notion.readback(client, page_id, insertion, kind)
        if check == "readback_ok" and kind == "opportunity":
            check = finalize(client, page_id, current, blocks)
        if check != "readback_ok":
            return finish(check)
        if kind == "opportunity":
            refreshed = resolve_parent(parent_query, notion.parent_scan(client, environ["HIRING_PIPELINE_PAGE_ID"], context))
        else:
            refreshed = resolve_child(parent, query, notion.child_scan(client, insertion, context))
            if notion.protected_snapshot(client, insertion, "opportunity") != before:
                return finish("readback_protected_changed")
        if refreshed.state != State.MATCH or not notion.same_notion_id(refreshed.page_id, page_id):
            return finish("readback_identity_mismatch")
        if kind == "opportunity":
            deferred.add(page_id)
        out["readback_failed"], out["readback_ok"] = 0, 1
        out["parents_created" if kind == "opportunity" else "rounds_created"] = 1
        return finish("parent_created" if kind == "opportunity" else "round_created")
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError, AttributeError) as error:
        out["cause"] = cause_of(error)                                 # counts only: a fixed code or the exception's class name, never its text
        return finish("readback_unreadable" if out["writes"] else "pipeline_incomplete")
