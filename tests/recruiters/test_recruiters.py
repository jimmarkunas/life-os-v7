import contextlib
import io
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
                                                    body="Lee Relay sent you a message about a role.\n\nHi, I'm a recruiter at Examplejobs Partners. Are you open to a call?"))
        self.assertEqual((shape, verdict, c["person"], c["company"], c["client"], c["needs_review"]), (C.HUMAN_NAMED_RELAY, "CANDIDATE", "Lee Relay", "", "", True))

    def test_a_board_relay_other_than_linkedin_keeps_qualifying_on_a_name(self):
        """D193 narrows only LinkedIn, where any member can message anyone; the recruiter boards keep their fixed-pattern rule."""
        shape, reason, (verdict, c) = classify(mail(sender_name="Lee Relay via Examplejobs", address="messages-noreply@connect.dice.com", subject="New message from Lee Relay",
                                                    body="Lee Relay sent you a message about a role."))
        self.assertEqual((shape, reason, verdict, c["person"], c["needs_review"]), (C.HUMAN_NAMED_RELAY, "RELAY_NAMED", "CANDIDATE", "Lee Relay", True))

    def test_an_unreadable_relay_never_invents_a_person(self):
        msg = mail(sender_name="Careers Team", address="m@linkedin.com", subject="You have a new message", body="Someone sent you a message.")
        self.assertEqual(classify(msg)[2], ("UNRESOLVED", "NAME_UNREADABLE"))
        single = mail(sender_name="Alex via Board", address="m@linkedin.com", subject="New message from Alex", body="Alex sent you a message.\nI'm a sourcer at Example Partners.")
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
    LINKEDIN = ("Message replied: Role for you\nInMail: You have a new message\n\n        Lee Relay\n\n      Reply\n      https://example.com/thread\n\n"
                "Hi, I'm a recruiter at Examplejobs Partners. Are you free Monday?\n")

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

    def test_a_blank_what_they_want_is_filled_from_a_clear_request_and_a_human_value_stays(self):
        body = SIGNED.replace("for a client.", "on behalf of Initech, who is hiring.") + "\nWould you be open to a quick call this week?\n"
        notion = FakeRecruiters([page("Pat Example", "Acme Staffing", role="Data Engineer", client="Human Client", what="x")])
        counts = run([mail(body=body, received="2026-10-08T09:00:00+00:00")], notion, live=True)
        row = store.read(notion.pages[0])
        self.assertEqual((counts["updated"], row["client"], row["what"]), (1, "Human Client", "x"))          # a human's value is never replaced
        self.assertTrue(all("What they want" not in props for _, props in notion.writes))
        blank = FakeRecruiters([page("Pat Example", "Acme Staffing", role="Data Engineer", what="")])
        run([mail(body=body, received="2026-10-08T09:00:00+00:00")], blank, live=True)
        self.assertEqual(store.read(blank.pages[0])["what"], "Schedule a call about Data Engineer opportunity.")

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
        with self.assertRaises(store.RecruitersError), contextlib.redirect_stdout(io.StringIO()):          # the degraded run prints its counts line; keep it out of the test log
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


OPPORTUNITY = (
    "Hi Alex,\nMy name is Riley, and I'm with (NW), a technology consulting and advisory organization that partners with leading companies.\n"
    "We are currently looking for Technical Project Manager, and I believe your experience aligns well.\nThe Opportunity\n"
    "Role: 401044 - Technical Project Manager\nLocation: Onsite - Springfield\nEmployment Type: W2\n"
    "I would welcome the opportunity to share more about the position. Would you be open to a 15–20-minute conversation today or tomorrow?\n"
    "Regards,\nRiley Sample | Technical Recruiter\nTECHNOLOGY ADVISORY & CONSULTING | AN INC. 500 COMPANY\nhttp://example.com\n")
WANT = "Schedule a 15–20-minute conversation about Technical Project Manager opportunity #401044."


def opportunity(**kw):
    return mail(sender_name="Riley Sample", address="riley.sample@example.com", subject="Career Opportunity with NW | 401044 - Technical Project Manager",
                body=kw.pop("body", OPPORTUNITY), **kw)


class RecruiterFieldTests(unittest.TestCase):
    """D193: company, role and request come only from fixed patterns in the sender's own words."""

    def fields(self, body, **kw):
        return classify(mail(body=body, **kw))[2][1]

    def test_a_parenthesized_first_person_company_a_role_label_and_a_clear_request_fill_the_row(self):
        verdict, c = classify(opportunity())[2]
        self.assertEqual((verdict, c["person"], c["company"], c["role"], c["client"], c["what"], c["needs_review"]),
                         ("CANDIDATE", "Riley Sample", "NW", "Technical Project Manager", "", WANT, False))
        self.assertNotRegex(c["what"], r"(?i)today|tomorrow|this week")                      # an evergreen summary keeps no relative day

    def test_company_statements_are_first_person_only_and_never_a_generic_phrase(self):
        sig = "\nRiley Sample | Technical Recruiter\n"
        for text, company in (("I'm with Acme Staffing, a firm in town.", "Acme Staffing"), ("I work with Globex Corp. on hiring.", "Globex Corp."),
                              ("I am with (Initech) and we are hiring.", "Initech"), ("I'm with you on that.", ""), ("I work with Fortune 500 companies.", ""),
                              ("I work with a variety of clients.", ""), ("Our team is with Acme and hiring.", "")):
            self.assertEqual(self.fields(text + sig)["company"], company, text)

    def test_role_labels_with_and_without_a_requisition(self):
        sig = "\nRiley Sample | Technical Recruiter\n"
        for text, role in (("Role: Data Engineer", "Data Engineer"), ("Position: REQ-12345 - Staff Engineer", "Staff Engineer"), ("Job Title: 7788 | Senior Analyst", "Senior Analyst"),
                           ("Title: Technical Recruiter", ""), ("Role: " + "very long text " * 12, ""), ("Role: ", ""), ("Position: Remote", "")):
            self.assertEqual(self.fields("I'm with Acme.\n" + text + sig)["role"], role, text)
        self.assertEqual(self.fields(SIGNED.replace("a Data Engineer role", "a Platform Lead role") + "Role: Other Title\n")["role"], "Platform Lead")   # an earlier key is never changed

    def test_what_they_want_uses_only_fixed_request_patterns(self):
        sig = "\nRiley Sample | Technical Recruiter\nAcme Staffing\n"
        cases = (("Please send your updated resume and your expected rate.", "Send an updated resume and provide expected rate."),
                 ("Let me know your availability this week.", "Share availability."),
                 ("Can we set up a 20 minute call?", "Schedule a 20-minute call."),
                 ("Please send your updated resume. No need to share your rate.", "Send an updated resume."),
                 ("There is no need to send your resume.", ""), ("I have a role you may like.", ""), ("Hope you are well.", ""))
        for text, want in cases:
            self.assertEqual(self.fields(text + sig)["what"], want, text)

    def test_conflicting_requests_are_blank_and_marked_for_review(self):
        c = self.fields("Can we set up a 15 minute call? Would you be open to a 45 minute meeting?\nRiley Sample | Technical Recruiter\nAcme Staffing\n")
        self.assertEqual((c["what"], c["needs_review"]), ("", True))

    def test_a_relay_never_gets_a_request(self):
        c = classify(mail(sender_name="Lee Relay via Examplejobs", address="messages-noreply@connect.dice.com", subject="New message from Lee Relay",
                          body="Lee Relay sent you a message. Would you be open to a call?"))[2][1]
        self.assertEqual(c["what"], "")


DIGEST = ("You have 1 new message\n\n.....................................\n\n{who}\n(https://linkedin.example/in/someone)\n\n"
          "View message: https://linkedin.example/comm/messaging/thread/abc\n\n.....................................\n\n----------------------------------------\n\n"
          "This email was intended for Jim Example (Technical Recruiter | Hiring Systems | Contract & FTE)\nLearn why we included this: https://linkedin.example/help\n"
          "You are receiving Messages digest emails.\nUnsubscribe: https://linkedin.example/unsub\n")


def notification(who="Morgan Example (I build developer communities and events)", **kw):
    return mail(sender_name="Morgan Example via LinkedIn", address="messaging-digest-noreply@linkedin.com", subject="Morgan just messaged you",
                body=DIGEST.format(who=who), noreply=True, url="https://mail.example.com/m/digest", **kw)


class LinkedInNotificationTests(unittest.TestCase):
    def test_a_named_notification_without_recruiting_evidence_is_unresolved_not_a_recruiter(self):
        shape, reason, (verdict, detail) = classify(notification())
        self.assertEqual((shape, reason, verdict, detail), (C.HUMAN_NAMED_RELAY, "RELAY_RECRUITING_UNVERIFIED", "UNRESOLVED", "RELAY_RECRUITING_UNVERIFIED"))

    def test_the_footer_describes_jim_and_is_never_the_senders_evidence(self):
        self.assertIn("Technical Recruiter", DIGEST)                                           # Jim's own headline in the footer contains a recruiter title
        self.assertEqual(classify(notification())[2][0], "UNRESOLVED")

    def test_a_recruiting_headline_or_first_person_statement_is_evidence(self):
        for who, proof in (("Morgan Example (Senior Technical Recruiter at Example Partners)", "HEADLINE_TITLE"), ("Morgan Example (Talent Acquisition Partner | Globex)", "HEADLINE_TITLE")):
            msg = notification(who=who)
            shape, reason, evidence = C.classify(msg)
            self.assertEqual((shape, reason, evidence["proof"]), (C.HUMAN_NAMED_RELAY, "RELAY_NAMED", proof))
            verdict, c = qualify.qualify(msg, shape, reason, evidence)
            self.assertEqual((verdict, c["person"], c["company"], c["needs_review"], c["what"]), ("CANDIDATE", "Morgan Example", "", True, ""))
        shown = notification().copy()
        shown["body"] = DIGEST.format(who="Morgan Example (events)").replace("View message:", "Hi Jim, I'm a recruiter at Example Partners.\nView message:")
        self.assertEqual(C.classify(shown)[2]["proof"], "FIRST_PERSON")

    def test_a_non_message_linkedin_mail_is_still_excluded(self):
        alert = mail(sender_name="LinkedIn Job Alerts", address="jobalerts-noreply@linkedin.com", subject="12 new jobs for you", body="Engineering Manager at Globex and 11 more")
        self.assertEqual(classify(alert)[0], C.OTHER)


class EnrichmentTests(unittest.TestCase):
    """D193: an existing row made from THIS message that only lacks company / role / request is enriched under the same page; another message, a human's value and a Done row are never touched."""

    SRC = "https://mail.google.com/mail/u/0/#all/t1"                   # what the Gmail reader builds from the (fake) thread id of every message in these tests
    OTHER = "https://mail.google.com/mail/u/0/#all/t-earlier"

    def test_a_blank_row_from_the_same_message_is_enriched_in_place_and_a_replay_changes_nothing(self):
        notion = FakeRecruiters([page("Riley Sample", pid="page-riley", last="2026-10-07T15:00:00+00:00", review=True, url=self.SRC)])
        counts = run([opportunity(received="2026-10-07T15:00:00+00:00")], notion, live=True)
        self.assertEqual((counts["updated"], counts["created"], counts["failed"], len(notion.pages)), (1, 0, 0, 1))
        row = store.read(notion.pages[0])
        self.assertEqual((row["page_id"], row["company"], row["role"], row["what"], row["done"], row["active"], row["needs_review"], row["source_url"]),
                         ("page-riley", "NW", "Technical Project Manager", WANT, False, True, True, self.SRC))      # Needs review is the human's to clear
        self.assertEqual(sorted(notion.writes[0][1]), ["Recruiter Company", "Role", "What they want"])
        again = run([opportunity(received="2026-10-07T15:00:00+00:00")], notion, live=True)
        self.assertEqual((again["updated"], again["created"], again["existing"], len(notion.writes), len(notion.pages)), (0, 0, 1, 1, 1))

    def test_another_message_from_the_same_recruiter_and_week_is_never_merged_into_the_row(self):
        earlier = page("Riley Sample", pid="earlier", url=self.OTHER, last="2026-10-06T10:00:00+00:00", review=True)
        notion = FakeRecruiters([earlier])
        counts = run([opportunity(received="2026-10-07T15:00:00+00:00")], notion, live=True)
        self.assertEqual((counts["created"], counts["updated"], counts["existing"], len(notion.pages)), (1, 0, 0, 2))
        original = store.read(notion.pages[0])
        self.assertEqual((original["page_id"], original["company"], original["role"], original["what"], original["source_url"], original["last_contact"]),
                         ("earlier", "", "", "", self.OTHER, "2026-10-06T10:00:00+00:00"))                       # untouched, original link kept
        self.assertEqual([kind for kind, _ in notion.writes], ["create"])
        created = store.read(notion.pages[1])
        self.assertEqual((created["company"], created["role"], created["source_url"]), ("NW", "Technical Project Manager", self.SRC))

    def test_a_missing_source_link_on_either_side_never_enriches(self):
        candidate = {"week_ending": "2026-10-11", "person": "Riley Sample", "company": "NW", "role": "Technical Project Manager", "client": "", "medium": "Email", "what": WANT,
                     "last_contact": "2026-10-07T15:00:00+00:00", "source_url": self.SRC, "needs_review": False}
        for row_url, cand_url in ((None, self.SRC), (self.SRC, ""), (None, "")):
            rows = [store.read(page("Riley Sample", pid="p", url=row_url))]
            self.assertEqual(store.plan(rows, {**candidate, "source_url": cand_url})[0], "create", (row_url, cand_url))
        notion = FakeRecruiters([page("Riley Sample", pid="p", url=None)])
        counts = run([opportunity()], notion, live=True)
        self.assertEqual((counts["created"], counts["updated"], len(notion.pages), store.read(notion.pages[0])["company"]), (1, 0, 2, ""))

    def test_a_nonblank_what_they_want_company_or_role_is_never_replaced(self):
        notion = FakeRecruiters([page("Riley Sample", company="NW", role="", what="Jim: call back Friday", pid="p", url=self.SRC)])
        run([opportunity()], notion, live=True)
        row = store.read(notion.pages[0])
        self.assertEqual((row["company"], row["role"], row["what"]), ("NW", "Technical Project Manager", "Jim: call back Friday"))
        self.assertEqual(sorted(notion.writes[0][1]), ["Role"])
        different = FakeRecruiters([page("Riley Sample", company="Other Corp", role="", pid="q", url=self.SRC)])
        counts = run([opportunity()], different, live=True)
        self.assertEqual((counts["created"], counts["updated"], len(different.pages)), (1, 0, 2))      # another company is another key, not an enrichment
        self.assertEqual(store.read(different.pages[0])["company"], "Other Corp")

    def test_an_existing_row_under_the_corrected_key_wins_over_a_blank_row(self):
        notion = FakeRecruiters([page("Riley Sample", pid="blank", url=self.SRC),
                                 page("Riley Sample", "NW", "Technical Project Manager", what=WANT, pid="full", last="2026-10-12T15:00:00+00:00", url=self.SRC)])
        counts = run([opportunity(received="2026-10-07T15:00:00+00:00")], notion, live=True)
        self.assertEqual((counts["existing"], counts["updated"], counts["created"], notion.writes), (1, 0, 0, []))

    def test_two_competing_matches_for_the_same_message_are_ambiguous_and_unchanged(self):
        notion = FakeRecruiters([page("Riley Sample", pid="a", url=self.SRC), page("Riley Sample", role="Other", pid="b", url=self.SRC),
                                 page("riley sample", pid="c", url=self.SRC)])
        before = [store.read(p) for p in notion.pages]
        counts = run([opportunity()], notion, live=True)
        self.assertEqual((counts["ambiguous"], counts["created"], counts["updated"], notion.writes), (1, 0, 0, []))
        self.assertEqual([store.read(p) for p in notion.pages], before)
        mixed = FakeRecruiters([page("Riley Sample", pid="a", url=self.SRC), page("Riley Sample", pid="b", url=self.OTHER)])
        counts = run([opportunity()], mixed, live=True)                                                        # only one of the two is from this message: it is the one enriched
        self.assertEqual((counts["updated"], counts["ambiguous"], store.read(mixed.pages[0])["company"], store.read(mixed.pages[1])["company"]), (1, 0, "NW", ""))

    def test_a_done_row_from_the_same_message_is_kept_and_a_done_row_from_another_is_not_touched(self):
        notion = FakeRecruiters([page("Riley Sample", done=True, active=False, pid="done", url=self.SRC)])
        counts = run([opportunity()], notion, live=True)
        self.assertEqual((counts["done_kept"], counts["created"], counts["updated"], notion.writes, len(notion.pages)), (1, 0, 0, [], 1))
        other = FakeRecruiters([page("Riley Sample", done=True, active=False, what="Jim called", pid="done2", url=self.OTHER)])
        counts = run([opportunity()], other, live=True)
        row = store.read(other.pages[0])
        self.assertEqual((counts["done_kept"], counts["created"], len(other.pages), row["done"], row["what"], row["company"], row["source_url"]), (0, 1, 2, True, "Jim called", "", self.OTHER))

    def test_a_new_row_carries_the_request_and_is_read_back(self):
        notion = FakeRecruiters()
        counts = run([opportunity()], notion, live=True)
        row = store.read(notion.pages[0])
        self.assertEqual((counts["created"], counts["failed"], row["company"], row["role"], row["what"], row["needs_review"]), (1, 0, "NW", "Technical Project Manager", WANT, False))
        self.assertEqual(run([opportunity()], notion, live=True)["existing"], 1)

    def test_a_write_that_does_not_read_back_is_a_failure_and_changes_no_state(self):
        notion = FakeRecruiters([page("Riley Sample", pid="p", url=self.SRC)], drop_writes=True)
        counts = run([opportunity()], notion, live=True)
        self.assertEqual((counts["updated"], counts["failed"]), (0, 1))


class QuarantineTests(unittest.TestCase):
    URL = "https://mail.google.com/mail/u/0/#all/t1"                    # what the Gmail reader builds from the (fake) thread id

    def row(self, **kw):
        base = dict(person="Morgan Example", review=True, url=self.URL, last="2026-10-07T15:00:00+00:00")
        return page(**{**base, **kw})

    def go(self, pages, sources=(URL,), live=True):
        notion = FakeRecruiters(pages)
        counts = stage.run(0, live, environ={}, gmail=ReadOnlyGmail([notification()]), outlook_accounts=[], notion=notion, now=NOW, quarantine_sources=sources)
        return counts, notion

    def test_an_unedited_automation_row_from_that_message_is_set_inactive_and_nothing_else(self):
        counts, notion = self.go([self.row(pid="mark")])
        row = store.read(notion.pages[0])
        self.assertEqual((counts["quarantined"], counts["unresolved"], counts["created"], counts["failed"]), (1, 1, 0, 0))
        self.assertEqual((row["active"], row["done"], row["needs_review"], row["person"], row["source_url"], row["last_contact"], row["company"], row["what"]),
                         (False, False, True, "Morgan Example", self.URL, "2026-10-07T15:00:00+00:00", "", ""))
        self.assertEqual(notion.writes, [("update", {"Active": {"checkbox": False}})])
        again = self.go(notion.pages)[0]
        self.assertEqual((again["quarantined"], again["quarantine_skipped"]), (0, 1))                  # already inactive: nothing to do

    def test_nothing_is_quarantined_unless_the_exact_message_link_is_passed_in(self):
        counts, notion = self.go([self.row(pid="mark")], sources=())
        self.assertEqual((counts["quarantined"], counts["would_quarantine"], notion.writes, store.read(notion.pages[0])["active"]), (0, 0, [], True))
        counts, notion = self.go([self.row(pid="mark")], sources=("https://mail.example.com/m/other",))
        self.assertEqual((counts["quarantined"], notion.writes), (0, []))

    def test_a_dry_run_counts_and_writes_nothing(self):
        counts, notion = self.go([self.row(pid="mark")], live=False)
        self.assertEqual((counts["would_quarantine"], counts["quarantined"], notion.writes), (1, 0, []))

    def test_a_row_a_human_has_touched_is_protected(self):
        for kw in ({"edited_by": "human-1"}, {"what": "Jim: ignore"}, {"role": "Chosen"}, {"company": "Acme"}, {"client": "X"}, {"done": True, "active": False},
                   {"review": False}, {"created_by": "human-1", "edited_by": "human-2"}, {"url": "https://mail.example.com/m/else"}):
            counts, notion = self.go([self.row(pid="mark", **kw)])
            self.assertEqual((counts["quarantined"], notion.writes), (0, []), kw)

    def test_two_rows_for_the_message_are_ambiguous_and_untouched(self):
        counts, notion = self.go([self.row(pid="a"), self.row(pid="b", role="")])
        self.assertEqual((counts["quarantined"], counts["ambiguous"], notion.writes), (0, 1, []))

    def test_a_quarantine_that_does_not_read_back_is_a_failure(self):
        notion = FakeRecruiters([self.row(pid="mark")], drop_writes=True)
        counts = stage.run(0, True, environ={}, gmail=ReadOnlyGmail([notification()]), outlook_accounts=[], notion=notion, now=NOW, quarantine_sources=(self.URL,))
        self.assertEqual((counts["quarantined"], counts["failed"]), (0, 1))

    def test_an_unverified_notification_creates_nothing_and_is_counted_with_its_code(self):
        notion = FakeRecruiters()
        counts = run([notification()], notion, live=True)
        self.assertEqual((counts["unresolved"], counts["human_relay"], counts["created"], notion.writes, notion.pages), (1, 0, 0, [], []))
        self.assertEqual(counts["unresolved_why"], {"RELAY_RECRUITING_UNVERIFIED": 1})
        self.assertNotIn("Morgan", json.dumps(counts))


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
