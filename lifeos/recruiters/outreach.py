"""Role-specific outreach on the Recruiters data source, linked to ONE exact Job Ledger job by the `Job` relation.

Only an explicit Mark as Sent creates an outreach row (Waiting, Follow-up Date two business days out), keyed by
(job page id, person). Inbound recruiter mail never matches a row that carries a `Job`. Fixed codes only; no
names in logs."""

from datetime import datetime
from urllib.parse import quote

from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.platform.notion_pages import query
from . import notion as store
from .identity import norm, week_ending

JOB = "Job"
MEDIUM = "LinkedIn"
PERSON_MAX = 80


def _id(value):
    return str(value or "").replace("-", "").lower()


def check_schema(notion, ledger_source):
    with fixed(NotionError, store.RecruitersError, "OUTREACH_SCHEMA_READ_FAILED"):
        props = notion.call("GET", f"/data_sources/{quote(notion.source, safe='')}").get("properties")
    job = (props or {}).get(JOB) if isinstance(props, dict) else None
    target = ((job or {}).get("relation") or {}).get("data_source_id")
    if not isinstance(job, dict) or job.get("type") != "relation" or _id(target) != _id(ledger_source):
        raise store.RecruitersError("OUTREACH_SCHEMA_MISSING")
    if not store.schema_ok(props):
        raise store.RecruitersError("RECRUITERS_NOTION_SCHEMA_MISMATCH")


def job_rows(notion, job_id):
    body = {"page_size": 100, "filter": {"property": JOB, "relation": {"contains": job_id}}}
    return [
        r
        for r in (
            store.read(p)
            for page in query(
                notion,
                None,
                body,
                store.RecruitersError,
                incomplete="RECRUITERS_NOTION_QUERY_INCOMPLETE",
                repeat="RECRUITERS_NOTION_PAGINATION_INVALID",
                capped="RECRUITERS_NOTION_PAGINATION_INCOMPLETE",
                limit=store.MAX_PAGES,
                read="RECRUITERS_NOTION_QUERY_FAILED",
            )
            for p in page["results"]
        )
        if _id(job_id) in map(_id, r["jobs"])
    ]


def valid_person(person):
    person = " ".join(str(person or "").split())
    return person if 0 < len(person) <= PERSON_MAX and person.isprintable() else ""


def employer_from(reason):
    """The person's CURRENT employer from a Network Leads line ("Listed at <company> as <title>, as of ..."), else
    ""."""
    if not str(reason or "").startswith("Listed at "):
        return ""
    where = str(reason)[len("Listed at ") :].rpartition(", as of ")[0].partition(" as ")[0]
    return "" if where == "this company" else valid_person(where)


def _props(job, person, now, employer=""):
    return {
        "Recruiter": {"title": rich_text(person)},
        **({"Company": {"rich_text": rich_text(employer)}} if employer else {}),
        "Client": {"rich_text": rich_text(job["company"])},
        "Role": {"rich_text": rich_text(job["role"])},
        "Medium": {"rich_text": rich_text(MEDIUM)},
        "Last Contact": {"date": {"start": now.replace(second=0, microsecond=0).isoformat()}},
        "Week Ending": {"date": {"start": week_ending(now)}},
        "Done": {"checkbox": False},
        "Active": {"checkbox": True},
        "Needs review": {"checkbox": False},
        "Action Status": {"select": {"name": store.WAITING}},
        "Follow-up Date": {"date": {"start": store.add_business_days(now.date(), store.FOLLOW_UP_DAYS).isoformat()}},
        JOB: {"relation": [{"id": job["id"]}]},
    }


def mark_sent(notion, job, person, now, ledger_source, employer=""):
    """Record a confirmed Mark as Sent for one job and person (employer stored as Company when known). An existing
    row is returned as it is; otherwise one non-retried create, then a fresh read must show exactly one Waiting
    row linked to the job. -> {"status": "recorded" | "already_recorded", "verified": True, "state", "action",
    "follow_up"}"""
    person, employer = valid_person(person), valid_person(employer)
    if not person or not job.get("id") or not job.get("company") or not job.get("role"):
        raise store.RecruitersError("OUTREACH_REQUEST_INVALID")
    check_schema(notion, ledger_source)
    mine = [r for r in job_rows(notion, job["id"]) if norm(r["person"]) == norm(person)]
    if len(mine) > 1:
        raise store.RecruitersError("OUTREACH_AMBIGUOUS")
    if mine:
        row = mine[0]
        state = "closed" if row["done"] or not row["active"] or row["action"] in store.HISTORY else "open"
        return {
            "status": "already_recorded",
            "verified": True,
            "state": state,
            "action": row["action"],
            "follow_up": row["follow_up"],
        }
    props = _props(job, person, now, employer)
    with fixed(NotionError, store.RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
        notion.call_once(
            "POST",
            "/pages",
            {"parent": {"type": "data_source_id", "data_source_id": notion.source}, "properties": props},
        )
    after = [r for r in job_rows(notion, job["id"]) if norm(r["person"]) == norm(person)]
    want = props["Follow-up Date"]["date"]["start"]
    if (
        len(after) != 1
        or after[0]["action"] != store.WAITING
        or after[0]["follow_up"] != want
        or after[0]["company"] != employer
        or after[0]["done"]
        or not after[0]["active"]
    ):
        raise store.RecruitersError("OUTREACH_READBACK_MISMATCH")
    return {"status": "recorded", "verified": True, "state": "open", "action": store.WAITING, "follow_up": want}


def _options(notion):
    props = notion.call("GET", f"/data_sources/{quote(notion.source, safe='')}").get("properties") or {}
    return {
        o.get("name")
        for o in ((props.get("Action Status") or {}).get("select") or {}).get("options") or []
        if isinstance(o, dict)
    }


def set_conversation(notion, job, person, status, ledger_source):
    """Update ONE recorded conversation (job, person) to Replied, Referral Offered, Referral Confirmed or Declined.
    Never creates a row, moves an open conversation backwards, or touches a closed one; refuses before any write
    when the Action Status option is missing. -> {"status", "verified", "action", "follow_up"}"""
    person = valid_person(person)
    if status not in (*store.CONVERSATION, store.DECLINED) or not person or not job.get("id"):
        raise store.RecruitersError("OUTREACH_REQUEST_INVALID")
    check_schema(notion, ledger_source)
    if status not in _options(notion):
        raise store.RecruitersError("OUTREACH_STATUS_UNAVAILABLE")
    mine = [r for r in job_rows(notion, job["id"]) if norm(r["person"]) == norm(person)]
    if len(mine) > 1:
        raise store.RecruitersError("OUTREACH_AMBIGUOUS")
    if not mine:
        raise store.RecruitersError("OUTREACH_NOT_RECORDED")
    row = mine[0]
    seen = {"verified": True, "action": row["action"], "follow_up": row["follow_up"]}
    if row["done"] or not row["active"] or row["action"] in store.HISTORY:
        return {**seen, "status": "not_changed"}
    if row["action"] == status:
        return {**seen, "status": "already_recorded"}
    # noqa: E731 - Waiting / Needs Action / blank rank below Replied
    rank = lambda a: store.CONVERSATION.index(a) if a in store.CONVERSATION else -1
    if status != store.DECLINED and rank(status) < rank(row["action"]):
        return {**seen, "status": "not_changed"}
    with fixed(NotionError, store.RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
        notion.update_page_properties(row["page_id"], {"Action Status": {"select": {"name": status}}})
    after = [r for r in job_rows(notion, job["id"]) if norm(r["person"]) == norm(person)]
    if (
        len(after) != 1
        or after[0]["page_id"] != row["page_id"]
        or after[0]["action"] != status
        or after[0]["follow_up"] != row["follow_up"]
        or after[0]["done"]
        or not after[0]["active"]
    ):
        raise store.RecruitersError("OUTREACH_READBACK_MISMATCH")
    return {"status": "recorded", "verified": True, "action": status, "follow_up": row["follow_up"]}


def project(rows, jobs, today):
    """The native Job Outreach table: one row per exact job. `jobs` maps a Ledger page id to {"company", "role",
    "url"}. -> [{"job", "outreach", "next", "link", "sort"}], most urgent follow-up first."""
    grouped = {}
    for r in rows:
        if r["done"] or not r["active"] or r["action"] in store.HISTORY:
            continue
        for jid in r["jobs"]:
            if _id(jid) in jobs:
                grouped.setdefault(_id(jid), []).append(r)
    out = []
    for jid, items in grouped.items():
        job = jobs[jid]
        act = [r for r in items if r["action"] == store.NEEDS_ACTION]
        dates = sorted(r["follow_up"] for r in items if r["action"] == store.WAITING and r["follow_up"])
        if act:
            nxt, sort = f"{len(act)} need{'s' if len(act) == 1 else ''} action · Today", "0000-00-00"
        elif dates:
            nxt, sort = (
                (
                    "Follow up · Today"
                    if dates[0] <= today.isoformat()
                    else f"Follow up · {datetime.fromisoformat(dates[0]):%b} {int(dates[0][8:])}"
                ),
                dates[0],
            )
        else:
            nxt, sort = "—", "9999-99-99"
        replied = len([r for r in items if r["action"] in store.CONVERSATION])
        confirmed = len([r for r in items if r["action"] == store.CONFIRMED])
        extra = (f" · {replied} replied" if replied else "") + (
            f" · {confirmed} referral{'s' if confirmed != 1 else ''} confirmed" if confirmed else ""
        )
        out.append(
            {
                "job": f"{job['company']} — {job['role']}",
                "outreach": f"{len(items)} contacted · {len(act)} need action{extra}",
                "next": nxt,
                "link": job["url"],
                "sort": sort,
            }
        )
    return sorted(out, key=lambda r: (r["sort"], r["job"]))
