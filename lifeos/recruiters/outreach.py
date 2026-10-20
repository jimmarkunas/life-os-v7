"""LIFE-21 / LIFE-47 (D221): role-specific outreach on the canonical Recruiters data source, linked to ONE exact Job Ledger job by the additive `Job` relation.

Only Jim's explicit Mark as Sent confirmation creates an outreach row: Waiting, a Follow-up Date two business days out, the job linked. The natural key is (job page id, person):
a replay returns the existing row and never creates a second one. Inbound recruiter mail never matches, updates or enriches a row that carries a `Job` (notion.matches /
notion.enrichable skip them), so the two kinds of row cannot collide; the D220 follow-up pass still reopens a due row. The Job Outreach projection is one row per exact job.
Fixed codes only; never a name, company or id in a log."""
from datetime import datetime
from urllib.parse import quote

from lifeos.platform.notion_client import NotionError, rich_text
from . import notion as store
from .identity import norm, week_ending

JOB = "Job"
MEDIUM = "LinkedIn"
PERSON_MAX = 80


def _id(value):
    return str(value or "").replace("-", "").lower()


def check_schema(notion, ledger_source):
    """The Recruiters data source must carry `Job` as a relation to the Job Ledger data source; anything else refuses before a write."""
    try:
        props = notion.call("GET", f"/data_sources/{quote(notion.source, safe='')}").get("properties")
    except NotionError:
        raise store.RecruitersError("OUTREACH_SCHEMA_READ_FAILED") from None
    job = (props or {}).get(JOB) if isinstance(props, dict) else None
    target = ((job or {}).get("relation") or {}).get("data_source_id")
    if not isinstance(job, dict) or job.get("type") != "relation" or _id(target) != _id(ledger_source):
        raise store.RecruitersError("OUTREACH_SCHEMA_MISSING")
    if any(not isinstance(props.get(n), dict) or props[n].get("type") != t for n, t in store.SCHEMA.items()):
        raise store.RecruitersError("RECRUITERS_NOTION_SCHEMA_MISMATCH")


def job_rows(notion, job_id):
    """Every Recruiters row linked to this exact job, following every page; an incomplete read raises (no create on an unproven absence)."""
    rows, cursor, seen = [], None, set()
    for _ in range(store.MAX_PAGES):
        body = {"page_size": 100, "filter": {"property": JOB, "relation": {"contains": job_id}}, **({"start_cursor": cursor} if cursor else {})}
        try:
            page = notion.query_data_source(None, body)
        except NotionError:
            raise store.RecruitersError("RECRUITERS_NOTION_QUERY_FAILED") from None
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise store.RecruitersError("RECRUITERS_NOTION_QUERY_INCOMPLETE")
        rows += [r for r in map(store.read, page["results"]) if _id(job_id) in map(_id, r["jobs"])]
        if not page["has_more"]:
            return rows
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise store.RecruitersError("RECRUITERS_NOTION_PAGINATION_INVALID")
        seen.add(cursor)
    raise store.RecruitersError("RECRUITERS_NOTION_PAGINATION_INCOMPLETE")


def valid_person(person):
    person = " ".join(str(person or "").split())
    return person if 0 < len(person) <= PERSON_MAX and person.isprintable() else ""


def _props(job, person, now):
    return {"Recruiter / Person": {"title": rich_text(person)}, "Client": {"rich_text": rich_text(job["company"])}, "Role": {"rich_text": rich_text(job["role"])},
            "Medium": {"rich_text": rich_text(MEDIUM)}, "Last Contact": {"date": {"start": now.replace(second=0, microsecond=0).isoformat()}},
            "Week Ending": {"date": {"start": week_ending(now)}}, "Done": {"checkbox": False}, "Active": {"checkbox": True}, "Needs review": {"checkbox": False},
            "Action Status": {"select": {"name": store.WAITING}}, "Follow-up Date": {"date": {"start": store.add_business_days(now.date(), store.FOLLOW_UP_DAYS).isoformat()}},
            JOB: {"relation": [{"id": job["id"]}]}}


def mark_sent(notion, job, person, now, ledger_source):
    """Jim's confirmed Mark as Sent for one exact job and one person. -> {"status": "recorded" | "already_recorded", "verified": True, "state": "open" | "closed",
    "action", "follow_up"}; an existing row for (job, person) is returned as it is (never rewritten, never relabelled Waiting).
    One non-retried create after absence is proven for (job, person), then a fresh read must show exactly that one row, Waiting, linked to the job."""
    person = valid_person(person)
    if not person or not job.get("id") or not job.get("company") or not job.get("role"):
        raise store.RecruitersError("OUTREACH_REQUEST_INVALID")
    check_schema(notion, ledger_source)
    mine = [r for r in job_rows(notion, job["id"]) if norm(r["person"]) == norm(person)]
    if len(mine) > 1:
        raise store.RecruitersError("OUTREACH_AMBIGUOUS")
    if mine:                                                          # a replay never writes; it reports the persisted state truthfully (D221)
        row = mine[0]
        state = "closed" if row["done"] or not row["active"] or row["action"] in store.HISTORY else "open"
        return {"status": "already_recorded", "verified": True, "state": state, "action": row["action"], "follow_up": row["follow_up"]}
    props = _props(job, person, now)
    try:
        notion.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": notion.source}, "properties": props})
    except NotionError:
        raise store.RecruitersError("RECRUITERS_NOTION_WRITE_FAILED") from None
    after = [r for r in job_rows(notion, job["id"]) if norm(r["person"]) == norm(person)]
    want = props["Follow-up Date"]["date"]["start"]
    if len(after) != 1 or after[0]["action"] != store.WAITING or after[0]["follow_up"] != want or after[0]["done"] or not after[0]["active"]:
        raise store.RecruitersError("OUTREACH_READBACK_MISMATCH")
    return {"status": "recorded", "verified": True, "state": "open", "action": store.WAITING, "follow_up": want}


def project(rows, jobs, today):
    """The Job Outreach rows: one per exact open job (never per person), from Recruiters rows linked to a job. `jobs` maps a Ledger page id to
    {"company", "role", "url"}; a row whose job is not in `jobs` (closed, unreadable) is not shown. Only what D221 records is counted: contacted conversations and their
    action / follow-up state. Replies and referrals are not tracked, so they are said to be untracked, never shown as 0 (D222). Most urgent first.
    -> [{"job", "outreach", "next", "link", "sort", "contacted", "company", "role", "needs", "due"}]; `due` is the earliest Waiting follow-up date (ISO) or None."""
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
            nxt, sort = "Needs action · Today", "0000-00-00"
        elif dates:
            nxt, sort = ("Today" if dates[0] <= today.isoformat() else f"{datetime.fromisoformat(dates[0]):%b} {int(dates[0][8:])}"), dates[0]
        else:
            nxt, sort = "—", "9999-99-99"
        out.append({"job": f"{job['company']} — {job['role']}", "outreach": f"{len(items)} contacted · replies not tracked · referrals not tracked", "next": nxt,
                    "link": job["url"], "sort": sort, "contacted": len(items), "company": job["company"], "role": job["role"], "needs": len(act),
                    "due": dates[0] if dates else None})
    return sorted(out, key=lambda r: (r["sort"], r["job"]))
