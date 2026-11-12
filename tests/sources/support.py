"""LIFE-544: test helpers that more than one test module (often in another domain) uses. Moved here out of test_*.py files, unchanged."""
from tests.interview.support import ENV
from lifeos.sources.interview import interview_evidence as ev
from datetime import datetime
from lifeos.jobs import ledger as jobs_ledger
from tests.interview.support import WritableFake
from lifeos.interview import create, notion
from unittest.mock import patch


def mail(subject="Schedule your interview - ExampleCo Program Manager", body="Please select a time for a call. Requisition 12345.", **extra):
    return {"provider": "gmail", "id": subject, "received": "2026-10-20T15:00:00Z", "sender_name": "ExampleCo Talent", "sender_address": "no-reply.example",
            "subject": subject, "body": body, "folder": "INBOX", "markers": {"bulk": False, "auto": True, "noreply": True}, "url": "https://example.test/thread/9", **extra}


def outreach(role="Program Manager", client="ExampleCo", person="Pat Example", url="https://example.test/thread/5", received="2026-10-20T15:00:00Z", extra=""):
    """A recruiter's first message: a person who says they are a recruiter, a role and (when given) the client."""
    return {"provider": "gmail", "id": url, "received": received, "sender_name": person, "sender_address": "pat-example", "subject": f"{role} opportunity", "folder": "INBOX",
            "markers": {"bulk": False, "auto": False, "noreply": False}, "url": url,
            "body": f"Hi Jim,\n\nI'm a senior recruiter at Staffing Example Inc. I am hiring for a {role} role" + (f" for our client {client}" if client else "") + f". {extra}\n\n{person}\nSenior Recruiter\nStaffing Example Inc\n"}


def event(title, start="2026-10-28T10:00:00-05:00", **extra):
    return {"id": title, "summary": title, "status": "confirmed", "start": {"dateTime": start}, **extra}


REQ, KEY = "777777", "recruiter-exampleco-777777"


LEAD = outreach(url="https://example.test/thread/lead", received="2026-10-19T15:00:00Z", extra=f"Requisition {REQ}.")


REQUEST = mail(subject="Schedule your interview - ExampleCo Program Manager", body=f"Please select a time for a call. Requisition {REQ}.",
               url="https://example.test/thread/request", received="2026-10-20T15:00:00Z")


NO_REQ = outreach(role="Data Analyst", client="OtherCo", url="https://example.test/thread/noreq", received="2026-10-19T16:00:00Z")


INVITE = "ExampleCo Program Manager Interview"


LIVE = {**ENV, "INTERVIEW_OS_WRITER": "v7", "INTERVIEW_LEADS": "on"}


NOW = datetime(2026, 10, 21, 12, tzinfo=ev.TZ)


class Ledger:
    """The Job Ledger, keyed like the real one: the target check, an exact Stable Job Key lookup, one create and its read-back."""
    source = "ledger-source"

    def __init__(self):
        self.rows = {}

    def call(self, method, path, body=None):
        if path == f"/data_sources/{self.source}":
            return {"properties": {n: {"type": k} for n, k in jobs_ledger.REQUIRED.items()}}
        if path.endswith("/query"):
            key = body["filter"]["rich_text"]["equals"]
            return {"results": [{"id": pid} for pid, row in self.rows.items() if row["stable_job_key"] == key]}
        row = self.rows[path.rsplit("/", 1)[1]]
        return {"properties": {"Stable Job Key": {"type": "rich_text", "rich_text": [{"plain_text": row["stable_job_key"]}]}, "Visible Lane": {"select": {"name": row["lane"]}}}}

    def create(self, props, blocks):
        pid = f"ledger-{len(self.rows) + 1}"
        self.rows[pid] = {"page_id": pid, "company": props["Company"]["rich_text"][0]["text"]["content"], "role": props["Job"]["title"][0]["text"]["content"],
                          "stable_job_key": props["Stable Job Key"]["rich_text"][0]["text"]["content"], "requisition": None,
                          "lane": props["Visible Lane"]["select"]["name"]}
        return pid

    def for_company(self, company):
        return [r for r in self.rows.values() if r["company"] == company]


class NotionLike(WritableFake):
    """The production-shaped fake, plus what Notion itself adds on read: every rich-text run carries its plain_text."""
    def register(self, prefix, items, depth=0):
        out = super().register(prefix, items, depth)
        for item in out:
            for run in (item.get(item["type"]) or {}).get("rich_text") or []:
                run.setdefault("plain_text", (run.get("text") or {}).get("content", ""))
        return out


class JourneyFixture:
    """The Interview journey harness: a fake Notion, a Job Ledger and one natural run of the Interview step. Shared by the Journey tests and the Through tests (another domain)."""

    def setUp(self):
        self.notion, self.ledger, self.events, self.recruiters = NotionLike(), Ledger(), [], None
        self.human = [dict(b) for b in self.notion.blocks["human"]]
        made = self.ledger.create                                       # LIFE-361: one Job Ledger: the row the lead writer makes is the row the Interview step finds
        def create(props, blocks):
            pid = made(props, blocks)
            row = self.ledger.rows[pid]
            self.notion.add_row(pid, row["company"], row["role"], key=row["stable_job_key"])
            return pid
        self.ledger.create = create

    def tick(self, *records):
        """One natural run of the Interview step, exactly as the runner calls it, with the mailbox and Calendar as they are now."""
        calendar = type("Calendar", (), {"list_events": lambda s, a, b: [e for e in self.events if e.get("status") != "cancelled"],
                                         "request": lambda s, m, p, params=None: {"items": list(self.events)}})()
        with patch.object(ev.pipeline, "read_applied", return_value=[]), patch.object(create, "today", lambda: NOW.date().isoformat()):   # the page's "today" is the journey's NOW, never the wall clock
            return ev.run(20, True, environ=LIVE, client=self.notion, calendar=calendar, now=NOW, ledger=object(), records=list(records),
                          ledger_rows=self.ledger.for_company, writer=self.ledger, recruiters=self.recruiters)

    def settle(self, *records):
        """Ticks until a tick writes nothing (each opportunity takes at most one write per tick); -> every tick's counts."""
        ticks = [self.tick(*records)]
        while ticks[-1]["writes"] and len(ticks) < 6:
            ticks.append(self.tick(*records))
        self.assertEqual(ticks[-1]["writes"], 0, "the journey never settled")
        self.assertEqual((ticks[-1]["failed"], ticks[-1]["why"].get("round_identity_incomplete")), (0, None), "a settled run must not stay DEGRADED")
        return ticks

    def shown(self, page_id):
        """Every text on a page, nested blocks and table cells included, as Jim reads it."""
        def walk(block_id):
            for b in self.notion.blocks.get(block_id, []):
                yield notion.text(b)
                yield from ("".join(r.get("text", {}).get("content", "") for r in cell) for cell in (b.get("table_row") or {}).get("cells", []))
                yield from walk(b.get("id"))
        return "\n".join(walk(page_id))

    def pages(self):
        """The opportunities this journey made (the fake pipeline also holds one human page, "Legacy — Manager", which must stay untouched)."""
        scan = notion.parents(self.notion, LIVE)                        # LIFE-361: an opportunity is its Ledger row
        return [(p, notion.child_scan(self.notion, p.page_id).items) for p in scan.items if p.active and p.page_id != "human"]
