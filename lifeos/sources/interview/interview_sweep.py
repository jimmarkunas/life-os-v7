"""Page-by-page sweep that converts every opportunity page, active or retired, to the current page layout.

Each page is classified by its own shape and the non-current ones are converted, a few per run, through the existing
writers; every write is proven by a before/after digest of the owner's own content and the sweep stops at the first
failure. `INTERVIEW_CONVERT` is `inventory` (classify and count only) or `all` (convert, up to `INTERVIEW_CONVERT_CAP`
pages per run, default 2); any other value is off. Writes also need INTERVIEW_OS_WRITER=v7. Counts and fixed codes
only.
"""

import re

from lifeos.interview import create, notion, view as views
from lifeos.interview.models import Parent, ParentQuery, RoundQuery
from lifeos.platform.names import split_title
from lifeos.platform.notion_client import NotionError
from lifeos.platform.runtime import DeadlineExceeded, RunContext

MODES = ("inventory", "all")
DEFAULT_CAP = 2
SWEEP_SECONDS = 300  # the job's time limit leaves the mail read, evidence and write stage first
PAGE_SECONDS = 75  # a page is not started with less than this left
CONVERTIBLE = ("human", "adopt_line", "skeleton", "prefold", "ui3b")
NEUTRAL = ("parent_unchanged",)
DONE = (
    "parent_adopted",
    "parent_upgraded",
    "parent_refreshed",
    "adopt_allowed",
    "upgrade_allowed",
    "refresh_allowed",
    "adopt_line_allowed",
)


SETTING = re.compile(
    r"(inventory|all)(?::(\d{1,2}))?(?:@([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}))?"
)


def _setting(environ):
    """(mode, cap text, page id) of `inventory`, `all`, `all:N` or `all:N@<page id>` (exactly one page); anything else
    is off."""
    found = SETTING.fullmatch((environ.get("INTERVIEW_CONVERT") or "").strip().lower())
    return (
        tuple(g or "" for g in found.groups())
        if found and (found.group(1) == "all" or not (found.group(2) or found.group(3)))
        else ("", "", "")
    )


def mode(environ):
    return _setting(environ)[0]


def target(environ):
    return _setting(environ)[2]


def cap(environ):
    for raw in (_setting(environ)[1], (environ.get("INTERVIEW_CONVERT_CAP") or "").strip()):
        if raw.isdigit() and 1 <= int(raw) <= 10:
            return int(raw)
    return DEFAULT_CAP


def classify(client, page_id, context=None):
    tops = notion.children(client, page_id, [notion.MAX_CALLS], context)
    if not tops:
        return "human"
    first = notion.text(tops[0])
    heads = [b for b in tops if b.get("type", "").startswith("heading") and notion.text(b) in notion.NOTES]
    if notion.is_fold(tops[0]):
        machine, lines, _ = create.machine_region(client, page_id, context)
        if machine is None or not lines or not notion.is_marker(notion.text(lines[0]), "opportunity"):
            return "unrecognized"
        if any(create.RENDER.fullmatch(notion.text(b)) for b in lines):
            return "current"
        return (
            "ui3b"
            if len(machine) > 1 and notion.text(machine[1]).startswith("Canonical opportunity parent")
            else "unrecognized"
        )
    if notion.is_marker(first, "opportunity"):
        return "skeleton" if not heads else "prefold" if len(heads) == 1 else "unrecognized"
    if first == notion.ADOPT and tops[0].get("type") == "paragraph":
        return "adopt_line"
    if "v7-interview" in first or any(notion.text(b) == notion.FOLD for b in tops):
        return "unrecognized"
    return "human"


def render_job(client, page_id, context=None):
    _, lines, _ = create.machine_region(client, page_id, context)
    found = [m for m in (create.RENDER.fullmatch(notion.text(b)) for b in lines) if m]
    return None if len(found) != 1 or found[0].group(1) == "none" else found[0].group(1)


def _convert(client, environ, live, context, parent, company, role, kind, key, pipeline, today):
    out = {"writes_planned": 0, "writes": 0, "readback_ok": 0, "readback_failed": 0}
    pq, query = ParentQuery(company, role), RoundQuery(True, None, None, None, stable_job_key=key)
    if kind == "human":
        code = create.begin_adopt(client, parent.page_id, live, out, context)
        if code != "adopt_line_added":
            return code, out
        kind = "adopt_line"
    if kind == "adopt_line":
        return create.adopt(client, environ, parent, pq, query, live, context, out), out
    if kind == "skeleton":
        tops = notion.children(client, parent.page_id, [notion.MAX_CALLS], context)
        return create.upgrade(client, environ, parent, pq, query, live, context, out, tops), out
    kids = notion.child_scan(client, parent.page_id, context, legacy=True)
    if not kids.complete:
        return "child_scan_incomplete", out
    current = views.opportunity(client, pq, query, pipeline, kids, today)
    return create.refresh(client, parent.page_id, current, live, out, context, migrate=True), out


def run(environ, live, client, scan, mint, legacy_key, context=None):
    """-> counts. `scan` is the complete parent scan; `mint(company, role)` is the key of a page with none of its own;
    `legacy_key(client, page_id, context)` reads an older page's own."""
    how = mode(environ)
    live = bool(live) and how == "all"
    context = context or RunContext.start(SWEEP_SECONDS)
    pipeline = (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    counts = {
        "mode": how,
        "inventory": {},
        "rounds": {"machine": 0, "human_titled": 0, "human_other": 0, "unread": 0},
        "retired": 0,
        "converted": 0,
        "waiting": 0,
        "failed": 0,
        "writes_planned": 0,
        "writes": 0,
        "readback_ok": 0,
        "why": {},
    }

    def note(code):
        counts["why"][code] = counts["why"].get(code, 0) + 1

    limit, stopped, only = cap(environ), False, target(environ)
    try:
        for index, parent in enumerate(scan.items):
            if context.remaining_seconds() < PAGE_SECONDS:  # not a failure: pages not yet seen wait for the next run
                counts["waiting"] += len(scan.items) - index
                note("time_budget")
                break
            context.require_time()
            company, role = split_title(parent.title)
            counts["retired"] += int(not parent.active)
            if not company.strip() or not role.strip():
                counts["inventory"]["identity_invalid"] = counts["inventory"].get("identity_invalid", 0) + 1
                continue
            kind = classify(client, parent.page_id, context)
            counts["inventory"][kind] = counts["inventory"].get(kind, 0) + 1
            kids = notion.child_scan(client, parent.page_id, context, legacy=True)
            if not kids.complete:
                counts["rounds"]["unread"] += 1
            for child in kids.items:
                counts["rounds"][
                    "machine" if child.identity_valid is True else "human_titled" if child.legacy else "human_other"
                ] += 1
            if kind == "current" and how == "all" and kids.complete and any(c.legacy for c in kids.items):
                kind = "refresh"
            if kind not in CONVERTIBLE and kind != "refresh":
                continue
            if only and not notion.same_notion_id(parent.page_id, only):
                continue
            if how != "all" or stopped or counts["converted"] >= limit:
                counts["waiting"] += 1
                continue
            key = (
                render_job(client, parent.page_id, context)
                if kind == "refresh"
                else (legacy_key(client, parent.page_id, context) if kind == "ui3b" else None) or mint(company, role)
            )
            code, out = _convert(
                client,
                environ,
                live,
                context,
                Parent(parent.page_id, parent.title, parent.active),
                company,
                role,
                kind,
                key,
                pipeline,
                create.today(),
            )
            note(code)
            for field in ("writes_planned", "writes", "readback_ok"):
                counts[field] += out[field]
            if code in DONE:
                counts["converted"] += 1
            elif code in NEUTRAL:
                pass
            else:
                counts["failed"] += 1
                stopped = True  # the first failure ends the sweep: the rest wait for the next run
            if out["readback_failed"] or code == "write_failed":
                stopped = True
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError) as error:
        counts["failed"] += 1
        counts["why"][create.cause_of(error)] = counts["why"].get(create.cause_of(error), 0) + 1
    return counts
