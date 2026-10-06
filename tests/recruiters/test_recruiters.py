import json
import pathlib
import re
import unittest
from datetime import datetime, timezone

from lifeos.platform import mail as platform_mail
from lifeos.recruiters import classify as C, identity, notion as store, qualify, stage
from tests.kit.recruiters import FakeRecruiters, ReadOnlyGmail, ReadOnlyOutlook, mail, page

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)                          # a Thursday
SIGNED = "Hi,\n\nI'm reaching out about a Data Engineer role for a client.\n\nBest,\nPat Example\nSenior Recruiter at Acme Staffing\n"
INHOUSE = "Hello,\n\nWe would love to talk about the Platform Lead position.\n\nThanks,\nSam Sample\nTalent Acquisition Partner | Globex\n"


def classify(msg):
    shape, reason, evidence = C.classify(msg)
    return shape, reason, qualify.qualify(msg, shape, reason, evidence)


class ClassifierTests(unittest.TestCase):
    def test_direct_human_recruiter_qualifies_with_a_source_backed_company(self):
        shape, _, (verdict, c) = classify(mail(body=SIGNED))
        self.assertEqual((shape, verdict, c["person"], c["company"], c["needs_review"]), (C.DIRECT_HUMAN, "CANDIDATE", "Pat Example", "Acme Staffing", False))

    def test_an_in_house_recruiter_is_a_recruiter(self):
        shape, _, (verdict, c) = classify(mail(sender_name="Sample, Sam", address="sam@example.com", body=INHOUSE))
        self.assertEqual((shape, verdict, c["person"], c["company"]), (C.DIRECT_HUMAN, "CANDIDATE", "Sam Sample", "Globex"))

    def test_an_automated_job_alert_is_excluded(self):
        shape, reason, (verdict, _) = classify(mail(sender_name="Jobs Digest", address="alerts@example.com", subject="12 new jobs for you", body=SIGNED, bulk=True))
        self.assertEqual((shape, verdict), (C.OTHER, "EXCLUDED"))

    def test_automation_signals_cannot_be_defeated_by_recruiter_looking_text(self):
        for kind in ({"bulk": True}, {"auto": True}, {"noreply": True}):
            self.assertEqual(classify(mail(subject="Recruiter: exciting opportunity", body=SIGNED, **kind))[0], C.OTHER, kind)
        self.assertEqual(classify(mail(subject="Automatic reply: recruiter", body=SIGNED))[0], C.OTHER)
        self.assertEqual(classify(mail(sender_name="Careers", address="careers@example.com", body=SIGNED))[0], C.OTHER)

    def test_a_hiring_manager_is_not_a_recruiter(self):
        body = "Hi,\n\nI'd like to set up time to talk about the role on my team.\n\nThanks,\nRobin Manager\nEngineering Manager, Globex\n"
        shape, reason, (verdict, _) = classify(mail(sender_name="Robin Manager", body=body))
        self.assertEqual((shape, reason, verdict), (C.OTHER, "HIRING_MANAGER", "EXCLUDED"))
        self.assertEqual(classify(mail(body="Our recruiter will contact you soon."))[0], C.OTHER)      # mentioning a recruiter is not being one

    def test_a_named_human_relay_qualifies_and_needs_review_without_a_company(self):
        shape, reason, (verdict, c) = classify(mail(sender_name="Lee Relay via Examplejobs", address="messages-noreply@linkedin.com", subject="New message from Lee Relay",
                                                    body="Lee Relay sent you a message about a role."))
        self.assertEqual((shape, verdict, c["person"], c["company"], c["client"], c["needs_review"]), (C.HUMAN_NAMED_RELAY, "CANDIDATE", "Lee Relay", "", "", True))

    def test_an_unreadable_relay_never_invents_a_person(self):
        msg = mail(sender_name="Careers Team", address="m@linkedin.com", subject="You have a new message", body="Someone sent you a message.")
        self.assertEqual(classify(msg)[2], ("UNRESOLVED", "NAME_UNREADABLE"))
        single = mail(sender_name="Alex via Board", address="m@linkedin.com", subject="New message from Alex", body="Alex sent you a message")
        self.assertEqual(classify(single)[2][1]["needs_review"], True)                                  # a one-word name is source-backed but never deterministic
        alert = mail(sender_name="Board", address="digest@linkedin.com", subject="Jobs for you", body="12 jobs match your search")
        self.assertEqual(classify(alert)[0], C.OTHER)

    def test_client_and_role_need_explicit_evidence(self):
        plain = classify(mail(body=SIGNED.replace("for a client", "")))[2][1]
        self.assertEqual(plain["client"], "")
        said = classify(mail(body=SIGNED.replace("for a client.", "on behalf of Initech, who is hiring.")))[2][1]
        self.assertEqual(said["client"], "Initech")
        self.assertEqual(classify(mail(subject="Hello", body="Hi\nI'm a recruiter at Acme.\n"))[2][1]["role"], "")

    def test_an_unreadable_sender_name_is_unresolved(self):
        self.assertEqual(classify(mail(sender_name="", address="x1@example.com", body=SIGNED))[2], ("UNRESOLVED", "NAME_UNREADABLE"))


class RealShapeTests(unittest.TestCase):
    LINKEDIN = "Message replied: Role for you\nInMail: You have a new message\n\n        Lee Relay\n\n      Reply\n      https://example.com/thread\n\nHi, are you free Monday?\n"

    def test_the_boards_plain_text_layout_gives_a_human_name(self):
        shape, reason, (verdict, c) = classify(mail(sender_name="", address="hit-reply@linkedin.com", subject="Message replied: Role for you", body=self.LINKEDIN))
        self.assertEqual((shape, reason, verdict, c["person"], c["needs_review"]), (C.HUMAN_NAMED_RELAY, "RELAY_NAMED", "CANDIDATE", "Lee Relay", True))

    def test_staffing_outreach_without_a_title_qualifies_only_with_two_solicitation_signals_and_a_human_name(self):
        body = "Hi,\n\nI have an urgent position. Please send your updated resume and expected rate.\n\nThanks,\nSam Sample\nNorthwind Staffing\n"
        shape, reason, (verdict, c) = classify(mail(sender_name="Sam Sample", address="sam@example.com", subject="Urgent Requirement", body=body))
        self.assertEqual((shape, reason, verdict, c["company"], c["needs_review"]), (C.DIRECT_HUMAN, "STAFFING_OUTREACH", "CANDIDATE", "Northwind Staffing", False))
        one = classify(mail(sender_name="Sam Sample", address="sam@example.com", body="Hi, please send your updated resume. Thanks, Sam"))
        self.assertEqual(one[0], C.OTHER)
        nameless = classify(mail(sender_name="Staffing Desk", address="desk@example.com", body=body))
        self.assertEqual(nameless[0], C.OTHER)


class MinutePrecisionTests(unittest.TestCase):
    def test_last_contact_is_kept_to_the_minute_so_a_replay_finds_nothing_to_update(self):
        notion = FakeRecruiters()
        msg = signed(received="2026-10-07T15:08:23+00:00")
        run([msg], notion, live=True)
        notion.pages[0]["properties"]["Last Contact"]["date"]["start"] = "2026-10-07T15:08:00.000+00:00"      # Notion keeps minutes only
        again = run([msg], notion, live=True)
        self.assertEqual((again["existing"], again["updated"], again["failed"], len(notion.writes)), (1, 0, 0, 1))
        later = run([signed(received="2026-10-07T15:09:59+00:00")], notion, live=True)
        self.assertEqual((later["updated"], later["failed"]), (1, 0))


class WeekTests(unittest.TestCase):
    def test_week_ending_is_the_sunday_of_the_monday_to_sunday_week_in_chicago(self):
        self.assertEqual(identity.week_ending("2026-10-05T14:00:00+00:00"), "2026-10-11")       # Monday morning Chicago
        self.assertEqual(identity.week_ending("2026-10-11T23:30:00-05:00"), "2026-10-11")       # Sunday night
        self.assertEqual(identity.week_ending("2026-10-12T03:00:00+00:00"), "2026-10-11")       # still Sunday evening in Chicago
        self.assertEqual(identity.week_ending("2026-10-12T06:00:00+00:00"), "2026-10-18")       # Monday 1 AM Chicago


def source(records):
    return {"gmail": ReadOnlyGmail(records)}


def run(records, notion, live=False, outlook=None):
    return stage.run(0, live, environ={}, gmail=ReadOnlyGmail(records), outlook_accounts=outlook or [], notion=notion, now=NOW)


def signed(**kw):
    return mail(body=SIGNED, **kw)


class StageTests(unittest.TestCase):
    def test_dry_run_writes_nothing_and_counts_only(self):
        notion = FakeRecruiters()
        counts = run([signed()], notion)
        self.assertEqual((counts["would_create"], counts["created"], notion.writes), (1, 0, []))
        self.assertEqual((counts["direct_human"], counts["needs_review"]), (1, 0))
        self.assertNotIn("Pat", json.dumps(counts))

    def test_live_creates_one_row_with_read_back_and_replay_does_not_duplicate(self):
        notion = FakeRecruiters()
        counts = run([signed()], notion, live=True)
        self.assertEqual((counts["created"], counts["failed"], len(notion.pages)), (1, 0, 1))
        row = store.read(notion.pages[0])
        self.assertEqual((row["person"], row["company"], row["week_ending"], row["done"], row["active"], row["needs_review"], row["what"], row["medium"]),
                         ("Pat Example", "Acme Staffing", "2026-10-11", False, True, False, "", "Email"))
        again = run([signed()], notion, live=True)
        self.assertEqual((again["created"], again["existing"], len(notion.pages)), (0, 1, 1))

    def test_a_later_message_updates_last_contact_not_a_duplicate(self):
        notion = FakeRecruiters([page("Pat Example", "Acme Staffing", role="Data Engineer", last="2026-10-06T15:00:00+00:00", what="wants a call", review=False)])
        counts = run([signed(received="2026-10-08T09:00:00+00:00")], notion, live=True)
        self.assertEqual((counts["updated"], counts["created"], len(notion.pages)), (1, 0, 1))
        row = store.read(notion.pages[0])
        self.assertEqual((row["last_contact"], row["what"], row["done"]), ("2026-10-08T09:00:00+00:00", "wants a call", False))

    def test_an_older_message_changes_nothing(self):
        notion = FakeRecruiters([page("Pat Example", "Acme Staffing", role="Data Engineer", last="2026-10-09T15:00:00+00:00")])
        counts = run([signed(received="2026-10-07T09:00:00+00:00")], notion, live=True)
        self.assertEqual((counts["existing"], notion.writes), (1, []))

    def test_a_later_week_creates_a_new_row(self):
        notion = FakeRecruiters([page("Pat Example", "Acme Staffing", role="Data Engineer", week="2026-10-04", last="2026-09-30T15:00:00+00:00")])
        counts = run([signed()], notion, live=True)
        self.assertEqual((counts["created"], len(notion.pages)), (1, 2))

    def test_done_is_preserved_and_never_reopened(self):
        notion = FakeRecruiters([page("Pat Example", "Acme Staffing", role="Data Engineer", done=True, active=False, what="Jim called back")])
        counts = run([signed(received="2026-10-08T09:00:00+00:00")], notion, live=True)
        self.assertEqual((counts["done_kept"], counts["updated"], counts["created"], notion.writes), (1, 0, 0, []))
        row = store.read(notion.pages[0])
        self.assertEqual((row["done"], row["active"], row["what"]), (True, False, "Jim called back"))

    def test_what_they_want_is_never_written_and_a_blank_client_fills_but_a_human_value_stays(self):
        body = SIGNED.replace("for a client.", "on behalf of Initech, who is hiring.")
        notion = FakeRecruiters([page("Pat Example", "Acme Staffing", role="Data Engineer", client="Human Client", what="x")])
        counts = run([mail(body=body, received="2026-10-08T09:00:00+00:00")], notion, live=True)
        row = store.read(notion.pages[0])
        self.assertEqual((counts["updated"], row["client"], row["what"]), (1, "Human Client", "x"))
        self.assertTrue(all("What they want" not in props for _, props in notion.writes))

    def test_duplicate_notion_rows_fail_closed(self):
        notion = FakeRecruiters([page("Pat Example", "Acme Staffing", role="Data Engineer", pid="a"), page("pat example", "acme staffing", role="data engineer", pid="b")])
        counts = run([signed()], notion, live=True)
        self.assertEqual((counts["ambiguous"], counts["created"], counts["updated"], notion.writes), (1, 0, 0, []))

    def test_a_create_without_authoritative_read_back_is_a_failure(self):
        notion = FakeRecruiters(drop_writes=True)
        counts = run([signed()], notion, live=True)
        self.assertEqual((counts["created"], counts["failed"]), (0, 1))

    def test_a_notion_query_failure_creates_nothing(self):
        notion = FakeRecruiters(fail_query=True)
        with self.assertRaises(store.RecruitersError):
            run([signed()], notion, live=True)
        self.assertEqual(notion.writes, [])

    def test_unknown_schema_means_no_mutation(self):
        notion = FakeRecruiters()
        notion.call = lambda method, path, body=None: {"properties": {"Recruiter / Person": {"type": "title"}}}
        with self.assertRaises(store.RecruitersError):
            run([signed()], notion, live=True)
        self.assertEqual(notion.writes, [])

    def test_missing_secrets_still_reports_a_dry_run_and_refuses_live(self):
        counts = stage.run(0, False, environ={}, gmail=ReadOnlyGmail([signed()]), outlook_accounts=[], now=NOW)
        self.assertEqual((counts["notion"], counts["direct_human"], counts["would_create"]), ("unavailable", 1, 0))
        with self.assertRaises(store.RecruitersError):
            stage.run(0, True, environ={}, gmail=ReadOnlyGmail([signed()]), outlook_accounts=[], now=NOW)

    def test_an_incomplete_source_is_degraded_and_nothing_is_written(self):
        notion = FakeRecruiters()
        broken = ReadOnlyGmail([signed()])
        broken.list_ids_complete = lambda *a, **k: (_ for _ in ()).throw(platform_mail.GmailError("GMAIL_LISTING_INCOMPLETE"))
        with self.assertRaises(store.RecruitersError):
            stage.run(0, True, environ={}, gmail=broken, outlook_accounts=[], notion=notion, now=NOW)
        self.assertEqual(notion.writes, [])

    def test_gmail_and_outlook_are_both_read_without_mutation_and_spam_is_included(self):
        outlook = ReadOnlyOutlook(inbox=[{"id": "o1", "receivedDateTime": "2026-10-07T15:00:00Z", "from": {"emailAddress": {"name": "Lee Outlook", "address": "lee@example.com"}},
                                          "subject": "Opportunity", "body": {"content": SIGNED.replace("Pat Example", "Lee Outlook")}, "webLink": "https://mail.example.com/o/1", "internetMessageHeaders": []}],
                                  junk=[])
        gmail = ReadOnlyGmail([signed(folder="JUNK")])
        notion = FakeRecruiters()
        counts = stage.run(0, True, environ={}, gmail=gmail, outlook_accounts=[("work", outlook)], notion=notion, now=NOW)
        self.assertEqual((counts["messages"], counts["created"], counts["outlook_accounts"]), (2, 2, 1))
        self.assertTrue(gmail.calls[0][1])                                                   # spam listed
        self.assertEqual(outlook.calls, ["inbox", "junkemail"])

    def test_needs_review_rows_are_flagged_on_the_existing_property(self):
        notion = FakeRecruiters()
        run([mail(body="Hi,\n\nI'm a recruiter. Interested?\n\nPat\n")], notion, live=True)
        self.assertEqual([(store.read(p)["person"], store.read(p)["company"], store.read(p)["needs_review"]) for p in notion.pages], [("Pat Example", "", True)])   # no company in the source: review, never a guess


class ContractTests(unittest.TestCase):
    ROOT = pathlib.Path(__file__).resolve().parents[2]

    def code(self):
        return {p.name: p.read_text() for p in (self.ROOT / "lifeos" / "recruiters").glob("*.py")} | {"mail.py": (self.ROOT / "lifeos/platform/mail.py").read_text()}

    def test_recruiters_never_calls_a_mail_mutation_path(self):
        banned = (".trash(", "relabel", "apply_amazon", "label_id(", ".move(", "folder_id(", "batchModify", "/modify", "/send", "sendMail", "/forward", "permanentDelete", "DELETE", "markRead", "isRead")
        for name, text in self.code().items():
            for token in banned:
                self.assertNotIn(token, re.sub(r'""".*?"""', "", text, flags=re.S), f"{name}: {token}")

    def test_no_hostinger_table_hiring_pipeline_or_scheduler(self):
        for name, text in self.code().items():
            if name == "mail.py":
                continue                                                                     # the shared platform record may read the Outlook token table; it creates nothing
            self.assertNotRegex(text, r"CREATE TABLE|lifeos\.hiring|lifeos\.jobs|snapshot_store|platform import db\b|TinyFish|tinyfish", name)
        workflows = self.ROOT / ".github" / "workflows"
        self.assertNotIn("recruiters", (workflows / "hourly.yml").read_text())
        self.assertNotIn("schedule", (workflows / "recruiters.yml").read_text().split("jobs:")[0].replace("# ", "#"))

    def test_the_workflow_is_manual_dry_by_default_and_the_secrets_are_the_recruiters_pair(self):
        text = (self.ROOT / ".github/workflows/recruiters.yml").read_text()
        self.assertIn("NOTION_RECRUITERS_TOKEN", text)
        self.assertIn("NOTION_RECRUITERS_DATA_SOURCE_ID", text)
        self.assertRegex(text, r"default: false")
        self.assertNotIn("cron:", text)

    def test_no_message_content_is_printed_or_raised(self):
        for name, text in self.code().items():
            code = re.sub(r'""".*?"""', "", text, flags=re.S)
            self.assertNotRegex(code, r"print\([^)]*(?:body|subject|sender|address|name)", name)


if __name__ == "__main__":
    unittest.main()
