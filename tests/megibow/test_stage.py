import unittest
from datetime import datetime, timedelta, timezone

from lifeos.megibow import card, classify as C, review
from lifeos.megibow.windows import CHI
from lifeos.sources import megibow as stage

NOW = datetime(2026, 10, 7, 13, 0, tzinfo=CHI)  # Wednesday
UTC = timezone.utc
CELL = lambda t: [{"type": "text", "plain_text": t, "text": {"content": t}}]


class FakeNotion:
    """A callout holding one table, plus a review data source."""
    def __init__(self, review_rows=None):
        self.callout = "old"
        self.rows = {f"r{i}": [["x"] * 10][0] for i in range(7)}
        self.patches, self.created, self.updated = [], [], []
        self.review_rows = review_rows or []
        self.shape_ok = True
        self.legacy = ""

    def call(self, method, path, body=None):
        if method == "GET" and path.startswith("/blocks/page/children"):
            return {"results": [{"id": "para", "type": "paragraph"}, {"id": "cal", "type": "callout"}], "has_more": False}
        if method == "GET" and path.startswith("/blocks/para"):
            return {"id": "para", "type": "paragraph"}
        if method == "GET" and path.startswith("/blocks/cal/children"):
            extra = [{"id": "leg", "type": "paragraph", "paragraph": {"rich_text": CELL(self.legacy)}}] if self.legacy else []
            return {"results": [{"id": "tbl", "type": "table", "table": {"table_width": 10 if self.shape_ok else 4}}] + extra, "has_more": False}
        if method == "GET" and path.startswith("/blocks/tbl/children"):
            return {"results": [{"id": k, "type": "table_row", "table_row": {"cells": [CELL(c) for c in v]}} for k, v in self.rows.items()], "has_more": False}
        if method == "GET" and path.startswith("/blocks/cal"):
            return {"id": "cal", "type": "callout", "callout": {"rich_text": CELL(self.callout)}}
        if method == "PATCH" and path == "/blocks/cal":
            self.callout = body["callout"]["rich_text"][0]["text"]["content"]
            self.patches.append("cal")
            return {}
        if method == "PATCH" and path.startswith("/blocks/r"):
            self.rows[path.split("/")[-1]] = [c[0]["text"]["content"] for c in body["table_row"]["cells"]]
            self.patches.append(path)
            return {}
        if method == "GET" and path.startswith("/data_sources/"):
            return {"properties": {n: {"type": t} for n, t in review.SCHEMA.items()}}
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None):
        self.created.append(body["properties"])
        return {}

    def query_data_source(self, source_id, body):
        return {"results": self.review_rows, "has_more": False}

    def update_page_properties(self, page_id, props):
        self.updated.append((page_id, props))


class FakeGmail:
    def __init__(self, records):
        self.records = records

    def list_ids_complete(self, query, limit):
        return list(self.records)

    def sent_record(self, i):
        return self.records[i]

    def profile_address(self):
        return "jim@example.com"


class FakeGcal:
    def __init__(self, items):
        self.items = items

    def list_events(self, a, b):
        return self.items


def sent(i, to, when, subject="Hello"):
    return {"id": i, "thread": "t", "sender": "jim@example.com", "to": to, "cc": "", "subject": subject, "snippet": "", "sent_at": when, "labels": [], "bulk": False}


def gevent(uid, who, start, created, summary="Intro", bridged=False):
    e = {"iCalUID": uid, "id": uid, "summary": summary, "start": {"dateTime": start.isoformat()}, "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
         "created": created.isoformat(), "organizer": {"self": True}, "attendees": [{"email": "jim@example.com", "self": True, "organizer": True}, {"email": who, "responseStatus": "accepted"}]}
    if bridged:
        e["extendedProperties"] = {"private": {"v7_src": "outlook"}}
    return e


ENV = {"MEGIBOW_PAGE_ID": "page", "MEGIBOW_REVIEW_DB_ID": "abc123", "NOTION_JIRA_TOKEN": "t"}
KNOWN = {"lensa"}


def run(gmail, gcal, notion, live=True, env=ENV, now=NOW):
    return stage.run(0, live, environ=env, gmail=gmail, gcal=gcal, outlook_clients=[], notion=notion, now=now, known=KNOWN)


class Card(unittest.TestCase):
    def table(self):
        return [[str(i)] * 10 for i in range(7)]

    def test_writes_in_place_then_noops(self):
        n = FakeNotion()
        first = card.write(n, "cal", "status", self.table(), True)
        self.assertEqual((first["rows_written"], first["text_written"]), (7, True))
        again = card.write(n, "cal", "status", self.table(), True)
        self.assertEqual((again["rows_written"], again["text_written"]), (0, False))

    def test_dry_run_writes_nothing(self):
        n = FakeNotion()
        card.write(n, "cal", "status", self.table(), False)
        self.assertEqual(n.patches, [])

    def test_wrong_shape_writes_nothing(self):
        n = FakeNotion()
        n.shape_ok = False
        with self.assertRaises(card.CardError):
            card.write(n, "cal", "status", self.table(), True)
        self.assertEqual(n.patches, [])

    def test_degraded_leaves_table_alone(self):
        n = FakeNotion()
        before = dict(n.rows)
        card.write(n, "cal", "DEGRADED", None, True)
        self.assertEqual(n.rows, before)
        self.assertEqual(n.callout, "DEGRADED")


class Stage(unittest.TestCase):
    def test_counts_dedupe_bridged_and_writes_table(self):
        mon = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
        gm = FakeGmail({"a": sent("a", "Pat <pat@lensa.com>", mon), "b": sent("b", "pat@lensa.com", mon + timedelta(hours=1)), "c": sent("c", "noreply@lensa.com", mon),
                        "d": sent("d", "jim@example.com", mon)})
        n = FakeNotion()
        gc = FakeGcal([gevent("u1", "pat@lensa.com", NOW + timedelta(days=2), NOW - timedelta(hours=5)), gevent("u2", "pat@lensa.com", NOW + timedelta(days=3), NOW, bridged=True)])
        counts = run(gm, gc, n)
        self.assertEqual(counts["status"][C.OUTREACH], 2)
        self.assertEqual(counts["status"][C.SCHEDULED], 1)
        self.assertEqual(n.rows["r2"][-1], "2")           # Outreach, current week
        self.assertEqual(n.rows["r3"][-1], "1")           # Scheduled
        self.assertEqual(n.rows["r1"][-1], "3")           # total
        self.assertTrue(n.callout.startswith("Last refreshed Wed Oct 7, 1:00 PM CT"))

    def test_review_row_created_once_and_answer_applied(self):
        mon = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
        gm = FakeGmail({"a": sent("a", "lee@jobright.ai", mon, "Career introduction")})
        n = FakeNotion()
        counts = run(gm, FakeGcal([]), n)
        self.assertEqual((counts["review_open"], counts["status"][C.OUTREACH]), (1, 0))
        self.assertEqual(len(n.created), 1)
        key = n.created[0]["Source key"]["rich_text"][0]["text"]["content"]
        answered = [{"id": "page1", "last_edited_time": "2026-10-07T12:00:00Z", "properties": {
            "Source key": {"rich_text": [{"plain_text": key}]}, "Resolution": {"select": {"name": "Count as Outreach"}}, "Status": {"select": {"name": "Open"}}}}]
        n2 = FakeNotion(answered)
        counts2 = run(gm, FakeGcal([]), n2, live=False)
        self.assertEqual((counts2["decisions_applied"], counts2["status"][C.OUTREACH], counts2["review_open"]), (1, 1, 0))
        self.assertEqual(n2.created, [])

    def test_failed_source_is_degraded_and_keeps_table(self):
        class Boom(FakeGcal):
            def list_events(self, a, b):
                from lifeos.platform.gcal import GcalError
                raise GcalError("GCAL_DOWN")
        n = FakeNotion()
        before = dict(n.rows)
        with self.assertRaises(stage.MegibowError):
            run(FakeGmail({}), Boom([]), n)
        self.assertEqual(n.rows, before)
        self.assertTrue(n.callout.startswith("DEGRADED"))
        self.assertIn("incomplete", n.callout)

    def test_legacy_totals_seed_cumulative(self):
        n = FakeNotion()
        n.legacy = "Legacy totals: Outreach 20, Scheduled 13, Networking Calls 11, Recruiter Calls 36, Company Calls 15"
        run(FakeGmail({}), FakeGcal([]), n)
        self.assertEqual(n.rows["r1"][1], "95")
        self.assertEqual(n.rows["r2"][1], "20")
        n.legacy = "Legacy totals: Outreach 20"
        self.assertIsNone(card.read_legacy(n, "cal"))

    def test_dry_run_changes_nothing(self):
        n = FakeNotion()
        run(FakeGmail({}), FakeGcal([]), n, live=False)
        self.assertEqual((n.patches, n.created, n.updated), ([], [], []))


if __name__ == "__main__":
    unittest.main()
