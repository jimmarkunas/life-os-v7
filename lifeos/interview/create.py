"""Insert-only B2 vertical slice; one page per evidence pass, no write retries."""
import json
from lifeos.platform.names import core, norm, split_title
from lifeos.platform.notion_client import NotionError
from lifeos.platform.runtime import DeadlineExceeded
from . import notion
from .identity import creation_eligibility, resolve_child, resolve_parent, valid_date
from .models import Ownership, State
from .template import opportunity_page, round_page

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
    """LIFE-46: one adoption of Jim's own page whose exact first line is `LIFE OS: adopt` (accepted UI 3b). The UI 3b sections and
    the Jim's notes heading go in right after that line, the line becomes the adopted marker, and nothing Jim wrote is edited,
    moved or deleted. Rounds come on a later run. A page that already has LIFE OS sections or a second marker is never adopted."""
    if query.confirmed_round is not True:
        return "pursuit_unconfirmed"
    blocks = notion.children(client, parent.page_id, [notion.MAX_CALLS], context)
    labels = [notion.text(b) for b in blocks]
    if (labels.count(notion.ADOPT) != 1 or notion.JIM_NOTES in labels
            or any("v7-interview" in label for label in labels)):
        return "adopt_conflict"
    before = notion.human_tail(client, parent.page_id, blocks)
    out["writes_planned"] = 1
    if not live:
        return "adopt_allowed"
    context.require_time()
    from datetime import datetime
    from zoneinfo import ZoneInfo
    marker = notion.MARKERS["opportunity"] + ";adopted=" + datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    sections = opportunity_page(parent_query, query, environ["HIRING_PIPELINE_PAGE_ID"].strip(), marker, adopted=True)
    try:
        notion.append_children(client, parent.page_id, blocks[0]["id"], sections)
    except NotionError:
        return "write_failed"
    out["writes"] = out["readback_failed"] = 1
    try:
        notion.set_paragraph(client, blocks[0]["id"], marker)
    except NotionError:
        return "write_failed"                                           # the adopt line stays; the next run reports adopt_conflict
    if notion.ownership(client, parent.page_id, "opportunity") != Ownership.MACHINE:
        return "readback_marker_missing"
    if notion.protected_snapshot(client, parent.page_id, "opportunity") is None:
        return "readback_protected_missing"
    if notion.human_tail(client, parent.page_id) != before:
        return "readback_protected_changed"
    out["readback_failed"], out["readback_ok"], out["parents_adopted"] = 0, 1, 1
    return "parent_adopted"


def apply(client, environ, parent_query, query, live, context, deferred):
    """Private evidence in, counts and fixed code out. deferred forbids parent+round in one run."""
    out = {"writes_planned": 0, "writes": 0, "readback_ok": 0, "readback_failed": 0,
           "parents_created": 0, "parents_adopted": 0, "rounds_created": 0, "code": "pipeline_incomplete"}
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
        before = None
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
            blocks = opportunity_page(parent_query, query, environ["HIRING_PIPELINE_PAGE_ID"].strip(), notion.MARKERS["opportunity"])
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
            children = notion.child_scan(client, parent.page_id, context)
            explicit = notion.explicit_child(client, query.explicit_child_page_id) if query.explicit_child_page_id else None
            child = resolve_child(parent, query, children, explicit)
            code = creation_eligibility(target, parent, owner, child, children, query)
            if code != "create_allowed":
                return finish(code)
            if parse_identity(identity_text(query)) is None:
                return finish("round_identity_incomplete")
            kind, insertion = "round", parent.page_id
            title = f"Interview {query.ordinal}" if query.ordinal else f"Interview — {query.interview_date}"
            blocks = round_page(parent_query, query, insertion, environ["HIRING_PIPELINE_PAGE_ID"].strip(), identity_text(query))
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
            page = notion.insert_page(client, insertion, title, blocks)
        except NotionError:
            return finish("write_failed")
        out["writes"] = 1
        out["readback_failed"] = 1
        page_id = page["id"]
        check = notion.readback(client, page_id, insertion, kind)
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
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError, AttributeError):
        return finish("readback_unreadable" if out["writes"] else "pipeline_incomplete")
