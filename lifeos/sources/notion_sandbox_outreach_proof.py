"""LIFE-10 §2 (TL comment 10754, Jim's plan approval on LIFE-10 comment 10773): a one-time, synthetic proof of the job-page outreach writer (D225) against the real Notion
API, confined to the sandbox Job Ledger. Manual only (diag.yml, stage notion-sandbox-outreach-proof, typed confirmation SYNTHETIC); never on a tick.

One page, `TEST — ServiceNow Outreach (SYNTHETIC)`, in the sandbox data source: created with synthetic neighbour blocks when no page has that title, reused when exactly one
does and it carries no outreach region yet, refused otherwise. Fictional conversations ("Avery Synthetic", then "Blake Synthetic") come from an in-memory adapter, so the
production Recruiters store is never read or written. The real `job_page_outreach.run` then runs four times against the sandbox client, each step proven by its own
read-back: create, unchanged replay, an in-place update that keeps the region's block ids and puts the Needs Action row first, unchanged replay. Without --live it only
reports whether the page exists. Fixed codes and counts only; the page id and every name stay out of the log."""
import os
from datetime import timedelta

from lifeos.network import surface as N
from lifeos.platform import report_region as R
from lifeos.platform.notion_client import Client, NotionError
from lifeos.recruiters import notion as store
from lifeos.sources import job_page_outreach as J
from lifeos.sources.notion_sandbox_access import SANDBOX_SOURCE

TITLE = "TEST — ServiceNow Outreach (SYNTHETIC)"
CONFIRM = "SYNTHETIC"
EXPECTED = ({"created": 1}, {"unchanged": 1}, {"updated": 1}, {"unchanged": 1})


def fail(code):
    return NotionError(f"NOTION_SANDBOX_PROOF_{code}")


def _text(content):
    return [{"type": "text", "text": {"content": content}}]


def _para(content):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _text(content)}}


def page_body():
    """The synthetic neighbours the writer must leave alone: a description marker, a description, a Network Leads toggle (NET-2.3 marker first) and Jim's notes."""
    leads = {"object": "block", "type": "toggle", "toggle": {"rich_text": _text("Network Leads (1)"),
                                                             "children": [_para(f"{N.MARK} synthetic"), _para("Casey Synthetic · Fictional Labs (SYNTHETIC)")]}}
    return [_para(f"{N.DESCRIPTION_MARK} | key=synthetic-servicenow-outreach"),
            _para("SYNTHETIC job description for the LIFE-10 §2 outreach table proof. Not a real vacancy."), leads,
            _para("Jim's notes (SYNTHETIC): the outreach writer must leave this paragraph untouched.")]


def page_properties():
    return {"Job": {"title": _text(TITLE)}, "Company": {"rich_text": _text("ServiceNow (SYNTHETIC)")}, "Role": {"rich_text": _text("Outreach table proof (SYNTHETIC)")}}


def conversation(person, job_id, action, last_contact, follow_up):
    """One fictional Recruiters row, shaped as the Recruiters data source returns it (lifeos.recruiters.notion.read)."""
    rich = lambda t: [{"type": "text", "plain_text": t, "text": {"content": t}}]   # noqa: E731
    return {"id": f"synthetic-{person.split()[0].lower()}", "properties": {
        "Recruiter / Person": {"type": "title", "title": rich(person)}, "Recruiter Company": {"type": "rich_text", "rich_text": rich("Fictional Recruiting Co (SYNTHETIC)")},
        "Role": {"type": "rich_text", "rich_text": rich("Outreach table proof (SYNTHETIC)")}, "Medium": {"type": "rich_text", "rich_text": rich("LinkedIn")},
        "Last Contact": {"type": "date", "date": {"start": last_contact}}, "Done": {"type": "checkbox", "checkbox": False}, "Active": {"type": "checkbox", "checkbox": True},
        "Action Status": {"type": "select", "select": {"name": action}}, "Follow-up Date": {"type": "date", "date": {"start": follow_up}},
        "Job": {"type": "relation", "relation": [{"id": job_id}]}}}


class Conversations:
    """The in-memory stand-in for the Recruiters data source: answers the writer's one query shape, holds nothing else, and cannot be written."""
    source = "synthetic-recruiters"

    def __init__(self):
        self.pages = []

    def query_data_source(self, source_id, body):
        if source_id is not None or ((body or {}).get("filter") or {}).get("property") != "Job":
            raise fail("UNEXPECTED_QUERY")
        return {"results": list(self.pages), "has_more": False}


class Counted:
    """The sandbox client with every write counted (any call but a GET or a data-source query)."""
    def __init__(self, client):
        self._client, self.writes = client, 0

    def __getattr__(self, name):
        return getattr(self._client, name)

    def _tally(self, method, path):
        if method != "GET" and not path.split("?")[0].endswith("/query"):
            self.writes += 1

    def call(self, method, path, body=None):
        self._tally(method, path)
        return self._client.call(method, path, body)

    def call_once(self, method, path, body=None, *rest):
        self._tally(method, path)
        return self._client.call_once(method, path, body, *rest)


def find_pages(client):
    body = {"page_size": 10, "filter": {"property": "Job", "title": {"equals": TITLE}}}
    found = client.query_data_source(SANDBOX_SOURCE, body)
    if not isinstance(found, dict) or not isinstance(found.get("results"), list) or found.get("has_more") is not False:
        raise fail("QUERY_INCOMPLETE")
    return [p["id"] for p in found["results"] if isinstance(p, dict) and not p.get("archived") and not p.get("in_trash")]


def ensure_page(client):
    """-> (page id, "created" | "reused"). One non-retried create, then the query decides what exists."""
    found = find_pages(client)
    if not found:
        try:
            client.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": SANDBOX_SOURCE}, "properties": page_properties(), "children": page_body()})
        except NotionError:
            pass                                                            # never retried: the query below decides what happened
        found, how = find_pages(client), "created"
    else:
        how = "reused"
    if len(found) != 1:
        raise fail("PAGE_NOT_UNIQUE")
    if J._owned(R.children(client, found[0], fail)):
        raise fail("ALREADY_RUN")
    return found[0], how


def _region(client, page_id):
    """The page's one outreach region (refuses zero or two)."""
    owned = J._owned(R.children(client, page_id, fail))
    if len(owned) != 1:
        raise fail("REGION_NOT_UNIQUE")
    return J._region(client, page_id, owned[0]["id"])


def run(limit, live, environ=os.environ, client=None, now=None):
    if (environ.get("PROOF_CONFIRM") or "").strip() != CONFIRM:
        raise fail("NOT_CONFIRMED")
    now = (now or J.datetime.now(J.CT)).astimezone(J.CT)
    client = Counted(client or Client({"NOTION_API_TOKEN": environ.get("NOTION_API_TOKEN") or "", "SANDBOX_SOURCE": SANDBOX_SOURCE}, source_name="SANDBOX_SOURCE"))
    if R.clean_id(client.source) != R.clean_id(SANDBOX_SOURCE):
        raise fail("NOT_SANDBOX")
    counts = {"notion_sandbox_outreach_proof": "live" if live else "dry_run", "page": "", "steps": [], "region_kept": False, "needs_action_first": False, "writes": 0, "failed": 0}
    if not live:
        counts["page"] = {0: "would_create", 1: "exists"}.get(len(find_pages(client)), "not_unique")
        return counts
    page_id, counts["page"] = ensure_page(client)
    talks = Conversations()
    today = now.date()
    talks.pages.append(conversation("Avery Synthetic", page_id, store.WAITING, today.isoformat(), store.add_business_days(today, store.FOLLOW_UP_DAYS).isoformat()))
    step = lambda at: J.run(0, True, environ={"JOB_PAGE_OUTREACH": "on"}, recruiters=talks, ledger=client, client=client, now=at)["codes"]   # noqa: E731
    counts["steps"] += [step(now), step(now)]
    first = _region(client, page_id)
    talks.pages.append(conversation("Blake Synthetic", page_id, store.NEEDS_ACTION, (today - timedelta(days=3)).isoformat(), today.isoformat()))
    later = now + timedelta(minutes=1)
    counts["steps"] += [step(later), step(later)]
    last = _region(client, page_id)
    counts["region_kept"] = (last["heading"]["id"], last["table"]["id"]) == (first["heading"]["id"], first["table"]["id"])
    counts["needs_action_first"] = len(last["rows"]) == 3 and R.cells_of(last["rows"][1])[0].startswith("Blake Synthetic")
    counts["writes"] = client.writes
    counts["failed"] = int(tuple(counts["steps"]) != EXPECTED or not counts["region_kept"] or not counts["needs_action_first"])
    return counts
