"""The existing Recruiters data source, and nothing else: create after absence is proven, read every write back.

Natural key: Week Ending + Recruiter + Company + Role (normalized); two matches are ambiguous and nothing changes.
A Done row is never reopened and a non-blank What they want is never replaced. A row for the same person, week and
non-empty Source URL that differs only in a blank Company or Role is enriched in place. First Contact is set once.
A Waiting row gets a Follow-up Date two business days out and returns to Needs Action when due."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote

from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.platform.notion_pages import query
from .identity import key, norm

SCHEMA = {
    "Recruiter": "title",
    "Company": "rich_text",
    "Client": "rich_text",
    "Role": "rich_text",
    "Medium": "rich_text",
    "What they want": "rich_text",
    "Last Contact": "date",
    "Week Ending": "date",
    "Done": "checkbox",
    "Active": "checkbox",
    "Needs review": "checkbox",
    "Source URL": "url",
    "Action Status": "select",
    "Follow-up Date": "date",
    "First Contact": "date",
}
MAX_PAGES = 20
NEEDS_ACTION, WAITING = "Needs Action", "Waiting"
REOPENABLE = ("", WAITING)
REPLIED, OFFERED, CONFIRMED, DECLINED = "Replied", "Referral Offered", "Referral Confirmed", "Declined"
CONVERSATION = (REPLIED, OFFERED, CONFIRMED)
HISTORY = ("Completed", "Dismissed", DECLINED)
FOLLOW_UP_DAYS = 2
FILLABLE = (
    ("Company", "company"),
    ("Role", "role"),
    ("Client", "client"),
    ("Medium", "medium"),
    ("What they want", "what"),
)


def schema_ok(props):
    return isinstance(props, dict) and all(
        isinstance(props.get(name), dict) and props[name].get("type") == kind for name, kind in SCHEMA.items()
    )


class RecruitersError(NotionError):
    """Raised with a fixed code."""


def check_schema(notion):
    with fixed(NotionError, RecruitersError, "RECRUITERS_NOTION_SCHEMA_READ_FAILED"):
        props = notion.call("GET", f"/data_sources/{quote(notion.source, safe='')}").get("properties")
    if not schema_ok(props):
        raise RecruitersError("RECRUITERS_NOTION_SCHEMA_MISMATCH")


def _text(prop):
    items = prop.get(prop.get("type")) if isinstance(prop, dict) else None
    return "".join(
        i.get("plain_text") or (i.get("text") or {}).get("content", "") for i in items or [] if isinstance(i, dict)
    )


def _actor(user):
    return str(user.get("id") or "") if isinstance(user, dict) else ""


def read(page):
    props = page.get("properties")
    if not isinstance(props, dict):
        raise RecruitersError("RECRUITERS_NOTION_ROW_INVALID")
    date = lambda name: ((props.get(name) or {}).get("date") or {}).get("start") or ""
    return {
        "page_id": page.get("id", ""),
        "person": _text(props.get("Recruiter")),
        "company": _text(props.get("Company")),
        "client": _text(props.get("Client")),
        "role": _text(props.get("Role")),
        "medium": _text(props.get("Medium")),
        "what": _text(props.get("What they want")),
        "last_contact": date("Last Contact"),
        "week_ending": date("Week Ending")[:10],
        "done": bool((props.get("Done") or {}).get("checkbox")),
        "active": bool((props.get("Active") or {}).get("checkbox")),
        "needs_review": bool((props.get("Needs review") or {}).get("checkbox")),
        "source_url": (props.get("Source URL") or {}).get("url") or "",
        "created_by": _actor(page.get("created_by")),
        "edited_by": _actor(page.get("last_edited_by")),
        "action": ((props.get("Action Status") or {}).get("select") or {}).get("name") or "",
        "follow_up": date("Follow-up Date")[:10],
        "first_contact": date("First Contact"),
        "jobs": [str(r.get("id") or "") for r in (props.get("Job") or {}).get("relation") or [] if isinstance(r, dict)],
        "contact": _formula(props.get("Contact")),
    }


def _formula(prop):
    value = (prop or {}).get("formula") if isinstance(prop, dict) else None
    return (value.get("string") or "").strip() if isinstance(value, dict) and value.get("type") == "string" else ""


def week_rows(notion, week):
    return rows_where(notion, {"property": "Week Ending", "date": {"equals": week}})


def matches(rows, candidate):
    wanted = key(candidate["week_ending"], candidate["person"], candidate["company"], candidate["role"])
    return [r for r in rows if not r["jobs"] and key(r["week_ending"], r["person"], r["company"], r["role"]) == wanted]


def _blank(value):
    return not str(value or "").strip()


def enrichable(rows, candidate):
    """Rows for the candidate's person and week with no exact key match, differing only in blank company / role and
    made from THIS message (equal, non-empty Source URL)."""
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
        return datetime.fromisoformat(candidate["last_contact"].replace("Z", "+00:00")) > datetime.fromisoformat(
            existing["last_contact"].replace("Z", "+00:00")
        )
    except ValueError:
        return True


def plan(rows, candidate):
    """-> (action, row, props); action is "create" | "update" | "existing" | "done" | "ambiguous"."""
    found = matches(rows, candidate) or enrichable(rows, candidate)
    if not found:
        return "create", None, None
    if len(found) > 1:
        return "ambiguous", None, None
    row = found[0]
    if row["done"]:
        return "done", row, None
    if row["action"] in HISTORY:
        return "existing", row, None
    props = {}
    if _newer(candidate, row):
        props["Last Contact"] = {"date": {"start": candidate["last_contact"]}}
        if candidate["source_url"]:
            props["Source URL"] = {"url": candidate["source_url"]}
        if row["action"] in REOPENABLE:
            props["Action Status"] = {"select": {"name": NEEDS_ACTION}}
    for name, field in FILLABLE:
        if _blank(row[field]) and candidate.get(field):
            props[name] = {"rich_text": rich_text(candidate[field])}
            if field == "what" and candidate.get("what_fallback"):
                props["Needs review"] = {"checkbox": True}
    return ("update", row, props) if props else ("existing", row, None)


FALLBACK_WHAT = "Reply to their message"


def what_or_fallback(candidate):
    """-> (text, used_fallback): the extracted ask, else `Reply to their message about <role or subject>.`; a fallback
    ticks Needs review."""
    ask = " ".join(str(candidate.get("what") or "").split())
    if ask:
        return ask, False
    topic = " ".join(str(candidate.get("role") or candidate.get("subject") or "").split()).strip(" .")[:120]
    return (f"{FALLBACK_WHAT} about {topic}." if topic else f"{FALLBACK_WHAT}."), True


def _create_props(c):
    props = {
        "Recruiter": {"title": rich_text(c["person"])},
        "Medium": {"rich_text": rich_text(c["medium"])},
        "Last Contact": {"date": {"start": c["last_contact"]}},
        "Week Ending": {"date": {"start": c["week_ending"]}},
        "Done": {"checkbox": False},
        "Active": {"checkbox": True},
        "Needs review": {"checkbox": bool(c["needs_review"])},
        "Action Status": {"select": {"name": NEEDS_ACTION}},
        "First Contact": {"date": {"start": c["last_contact"]}},
    }
    for name, field in (("Company", "company"), ("Client", "client"), ("Role", "role"), ("What they want", "what")):
        if c.get(field):
            props[name] = {"rich_text": rich_text(c[field])}
    if c["source_url"]:
        props["Source URL"] = {"url": c["source_url"]}
    return props


def create(notion, candidate):
    """Create one row (one non-retried write), then prove exactly one row has the key."""
    with fixed(NotionError, RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
        notion.call_once(
            "POST",
            "/pages",
            {
                "parent": {"type": "data_source_id", "data_source_id": notion.source},
                "properties": _create_props(candidate),
            },
        )
    found = matches(week_rows(notion, candidate["week_ending"]), candidate)
    if len(found) != 1:
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    row = found[0]
    if (
        row["done"]
        or not row["active"]
        or row["needs_review"] != bool(candidate["needs_review"])
        or row["what"] != (candidate.get("what") or "")
        or row["action"] != NEEDS_ACTION
        or not _same_instant(row["first_contact"], candidate["last_contact"])
    ):
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")


def update(notion, row, props, candidate):
    """One non-retried update, then prove: same page, Done / Active / Needs review untouched, written values present."""
    with fixed(NotionError, RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
        notion.update_page_properties(row["page_id"], props)
    found = matches(week_rows(notion, candidate["week_ending"]), candidate)
    if len(found) != 1 or found[0]["page_id"] != row["page_id"]:
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    after = found[0]
    if (after["done"], after["active"], after["needs_review"], after["person"]) != (
        row["done"],
        row["active"],
        True if "Needs review" in props else row["needs_review"],
        row["person"],
    ):
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    if "Last Contact" in props and not _same_instant(after["last_contact"], candidate["last_contact"]):
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    if (
        after["action"] != (NEEDS_ACTION if "Action Status" in props else row["action"])
        or after["follow_up"] != row["follow_up"]
        or after["first_contact"] != row["first_contact"]
    ):
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
    return rows_where(notion, {"property": "Action Status", "select": {"equals": WAITING}})


def rows_where(notion, where, source=None):
    return [
        read(p)
        for page in query(
            notion,
            source,
            {"page_size": 100, "filter": where},
            RecruitersError,
            incomplete="RECRUITERS_NOTION_QUERY_INCOMPLETE",
            repeat="RECRUITERS_NOTION_PAGINATION_INVALID",
            capped="RECRUITERS_NOTION_PAGINATION_INCOMPLETE",
            limit=MAX_PAGES,
            read="RECRUITERS_NOTION_QUERY_FAILED",
        )
        for p in page["results"]
    ]


def follow_up_plan(row, today):
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
    if {k: v for k, v in after[0].items() if k != "edited_by"} != {
        k: v for k, v in expected.items() if k != "edited_by"
    }:
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")


CLOSE_TO = ("Completed", "Dismissed")


def own_row(notion, page_id, source=None):
    """ONE row of this data source by exact page id, read fresh; a page elsewhere is refused."""
    with fixed(NotionError, RecruitersError, "RECRUITERS_ROW_UNREADABLE"):
        page = notion.get_page(page_id)
    flat = lambda value: str(value or "").replace("-", "").lower()  # noqa: E731
    parent = page.get("parent") if isinstance(page, dict) else None
    if (
        not isinstance(parent, dict)
        or parent.get("type") != "data_source_id"
        or not flat(parent.get("data_source_id"))
        or flat(parent.get("data_source_id")) != flat(source or notion.source)
        or page.get("in_trash")
        or page.get("archived")
    ):
        raise RecruitersError("RECRUITERS_ROW_NOT_IN_SOURCE")
    return read(page)


CLOSED_FLAGS = {"Done": {"checkbox": True}, "Active": {"checkbox": False}, "Needs review": {"checkbox": False}}
CHICAGO = ZoneInfo("America/Chicago")


def _closed_flags(row):
    return row["done"] and not row["active"] and not row["needs_review"]


def reply_day(row):
    """The day a Needs Action row's reply is due: its Follow-up Date, else one business day after First Contact
    (Central Time)."""
    try:
        return date.fromisoformat(row["follow_up"][:10])
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat((row.get("first_contact") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return add_business_days((moment.astimezone(CHICAGO) if moment.tzinfo else moment).date(), 1)


def overdue_rows(rows, today):
    out = [
        r
        for r in rows
        if r["action"] == NEEDS_ACTION and not r["done"] and r["active"] and (reply_day(r) or today) < today
    ]
    return sorted(out, key=lambda r: (r["person"].strip().lower(), r["page_id"]))


CLOSED_STATUSES = ("Completed", "Dismissed", DECLINED)


def sync_plan(row):
    """-> properties that make Done / Active / Needs review agree with Action Status, or None."""
    if row["action"] in CLOSED_STATUSES:
        return dict(CLOSED_FLAGS) if not _closed_flags(row) else None
    if row["action"] == NEEDS_ACTION and (row["done"] or not row["active"]):
        return {"Done": {"checkbox": False}, "Active": {"checkbox": True}}
    return None


def disagreeing_rows(notion):
    closed = {
        "and": [
            {"or": [{"property": "Action Status", "select": {"equals": s}} for s in CLOSED_STATUSES]},
            {
                "or": [
                    {"property": "Done", "checkbox": {"equals": False}},
                    {"property": "Active", "checkbox": {"equals": True}},
                    {"property": "Needs review", "checkbox": {"equals": True}},
                ]
            },
        ]
    }
    open_ = {
        "and": [
            {"property": "Action Status", "select": {"equals": NEEDS_ACTION}},
            {
                "or": [
                    {"property": "Done", "checkbox": {"equals": True}},
                    {"property": "Active", "checkbox": {"equals": False}},
                ]
            },
        ]
    }
    seen, out = set(), []
    for flt in (closed, open_):
        for r in rows_where(notion, flt):
            if r["page_id"] not in seen:
                seen.add(r["page_id"])
                out.append(r)
    return out


def default_follow_up(row):
    """The reply-by date for a Needs Action row without one: one business day after First Contact, else Last Contact."""
    if row["action"] != NEEDS_ACTION or row["done"] or not row["active"] or row["follow_up"]:
        return None
    for field in ("first_contact", "last_contact"):
        try:
            moment = datetime.fromisoformat((row.get(field) or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        return add_business_days((moment.astimezone(CHICAGO) if moment.tzinfo else moment).date(), 1)
    return None


def set_props(notion, row, props, expect):
    """One non-retried update, then a fresh read of the same page: `expect` must hold and nothing else may change."""
    with fixed(NotionError, RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
        notion.update_page_properties(row["page_id"], props)
    after = own_row(notion, row["page_id"])
    want = {**row, **expect}
    if {k: v for k, v in after.items() if k != "edited_by"} != {k: v for k, v in want.items() if k != "edited_by"}:
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")


def close_row(notion, page_id, status, row=None):
    """Close ONE row as Completed or Dismissed (Done on; Active and Needs review off) in one write, then read back; a
    closed row whose flags disagree is repaired. -> {"status": "recorded" | "already_recorded" | "not_changed",
    "verified": True, "action"}"""
    if status not in CLOSE_TO:
        raise RecruitersError("RECRUITERS_CLOSE_INVALID")
    if row is None:
        check_schema(notion)
        row = own_row(notion, page_id)
    if row["action"] == status and _closed_flags(row):
        return {"status": "already_recorded", "verified": True, "action": row["action"]}
    if row["action"] != status and (row["done"] or not row["active"] or row["action"] not in (NEEDS_ACTION, WAITING)):
        return {"status": "not_changed", "verified": True, "action": row["action"]}
    with fixed(NotionError, RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
        notion.update_page_properties(row["page_id"], {"Action Status": {"select": {"name": status}}, **CLOSED_FLAGS})
    after = own_row(notion, page_id)
    keep = lambda r: {k: v for k, v in r.items() if k not in ("action", "edited_by", "done", "active", "needs_review")}  # noqa: E731
    if after["action"] != status or not _closed_flags(after) or keep(after) != keep(row):
        raise RecruitersError("RECRUITERS_READBACK_MISMATCH")
    return {"status": "recorded", "verified": True, "action": status}
