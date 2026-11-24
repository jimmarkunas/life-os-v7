"""LIFE-931: the Interviews list on Hiring Pipeline, one row per machine round, kept current by the Interview job.

The database is a child of the Hiring Pipeline page titled `Interviews`, found by that title and written with the Interview integration (no new token). Every cell is read from the round page's own snapshot, so a fact the page does not hold reads `Not verified`.
The list never writes a round page and never lists a page Jim made by hand. Jim does not type in it: a row whose round is gone is marked Retired, never deleted."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from lifeos.platform import ct_stamp
from lifeos.platform.names import split_title
from lifeos.platform.notion_client import NotionError, rich_text
from . import held, notion

TITLE = "Interviews"
NV = "Not verified"
PROPERTIES = {"Interview": "title", "Company": "rich_text", "Role": "rich_text", "Round": "number", "Date (CT)": "date", "When": "rich_text", "Interviewer": "rich_text",
              "Status": "select", "Transcript": "checkbox", "Round page": "url", "Updated (CT)": "rich_text"}
SCREEN = re.compile(r"^Round type: (?P<kind>Recruiter screen(?: \(.*\))?)$")      # LIFE-958: the one marker line a round's snapshot carries when the call was a screen, not a numbered round (set once, read here, never rewritten)
SNAPSHOT_WHO = re.compile(r"^Interviewer: (?P<who>.*?) · Status: (?P<status>.*)$")
LIMIT = 100


def _today():
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")


def find_source(client, root, context=None):
    """-> (code, data source id): roster_found, roster_absent (no database titled Interviews, nothing is written), roster_ambiguous or roster_unreadable (its schema is not the contracted one)."""
    try:
        blocks = [b for b in notion.children(client, root, [notion.MAX_CALLS], context) if b.get("type") == "child_database"
                  and ((b.get("child_database") or {}).get("title") or "") == TITLE]
        if not blocks:
            return "roster_absent", None
        if len(blocks) > 1:
            return "roster_ambiguous", None
        sources = (client.call("GET", f"/databases/{blocks[0]['id']}") or {}).get("data_sources") or []
        if len(sources) != 1:
            return "roster_unreadable", None
        props = (client.call("GET", f"/data_sources/{sources[0]['id']}") or {}).get("properties") or {}
        if any((props.get(name) or {}).get("type") != kind for name, kind in PROPERTIES.items()):
            return "roster_unreadable", None
        return "roster_found", sources[0]["id"]
    except (NotionError, KeyError, TypeError, ValueError, AttributeError):
        return "roster_unreadable", None


def page_url(page_id):
    return "https://www.notion.so/" + str(page_id).replace("-", "")


def status_of(raw, date, today):
    """The list's Status for the snapshot's own status text and the round's date."""
    head = (raw or "").split(" · ")[0].strip()
    if head.startswith("Scheduling Pending"):
        return "Scheduling pending"
    if head.startswith("Cancelled"):
        return "Cancelled"
    if head.startswith("Held"):
        return "Held"
    if date and date < today:
        return "Date passed"                                           # dated in the past, but nothing on the page says it was held
    return "Scheduled"


def facts(client, parent, child, context=None, today=None):
    """One row's cells, read from the round page: -> dict, or None when the page's snapshot is not the one this writer makes."""
    budget = [notion.MAX_CALLS]
    tops = notion.children(client, child.page_id, budget, context)
    callouts = [b for b in tops[1:] if b.get("type") == "callout"]
    if not tops or not notion.is_fold(tops[0]) or not callouts:
        return None
    lines = [notion.text(b) for b in notion.children(client, callouts[0]["id"], budget, context)]
    who = status = None
    for line in lines:
        found = SNAPSHOT_WHO.match(line)
        if found:
            who, status = found.group("who").strip() or NV, found.group("status").strip()
    if status is None:
        return None
    when = next((line[len("When: "):] for line in lines if line.startswith("When: ")), NV)
    screen = next((m.group("kind") for m in (SCREEN.match(line) for line in lines) if m), None)
    transcript = False
    for block in tops:
        if block.get("type") == "child_page" and (block.get("child_page") or {}).get("title") == "Full Transcript":
            transcript = any(notion.text(b).strip() for b in notion.children(client, block["id"], [notion.MAX_CALLS], context))
    company, role = split_title(parent.title)
    date = child.interview_date
    natural = status_of(status, date, today or _today())
    state = "Retired" if parent.active is False else natural               # LIFE-958: a round of an opportunity under Retired Opportunities (or no longer active) is Retired, so it leaves the Newest first view
    return {"company": company, "role": role, "round": None if screen else child.ordinal, "kind": screen, "date": date, "when": when, "who": who,
            "status": state, "held": natural == "Held" and state != "Retired", "transcript": transcript, "url": page_url(child.page_id)}


def properties(row, stamp):
    """The Notion properties of a row (the round page's own URL is its key)."""
    title = f"{row['company']} · {row['kind']}" if row.get("kind") else f"{row['company']} · Interview {row['round']}" if row["round"] else f"{row['company']} · Interview"
    props = {"Interview": {"title": rich_text(title)}, "Company": {"rich_text": rich_text(row["company"])}, "Role": {"rich_text": rich_text(row["role"])},
             "Round": {"number": row["round"]}, "Date (CT)": {"date": {"start": row["date"]} if row["date"] else None}, "When": {"rich_text": rich_text(row["when"])},
             "Interviewer": {"rich_text": rich_text(row["who"])}, "Status": {"select": {"name": row["status"]}}, "Transcript": {"checkbox": bool(row["transcript"])},
             "Round page": {"url": row["url"]}, "Updated (CT)": {"rich_text": rich_text(stamp)}}
    return props


def _plain(prop):
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in ("title", "rich_text"):
        return "".join(p.get("plain_text", (p.get("text") or {}).get("content", "")) for p in value or [])
    if kind == "select":
        return (value or {}).get("name")
    if kind == "date":
        return (value or {}).get("start")
    return value


CELLS = ("Interview", "Company", "Role", "Round", "Date (CT)", "When", "Interviewer", "Status", "Transcript", "Round page")


def same(existing, wanted):
    """True when every cell but the update stamp already reads as wanted."""
    have = existing.get("properties") or {}
    return all(_plain(have.get(name) or {}) == _plain({"type": PROPERTIES[name], PROPERTIES[name]: wanted[name][PROPERTIES[name]]}) for name in CELLS)


def existing_rows(client, source, context=None):
    """{round page url: row} for every live row; a read that is not complete raises NotionError."""
    rows, cursor = {}, None
    for _ in range(20):
        if context:
            context.require_time()
        page = client.query_data_source(source, {"page_size": LIMIT, **({"start_cursor": cursor} if cursor else {})})
        for row in page.get("results") or []:
            if row.get("archived") or row.get("in_trash"):
                continue
            url = _plain((row.get("properties") or {}).get("Round page") or {"type": "url", "url": None})
            if url:
                rows.setdefault(url, row)
        cursor = page.get("next_cursor")
        if not page.get("has_more") or not cursor:
            return rows
    raise NotionError("INTERVIEW_SCAN_INCOMPLETE")


def sync(client, environ, rounds, live, context, counts, stamp=None, today=None):
    """Bring the list up to date: one row per machine round in `rounds` ((parent, child) pairs), a changed row updated, a missing one created, a row whose round is gone marked Retired.
    Counts and fixed codes only. -> the fixed code."""
    code, source = find_source(client, (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip(), context)
    counts["why"][code] = counts["why"].get(code, 0) + 1
    if source is None:
        return code
    stamp = stamp or ct_stamp.stamp()
    try:
        have = existing_rows(client, source, context)
        wanted, seen = {}, {page_url(child.page_id) for _, child in rounds}
        for parent, child in rounds:
            if context:
                context.require_time()
            row = facts(client, parent, child, context, today)
            if row is None:
                counts["roster_skipped"] = counts.get("roster_skipped", 0) + 1
                continue
            wanted[row["url"]] = row
            if row["held"]:
                held.repair(client, child.page_id, live, counts, context)                      # LIFE-958: a Held round's Verify-scheduling sentence becomes the page's own follow-up, or None
        for url, row in wanted.items():
            props = properties(row, stamp)
            old = have.get(url)
            if old is not None and same(old, props):
                continue
            counts["roster_planned"] = counts.get("roster_planned", 0) + 1
            if not live:
                continue
            if old is None:
                client.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": source}, "properties": props})
                counts["roster_created"] = counts.get("roster_created", 0) + 1
            else:
                client.call("PATCH", f"/pages/{old['id']}", {"properties": props})
                counts["roster_updated"] = counts.get("roster_updated", 0) + 1
        for url, old in have.items():
            if url in seen or _plain((old.get("properties") or {}).get("Status") or {"type": "select", "select": None}) == "Retired":
                continue
            counts["roster_planned"] = counts.get("roster_planned", 0) + 1
            if live:
                client.call("PATCH", f"/pages/{old['id']}", {"properties": {"Status": {"select": {"name": "Retired"}}, "Updated (CT)": {"rich_text": rich_text(stamp)}}})
                counts["roster_retired"] = counts.get("roster_retired", 0) + 1
        return "roster_synced"
    except (NotionError, KeyError, TypeError, ValueError, AttributeError):
        counts["why"]["roster_failed"] = counts["why"].get("roster_failed", 0) + 1
        return "roster_failed"
