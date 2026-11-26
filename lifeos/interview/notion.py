"""Notion evidence and reusable protected read-back primitives."""
from lifeos.platform.notion_ids import notion_id
import hashlib
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import quote
from lifeos.platform.names import same_company, same_role, split_title, tokens
from lifeos.platform import notion_pages, notion_text
from lifeos.platform.notion_client import Client, NotionError, rich_text
from lifeos.platform.runtime import DeadlineExceeded
from .models import Child, Ownership, Parent, Scan

MARKERS = {kind: f"v7-interview:1;owner=machine;kind={kind}" for kind in ("opportunity", "round")}
PROTECTED = ("Live Notes", "Raw Notes")
# Typing ADOPT as the first line of an opportunity page hands it to this writer: the line becomes the
# opportunity marker plus ";adopted=<date>", and everything from the JIM_NOTES heading on stays the person's.
ADOPT = "LIFE OS: adopt"
JIM_NOTES = "Jim’s Notes"
LEGACY_NOTES = "🔒 Jim's notes"
NOTES = (JIM_NOTES, LEGACY_NOTES)
# The marker (and a round's identity line) sit in one collapsed toggle at the top; older pages use plain paragraphs.
FOLD = "⚙️ LIFE OS"
ROUND_FOLD = "⚙️ LIFE OS · Source Health"
ADOPTED = re.compile(re.escape(MARKERS["opportunity"]) + r";adopted=(\d{4}-\d{2}-\d{2})")
MAX_CALLS, MAX_DEPTH = 40, 4
PROTECTED_MAX_DEPTH = 12                    # human blocks nest deeper than page scans reach; past this the read fails closed
PROTECTED_MAX_CALLS = 400


def same_notion_id(a, b):
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    left, right = a.replace("-", "").lower(), b.replace("-", "").lower()
    return bool(left) and bool(right) and left == right


def environment(environ):
    return {"NOTION_API_TOKEN": (environ.get("NOTION_INTERVIEW_TOKEN") or "").strip(),
            "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "interview-unused"}


def make_client(environ=os.environ):
    return Client(environment(environ))


def is_marker(value, kind):
    if value == MARKERS.get(kind):
        return True
    from .identity import valid_date
    found = ADOPTED.fullmatch(value) if kind == "opportunity" else None
    return bool(found) and valid_date(found.group(1))


def is_fold(block):
    return block.get("type") == "toggle" and text(block) in (FOLD, ROUND_FOLD)


def machine_lines(client, page_id, size):
    """(lines, fold): the first `size` machine lines, read from the folded toggle when the first block is one."""
    blocks = client.call("GET", f"/blocks/{page_id}/children?page_size={size}")["results"]
    if blocks and is_fold(blocks[0]):
        return client.call("GET", f"/blocks/{blocks[0]['id']}/children?page_size={size}")["results"], blocks[0]
    return blocks, None


text = notion_text.block_text


def target_check(client, environ):
    root = (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    if not root or not environment(environ)["NOTION_API_TOKEN"]:
        return "target_config_missing"
    try:
        page = client.call("GET", f"/pages/{root}")
        titles = [p.get("title") for p in (page.get("properties") or {}).values() if p.get("type") == "title"]
        title = "".join(p.get("plain_text", (p.get("text") or {}).get("content", "")) for p in titles[0]) if len(titles) == 1 else ""
        return "target_ok" if title == "Hiring Pipeline" and not page.get("archived") and not page.get("in_trash") else "target_mismatch"
    except (NotionError, KeyError, TypeError, ValueError):
        return "target_unreadable"


def ownership(client, page_id, kind):
    try:
        blocks, _ = machine_lines(client, page_id, 1)
        first = text(blocks[0]) if blocks else ""
        if is_marker(first, kind):
            return Ownership.MACHINE
        if kind == "opportunity" and first == ADOPT and blocks[0].get("type") == "paragraph":
            return Ownership.ADOPT
        return Ownership.UNKNOWN if "v7-interview" in first else Ownership.HUMAN
    except (NotionError, KeyError, TypeError, ValueError):
        return Ownership.UNKNOWN


def children(client, page_id, budget, context=None):
    def fetch(cursor):
        if budget[0] <= 0:
            raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
        if context:
            context.require_time()
        budget[0] -= 1
        path = f"/blocks/{page_id}/children?page_size=100"
        return client.call("GET", path + (f"&start_cursor={quote(cursor, safe='')}" if cursor else ""))
    return [block for data in notion_pages.pages(fetch, NotionError, "INTERVIEW_SCAN_INCOMPLETE") for block in data["results"]]


def parent_scan(client, root, context=None):
    found, budget = [], [MAX_CALLS]

    def walk(page_id, active, depth):
        if depth > MAX_DEPTH:
            raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
        current = active
        blocks = children(client, page_id, budget, context)
        containers = [b for b in blocks if b.get("type") == "child_page"
                      and (b.get("child_page") or {}).get("title") == "Active Opportunities"]
        if same_notion_id(page_id, root) and len(containers) > 1:
            raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
        for block in blocks:
            kind = block.get("type", "")
            title = (block.get("child_page") or {}).get("title", "") if kind == "child_page" else text(block)
            if kind == "child_page" and title in ("Active Opportunities", "Retired Opportunities"):
                if title == "Active Opportunities":
                    if not same_notion_id(page_id, root):
                        continue
                    container = client.call("GET", f"/pages/{block['id']}")
                    if not same_notion_id((container.get("parent") or {}).get("page_id"), root) or container.get("archived") or container.get("in_trash"):
                        raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
                walk(block["id"], title == "Active Opportunities", depth + 1)
            elif title in ("Active Opportunities", "Retired Opportunities") and (kind.startswith("heading") or kind == "toggle"):
                current = title == "Active Opportunities"
                if block.get("has_children"):
                    walk(block["id"], current, depth + 1)
            elif kind == "child_page":
                if current is None:
                    continue
                found.append(Parent(block["id"], title, current))
            elif block.get("has_children") and kind in ("toggle", "column", "column_list"):
                walk(block["id"], current, depth + 1)

    try:
        walk(root, None, 0)
        return Scan(tuple(found), True)
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return Scan(tuple(found), False)


LEDGER_PAGES = 20
LEDGER_STAGE, LEDGER_DECISION, LEDGER_ACTIVE = "Stage", "Hiring Decision", ("Applied", "Interviewing", "Offer")


def _ledger_text(prop, kind):
    return notion_text.plain((prop or {}).get(kind)).strip()


def _ledger_pages(client, source_id, body, context):
    def fetch(cursor):
        if context:
            context.require_time()
        return client.query_data_source(source_id, {**body, **({"start_cursor": cursor} if cursor else {})})
    return notion_pages.pages(fetch, NotionError, "INTERVIEW_SCAN_INCOMPLETE", limit=LEDGER_PAGES)


def parents_from_ledger(client, source_id, context=None):
    """Every Job Ledger row whose Stage is set, as a Parent. An incomplete read gives Scan((), False), never a partial
    list."""
    try:
        rows = [row for page in _ledger_pages(client, source_id, {"page_size": 100, "filter": {"property": LEDGER_STAGE, "select": {"is_not_empty": True}}}, context)
                for row in page["results"]]
        found = []
        for row in map(_ledger_row, rows):
            if not (row["company"] and row["role"] and row["stage"] and row["id"]):
                continue
            found.append(Parent(row["id"], f"{row['company']} - {row['role']}", row["stage"] in LEDGER_ACTIVE and not row["decision"], row["stage"]))
        return Scan(tuple(found), True)
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError, AttributeError):
        return Scan((), False)


def parents(client, environ, context=None):
    """The opportunities every reader works from: Ledger rows with Stage, plus page-tree pages no row matches on company
    AND role. An incomplete read of either is an incomplete scan.
    """
    tree = parent_scan(client, (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip(), context)
    source = ((environ or {}).get("NOTION_JOB_LEDGER_DATA_SOURCE_ID") or "").strip()
    if not source:
        return tree
    ledger = parents_from_ledger(client, source, context)
    if not ledger.complete or not tree.complete:
        return Scan((), False)
    keys = [split_title(p.title) for p in ledger.items]
    kept = [p for p in tree.items if not any(same_company(split_title(p.title)[0], c) and same_role(split_title(p.title)[1], r) for c, r in keys)]
    return Scan(tuple(ledger.items) + tuple(kept), True, len(ledger.items), len(kept))


LEDGER_STAGES = ("Found", "Applied", "Interviewing", "Offer", "Working", "Ended")
MANUAL = "Manual"


def _ledger_row(row):
    props = row.get("properties") or {}
    return {"id": row.get("id"), "company": _ledger_text(props.get("Company"), "rich_text"), "role": _ledger_text(props.get("Job"), "title"),
            "key": _ledger_text(props.get("Stable Job Key"), "rich_text"),
            "stage": ((props.get(LEDGER_STAGE) or {}).get("select") or {}).get("name"),
            "decision": ((props.get(LEDGER_DECISION) or {}).get("select") or {}).get("name")}


def ledger_row(client, source_id, company, role, key=None, context=None):
    """The one Job Ledger row for a pursuit, by Stable Job Key when given, else by Company and Job. Reads completely
    or not at all. -> (code, row): ledger_row_found, ledger_row_missing, ledger_row_ambiguous (nothing guessed),
    ledger_unreadable."""
    words = tokens(company)
    if not words or not role.strip():
        return "ledger_unreadable", None
    try:
        rows = [_ledger_row(r) for page in _ledger_pages(client, source_id, {"page_size": 100, "filter": {"property": "Company", "rich_text": {"contains": words[0]}}}, context)
                for r in page["results"] if not r.get("archived") and not r.get("in_trash")]
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError, AttributeError):
        return "ledger_unreadable", None
    keyed = [r for r in rows if key and r["key"] == key]
    named = keyed or [r for r in rows if same_company(r["company"], company) and same_role(r["role"], role)]
    if len(named) > 1:
        return "ledger_row_ambiguous", None
    return ("ledger_row_found", named[0]) if named else ("ledger_row_missing", None)


def create_ledger_row(client, source_id, company, role, key=None):
    """A new Manual Ledger row. Stage is set later, as the commit point. One request, never retried: a create whose
    answer is lost is found by name on the next run."""
    props = {"Job": {"title": rich_text(role)}, "Company": {"rich_text": rich_text(company)}, "Source Types": {"multi_select": [{"name": MANUAL}]}}
    if key:
        props["Stable Job Key"] = {"rich_text": rich_text(key)}
    return client.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": source_id}, "properties": props})["id"]


def set_stage(client, row_id, stage):
    if stage not in LEDGER_STAGES:
        raise ValueError("stage")
    client.call("PATCH", f"/pages/{row_id}", {"properties": {LEDGER_STAGE: {"select": {"name": stage}}}})


def ledger_shape(client, source_id, context=None):
    """GET-only probe: creation is supported when Stage is a select with exactly the six expected options and Source
    Types offers Manual."""
    out = {"ledger_stage_options": 0, "insertion_supported": False, "code": "ledger_shape_incomplete"}
    if not source_id:
        out["code"] = "ledger_config_missing"
        return out
    try:
        if context:
            context.require_time()
        props = (client.call("GET", f"/data_sources/{source_id}") or {}).get("properties") or {}
        stage, sources = props.get(LEDGER_STAGE) or {}, props.get("Source Types") or {}
        names = [o.get("name") for o in ((stage.get("select") or {}).get("options") or [])]
        out["ledger_stage_options"] = len(names)
        manual = MANUAL in [o.get("name") for o in ((sources.get("multi_select") or {}).get("options") or [])]
        if stage.get("type") == "select" and sorted(names) == sorted(LEDGER_STAGES) and len(names) == len(LEDGER_STAGES) and manual:
            out["insertion_supported"], out["code"] = True, "ledger_shape_supported"
    except DeadlineExceeded:
        out["code"] = "ledger_shape_incomplete"
    except (NotionError, KeyError, TypeError, ValueError, AttributeError):
        out["code"] = "ledger_shape_unreadable"
    return out


LEGACY_TITLE = re.compile(r"^Interview (\d{1,2}):\s*(.+?)\s*\((\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\)\s*$")


def legacy_companies(environ):
    return [c.strip() for c in ((environ or {}).get("INTERVIEW_TEST_COMPANIES") or "").split(",") if c.strip()]


def is_legacy_company(environ, company):
    words = set(tokens(company))
    return any(tokens(c) and set(tokens(c)) <= words for c in legacy_companies(environ))


def legacy_round(block, child):
    """`Interview 1: Name (9/10/26)` -> the same Child with number, interviewer and date, marked legacy; other titles
    unchanged."""
    found = LEGACY_TITLE.match(str((block.get("child_page") or {}).get("title") or ""))
    if not found:
        return child
    number, who, month, day, year = found.groups()
    year = int(year) + (2000 if len(year) == 2 else 0)
    try:
        date = datetime(year, int(month), int(day)).date().isoformat()
    except ValueError:
        return child
    return Child(child.page_id, child.parent_id, date, who, int(number), child.identity_valid, True, child.created)


NEST_KINDS = ("column_list", "column", "toggle")


def _nested_pages(client, blocks, budget, context, depth=1):
    out = []
    for block in blocks:
        if block.get("type") in NEST_KINDS and block.get("has_children"):
            if depth > MAX_DEPTH:
                raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
            kids = children(client, block["id"], budget, context)
            out.extend((kid, block["id"]) for kid in kids if kid.get("type") == "child_page")
            out.extend(_nested_pages(client, kids, budget, context, depth + 1))
    return out


def child_scan(client, parent_id, context=None, legacy=False):
    try:
        budget = [MAX_CALLS]
        blocks = children(client, parent_id, budget, context)
        found = []
        for block in blocks:
            if block.get("type") != "child_page":
                continue
            if context:
                context.require_time()
            if len(found) >= MAX_CALLS:
                raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
            child = explicit_child(client, block["id"])
            if child is None or not same_notion_id(child.parent_id, parent_id):
                raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
            found.append(legacy_round(block, child) if legacy and child.identity_valid is None else child)
        if legacy:                                                              # hand-made `Interview N: Name (m/d/yy)` pages of named pursuits count, by title only
            for block, container in _nested_pages(client, blocks, budget, context):
                if len(found) >= MAX_CALLS:
                    raise NotionError("INTERVIEW_SCAN_INCOMPLETE")
                child = explicit_child(client, block["id"], home=parent_id, via=container)
                if child is not None and child.identity_valid is None and same_notion_id(child.parent_id, parent_id):
                    nested = legacy_round(block, child)
                    if nested.legacy:
                        found.append(nested)
        return Scan(tuple(found), True)
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        return Scan((), False)


def explicit_child(client, page_id, home=None, via=None):
    """The page's identity. `home` and `via` describe a page nested in a column or toggle of its parent (`via` is its API
    parent block).
    """
    page = client.call("GET", f"/pages/{page_id}")
    if page.get("archived") or page.get("in_trash"):
        return None
    parent = page.get("parent") or {}
    if home and via and same_notion_id(parent.get("block_id"), via):
        parent = {"page_id": home}
    from .create import parse_identity
    blocks, _ = machine_lines(client, page_id, 2)
    first = text(blocks[0]) if blocks else ""
    machine = first == MARKERS["round"]
    created = page.get("created_time") if isinstance(page.get("created_time"), str) else None
    if not machine:
        return Child(page_id, parent.get("page_id", ""), identity_valid=False if "interview:" in first else None, created=created)
    data = parse_identity(text(blocks[1])) if len(blocks) > 1 and blocks[1].get("type") == "paragraph" else None
    if data is None:
        return Child(page_id, parent.get("page_id", ""), identity_valid=False, created=created)
    return Child(page_id, parent.get("page_id", ""), **data, identity_valid=True, created=created)


def _protected_children(client, page_id, budget):
    try:
        return children(client, page_id, budget)
    except NotionError:
        raise NotionError("INTERVIEW_PROTECTED_BUDGET" if budget[0] <= 0 else "INTERVIEW_PROTECTED_READ") from None


def _subtree(client, block, budget, depth=0):
    if depth > PROTECTED_MAX_DEPTH:
        raise NotionError("INTERVIEW_PROTECTED_DEPTH")
    value = {k: v for k, v in block.items() if k not in ("last_edited_time", "last_edited_by")}
    if block.get("type") in ("child_page", "child_database"):
        return {"id": value.get("id"), "type": value.get("type")}
    body = value.get(value.get("type"))
    if isinstance(body, dict) and body.get("type") == "file" and isinstance(body.get("file"), dict):
        value[value["type"]] = {**body, "file": {}}                              # a Notion-hosted file's signed URL changes on every read
    if block.get("has_children"):
        value["protected_children"] = [_subtree(client, b, budget, depth + 1) for b in _protected_children(client, block["id"], budget)]
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def protected_snapshot(client, page_id, kind="round"):
    """Call only around an authorized mutation. Hashes ordered anchors and all descendant evidence privately. An
    opportunity's notes region runs to the end of the page but leaves out child pages (Notion appends new rounds
    there)."""
    budget, regions = [PROTECTED_MAX_CALLS], {}
    labels = PROTECTED + (NOTES if kind == "opportunity" else ())
    current = None
    order = []
    tops = _protected_children(client, page_id, budget)
    for block in tops:
        label = text(block)
        if current in NOTES:
            if block.get("type") != "child_page":
                regions[current].append(_subtree(client, block, budget))
            continue
        if label in labels and (block.get("type", "").startswith("heading") or block.get("type") == "toggle"):
            if label in regions:
                raise NotionError("INTERVIEW_PROTECTED_SHAPE")
            current = label
            order.append((label, block.get("id")))
            regions[label] = []
        elif block.get("type", "").startswith("heading"):
            current = None
        if current:
            regions[current].append(_subtree(client, block, budget))
    if kind == "round" and not regions:
        return _round_regions(client, tops, budget)
    if not (all(label in regions for label in PROTECTED) or any(label in regions for label in NOTES)):
        return None
    return _digest([order, regions])


RAW_HEADING, RAW_TOGGLE, LIVE_PANE = "Protected Raw Notes", "Raw notes · open to write", "Live Notes"


def _round_regions(client, tops, budget):
    lists = [b for b in tops if b.get("type") == "column_list"]
    columns = children(client, lists[0]["id"], budget) if len(lists) == 1 else []
    left = children(client, columns[0]["id"], budget) if columns else []
    tabs = [b for b in left if b.get("type") == "tab"]
    heads = [i for i, b in enumerate(left) if b.get("type") == "heading_3" and text(b) == RAW_HEADING]
    if len(tabs) != 1 or len(heads) != 1 or len(left) <= heads[0] + 1 or left[heads[0] + 1].get("type") != "toggle":
        return None
    live = [b for b in children(client, tabs[0]["id"], budget) if text(b) == LIVE_PANE]
    if len(live) != 1:
        return None
    return _digest([_subtree(client, live[0], budget), _subtree(client, left[heads[0] + 1], budget)])


def human_tail(client, page_id, blocks=None):
    budget = [PROTECTED_MAX_CALLS]
    if blocks is None:
        blocks = _protected_children(client, page_id, budget)
        heads = [i for i, b in enumerate(blocks) if text(b) in NOTES and b.get("type", "").startswith("heading")]
        if len(heads) != 1 or len(blocks) <= heads[0] + 1 or blocks[heads[0] + 1].get("type") != "callout":
            raise NotionError("INTERVIEW_PROTECTED_SHAPE")
        blocks = blocks[heads[0] + 2:]
    else:
        blocks = blocks[1:]
    return _digest([_subtree(client, b, budget) for b in blocks if b.get("type") != "child_page"])


# A column list, column, table or tab is created with its children, and one request carries two levels:
# write_tree sends each block with the leading children that fit and appends the rest; a column whose first child
# cannot fit starts with an empty placeholder paragraph, deleted afterwards.
MUST, TREE_MAX_CALLS = ("column_list", "column", "table", "tab"), 200
FULL_WIDTH, TEMPLATE_WAIT = notion_id("FULL_WIDTH_TEMPLATE"), 8
PLACEHOLDER = {"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}


def kids(block):
    return (block.get(block.get("type")) or {}).get("children") or []


def _shell(block, depth):
    kind, inner, kept = block["type"], kids(block), []
    if depth < 2:
        for child in inner:
            plan = _shell(child, depth + 1)
            if plan is None:
                break
            kept.append((child, plan))
    payload = {**block, kind: {k: v for k, v in block[kind].items() if k != "children"}}
    placeholder = False
    if kept:
        payload[kind]["children"] = [plan[0] for _, plan in kept]
    elif inner and kind in MUST:
        if kind != "column" or depth >= 2:
            return None
        payload[kind]["children"], placeholder = [PLACEHOLDER], True
    return payload, kept, inner[len(kept):], placeholder


def _deferred(plan):
    _, kept, rest, placeholder = plan
    return bool(rest) or placeholder or any(_deferred(p) for _, p in kept)


def _fill(client, block_id, plan, budget):
    _, kept, rest, placeholder = plan
    made = children(client, block_id, budget) if placeholder or any(_deferred(p) for _, p in kept) else []
    for (_, inner), item in zip(kept, made):
        if _deferred(inner):
            _fill(client, item["id"], inner, budget)
    if rest:
        write_tree(client, block_id, rest, budget=budget)
    if placeholder:
        budget[0] -= 1
        delete_block(client, made[0]["id"])


FILLER_MAX = 3


def clear_template_filler(client, page_id, budget):
    """Delete the empty leading paragraphs a template page leaves on a page just created. The first block must be the
    machine fold or marker, or ownership() reads the page as a human's and it is never refreshed.
    """
    top = children(client, page_id, budget)
    gone = 0
    while gone < FILLER_MAX and len(top) - gone > 1:
        block = top[gone]
        runs = (block.get("paragraph") or {}).get("rich_text") or []
        if block.get("type") != "paragraph" or block.get("has_children") or text(block).strip() or any(r.get("type", "text") != "text" for r in runs):
            break
        delete_block(client, block["id"])
        gone += 1
    return gone


def write_tree(client, parent_id, blocks, after=None, title=None, budget=None, icon=None, template=None, start=False):
    """Write `blocks` (nested `children` at any depth) under parent_id, or as a new page titled `title`; returns the
    id written into. `start` puts them above existing content. Fails closed (NotionError) on an unwritable shape."""
    budget = budget if budget is not None else [TREE_MAX_CALLS]
    plans = [_shell(block, 0) for block in blocks]
    if any(plan is None for plan in plans):
        raise NotionError("INTERVIEW_TREE_UNWRITABLE")
    budget[0] -= 1
    if budget[0] < 0:
        raise NotionError("INTERVIEW_TREE_BUDGET")
    body = {"children": [plan[0] for plan in plans]}
    if title is not None:
        page = {"parent": {"type": "page_id", "page_id": parent_id}, "properties": {"title": {"type": "title", "title": rich_text(title)}},
                **({"icon": icon} if icon else {})}
        if template:
            target = client.call("POST", "/pages", {**page, "template": {"type": "template_id", "template_id": template}})["id"]
            time.sleep(TEMPLATE_WAIT)
            clear_template_filler(client, target, budget)               # a template landing at any time leaves an empty paragraph above the machine fold
            written = write_tree(client, target, blocks, budget=budget)
            clear_template_filler(client, target, budget)
            return written
        target = client.call("POST", "/pages", {**page, **body})["id"]
        made = children(client, target, budget)
    else:
        target = parent_id
        place = {"after": after} if after else {"position": {"type": "start"}} if start else {}
        made = client.call("PATCH", f"/blocks/{parent_id}/children", {**body, **place})["results"]
    if len(made) < len(plans):
        raise NotionError("INTERVIEW_TREE_INCOMPLETE")
    for plan, item in zip(plans, made):
        if _deferred(plan):
            _fill(client, item["id"], plan, budget)
    return target


def _plain(runs):
    return "".join(f"@{((r.get('mention') or {}).get('page') or {}).get('id', '').replace('-', '')}" if r.get("type") == "mention"
                   else r.get("plain_text", (r.get("text") or {}).get("content", "")) for r in runs or [])


def _node(kind, body, value):
    node = {"type": kind, "text": value if kind == "child_page" else _plain(body.get("rich_text"))}
    for key in ("color", "width_ratio", "table_width", "has_column_header", "has_row_header", "is_toggleable"):
        if body.get(key) not in (None, False, "default"):
            node[key] = round(body[key], 4) if key == "width_ratio" else body[key]
    icon = body.get("icon") or {}
    if icon:
        node["icon"] = icon.get("emoji") or (icon.get("external") or {}).get("url") or icon.get("type")
    if kind == "table_row":
        node["cells"] = [_plain(cell) for cell in body.get("cells", [])]
    return node


def as_nodes(blocks):
    return [{**_node(b["type"], b[b["type"]], text(b)), **({"children": as_nodes(kids(b))} if kids(b) else {})} for b in blocks]


def region_nodes(client, blocks, budget, skip=(), depth=0):
    out = []
    for block in blocks:
        if block.get("id") in skip:
            continue
        kind = block.get("type", "")
        body = block.get(kind) or {}
        node = _node(kind, body, body.get("title", "") if kind == "child_page" else text(block))
        if block.get("has_children") and kind != "child_page":
            if depth >= 8:
                raise NotionError("INTERVIEW_TREE_INCOMPLETE")
            node["children"] = region_nodes(client, children(client, block["id"], budget), budget, skip, depth + 1)
        out.append(node)
    return out


def read_tree(client, block_id, budget, skip=()):
    return region_nodes(client, children(client, block_id, budget), budget, skip)


CONTENT = ("paragraph", "bulleted_list_item", "numbered_list_item", "to_do", "quote", "table_row", "link_to_page")


def outline(nodes, depth=0, parent="", icons=True):
    """The structural outline of a tree (types, nesting, order, layout); text content is data, not structure."""
    out = []
    for node in nodes:
        kind = node["type"]
        if kind in CONTENT and parent != "tab":
            continue
        label = node["text"] if kind.startswith("heading") or kind == "toggle" or parent == "tab" else ""
        extra = [f"{k}={node[k]}" for k in ("width_ratio", "color", "icon", "table_width", "has_column_header", "has_row_header")
                 if k in node and (icons or k != "icon")]
        out.append(f"{depth}:{kind}" + (f" {label}" if label else "") + (f" [{' '.join(extra)}]" if extra else ""))
        out += outline(node.get("children", []), depth + 1, kind, icons)
    return out


def tree_digest(nodes):
    return _digest(nodes)[:16]


def set_text(client, block_id, kind, rich):
    return client.call("PATCH", f"/blocks/{block_id}", {kind: {"rich_text": rich}})


def append_children(client, page_id, heading_id, blocks):
    return client.call("PATCH", f"/blocks/{page_id}/children", {"children": blocks, "after": heading_id})


def paragraph(content):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(content)}}


def delete_block(client, block_id):
    return client.call("DELETE", f"/blocks/{block_id}")


PRESERVED, PRESERVE_BATCH = "Preserved edits", 90


def is_preserved(block):
    return block.get("type") == "toggle" and text(block) == PRESERVED


def writable(runs):
    out = []
    for run in runs or []:
        kind = run.get("type") or ("mention" if run.get("mention") else "text")
        body = (({"type": "mention", "mention": run["mention"]} if kind == "mention" else {"type": "text", "text": {k: v for k, v in (run.get("text") or {"content": run.get("plain_text", "")}).items() if v is not None}})
                | ({"annotations": run["annotations"]} if run.get("annotations") else {}))
        out.append(body)
    return out


def preserve(client, page_id, toggle, lines, stamp, after=None):
    """Copy `lines` verbatim under `stamp` into the page's one Preserved edits toggle and read them back. Any failure is
    one fixed code.
    """
    batch = [paragraph(stamp)] + [paragraph(line) for line in lines]
    try:
        head = batch[:PRESERVE_BATCH]
        if toggle is None:
            made = client.call("PATCH", f"/blocks/{page_id}/children", {"children": [{"object": "block", "type": "toggle", "toggle": {"rich_text": rich_text(PRESERVED), "color": "gray", "children": head}}],
                                                                       **({"after": after} if after else {})})
            toggle_id = made["results"][0]["id"]
        else:
            toggle_id = toggle["id"]
            client.call("PATCH", f"/blocks/{toggle_id}/children", {"children": head})
        for at in range(PRESERVE_BATCH, len(batch), PRESERVE_BATCH):
            client.call("PATCH", f"/blocks/{toggle_id}/children", {"children": batch[at:at + PRESERVE_BATCH]})
        held = [text(b) for b in children(client, toggle_id, [MAX_CALLS])]
        if held[-len(batch):] != [text(b) for b in batch]:
            raise NotionError("INTERVIEW_PRESERVE_FAILED")
    except (NotionError, KeyError, IndexError, TypeError):
        raise NotionError("INTERVIEW_PRESERVE_FAILED") from None
    return toggle_id


DERIVED_MAX_CALLS = 100


def derived_blocks(client, page_id):
    heading, payload, ended = None, [], False
    for data in notion_pages.children(client, page_id, NotionError, incomplete="INTERVIEW_SCAN_INCOMPLETE", limit=DERIVED_MAX_CALLS):
        for block in data["results"]:
            kind = block.get("type")
            major = kind in ("heading_1", "heading_2")
            if major and text(block) == "Derived":
                if heading is not None:
                    raise NotionError("INTERVIEW_DERIVED_MISSING")
                heading = block
            elif heading is not None and not ended:
                if major:
                    ended = True
                else:
                    payload.append(block)
    if heading is None:
        raise NotionError("INTERVIEW_DERIVED_MISSING")
    return heading, payload


def readback(client, page_id, parent_id, kind, before=None):
    """Verify a held page: live, first block is the machine marker for `kind`, protected content unchanged.
    `parent_id` unused."""
    try:
        page = client.call("GET", f"/pages/{page_id}")
        if page.get("archived") or page.get("in_trash"):
            return "readback_page_gone"
        blocks, _ = machine_lines(client, page_id, 1)
        if not blocks or not is_marker(text(blocks[0]), kind):
            return "readback_marker_missing"
        after = protected_snapshot(client, page_id, kind)
        if after is None:
            return "readback_protected_missing"
        return "readback_protected_changed" if before is not None and before != after else "readback_ok"
    except NotionError as error:
        return "readback_page_gone" if str(error) == "NOTION_HTTP_404" else "readback_unreadable"
    except (KeyError, TypeError, ValueError):
        return "readback_unreadable"
