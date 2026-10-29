"""JFM-221 (Jim, Oct 29 2026): the Jim & Matt dashboard fed from Jira. Fakes only: invented issues, a fake Jira (read only) and a fake Notion. Nothing is live."""
import itertools
import unittest
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from lifeos.jira import dash_data as D, dash_render as R, dashboard as S
from lifeos.jira.dash_scrub import scrub, terms
from lifeos.platform.notion_client import NotionError

ROOT = "a" * 32
NOW = datetime(2026, 10, 29, 13, 5, tzinfo=D.CENTRAL)             # Thursday 1:05 PM CT: the check-in owed is Wed Oct 28
SITE = "https://example.test"


def issue(key, summary, cat="new", status=None, changed=None, due=None, labels=(), parent=None, parent_level=1, level=0, desc=None):
    f = {"summary": summary, "status": {"name": status or {"new": "To Do", "indeterminate": "In Progress", "done": "Done"}[cat], "statusCategory": {"key": cat}},
         "statuscategorychangedate": changed, "duedate": due, "labels": list(labels), "issuetype": {"name": "Task", "hierarchyLevel": level}}
    if parent:
        f["parent"] = {"key": parent, "fields": {"issuetype": {"hierarchyLevel": parent_level}}}
    if desc:
        f["description"] = desc
    return {"key": key, "fields": f}


def adf(*blocks):
    def text(t):
        return {"type": "text", "text": t}
    return {"type": "doc", "content": [{"type": "heading", "content": [text(b[1])]} if b[0] == "h" else {"type": "paragraph", "content": [text(b[1])]} for b in blocks]}


VISION = adf(("h", "Mary Morrissey vision (present tense)"), ("p", "\"It's January 2, 2027, and I'm paid $70/hr by two contracts.\""), ("h", "Current reality"), ("p", "Runway: 6 months"))
ISSUES = [
    issue("JFM-1", "Shipped the deck", "done", changed="2026-10-27T10:00:00.000-0500", parent="JFM-9"),
    issue("JFM-2", "Call Pat at 312-555-0100 about the $220K role", due="2026-11-02", parent="JFM-9"),
    issue("JFM-3", "Old overdue thing", "indeterminate", due="2026-10-20", parent="JFM-104"),
    issue("JFM-4", "Q4 Career Win: first paid contract", labels=("q4-qbr-2026", "must-win"), due="2026-11-20", parent="JFM-9", desc=VISION),
    issue("JFM-5", "Stuck item", "indeterminate", status="Blocked", parent="JFM-106"),
    issue("JFM-6", "Done long ago", "done", changed="2026-08-01T10:00:00.000-0500", parent="JFM-105"),
    issue("JFM-7", "A QBR decision", labels=("q4-qbr-2026",), parent="JFM-102"),
    issue("MEJ-10", "Matt sub-task", "done", changed="2026-10-28T09:00:00.000-0500", parent="MEJ-11", parent_level=0),
    issue("MEJ-11", "Matt task", parent="MEJ-2"),
    issue("MEJ-12", "Email matt@example.com the mix", labels=("must-win",), due="2026-10-30", parent="MEJ-2"),
]
EPIC_OF_TASK = {"MEJ-11": "MEJ-2"}


class Jira:
    def __init__(self, issues=ISSUES, blocked_comment=False):
        self.issues, self.blocked_comment, self.writes = {i["key"]: i for i in issues}, blocked_comment, []

    def _match(self, jql):
        out = []
        for i in self.issues.values():
            f = i["fields"]
            cat = f["status"]["statusCategory"]["key"]
            if jql.startswith("key in ("):
                keys = jql[len("key in ("):-1].split(",")
                if i["key"] in keys:
                    out.append({"key": i["key"], "fields": {"parent": {"key": EPIC_OF_TASK[i["key"]], "fields": {"issuetype": {"hierarchyLevel": 1}}}}
                                if i["key"] in EPIC_OF_TASK else {}})
                continue
            if "statusCategory != Done" in jql and cat == "done":
                continue
            if "statusCategory = Done" in jql and cat != "done":
                continue
            if ">= -120d" in jql and (not f["statuscategorychangedate"] or f["statuscategorychangedate"] < "2026-07-01"):
                continue
            if "labels in (" in jql and not ({"must-win", "q4-qbr-2026"} & set(f["labels"])):
                continue
            if "labels = q4-qbr-2026" in jql and ("q4-qbr-2026" not in f["labels"] or "Win" not in f["summary"]):
                continue
            out.append(i)
        return out

    def get(self, path, params=None):
        if path == "/rest/api/3/search/jql":
            found = self._match(params["jql"])
            start = int(params.get("nextPageToken") or 0)
            page = found[start:start + 3]                                                  # small pages: paging is exercised
            more = start + 3 < len(found)
            return {"issues": page, **({"nextPageToken": str(start + 3), "isLast": False} if more else {"isLast": True})}
        if path.startswith("/rest/api/3/issue/"):
            comments = [{"created": "2026-10-28T10:00:00.000-0500"}] if self.blocked_comment else []
            return {"fields": {"comment": {"comments": comments}},
                    "changelog": {"histories": [{"created": "2026-10-20T10:00:00.000-0500", "items": [{"field": "status", "toString": "Blocked"}]}]}}
        raise AssertionError(path)

    def post(self, *a, **k):
        self.writes.append(a)
        raise AssertionError("the dashboard never writes to Jira")


class Notion:
    """A small Notion: blocks with children, pages under pages, databases with one data source."""
    def __init__(self):
        self.ids = (f"{n:032x}" for n in itertools.count(1000))
        self.kids, self.blocks, self.rows, self.calls, self.trashed = {ROOT: []}, {}, {}, 0, set()
        self.fail_on = None
        for title in (S.CHECKINS, S.QBRS):
            self._add(ROOT, {"type": "child_page", "child_page": {"title": title}})
        for title in (S.ARCHIVE, S.MATT_MEGIBOW):
            db = self._add(ROOT, {"type": "child_database", "child_database": {"title": title}})
            self.rows[db] = []
        self.megibow = self._add(ROOT, {"type": "callout", "callout": {"rich_text": []}})
        table = self._add(self.megibow, {"type": "table", "table": {}})
        cells = [["Activity", "Cumulative", "Oct 12", "Oct 19", "Oct 26 (current)"], ["Outreach", "21", "0", "3", "1"], ["Scheduled", "16", "1", "1", "1"],
                 ["Networking Calls", "11", "0", "1", "0"], ["Recruiter Calls", "36", "0", "0", "0"], ["Company Calls", "15", "0", "1", "0"]]
        for row in cells:
            self._add(table, {"type": "table_row", "table_row": {"cells": [[{"plain_text": c}] for c in row]}})

    def _add(self, parent, block):
        bid = next(self.ids)
        self.blocks[bid] = {**block, "id": bid}
        self.kids.setdefault(parent, []).append(bid)
        self.kids.setdefault(bid, [])
        return bid

    def page_titled(self, parent, title):
        return next((b for b in self.kids[parent] if self.blocks[b].get("child_page", {}).get("title") == title and b not in self.trashed), None)

    def texts(self, page):
        out = []
        for b in (k for k in self.kids[page] if k not in self.trashed):
            block = self.blocks[b]
            body = block.get(block["type"]) or {}
            out.append("".join(r["text"]["content"] for r in body.get("rich_text", [])))
        return out

    def call(self, method, path, body=None):
        self.calls += 1
        if self.fail_on and self.fail_on(method, path):
            raise NotionError("NOTION_HTTP_500")
        parts = urlparse(path)
        seg = parts.path.strip("/").split("/")
        if method == "GET" and seg[0] == "blocks" and seg[2] == "children":
            visible = [self.blocks[b] for b in self.kids[seg[1]] if b not in self.trashed]
            start = int((parse_qs(parts.query).get("start_cursor") or ["0"])[0])
            return {"results": visible[start:start + 100], "has_more": start + 100 < len(visible), "next_cursor": str(start + 100)}
        if method == "GET" and seg[0] == "databases":
            return {"data_sources": [{"id": "ds" + seg[1]}]}
        if method == "POST" and path == "/pages":
            parent = body["parent"]
            if parent.get("type") == "data_source_id":
                self.rows[parent["data_source_id"][2:]].append({"properties": body["properties"]})
                return {"id": next(self.ids)}
            title = "".join(r["text"]["content"] for r in body["properties"]["title"]["title"])
            pid = self._add(parent["page_id"], {"type": "child_page", "child_page": {"title": title}})
            for child in body.get("children") or []:
                self._add(pid, child)
            return {"id": pid}
        if method == "PATCH" and seg[0] == "blocks" and seg[2] == "children":
            return {"results": [{"id": self._add(seg[1], c)} for c in body["children"]]}
        if method == "DELETE" and seg[0] == "blocks":
            self.trashed.add(seg[1])
            return {}
        if method == "PATCH" and seg[0] == "pages":
            self.blocks[seg[1]]["child_page"]["title"] = "".join(r["text"]["content"] for r in body["properties"]["title"]["title"])
            return {}
        raise AssertionError((method, path))

    call_once = call

    def query_data_source(self, source, body=None):
        self.calls += 1
        rows = [{"properties": {"Key": {"rich_text": r["properties"]["Key"]["rich_text"]}} if "Key" in r["properties"] else r["properties"]} for r in self.rows[source[2:]]]
        return {"results": rows, "has_more": False}


ENV = {"JM_DASHBOARD_PAGE_ID": ROOT, "JM_DASHBOARD_REDACT_TERMS": "Pat, Lisa", "JIRA_SITE_URL": SITE}


def go(notion, live=True, jira=None, now=NOW, env=None):
    return S.run(0, live, {**ENV, "MEGIBOW_BLOCK_ID": notion.megibow, **(env or {})}, jira=jira or Jira(), client=notion, now=now, push=lambda *a: None)


class Scrub(unittest.TestCase):
    def test_money_contacts_long_numbers_and_listed_terms_never_reach_notion(self):
        t = terms({"JM_DASHBOARD_REDACT_TERMS": "Pat, Lisa,  , x"})
        for raw, want in (("Teladoc — Principal PM (FT, $220K): win the offer", "Teladoc — Principal PM (FT, [private]): win the offer"),
                          ("Contract ($70/hr): low priority", "Contract ([private]): low priority"),
                          ("$500/month recurring revenue", "[private] recurring revenue"),
                          ("Email matt@example.com", "Email [private]"), ("Call 312-555-0100 now", "Call [private] now"),
                          ("Account 12345678 closed", "Account [private] closed"), ("SSN 123-45-6789", "SSN [private]"),
                          ("Weekend with Lisa and Patrick", "Weekend with [private] and Patrick"),
                          ("Q4 Career Win: first paid contract by Nov 20 (JFM-223)", "Q4 Career Win: first paid contract by Nov 20 (JFM-223)")):
            self.assertEqual(scrub(raw, t)[0], want)


class Data(unittest.TestCase):
    def test_the_vision_is_only_the_text_under_its_heading(self):
        self.assertEqual(D.vision(VISION), "\"It's January 2, 2027, and I'm paid $70/hr by two contracts.\"")
        self.assertIsNone(D.vision(adf(("h", "Deadline"), ("p", "Nov 20"))))
        self.assertIsNone(D.vision(None))

    def test_done_is_the_status_category_change_date_and_due_and_overdue_are_open_only(self):
        issues = D.resolve_epics(Jira(), [D.normalize(i, SITE) for i in ISSUES])
        w = D.window([i for i in issues if i["person"] == "Jim"], NOW)
        self.assertEqual([i["key"] for i in w["done"]], ["JFM-1"])
        self.assertEqual([i["key"] for i in w["due"]], ["JFM-2"])
        self.assertEqual([i["key"] for i in w["overdue"]], ["JFM-3"])
        self.assertEqual({i["key"]: i["epic"] for i in issues}["MEJ-10"], "MEJ-2")              # a sub-task takes its task's epic

    def test_the_check_in_owed_is_the_latest_wednesday_at_7_am_central(self):
        for now, want in ((NOW, "Check-in — Wed Oct 28, 2026"), (datetime(2026, 11, 4, 6, 59, tzinfo=D.CENTRAL), "Check-in — Wed Oct 28, 2026"),
                          (datetime(2026, 11, 4, 7, 0, tzinfo=D.CENTRAL), "Check-in — Wed Nov 4, 2026")):
            self.assertEqual(S.checkin_title(S.wednesday(now)), want)

    def test_the_quarter_label_and_name(self):
        self.assertEqual((D.quarter_label(NOW.date()), D.quarter_name(NOW.date())), ("q4-qbr-2026", "Q4 2026"))


class Pages(unittest.TestCase):
    def test_one_run_writes_the_check_in_the_synopsis_and_the_archive_and_a_rerun_writes_nothing(self):
        n = Notion()
        first = go(n)
        self.assertEqual((first["checkin"], first["synopsis"], first["archived"]), ("written", "written", 3))
        page = n.page_titled(n.kids[ROOT][0], "Check-in — Wed Oct 28, 2026")
        shown = n.texts(page)
        self.assertIn("Done in the last 7 days (1)", shown)
        self.assertIn("JFM-1 — Shipped the deck · done Oct 27", shown)
        self.assertIn("JFM-2 — Call [private] at [private] about the [private] role · due Nov 2 · To Do", shown)
        self.assertIn("JFM-3 — Old overdue thing · was due Oct 20 · In Progress", shown)
        self.assertIn("JFM-5 — Stuck item · add a comment saying why it is blocked", shown)
        self.assertIn("MEJ-12 — Email [private] the mix · To Do · due Oct 30", shown)
        self.assertIn("MegIBOW · week of Oct 19", shown)
        self.assertIn("Total 6 / min 5 · ⚠️ below target", shown)                               # Recruiter Calls 0: below target although the total is met
        self.assertIn("No activity yet in: Recruiter Calls", shown)
        self.assertIn("Not entered for this week yet.", shown)                                   # Matt's manual table has no row yet
        self.assertTrue(shown[-1].startswith(R.DONE_MARK))
        synopsis = n.texts(n.page_titled(n.kids[ROOT][1], "Q4 2026 — Quarterly Synopsis"))
        self.assertIn("\"It's January 2, 2027, and I'm paid [private] by two contracts.\"", synopsis)
        self.assertNotIn("Runway: 6 months", synopsis)                                            # only the vision section, never the rest of the ticket
        self.assertIn("JFM-7 — A QBR decision · To Do", synopsis)
        keys = sorted(r["properties"]["Key"]["rich_text"][0]["text"]["content"] for r in n.rows[n.kids[ROOT][2]])
        self.assertEqual(keys, ["JFM-1", "JFM-6", "MEJ-10"])
        calls = n.calls
        again = go(n)
        self.assertEqual((again["checkin"], again["synopsis"], again["archived"]), ("kept", "unchanged", 0))
        self.assertEqual(len(n.rows[n.kids[ROOT][2]]), 3)
        self.assertLess(n.calls - calls, calls)

    def test_a_dry_run_writes_nothing(self):
        n = Notion()
        before = {k: list(v) for k, v in n.kids.items()}
        out = go(n, live=False)
        self.assertEqual((out["checkin"], out["synopsis"], out["archived"], out["archive_pending"]), ("would_write", "would_write", 0, 3))
        self.assertEqual({k: v for k, v in n.kids.items() if k in before}, before)

    def test_a_half_written_check_in_is_replaced_and_a_whole_one_is_frozen(self):
        n = Notion()
        half = n._add(n.kids[ROOT][0], {"type": "child_page", "child_page": {"title": "Check-in — Wed Oct 28, 2026"}})
        n._add(half, {"type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "partial"}}]}})
        self.assertEqual(go(n)["checkin"], "written")
        self.assertEqual(n.blocks[half]["child_page"]["title"], "Check-in — Wed Oct 28, 2026 (incomplete)")       # set aside, never deleted
        whole = n.page_titled(n.kids[ROOT][0], "Check-in — Wed Oct 28, 2026")
        self.assertEqual(go(n, now=datetime(2026, 11, 3, 9, 0, tzinfo=D.CENTRAL))["checkin"], "kept")
        self.assertEqual(n.page_titled(n.kids[ROOT][0], "Check-in — Wed Oct 28, 2026"), whole)

    def test_the_synopsis_is_rewritten_only_when_jira_changes(self):
        n, jira = Notion(), Jira()
        go(n, jira=jira)
        jira.issues["JFM-7"]["fields"]["status"] = {"name": "Done", "statusCategory": {"key": "done"}}
        jira.issues["JFM-7"]["fields"]["statuscategorychangedate"] = "2026-10-29T12:00:00.000-0500"
        self.assertEqual(go(n, jira=jira)["synopsis"], "updated")
        synopsis = n.texts(n.page_titled(n.kids[ROOT][1], "Q4 2026 — Quarterly Synopsis"))
        self.assertIn("JFM-7 — A QBR decision · Done", synopsis)
        self.assertEqual(sum(1 for t in synopsis if t.startswith("Jim")), 1)                      # old content removed, never doubled

    def test_matts_manual_megibow_row_and_an_explained_block_are_read(self):
        n = Notion()
        matt = n.kids[ROOT][3]
        n.rows[matt].append({"properties": {"Week Start": {"date": {"start": "2026-10-19"}}, **{c: {"number": 1} for c in R.CATEGORIES}}})
        shown = n.texts(n.page_titled(n.kids[ROOT][0], "Check-in — Wed Oct 28, 2026")) if go(n, jira=Jira(blocked_comment=True)) else []
        self.assertIn("Total 5 / min 5 · ✅ minimum met", shown)
        self.assertIn("Blocked with no reason in a comment (0)", shown)


class Failure(unittest.TestCase):
    def test_a_missing_page_is_refused_and_pushes_one_alert_while_the_check_in_is_owed(self):
        n, pushed = Notion(), []
        n.kids[ROOT] = n.kids[ROOT][1:]
        wednesday = datetime(2026, 11, 4, 7, 10, tzinfo=D.CENTRAL)
        with self.assertRaises(S.DashError) as caught:
            S.run(0, True, {**ENV}, jira=Jira(), client=n, now=wednesday, push=lambda *a: pushed.append(a))
        self.assertEqual(str(caught.exception), "JM_DASH_LAYOUT_MISSING")
        self.assertEqual([p[0] for p in pushed], ["jm_dashboard_failed"])
        self.assertNotIn("Pat", " ".join(pushed[0][1:]))
        with self.assertRaises(S.DashError):
            S.run(0, True, {**ENV}, jira=Jira(), client=n, now=datetime(2026, 11, 5, 14, 0, tzinfo=D.CENTRAL), push=lambda *a: pushed.append(a))
        self.assertEqual(len(pushed), 1)                                                          # not every hour: only while owed, or at 9 AM

    def test_a_notion_failure_is_a_fixed_code_and_jira_is_never_written(self):
        n, jira = Notion(), Jira()
        n.fail_on = lambda method, path: method == "POST"
        with self.assertRaises(S.DashError) as caught:
            go(n, jira=jira)
        self.assertEqual(str(caught.exception), "NOTION_HTTP_500")
        self.assertEqual(jira.writes, [])
        with self.assertRaises(S.DashError) as caught:
            S.run(0, True, {"JIRA_SITE_URL": SITE}, jira=jira, client=n, now=NOW, push=lambda *a: None)
        self.assertEqual(str(caught.exception), "JM_DASH_CONFIG_MISSING")


if __name__ == "__main__":
    unittest.main()
