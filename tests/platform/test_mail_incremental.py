"""LIFE-906: an incremental Gmail read: mail an earlier COMPLETE read holds is not read again, the result equals a full read, and anything uncertain falls back to the full window."""
import json
import unittest
import zlib
from datetime import datetime, timedelta, timezone

from lifeos.platform import mail
from lifeos.platform.gmail import GmailError
from lifeos.sources.interview import mail_snapshot as snap

UTC = timezone.utc
NOW = datetime(2026, 11, 23, 3, 0, tzinfo=UTC)


class Gmail:
    """Ids newest first; `received` per id; records how many reads each id got."""
    def __init__(self, listing, received, bad=()):
        self.listing, self.received, self.bad, self.reads, self.retries = list(listing), received, set(bad), [], 0

    def list_ids_complete(self, query, limit, include_spam=False):
        return list(self.listing)

    def mail_record(self, message_id):
        self.reads.append(message_id)
        if message_id in self.bad:
            raise GmailError("GMAIL_MESSAGE_INCOMPLETE")
        return {"id": message_id, "received_at": self.received[message_id], "headers": {"from": f"P <{message_id}@example.test>", "subject": f"S {message_id}"}, "body_text": "b " + message_id,
                "label_ids": ["INBOX"], "thread": "t" + message_id}


def ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat()


RECEIVED = {"new1": ago(0.5), "new2": ago(5), "old1": ago(30), "old2": ago(60), "old3": ago(90)}


class Incremental(unittest.TestCase):
    def full(self, listing):
        return mail.gmail_records(Gmail(listing, RECEIVED), NOW)

    def test_unchanged_mail_is_not_read_again_and_the_result_equals_a_full_read(self):
        held = {r["id"]: r for r in self.full(["old1", "old2", "old3"])[0]}
        listing = ["new1", "new2", "old1", "old2", "old3"]
        gmail, timing = Gmail(listing, RECEIVED), {}
        got = mail.gmail_records(gmail, NOW, reuse=held, timing=timing)
        self.assertEqual(sorted(gmail.reads), ["new1", "new2"])                                        # new mail is read once, nothing else
        self.assertEqual(got, self.full(listing))                                                      # same records, same order, same unreadable count
        self.assertEqual((timing["read"], timing["reused"]), (2, 3))

    def test_mail_that_arrived_in_the_last_two_hours_is_read_again_and_an_unlisted_id_is_dropped(self):
        held = {r["id"]: r for r in self.full(["new1", "old1", "old3"])[0]}
        gmail = Gmail(["new1", "old1"], RECEIVED)                                                       # old3 aged out of the listing
        got, _ = mail.gmail_records(gmail, NOW, reuse=held)
        self.assertEqual(gmail.reads, ["new1"])                                                        # fresh: its folder can still change
        self.assertEqual([r["id"] for r in got], ["new1", "old1"])

    def test_no_checkpoint_reads_the_full_window_and_a_held_record_without_a_usable_time_is_read(self):
        gmail = Gmail(["old1", "old2"], RECEIVED)
        mail.gmail_records(gmail, NOW, reuse={})
        self.assertEqual(sorted(gmail.reads), ["old1", "old2"])
        bad = {"old1": {"id": "old1", "received": "not a time"}}
        gmail = Gmail(["old1"], RECEIVED)
        mail.gmail_records(gmail, NOW, reuse=bad)
        self.assertEqual(gmail.reads, ["old1"])

    def test_a_reused_record_is_a_copy_and_an_unreadable_new_message_is_counted_not_reused(self):
        held = {r["id"]: r for r in self.full(["old1"])[0]}
        got, unreadable = mail.gmail_records(Gmail(["new2", "old1"], RECEIVED, bad={"new2"}), NOW, reuse=held)
        self.assertEqual((unreadable, [r["id"] for r in got]), (1, ["old1"]))
        got[0]["subject"] = "changed"
        self.assertNotEqual(held["old1"]["subject"], "changed")                                         # the checkpoint is never edited through a result


class Db:
    """Only the checkpoint query: rows are (taken_at, days, complete, fingerprint, payload); it answers the newest COMPLETE row, like the real WHERE clause."""
    def __init__(self, rows, down=False):
        self.rows, self.down = rows, down

    def __call__(self):
        if self.down:
            raise RuntimeError("down")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, args=()):
        assert "complete = 1" in sql
        self.found = sorted((r for r in self.rows if r[2]), key=lambda r: r[0], reverse=True)[:1]

    def fetchone(self):
        return (self.found[0][0], self.found[0][1], self.found[0][3], self.found[0][4]) if self.found else None


def row(taken, days=14, complete=True, records=None, corrupt=False):
    records = records if records is not None else [{"provider": "gmail", "id": "g1", "received": ago(30)}, {"provider": "outlook", "id": "a:1", "received": ago(30)}]
    return (taken, days, complete, "0" * 64 if corrupt else snap.fingerprint(records), zlib.compress(json.dumps({"records": records}).encode()))


class FullHours(unittest.TestCase):
    def test_the_whole_window_is_read_in_the_full_read_hours_and_the_checkpoint_is_used_in_the_others(self):
        from unittest import mock
        from lifeos.sources.interview import interview_evidence as ev
        seen = []

        def fake_read(environ, now, gmail, outlook, days, seconds, detail, **kw):
            seen.append(bool(kw.get("reuse")))
            detail["gmail"] = {"listed": 1, "read": 0 if kw.get("reuse") else 1, "reused": 1 if kw.get("reuse") else 0, "retries": 0}
            return [], 0
        for hour, reused in ((12, False), (13, True), (18, False), (23, True)):
            when = datetime(2026, 11, 23, hour, 5, tzinfo=ev.TZ)
            with mock.patch.object(ev, "read_mail", fake_read), mock.patch.object(snap, "previous", return_value={"g1": {"id": "g1"}}), mock.patch.object(snap, "save", return_value=True):
                counts = snap.stage(0, True, environ={}, now=when, connect=lambda: None)
            self.assertEqual((seen[-1], counts["gmail"]["reused"]), (reused, 1 if reused else 0), hour)


class Checkpoint(unittest.TestCase):
    def test_the_latest_complete_snapshot_of_this_window_is_the_checkpoint_gmail_only(self):
        got = snap.previous(Db([row(NOW - timedelta(minutes=50))]), NOW, 14)
        self.assertEqual(list(got), ["g1"])

    def test_a_partial_read_never_becomes_the_checkpoint_the_older_complete_one_stays(self):
        older, partial = row(NOW - timedelta(minutes=110), records=[{"provider": "gmail", "id": "old", "received": ago(30)}]), row(NOW - timedelta(minutes=50), complete=False)
        self.assertEqual(list(snap.previous(Db([older, partial]), NOW, 14)), ["old"])

    def test_stale_other_window_corrupt_future_or_unreadable_all_fall_back_to_the_full_window(self):
        for rows in ([row(NOW - timedelta(hours=4))], [row(NOW - timedelta(minutes=50), days=7)], [row(NOW - timedelta(minutes=50), corrupt=True)], [row(NOW + timedelta(minutes=5))], []):
            self.assertEqual(snap.previous(Db(rows), NOW, 14), {})
        self.assertEqual(snap.previous(Db([], down=True), NOW, 14), {})


if __name__ == "__main__":
    unittest.main()
