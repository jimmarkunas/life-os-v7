"""LIFE-906: Amazon retention reads only mail its checkpoint does not hold. New mail is read once; unchanged mail is judged against Notion again with no Gmail read; a missing, stale, other-parser or unreadable
checkpoint falls back to the full read; a run that did not finish leaves the checkpoint; the verdicts equal a full read's."""
import unittest
from datetime import datetime, timedelta, timezone

from lifeos.amazon import retention
from lifeos.platform import mail_checkpoint
from tests.amazon.test_retention import NOW, TOKEN, RetentionGmail, recorded_store
from tests.jobs.fakes_jobsdb import JobsDB

STAMP = NOW.replace(tzinfo=None)
LIVE = {**TOKEN, "LIFEOS_ACQ_DB_NAME": "x"}


class CountingGmail(RetentionGmail):
    def __init__(self, messages):
        super().__init__(messages)
        self.reads = []

    def message_record(self, message_id, keep_lines=False):
        self.reads.append(message_id)
        return super().message_record(message_id, keep_lines)


def counting():
    gmail, notion = recorded_store()
    copy = CountingGmail(gmail.messages)
    return copy, notion


class Checkpoint(unittest.TestCase):
    def setUp(self):
        self.db = JobsDB()
        self.parser = retention.parser_version()

    def seen(self, now=STAMP, parser=None, connect=None):
        connect = connect or (lambda: self.db)
        parser = parser or self.parser
        return (lambda: mail_checkpoint.load(retention.READER, parser, now, connect), lambda digests: mail_checkpoint.save(retention.READER, parser, now, digests, connect))

    def run_(self, gmail, notion, seen, live=False):
        return retention.run(100, live, environ=LIVE, gmail=gmail, notion=notion, now=NOW, seen=seen)

    def verdicts(self, counts):
        return {k: v for k, v in counts.items() if k not in ("gmail", "checkpoint")}

    def test_the_first_run_reads_everything_and_saves_the_second_reads_nothing_and_judges_the_same(self):
        gmail, notion = counting()
        first = self.run_(gmail, notion, self.seen())
        self.assertEqual((first["gmail"], first["checkpoint"], sorted(gmail.reads)), ({"listed": 2, "read": 2, "reused": 0}, "saved", ["m-1", "m-2"]))
        gmail.reads.clear()
        second = self.run_(gmail, notion, self.seen())
        self.assertEqual((second["gmail"], gmail.reads), ({"listed": 2, "read": 0, "reused": 2}, []))
        self.assertEqual(self.verdicts(second), self.verdicts(first))                   # the same counts and reasons as the full read

    def test_new_mail_is_read_once_and_the_rest_is_not(self):
        gmail, notion = counting()
        self.run_(gmail, notion, self.seen())
        gmail.messages["m-3"] = {**gmail.messages["m-2"], "id": "m-3"} if "id" in gmail.messages["m-2"] else dict(gmail.messages["m-2"])
        gmail.reads.clear()
        counts = self.run_(gmail, notion, self.seen())
        self.assertEqual((counts["gmail"], gmail.reads), ({"listed": 3, "read": 1, "reused": 2}, ["m-3"]))

    def test_a_stale_checkpoint_a_different_parser_and_an_unreadable_store_all_read_in_full(self):
        gmail, notion = counting()
        self.run_(gmail, notion, self.seen())
        for label, seen in (("stale", self.seen(now=STAMP + timedelta(hours=mail_checkpoint.REUSE_HOURS + 1))),
                            ("parser", self.seen(parser="0" * 16)),
                            ("store", self.seen(connect=lambda: (_ for _ in ()).throw(RuntimeError("down"))))):
            gmail.reads.clear()
            counts = self.run_(gmail, notion, seen)
            self.assertEqual((counts["gmail"]["read"], counts["gmail"]["reused"]), (2, 0), label)

    def test_a_run_that_did_not_finish_leaves_the_checkpoint_alone(self):
        gmail, notion = counting()
        self.run_(gmail, notion, self.seen())
        before = self.db.raw.execute("SELECT message_id, seen_at FROM v7_mail_checkpoint ORDER BY message_id").fetchall()
        gmail.messages["m-3"] = dict(gmail.messages["m-2"])
        later = self.seen(now=STAMP + timedelta(hours=1))
        gmail.message_record = lambda *a, **k: (_ for _ in ()).throw(retention.GmailError("GMAIL_HTTP_500"))        # the next read fails part way
        counts = self.run_(gmail, notion, later)
        self.assertNotIn("checkpoint", counts)
        self.assertEqual(self.db.raw.execute("SELECT message_id, seen_at FROM v7_mail_checkpoint ORDER BY message_id").fetchall(), before)

    def test_a_trashed_message_leaves_the_checkpoint_and_the_digest_holds_no_mail_text(self):
        gmail, notion = counting()
        counts = self.run_(gmail, notion, self.seen(), live=True)
        self.assertEqual((counts["trashed"], counts["checkpoint"]), (1, "saved"))
        rows = self.db.raw.execute("SELECT message_id, digest FROM v7_mail_checkpoint").fetchall()
        self.assertEqual([r[0] for r in rows], ["m-2"])                                   # m-1 was trashed; only the kept mail stays
        import json
        digest = json.loads(rows[0][1])
        self.assertEqual(sorted(digest), ["events", "received_at", "shape"])             # order number, status and a total flag: never a subject, a body or an address
        self.assertEqual(sorted(digest["events"][0]), ["has_total", "order_id", "reason", "status"])

    def test_no_store_credentials_means_the_old_behavior(self):
        gmail, notion = counting()
        counts = retention.run(100, False, environ=TOKEN, gmail=gmail, notion=notion, now=NOW)
        self.assertEqual((counts["gmail"], "checkpoint" in counts), ({"listed": 2, "read": 2, "reused": 0}, False))


if __name__ == "__main__":
    unittest.main()
