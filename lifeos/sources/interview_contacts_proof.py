"""LIFE-88 E10 synthetic QA: the opportunity page's LinkedIn Contacts table, through the real V7 path, on native Notion QA surfaces. Manual only: interview-proof with
`confirm = E10` (Interview token only). SYNTHETIC EVIDENCE, NOT a real-world hiring event and NOT a live-production E10 PASS.

Every write goes to a FRESH sandbox under the QA Interview OS parent: its own Job Ledger data source with synthetic rows (their Network Leads blocks written by the real
surface writer, `surface.build_block`), a synthetic human page, and one QA opportunity page per case. The contacts are fictional ("... Synthetic", company and title marked
SYNTHETIC, no profile links). The production Walmart Ledger row and the production Smith opportunity page are only READ, for the job's own description text and identity;
no production page, Ledger row, roster, status or record is written. Real code under test: interview_evidence.network_contacts (the contact reader), view.opportunity,
the UI 3c renderer and Notion writer (create), then an independent read-back with a NEW client. PASS/FAIL per assertion, counts only; no name, id or link is printed."""
from datetime import datetime, timedelta
from urllib.parse import quote

from lifeos.interview import create, notion, template, view
from lifeos.interview.models import ParentQuery, RoundQuery
from lifeos.interview.ui_probe import LEDGER_ROW, QA_PARENT, _seed
from lifeos.jobs import identity
from lifeos.network import match, surface
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.sources import interview_evidence as ev, m1m2_proof as base

CONFIRM = "E10"
SYN = "(SYNTHETIC)"
SMITH_PAGE = "3d53c5a0592681f4a8ffda29dc374cb0"          # the production Smith opportunity page: read only (its recovered job description)
SMITH_COMPANY, SMITH_ROLE = "Smith", "Senior Project Manager — Commerce"
SMITH_URL = "https://www.indeed.com/viewjob?jk=31a46a8ea6620375"
WALMART_URL = "https://careers.walmart.com/us/en/jobs/R-2612137"
HEADER = ["Person", "Role / Relevance", "Outreach Status"]
NONE_ROW = ["No separately verified contacts", "—", "Not tracked"]
DEGRADED_ROW = ["DEGRADED · Network leads unreadable this run", "—", "Not tracked"]
PEOPLE = (("Avery Synthetic", "Director, Operations Excellence", 10), ("Blake Synthetic", "Senior Manager, Program Delivery", 60), ("Casey Synthetic", "Technical Recruiter", 150))
FRESH_NOTE = "Synthetic Ledger row for LIFE-88 E10 QA. Not a real vacancy record."


def fictional_leads(today, company):
    """Three fictional contacts as the writer's display dicts: a fresh, an aging and a stale observation, so the three relevance lines differ."""
    out = []
    for number, (name, title, age) in enumerate(PEOPLE, 1):
        lead = {"person_id": 9000 + number, "tier": match.CURRENT, "freshness": match.freshness(age), "age_days": age, "name": name, "company": f"{company} {SYN}",
                "title": f"{title} {SYN}", "verified": today - timedelta(days=age), "url_key": None}
        lead["evidence_hash"] = surface.evidence_hash(lead, "qa-synthetic", lead["title"], lead["verified"])
        out.append(lead)
    return out


class Unreadable:
    """The real client, except that reading one page's blocks fails: the injected fault behind the DEGRADED case."""
    def __init__(self, client, page_id):
        self._client, self._page = client, page_id

    def call(self, method, path, body=None):
        if method == "GET" and f"/blocks/{quote(self._page, safe='')}/children" in path:
            raise NotionError("NOTION_HTTP_404")
        return self._client.call(method, path, body)

    def __getattr__(self, name):
        return getattr(self._client, name)


def sandbox(client, now):
    """A fresh sandbox under the QA parent. -> its ids: the container, the synthetic human page, the synthetic Job Ledger data source."""
    container = notion.write_tree(client, QA_PARENT, [], title=f"LIFE-88 E10 synthetic Network Leads QA · {now.strftime('%Y-%m-%d %I:%M %p')} CT (SYNTHETIC, QA-only)")
    human = notion.write_tree(client, container, [base._block("paragraph", "Synthetic human notes. LIFE OS must never change this page.")], title=f"Human page {SYN}")
    ledger = base._database(client, container, "Job Ledger (synthetic)", {n: {k: {}} for n, k in base.ledger_schema().items()})
    return {"container": container, "human": human, "ledger": ledger}


def description_lines(client, page_id, after=None, size=12):
    """READ ONLY: the first lines of a production page's own job description (the text under the heading that starts with `after`, else the page's own opening lines)."""
    try:
        tops = notion.children(client, page_id, [notion.MAX_CALLS])
    except NotionError:
        return []
    items = [(b.get("type", ""), notion.text(b)) for b in tops]
    if after:
        start = next((i for i, (kind, text) in enumerate(items) if kind.startswith("heading") and text.startswith(after)), None)
        items = items[start + 1:] if start is not None else []
    out = []
    for kind, text in items:
        if kind.startswith("heading") and out:
            break
        if text.strip() and not text.startswith("v7-"):
            out.append(text.strip()[:300])
        if len(out) == size:
            break
    return out


def ledger_row(client, box, company, role, key, apply_url, lines, leads=None):
    """One synthetic Ledger row: its properties, then its body (description marker first, the job description text, the Network Leads block written by the real surface writer, a human note)."""
    props = {"Job": {"title": rich_text(role)}, "Company": {"rich_text": rich_text(company)}, "Stable Job Key": {"rich_text": rich_text(key)}, "Apply URL": {"url": apply_url}}
    page = client.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": box["ledger"]}, "properties": props})["id"]
    body = [base._block("paragraph", f"{surface.DESCRIPTION_MARK} | key={key}"), base._block("paragraph", FRESH_NOTE), *[base._block("paragraph", t) for t in lines]]
    if leads:
        body.append(surface.build_block(leads))
    body.append(base._block("paragraph", f"Synthetic note below the Network Leads {SYN}."))
    client.call_once("PATCH", f"/blocks/{quote(page, safe='')}/children", {"children": body})
    return page


def contacts_table(reader, page_id):
    """The rows of the page's LinkedIn Contacts table (header matched), as Notion holds them -> [[cell text] x 3], or None when there is no such table."""
    budget = [200]

    def cells(row):
        return ["".join(p.get("plain_text") or (p.get("text") or {}).get("content", "") for p in cell) for cell in (row.get("table_row") or {}).get("cells") or []]

    def walk(block_id, depth):
        for block in notion.children(reader, block_id, budget):
            if block.get("type") == "table":
                rows = [cells(r) for r in notion.children(reader, block["id"], budget)]
                if rows and rows[0] == HEADER:
                    return rows[1:]
            elif block.get("has_children") and block.get("type") != "child_page" and depth < 6:
                found = walk(block["id"], depth + 1)
                if found is not None:
                    return found
        return None
    return walk(page_id, 0)


def drive(client, reader, box, now, results, counts):
    check = lambda name, ok: results.append((name, bool(ok)))                                                  # noqa: E731
    today, container = now.date(), box["container"]
    props = client.call("GET", f"/pages/{LEDGER_ROW}").get("properties") or {}                                # production Walmart row: READ ONLY
    company, role, key = view._plain(props.get("Company")), view._plain(props.get("Job")), view._plain(props.get("Stable Job Key"))
    walmart_jd, smith_jd = description_lines(client, LEDGER_ROW), description_lines(client, SMITH_PAGE, after="Job Description — Recovered")
    check("0a the Walmart and Smith job descriptions are read (read only) from their production pages", company and role and key and walmart_jd and smith_jd)
    smith_key = identity.url_key(SMITH_URL)
    positive = fictional_leads(today, "Walmart")
    w1 = ledger_row(client, box, company, role, key, WALMART_URL, walmart_jd, positive)
    s1 = ledger_row(client, box, SMITH_COMPANY, SMITH_ROLE, smith_key, SMITH_URL, smith_jd)
    w2 = ledger_row(client, box, company, role, key, WALMART_URL, walmart_jd, fictional_leads(today, "Walmart"))
    w3 = ledger_row(client, box, company, role, f"qa-synthetic-other-job-{key[:8]}", WALMART_URL, walmart_jd, fictional_leads(today, "Walmart"))
    cases = (("positive", ParentQuery(company, role), key, w1, client), ("zero contacts", ParentQuery(SMITH_COMPANY, SMITH_ROLE), smith_key, s1, client),
             ("unreadable source", ParentQuery(company, role), key, w2, Unreadable(client, w2)), ("wrong job", ParentQuery(company, role), key, w3, client),
             ("no exact Ledger link", ParentQuery(SMITH_COMPANY, SMITH_ROLE), smith_key, None, client))
    pages, views = {}, {}
    for name, parent, job_key, ledger_id, reading in cases:
        query = RoundQuery(True, None, None, None, stable_job_key=job_key, ledger_page_id=ledger_id)
        evidence, _ = ev.network_contacts(reading, [(parent, query)])                                          # the real contact reader
        current = view.opportunity(reading, parent, evidence[0][1], container, None, create.today())          # the real opportunity view
        blocks = create.rendered(current, tail=None)[0]                                                        # the real renderer
        page = notion.write_tree(client, container, blocks, title=f"E10 QA · {name} {SYN}", icon=template.PAGE_ICONS["opportunity"], template=notion.FULL_WIDTH)
        check(f"1 {name}: the QA page is written and read back as the approved structure", create.finalize(client, page, current, blocks) == "readback_ok")
        _seed(client, page, "Live Notes · protected space")                                                    # a synthetic human note in the protected region
        pages[name], views[name] = page, (parent, evidence[0][1], current)
    expected = [[lead["name"], surface.reason(lead), "Not tracked"] for lead in positive]
    shown = lambda name: base._rows_text(reader, pages[name])                                                  # noqa: E731
    row_fact = lambda name: contacts_table(reader, pages[name])                                                # noqa: E731
    check("2a positive: the LinkedIn Contacts table shows exactly the three fictional contacts with their relevance lines", row_fact("positive") == expected)
    check("2b positive: the Source Health line counts 3 Network leads from the job's Ledger page", "LinkedIn contacts: 3 Network lead(s) from the job's Ledger page" in shown("positive"))
    check("2c positive: the three relevance lines differ (fresh, aging, stale) and the stale one says verify first", len({row[1] for row in expected}) == 3 and expected[2][1].endswith("verify first"))
    check("3 zero contacts (Smith): the table says no separately verified contacts and the page says no Network leads on the Ledger page",
          row_fact("zero contacts") == [NONE_ROW] and "LinkedIn contacts: no Network leads on the job's Ledger page" in shown("zero contacts"))
    check("4 unreadable source: the table and the Source Health line both say DEGRADED, never none",
          row_fact("unreadable source") == [DEGRADED_ROW] and "LinkedIn contacts: DEGRADED · Network leads unreadable this run" in shown("unreadable source"))
    wrong = shown("wrong job")
    check("5 wrong job: another job's Ledger page shows no contact on this page and the row is not linked",
          row_fact("wrong job") == [NONE_ROW] and not any(p[0] in wrong for p in PEOPLE) and "LinkedIn contacts: not connected (no exact Job Ledger row)" in wrong)
    check("6 no exact Ledger link (Smith): not connected, no contact shown",
          row_fact("no exact Ledger link") == [NONE_ROW] and "LinkedIn contacts: not connected (no exact Job Ledger row)" in shown("no exact Ledger link"))
    # replay, then a real change at the source: Jim's Dismiss tick on one lead, re-read and refreshed in place
    parent, query, _ = views["positive"]
    page, out = pages["positive"], {"writes": 0, "readback_ok": 0, "readback_failed": 0, "writes_planned": 0}
    notes, human_before = notion.protected_snapshot(client, page, "opportunity"), base._human_tree(client, box["human"])
    others = {n: notion.tree_digest(notion.read_tree(client, pages[n], [notion.TREE_MAX_CALLS])) for n in pages if n != "positive"}
    check("7a positive: a replay writes nothing", create.refresh(client, page, views["positive"][2], True, out) == "parent_unchanged")
    toggle = next(b for b in notion.children(client, w1, [notion.MAX_CALLS]) if b.get("type") == "toggle")
    tick = next(b for b in notion.children(client, toggle["id"], [notion.MAX_CALLS]) if b.get("type") == "to_do" and notion.text(b).startswith(f"Dismiss · {positive[2]['person_id']}:"))
    client.call("PATCH", f"/blocks/{quote(tick['id'], safe='')}", {"to_do": {"rich_text": rich_text(notion.text(tick)), "checked": True}})
    evidence, _ = ev.network_contacts(client, [(parent, query)])
    again = view.opportunity(client, parent, evidence[0][1], container, None, create.today())
    check("7b positive: after a Dismiss tick the page refreshes in place", create.refresh(client, page, again, True, out) == "parent_refreshed")
    check("7c positive: the refreshed table shows only the two undismissed contacts", contacts_table(reader, page) == expected[:2])
    check("8a Jim's Notes and the protected region on the QA page are unchanged", notion.protected_snapshot(client, page, "opportunity") == notes)
    check("8b the synthetic human page is unchanged", base._human_tree(client, box["human"]) == human_before)
    check("8c the other QA case pages are unchanged by the positive page's refresh",
          others == {n: notion.tree_digest(notion.read_tree(client, pages[n], [notion.TREE_MAX_CALLS])) for n in pages if n != "positive"})
    counts["pages"] = len(pages)


def run(environ, live, now=None, client=None, reader=None):
    """-> counts only: assertions as PASS/FAIL, `failed`, and the label that this is synthetic evidence."""
    if (environ.get("INTERVIEW_JOURNEY_PROOF") or "").strip() != CONFIRM:
        return {"proof": "refused", "code": "proof_confirm_missing", "failed": 1, "why": {}, "writes": 0}
    if live:
        return {"proof": "refused", "code": "proof_requires_dry_dispatch", "failed": 1, "why": {}, "writes": 0}
    now, client = (now or datetime.now(ev.TZ)).astimezone(ev.TZ), client or notion.make_client(environ)
    try:
        box = sandbox(client, now)
    except NotionError as error:                                             # e.g. the integration may not create a database under the QA parent
        return {"proof": "blocked", "code": f"sandbox:{str(error)[:40]}", "detail": str(getattr(error, "detail", ""))[:80], "failed": 1, "why": {}, "writes": 0}
    results, counts = [], {}
    try:
        drive(client, reader or notion.make_client(environ), box, now, results, counts)
    except NotionError as error:                                             # a refusal is a failed assertion with its fixed code, never a crash or a silent PASS
        results.append((f"stopped: {str(error)[:60]}", False))
    return {"proof": "e10", "synthetic": True, "assertions": {name: "PASS" if ok else "FAIL" for name, ok in results}, "passed": sum(ok for _, ok in results),
            "failed": sum(not ok for _, ok in results), "why": {}, **counts}
