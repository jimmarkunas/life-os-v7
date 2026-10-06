"""The existing Recruiters data source, and nothing else. Natural key: Week Ending + Recruiter / Person + Recruiter Company + Role (normalized, no fuzzy matching).

Create only after absence is proven for the week; update only a row that is the single match, not Done, with a newer message; two matches is an ambiguity and nothing is changed.
Every write is read back and checked: owned fields, Done preserved, What they want preserved, no duplicate. V7 never writes What they want or Done on an existing row."""
from datetime import datetime
from urllib.parse import quote

from lifeos.platform.notion_client import NotionError, rich_text
from .identity import key

SCHEMA = {"Recruiter / Person": "title", "Recruiter Company": "rich_text", "Client": "rich_text", "Role": "rich_text", "Medium": "rich_text", "What they want": "rich_text",
          "Last Contact": "date", "Week Ending": "date", "Done": "checkbox", "Active": "checkbox", "Needs review": "checkbox", "Source URL": "url"}
MAX_PAGES = 20


class RecruitersError(NotionError):
    """Fixed failure codes only; never a name, company, address or value."""


def check_schema(notion):
    try:
        props = notion.call("GET", f"/data_sources/{quote(notion.source, safe='')}").get("properties")
    except NotionError:
        raise RecruitersError("RECRUITERS_NOTION_SCHEMA_READ_FAILED") from None
    if not isinstance(props, dict) or any(not isinstance(props.get(n), dict) or props[n].get("type") != t for n, t in SCHEMA.items()):
        raise RecruitersError("RECRUITERS_NOTION_SCHEMA_MISMATCH")


def _text(prop):
    items = prop.get(prop.get("type")) if isinstance(prop, dict) else None
    return "".join(i.get("plain_text") or (i.get("text") or {}).get("content", "") for i in items or [] if isinstance(i, dict))


def read(page):
    props = page.get("properties")
    if not isinstance(props, dict):
        raise RecruitersError("RECRUITERS_NOTION_ROW_INVALID")
    date = lambda name: ((props.get(name) or {}).get("date") or {}).get("start") or ""
    return {"page_id": page.get("id", ""), "person": _text(props.get("Recruiter / Person")), "company": _text(props.get("Recruiter Company")), "client": _text(props.get("Client")),
            "role": _text(props.get("Role")), "medium": _text(props.get("Medium")), "what": _text(props.get("What they want")), "last_contact": date("Last Contact"),
            "week_ending": date("Week Ending")[:10], "done": bool((props.get("Done") or {}).get("checkbox")), "active": bool((props.get("Active") or {}).get("checkbox")),
            "needs_review": bool((props.get("Needs review") or {}).get("checkbox")), "source_url": (props.get("Source URL") or {}).get("url") or ""}


def week_rows(notion, week):
    """Every row whose Week Ending is `week`, following every page; an incomplete read raises (no create on an unproven absence)."""
    rows, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):
        body = {"page_size": 100, "filter": {"property": "Week Ending", "date": {"equals": week}}, **({"start_cursor": cursor} if cursor else {})}
        try:
            page = notion.query_data_source(None, body)
        except NotionError:
            raise RecruitersError("RECRUITERS_NOTION_QUERY_FAILED") from None
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise RecruitersError("RECRUITERS_NOTION_QUERY_INCOMPLETE")
        rows += [read(p) for p in page["results"]]
        if not page["has_more"]:
            return rows
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise RecruitersError("RECRUITERS_NOTION_PAGINATION_INVALID")
        seen.add(cursor)
    raise RecruitersError("RECRUITERS_NOTION_PAGINATION_INCOMPLETE")


def matches(rows, candidate):
    wanted = key(candidate["week_ending"], candidate["person"], candidate["company"], candidate["role"])
    return [r for r in rows if key(r["week_ending"], r["person"], r["company"], r["role"]) == wanted]


def _newer(candidate, existing):
    try:
        return datetime.fromisoformat(candidate["last_contact"].replace("Z", "+00:00")) > datetime.fromisoformat(existing["last_contact"].replace("Z", "+00:00"))
    except ValueError:
        return True


def plan(rows, candidate):
    """-> ("create", None, None) | ("update", row, props) | ("existing", row, None) | ("done", row, None) | ("ambiguous", None, None)"""
    found = matches(rows, candidate)
    if not found:
        return "create", None, None
    if len(found) > 1:
        return "ambiguous", None, None
    row = found[0]
    if row["done"]:
        return "done", row, None                                    # a later message never reopens, and never touches, a Done row
    props = {}
    if _newer(candidate, row):
        props["Last Contact"] = {"date": {"start": candidate["last_contact"]}}
        if candidate["source_url"]:
            props["Source URL"] = {"url": candidate["source_url"]}
    for name, field in (("Client", "client"), ("Medium", "medium")):  # fill a blank from source evidence; never replace a human's value
        if not row[field] and candidate[field]:
            props[name] = {"rich_text": rich_text(candidate[field])}
    return ("update", row, props) if props else ("existing", row, None)


def _create_props(c):
    props = {"Recruiter / Person": {"title": rich_text(c["person"])}, "Medium": {"rich_text": rich_text(c["medium"])}, "Last Contact": {"date": {"start": c["last_contact"]}},
             "Week Ending": {"date": {"start": c["week_ending"]}}, "Done": {"checkbox": False}, "Active": {"checkbox": True}, "Needs review": {"checkbox": bool(c["needs_review"])}}
    for name, field in (("Recruiter Company", "company"), ("Client", "client"), ("Role", "role")):
        if c[field]:
            props[name] = {"rich_text": rich_text(c[field])}
    if c["source_url"]:
        props["Source URL"] = {"url": c["source_url"]}
    return props


def create(notion, candidate):
    """Create one row (one non-retried write), then prove it: exactly one row has the key and it holds the owned values with Done false and What they want blank."""
    try:
        notion.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": notion.source}, "properties": _create_props(candidate)})
    except NotionError:
        raise RecruitersError("RECRUITERS_NOTION_WRITE_FAILED") from None
    found = matches(week_rows(notion, candidate["week_ending"]), candidate)
    if len(found) != 1:
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    row = found[0]
    if row["done"] or not row["active"] or row["needs_review"] != bool(candidate["needs_review"]) or row["what"]:
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")


def update(notion, row, props, candidate):
    """One non-retried bounded update, then prove it: still one row, same page, Done / What they want / Active / Needs review untouched, written values present."""
    try:
        notion.update_page_properties(row["page_id"], props)
    except NotionError:
        raise RecruitersError("RECRUITERS_NOTION_WRITE_FAILED") from None
    found = matches(week_rows(notion, candidate["week_ending"]), candidate)
    if len(found) != 1 or found[0]["page_id"] != row["page_id"]:
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    after = found[0]
    if (after["done"], after["what"], after["active"], after["needs_review"]) != (row["done"], row["what"], row["active"], row["needs_review"]):
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    if "Last Contact" in props and not _same_instant(after["last_contact"], candidate["last_contact"]):
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    for name, field in (("Client", "client"), ("Medium", "medium")):
        if name in props and after[field] != candidate[field]:
            raise RecruitersError("RECRUITERS_READBACK_MISMATCH")


def _same_instant(a, b):
    try:
        return datetime.fromisoformat(a.replace("Z", "+00:00")) == datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return False
