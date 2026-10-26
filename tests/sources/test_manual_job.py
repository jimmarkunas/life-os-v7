import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from lifeos.jobs import identity
from lifeos.sources import manual_job as mj

URL = "https://careers.example.com/en/job/example-city/senior-program-manager/159/101596083584"
NOW = datetime(2026, 10, 26, 15, 0, tzinfo=ZoneInfo("America/Chicago"))
BASE = {"MANUAL_JOB_URL": URL, "MANUAL_JOB_COMPANY": "ExampleCo", "MANUAL_JOB_TITLE": "Senior Program Manager", "MANUAL_JOB_ROUND_DAY": "2026-10-13"}


class Cursor:
    def __init__(self, new=True, held=False):
        self.sql, self.rowcount, self.held = [], 1 if new else 0, held

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, args=()):
        self.sql.append((sql.split()[0], args))

    def fetchone(self):
        return (1,) if self.held else None

    def fetchall(self):
        return []


def go(environ, live=False, cursor=None, **kw):
    cursor = cursor or Cursor()
    seen = {}

    def fake_stage(limit, live, environ, client=None, evidence=(), context=None):
        seen.update(live=live, evidence=list(evidence))
        return {"blocked": 0, "why": {"parent_create_allowed": 1}}

    with patch.object(mj.stage, "run", fake_stage):
        counts = mj.run(40, live, environ={**BASE, **environ}, client=object(), now=NOW, connect=lambda: cursor, context=object())
    return counts, seen, cursor


class ManualJob(unittest.TestCase):
    def test_a_dry_run_reads_the_store_and_plans_the_interview_write_without_either_write(self):
        counts, seen, cursor = go({})
        self.assertEqual((counts["job"], counts["failed"], seen["live"]), ("new", 0, False))
        self.assertEqual([kind for kind, _ in cursor.sql], ["SELECT"])
        (parent, query), = seen["evidence"]
        self.assertEqual((parent.company, parent.role, query.interview_date, query.ordinal, query.stable_job_key), ("ExampleCo", "Senior Program Manager", "2026-10-13", 1, identity.url_hash(URL)))

    def test_a_held_job_is_present_and_a_live_run_adds_a_new_one_resolved_in_the_recruiter_lane(self):
        self.assertEqual(go({}, cursor=Cursor(held=True))[0]["job"], "present")
        counts, seen, cursor = go({"INTERVIEW_OS_WRITER": "v7"}, live=True)
        insert = next(args for kind, args in cursor.sql if kind == "INSERT")
        self.assertEqual((counts["job"], seen["live"]), ("new", True))
        self.assertTrue({"RESOLVED", "Recruiter", "manual", "Manual"} <= set(insert))
        update = cursor.sql[-1]
        self.assertEqual((update[0], update[1][0], update[1][1]), ("UPDATE", URL, "employer"))
        again = go({"INTERVIEW_OS_WRITER": "v7"}, live=True, cursor=Cursor(new=False))
        self.assertEqual(again[0]["job"], "present")

    def test_the_interview_write_needs_the_writer_switch_even_when_live(self):
        self.assertFalse(go({}, live=True)[1]["live"])

    def test_a_request_message_link_makes_a_pending_round_and_every_bad_input_is_a_fixed_code(self):
        _, seen, _ = go({"MANUAL_JOB_ROUND_DAY": "", "MANUAL_JOB_REQUEST_URL": "https://mail.example.com/t/1"})
        (_, query), = seen["evidence"]
        self.assertEqual((query.interview_date, query.source), (None, "https://mail.example.com/t/1"))
        for label, change, code in (("no title", {"MANUAL_JOB_TITLE": ""}, "MANUAL_JOB_IDENTITY_MISSING"), ("http link", {"MANUAL_JOB_URL": "http://careers.example.com/job/1"}, "MANUAL_JOB_LINK_INVALID"),
                                    ("bad day", {"MANUAL_JOB_ROUND_DAY": "10/13/2026"}, "MANUAL_JOB_DAY_INVALID"), ("bad request", {"MANUAL_JOB_REQUEST_URL": "mail"}, "MANUAL_JOB_REQUEST_INVALID"),
                                    ("no round evidence", {"MANUAL_JOB_ROUND_DAY": ""}, "MANUAL_JOB_ROUND_EVIDENCE_MISSING")):
            counts, seen, cursor = go(change)
            self.assertEqual((counts["why"], counts["failed"], seen, cursor.sql), ({code: 1}, 1, {}, []), label)

    def test_a_store_failure_stops_before_the_interview_write_and_logs_no_text(self):
        def boom():
            raise RuntimeError("db host example.com")
        with patch.object(mj.stage, "run") as writer:
            counts = mj.run(40, False, environ=BASE, client=object(), now=NOW, connect=boom, context=object())
        self.assertEqual((counts["why"], writer.call_count), ({"MANUAL_JOB_STORE_FAILED": 1}, 0))
        self.assertNotIn("example.com", str(counts))


if __name__ == "__main__":
    unittest.main()
