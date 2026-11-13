"""The existing Recruiters data source, and nothing else: create after absence is proven, update only a single non-Done match, read every write back.

Natural key: Week Ending + Recruiter / Person + Recruiter Company + Role (normalized, no fuzzy matching). Two matches is an ambiguity and nothing is changed.
V7 never writes Done, never reopens a Done row and never replaces a non-blank What they want. Enrichment of a blank company or role: D193. Action state: D220 (RECR-1.4)."""
# Detail kept from the former docstring:
# - A row with the same person and week, the SAME non-empty Source URL as the message, and only a BLANK Recruiter Company or Role as the difference is the same row, enriched under the same
#   page (D193): only blank fields are filled (company, role, What they want, client, medium), never a human's value, and the corrected key must then resolve to exactly that page.
#   A different or missing source link is a different message: nothing is merged and the original link is never overwritten.
# - Every write is read back and checked: owned fields, Done / Active / Needs review preserved, no duplicate.
# - First Contact (LIFE-20, Jim Oct 23 2026): set once, on create, to the date of the message the row is created from; an update never writes or changes it; older rows are never backfilled.
# - Action state (RECR-1.4, D220): a new row starts Needs Action; a newer message reopens a Waiting or blank row as Needs Action; a Waiting row with no Follow-up Date gets one two business
#   days out, and a Waiting row whose Follow-up Date has come returns to Needs Action. Completed and Dismissed rows, Done rows and inactive rows are history and are never changed.
from datetime import date, datetime, timedelta
from urllib.parse import quote

from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.platform.notion_pages import query
from .identity import key, norm

SCHEMA = {"Recruiter / Person": "title", "Recruiter Company": "rich_text", "Client": "rich_text", "Role": "rich_text", "Medium": "rich_text", "What they want": "rich_text",
          "Last Contact": "date", "Week Ending": "date", "Done": "checkbox", "Active": "checkbox", "Needs review": "checkbox", "Source URL": "url",
          "Action Status": "select", "Follow-up Date": "date", "First Contact": "date"}
MAX_PAGES = 20
NEEDS_ACTION, WAITING = "Needs Action", "Waiting"
REOPENABLE = ("", WAITING)
REPLIED, OFFERED, CONFIRMED, DECLINED = "Replied", "Referral Offered", "Referral Confirmed", "Declined"
CONVERSATION = (REPLIED, OFFERED, CONFIRMED)                          # Jim-recorded progress of a job-linked outreach row, in order (offered is never confirmed)
HISTORY = ("Completed", "Dismissed", DECLINED)                        # history: a later message changes nothing on these rows
FOLLOW_UP_DAYS = 2
FILLABLE = (("Recruiter Company", "company"), ("Role", "role"), ("Client", "client"), ("Medium", "medium"), ("What they want", "what"))


class RecruitersError(NotionError):
    """Fixed failure codes only; never a name, company, address or value."""


def check_schema(notion):
    with fixed(NotionError, RecruitersError, "RECRUITERS_NOTION_SCHEMA_READ_FAILED"):
        props = notion.call("GET", f"/data_sources/{quote(notion.source, safe='')}").get("properties")
    if not isinstance(props, dict) or any(not isinstance(props.get(n), dict) or props[n].get("type") != t for n, t in SCHEMA.items()):
        raise RecruitersError("RECRUITERS_NOTION_SCHEMA_MISMATCH")


def _text(prop):
    items = prop.get(prop.get("type")) if isinstance(prop, dict) else None
    return "".join(i.get("plain_text") or (i.get("text") or {}).get("content", "") for i in items or [] if isinstance(i, dict))


def _actor(user):
    return str(user.get("id") or "") if isinstance(user, dict) else ""


def read(page):
    props = page.get("properties")
    if not isinstance(props, dict):
        raise RecruitersError("RECRUITERS_NOTION_ROW_INVALID")
    date = lambda name: ((props.get(name) or {}).get("date") or {}).get("start") or ""
    return {"page_id": page.get("id", ""), "person": _text(props.get("Recruiter / Person")), "company": _text(props.get("Recruiter Company")), "client": _text(props.get("Client")),
            "role": _text(props.get("Role")), "medium": _text(props.get("Medium")), "what": _text(props.get("What they want")), "last_contact": date("Last Contact"),
            "week_ending": date("Week Ending")[:10], "done": bool((props.get("Done") or {}).get("checkbox")), "active": bool((props.get("Active") or {}).get("checkbox")),
            "needs_review": bool((props.get("Needs review") or {}).get("checkbox")), "source_url": (props.get("Source URL") or {}).get("url") or "",
            "created_by": _actor(page.get("created_by")), "edited_by": _actor(page.get("last_edited_by")),
            "action": ((props.get("Action Status") or {}).get("select") or {}).get("name") or "", "follow_up": date("Follow-up Date")[:10], "first_contact": date("First Contact"),
            "jobs": [str(r.get("id") or "") for r in (props.get("Job") or {}).get("relation") or [] if isinstance(r, dict)], "contact": _formula(props.get("Contact"))}


def _formula(prop):
    """The `Contact` formula's text (a profile or mail link the database itself computes), or "" when it is absent or not text. Read-only: V7 never writes it."""
    value = (prop or {}).get("formula") if isinstance(prop, dict) else None
    return (value.get("string") or "").strip() if isinstance(value, dict) and value.get("type") == "string" else ""


def week_rows(notion, week):
    """Every row whose Week Ending is `week`, following every page; an incomplete read raises (no create on an unproven absence)."""
    return rows_where(notion, {"property": "Week Ending", "date": {"equals": week}})


def matches(rows, candidate):
    wanted = key(candidate["week_ending"], candidate["person"], candidate["company"], candidate["role"])
    return [r for r in rows if not r["jobs"] and key(r["week_ending"], r["person"], r["company"], r["role"]) == wanted]     # a job-linked outreach row is never an inbound match (D221)


def _blank(value):
    return not str(value or "").strip()


def enrichable(rows, candidate):
    """Rows for the candidate's person and week with no exact key match whose only differences are blank company / role fields the candidate can fill, and that were made from
    THIS message: the row's Source URL must equal the candidate's, exactly and non-empty. Same recruiter and same week is not the same message (two roles, two threads)."""
    out = []
    source = str(candidate.get("source_url") or "")
    if not source:
        return out
    for r in rows:
        if r["jobs"] or r["week_ending"] != candidate["week_ending"] or norm(r["person"]) != norm(candidate["person"]):
            continue
        if r["source_url"] != source:
            continue
        fills = 0
        for field in ("company", "role"):
            if _blank(r[field]):
                fills += 1 if candidate[field] else 0
            elif norm(r[field]) != norm(candidate[field]):
                fills = -99
        if fills > 0:
            out.append(r)
    return out


def _newer(candidate, existing):
    try:
        return datetime.fromisoformat(candidate["last_contact"].replace("Z", "+00:00")) > datetime.fromisoformat(existing["last_contact"].replace("Z", "+00:00"))
    except ValueError:
        return True


def plan(rows, candidate):
    """-> ("create", None, None) | ("update", row, props) | ("existing", row, None) | ("done", row, None) | ("ambiguous", None, None)"""
    found = matches(rows, candidate) or enrichable(rows, candidate)
    if not found:
        return "create", None, None
    if len(found) > 1:
        return "ambiguous", None, None
    row = found[0]
    if row["done"]:
        return "done", row, None                                    # a later message never reopens, and never touches, a Done row
    if row["action"] in HISTORY:
        return "existing", row, None
    props = {}
    if _newer(candidate, row):
        props["Last Contact"] = {"date": {"start": candidate["last_contact"]}}
        if candidate["source_url"]:
            props["Source URL"] = {"url": candidate["source_url"]}
        if row["action"] in REOPENABLE:                               # a new reply is something to act on again
            props["Action Status"] = {"select": {"name": NEEDS_ACTION}}
    for name, field in FILLABLE:                                      # fill a blank from source evidence; never replace a human's value
        if _blank(row[field]) and candidate.get(field):
            props[name] = {"rich_text": rich_text(candidate[field])}
    return ("update", row, props) if props else ("existing", row, None)


def _create_props(c):
    props = {"Recruiter / Person": {"title": rich_text(c["person"])}, "Medium": {"rich_text": rich_text(c["medium"])}, "Last Contact": {"date": {"start": c["last_contact"]}},
             "Week Ending": {"date": {"start": c["week_ending"]}}, "Done": {"checkbox": False}, "Active": {"checkbox": True}, "Needs review": {"checkbox": bool(c["needs_review"])},
             "Action Status": {"select": {"name": NEEDS_ACTION}}, "First Contact": {"date": {"start": c["last_contact"]}}}      # LIFE-20: the date of the message this row is created from; set once, never updated
    for name, field in (("Recruiter Company", "company"), ("Client", "client"), ("Role", "role"), ("What they want", "what")):
        if c.get(field):
            props[name] = {"rich_text": rich_text(c[field])}
    if c["source_url"]:
        props["Source URL"] = {"url": c["source_url"]}
    return props


def create(notion, candidate):
    """Create one row (one non-retried write), then prove it: exactly one row has the key and it holds the owned values with Done false and What they want as written."""
    with fixed(NotionError, RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
        notion.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": notion.source}, "properties": _create_props(candidate)})
    found = matches(week_rows(notion, candidate["week_ending"]), candidate)
    if len(found) != 1:
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    row = found[0]
    if (row["done"] or not row["active"] or row["needs_review"] != bool(candidate["needs_review"]) or row["what"] != (candidate.get("what") or "")
            or row["action"] != NEEDS_ACTION or not _same_instant(row["first_contact"], candidate["last_contact"])):
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")


def update(notion, row, props, candidate):
    """One non-retried bounded update, then prove it: still one row under the candidate's key, the same page, Done / Active / Needs review untouched, every written value present
    and every value that was not written unchanged (a human's What they want, company or role is never replaced)."""
    with fixed(NotionError, RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
        notion.update_page_properties(row["page_id"], props)
    found = matches(week_rows(notion, candidate["week_ending"]), candidate)
    if len(found) != 1 or found[0]["page_id"] != row["page_id"]:
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    after = found[0]
    if (after["done"], after["active"], after["needs_review"], after["person"]) != (row["done"], row["active"], row["needs_review"], row["person"]):
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    if "Last Contact" in props and not _same_instant(after["last_contact"], candidate["last_contact"]):
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    if (after["action"] != (NEEDS_ACTION if "Action Status" in props else row["action"]) or after["follow_up"] != row["follow_up"]
            or after["first_contact"] != row["first_contact"]):                                  # a later message never moves the first contact
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    for name, field in FILLABLE:
        expected = candidate[field] if name in props else row[field]
        if after[field] != expected:
            raise RecruitersError("RECRUITERS_READBACK_MISMATCH")


def _same_instant(a, b):
    try:
        return datetime.fromisoformat(a.replace("Z", "+00:00")) == datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return False


def add_business_days(day, n):
    while n:
        day += timedelta(days=1)
        n -= day.weekday() < 5
    return day


def waiting_rows(notion):
    """Every Waiting row, following every page; an incomplete read raises (nothing is changed on a partial view)."""
    return rows_where(notion, {"property": "Action Status", "select": {"equals": WAITING}})


def rows_where(notion, where):
    """Every row matching the Notion filter `where`, following every page; an incomplete read raises."""
    return [read(p) for page in query(notion, None, {"page_size": 100, "filter": where}, RecruitersError, incomplete="RECRUITERS_NOTION_QUERY_INCOMPLETE", repeat="RECRUITERS_NOTION_PAGINATION_INVALID", capped="RECRUITERS_NOTION_PAGINATION_INCOMPLETE", limit=MAX_PAGES, read="RECRUITERS_NOTION_QUERY_FAILED") for p in page["results"]]


def follow_up_plan(row, today):
    """-> ("schedule", props) | ("due", props) | (None, None) for one Waiting row. A Done or inactive row, or a date that is not a date, is left alone."""
    if row["done"] or not row["active"] or row["action"] != WAITING:
        return None, None
    if not row["follow_up"]:
        return "schedule", {"Follow-up Date": {"date": {"start": add_business_days(today, FOLLOW_UP_DAYS).isoformat()}}}
    try:
        due = date.fromisoformat(row["follow_up"])
    except ValueError:
        return None, None
    return ("due", {"Action Status": {"select": {"name": NEEDS_ACTION}}}) if due <= today else (None, None)


def set_follow_up(notion, row, props):
    """One non-retried update, then prove it on a fresh read of the row's week: same page, only the written field changed."""
    with fixed(NotionError, RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
        notion.update_page_properties(row["page_id"], props)
    after = [r for r in week_rows(notion, row["week_ending"]) if r["page_id"] == row["page_id"]]
    if len(after) != 1:
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    expected = dict(row)
    if "Follow-up Date" in props:
        expected["follow_up"] = props["Follow-up Date"]["date"]["start"]
    if "Action Status" in props:
        expected["action"] = props["Action Status"]["select"]["name"]
    if {k: v for k, v in after[0].items() if k != "edited_by"} != {k: v for k, v in expected.items() if k != "edited_by"}:
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")


CLOSE_TO = ("Completed", "Dismissed")                                 # LIFE-20 (Jim, Oct 23 2026): the two ways Jim closes an open recruiter row from the Daily Report


def own_row(notion, page_id, source=None):
    """ONE row of this data source (`source`, else the client's own) by its exact page id, read fresh; a page anywhere else is refused."""
    with fixed(NotionError, RecruitersError, "RECRUITERS_ROW_UNREADABLE"):
        page = notion.get_page(page_id)
    flat = lambda value: str(value or "").replace("-", "").lower()      # noqa: E731
    parent = page.get("parent") if isinstance(page, dict) else None
    if (not isinstance(parent, dict) or parent.get("type") != "data_source_id" or not flat(parent.get("data_source_id"))
            or flat(parent.get("data_source_id")) != flat(source or notion.source) or page.get("in_trash") or page.get("archived")):
        raise RecruitersError("RECRUITERS_ROW_NOT_IN_SOURCE")
    return read(page)


def close_row(notion, page_id, status):
    """Jim's confirmed close of ONE open row (Needs Action or Waiting, not Done, Active) as Completed or Dismissed. Only Action Status is written, once, then the row is read back:
    the status as written and every other value unchanged. A row already closed that way is left alone; any other row is not changed.
    -> {"status": "recorded" | "already_recorded" | "not_changed", "verified": True, "action"}"""
    if status not in CLOSE_TO:
        raise RecruitersError("RECRUITERS_CLOSE_INVALID")
    check_schema(notion)
    row = own_row(notion, page_id)
    if row["action"] == status:
        return {"status": "already_recorded", "verified": True, "action": row["action"]}
    if row["done"] or not row["active"] or row["action"] not in (NEEDS_ACTION, WAITING):
        return {"status": "not_changed", "verified": True, "action": row["action"]}
    with fixed(NotionError, RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
        notion.update_page_properties(row["page_id"], {"Action Status": {"select": {"name": status}}})
    after = own_row(notion, page_id)
    keep = lambda r: {k: v for k, v in r.items() if k not in ("action", "edited_by")}      # noqa: E731
    if after["action"] != status or keep(after) != keep(row):
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    return {"status": "recorded", "verified": True, "action": status}
