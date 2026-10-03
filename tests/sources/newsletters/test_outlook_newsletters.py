import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from lifeos.platform.outlook import OutlookError
from lifeos.sources.newsletters import ingest, outlook

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
ENV = {"OUTLOOK_CLIENT_ID": "cid", "OUTLOOK_NEWSLETTER_ACCOUNTS": "personal"}


class Card:
    def __init__(self, url):
        self.url, self.title, self.company, self.location_text, self.salary_text = url, "t", "c", "l", ""
        self.age_days = 0
        self.provider_score = None


def msg(mid, sender, received="2026-10-07T10:00:00Z"):
    return {"id": mid, "receivedDateTime": received, "from": {"emailAddress": {"address": sender}}}


class FakeClient:
    """An Outlook mailbox with an Inbox and (once created) a J Newsletters folder; records every move."""

    def __init__(self, messages, html=None, deny_write=False, move_ok=True):
        self.inbox, self.folder, self.html, self.fetched = list(messages), None, html or {}, []
        self.deny_write, self.move_ok, self.moves = deny_write, move_ok, []

    def __call__(self, client_id, token, save):
        return self

    def messages(self, folder, since, limit=5000):
        if folder == "inbox":
            return list(self.inbox)
        return list(self.folder or [])

    def folder_id(self, name, create=False):
        assert name == "J Newsletters"
        if self.folder is None and create:
            if self.deny_write:
                raise OutlookError("OUTLOOK_HTTP_403")
            self.folder = []
        return "folder-1" if self.folder is not None else None

    def move(self, mid, folder_id):
        self.moves.append(mid)
        if not self.move_ok:
            return False
        item = next(m for m in self.inbox if m["id"] == mid)
        self.inbox.remove(item)
        self.folder.append(item)
        return True

    def message_html(self, mid):
        self.fetched.append(mid)
        return self.html.get(mid, "<html></html>")


class Db:
    """Context manager that is also its own connection; the seen table is a dict keyed by msg_key."""

    def __init__(self, seen=()):
        self.seen, self.cards = {k: "saved" for k in seen}, []

    def __enter__(self): return self
    def __exit__(self, *e): return False
    def cursor(self): return Cur(self)


class Cur:
    def __init__(self, db): self.db, self.out = db, []
    def __enter__(self): return self
    def __exit__(self, *e): return False

    def execute(self, sql, args=None):
        if sql.startswith("SELECT msg_key"):
            self.out = [(k,) for k in args if k in self.db.seen]
        elif sql.startswith("INSERT IGNORE INTO v7_outlook_mail_seen"):
            self.db.seen.setdefault(args[0], args[3])

    def fetchall(self): return self.out


def go(messages, html, db=None, live=True, parsers=None, looks=None, client=None):
    db = db or Db()
    client = client or FakeClient(messages, html)
    saved = []
    with patch.object(outlook.outlook_tokens, "ensure_schema"), patch.object(outlook.outlook_tokens, "load", return_value="R"), \
            patch.object(outlook.jobs_store, "ensure_schema"), patch.object(outlook.ingest, "backfill_provider"), \
            patch.object(outlook.ingest, "save_cards", side_effect=lambda conn, rule, key, cards, received: (saved.append((rule, key)), (len(cards), 0))[1]), \
            patch.dict(ingest.PARSERS, parsers or {"lensa": lambda h: [Card("https://x/1")] if "card" in h else [],
                                                   "jobright": lambda h: [], "linkedin-alerts": lambda h: []}), \
            patch.dict(ingest.LOOKS_LIKE_JOBS, looks or {"lensa": lambda h: "jobs" in h, "jobright": lambda h: False,
                                                         "linkedin-alerts": lambda h: False}):
        out = outlook.run(100, live, environ=ENV, connect=lambda: db, outlook_factory=client, now=NOW)
    return out, db, client, saved


class OutlookNewsletterTests(unittest.TestCase):
    def test_keys_are_short_stable_and_distinct(self):
        a = outlook.message_key("AAMk" + "x" * 150)
        self.assertEqual(len(a), 40)
        self.assertEqual(a, outlook.message_key("AAMk" + "x" * 150))
        self.assertNotEqual(a, outlook.message_key("AAMk" + "y" * 150))

    def test_rules_cover_alert_senders_and_courses_but_never_dice_private_email(self):
        self.assertEqual(outlook.rule_of("jobalert@lensa.com"), ("lensa", True))
        self.assertEqual(outlook.rule_of("dice@connect.dice.com"), ("dice", True))
        self.assertEqual(outlook.rule_of("no-reply@jobs.reed.co.uk"), ("reed", True))
        course_sender = "updates" + chr(64) + "courses.reed.co.uk"
        self.assertEqual(outlook.rule_of(course_sender), ("reed-course", False))
        for human in ("abc-def-ghi@user.dice.com", "kosi@recruiter.dice.com", "someone@example.com"):
            self.assertEqual(outlook.rule_of(human), (None, False))

    def test_job_alert_mail_is_filed_in_the_folder_saved_and_remembered_other_mail_is_left_alone(self):
        messages = [msg("m1", "jobalert@lensa.com"), msg("m2", "someone@example.com")]
        client = FakeClient(messages, {"m1": "card"})
        out, db, client, saved = go(messages, {"m1": "card"}, client=client)
        self.assertEqual((out["moved"], out["move_failed"], client.moves, [m["id"] for m in client.inbox]), (1, 0, ["m1"], ["m2"]))
        self.assertEqual((out["messages"], out["cards"], out["new_jobs"], out["marked_seen"], out["unsupported_sender"]), (1, 1, 1, 1, 1))
        self.assertEqual(client.fetched, ["m1"])                 # unrelated mail is never opened
        again, _, client2, saved2 = go(None, {"m1": "card"}, db=db, client=client)
        self.assertEqual((again["already_seen"], again["new_jobs"], again["moved"], saved2), (1, 0, 0, []))

    def test_dice_and_reed_job_alerts_are_ingested_but_courses_and_private_mail_are_not(self):
        messages = [msg("d1", "dice@connect.dice.com"), msg("r1", "no-reply@jobs.reed.co.uk"),
                    msg("c1", "updates" + chr(64) + "courses.reed.co.uk"), msg("p1", "abc@user.dice.com"),
                    msg("p2", "human@recruiter.dice.com")]
        html = {
            "d1": '<a href="https://www.dice.com/job-detail/EX123"><span>Platform Analyst</span><span>Example Systems</span><span>Location: Remote</span></a>',
            "r1": '<a href="https://www.reed.co.uk/jobs/data-specialist/12345678"><span>Data Specialist</span><span>Example Group</span><span>Location: Remote</span></a>',
        }
        out, db, client, saved = go(messages, html, client=FakeClient(messages, html))
        self.assertEqual((out["moved"], out["no_parser"], out["new_jobs"], out["skipped"]), (3, 1, 2, 0))
        self.assertEqual([row[0] for row in saved], ["dice", "reed"])
        self.assertEqual(set(client.fetched), {"d1", "r1"})
        self.assertEqual(len(db.seen), 2)
        self.assertEqual([m["id"] for m in client.inbox], ["p1", "p2"])

    def test_malformed_alert_card_is_skipped_and_remains_unseen(self):
        messages = [msg("d1", "dice@connect.dice.com")]
        html = {"d1": '<a href="https://www.dice.com/job-detail/EX999"><span>View job</span></a>'}
        out, db, _, saved = go(messages, html)
        self.assertEqual((out["cards"], out["skipped"], out["no_cards"], out["marked_seen"], saved), (0, 1, 1, 0, []))
        self.assertEqual(db.seen, {})

    def test_dry_run_moves_saves_and_remembers_nothing(self):
        client = FakeClient([msg("m1", "jobalert@lensa.com")], {"m1": "card"})
        out, db, client, saved = go(None, {}, live=False, client=client)
        self.assertEqual((out["would_move"], out["moved"], client.moves, out["new_jobs"], out["marked_seen"], saved, db.seen),
                         (1, 0, [], 0, 0, [], {}))
        self.assertEqual(out["cards"], 1)

    def test_a_read_only_sign_in_keeps_ingesting_from_the_inbox_and_moves_nothing(self):
        client = FakeClient([msg("m1", "jobalert@lensa.com")], {"m1": "card"}, deny_write=True)
        out, db, client, saved = go(None, {}, client=client)
        self.assertEqual((out["write_denied"], out["moved"], out["new_jobs"], out["marked_seen"]), (1, 0, 1, 1))
        self.assertEqual([m["id"] for m in client.inbox], ["m1"])

    def test_a_move_that_cannot_be_read_back_is_counted_and_the_mail_is_still_ingested_from_where_it_is(self):
        client = FakeClient([msg("m1", "jobalert@lensa.com")], {"m1": "card"}, move_ok=False)
        out, _, client, _ = go(None, {}, client=client)
        self.assertEqual((out["moved"], out["move_failed"], out["new_jobs"]), (0, 1, 1))

    def test_a_parser_gap_stays_unseen_and_a_non_job_mail_is_closed(self):
        out, db, _, _ = go([msg("m1", "jobalert@lensa.com"), msg("m2", "jobalert@lensa.com")], {"m1": "jobs here", "m2": "reset your password"})
        self.assertEqual((out["no_cards"], out["non_job_mail"], out["marked_seen"]), (1, 1, 1))
        self.assertNotIn(outlook.message_key("m1"), db.seen)       # retried next run
        self.assertEqual(db.seen[outlook.message_key("m2")], "non_job")

    def test_mail_older_than_the_window_is_closed_without_being_opened(self):
        out, db, client, _ = go([msg("old", "jobalert@lensa.com", received="2025-01-01T00:00:00Z")], {})
        self.assertEqual((out["stale_mail"], client.fetched, db.seen[outlook.message_key("old")]), (1, [], "stale"))

    def test_failures_are_fixed_codes_by_position_and_output_is_counts_only(self):
        with patch.object(outlook.outlook_tokens, "ensure_schema"), patch.object(outlook.outlook_tokens, "load", return_value=None), \
                patch.object(outlook.jobs_store, "ensure_schema"), patch.object(outlook.ingest, "backfill_provider"):
            with self.assertRaises(OutlookError) as error:
                outlook.run(1, True, environ=dict(ENV, OUTLOOK_NEWSLETTER_ACCOUNTS="personal,work"), connect=lambda: Db(),
                            outlook_factory=FakeClient([]), now=NOW)
        self.assertEqual(str(error.exception), "OUTLOOK_NEWSLETTERS_FAILED:2of2:OUTLOOK_NOT_SIGNED_IN@1,OUTLOOK_NOT_SIGNED_IN@2")
        out, _, _, _ = go([msg("m1", "jobalert@lensa.com")], {"m1": "card"})
        for private in ("lensa.com", "m1", "x/1", "J Newsletters"):
            self.assertNotIn(private, json.dumps(out))
        with self.assertRaises(OutlookError):
            outlook.run(1, False, environ={}, connect=lambda: Db(), outlook_factory=FakeClient([]), now=NOW)


if __name__ == "__main__":
    unittest.main()
