"""LIFE-114 (Jim, Oct 29 2026): Materials Sent on the opportunity page. Jim records an application form / portal answers (an optional link) or a Google Drive file
(a project plan, an article) on Manage, through the signed review and confirm, stored on the opportunity's own record; his own emails on the job's request thread are
read from Gmail (read only, metadata only). Cover letters are out. Every name is invented; nothing is live."""
import unittest
from dataclasses import replace
from unittest import mock

from lifeos.hiring import decision as D, models as M
from lifeos.interview import create, notion, template, view
from lifeos.interview.models import ParentQuery, RoundQuery
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.sources import interview_evidence, manage_app
from tests.hiring import test_decision as TD
from tests.hiring.test_hand_set import M2, SET_AT, OP, forms, hand_text, ledger_ready, notes_page
from tests.hiring.test_mark_complete import lines, opp, texts
from tests.hiring.test_notes_decision import COMPANY, ROLE

FORM = M.sent_line(M.FORM, "Workday application answers", "https://example.test/apply/1", SET_AT, OP)
FILE_ID = "1AbCdEfGhIjKlMnOp"
DRIVE_URL = f"https://docs.google.com/document/d/{FILE_ID}/edit"


class Drive:
    def __init__(self, trashed=False, name="Launch plan for ExampleCo"):
        self.meta = {"id": FILE_ID, "name": name, "trashed": trashed, "webViewLink": f"https://docs.google.com/document/d/{FILE_ID}/edit?usp=drivesdk"}
        self.gets = 0

    def get(self, fid):
        assert fid == FILE_ID
        self.gets += 1
        return dict(self.meta)


class Manage(unittest.TestCase):
    def test_a_form_is_reviewed_then_recorded_as_one_line_read_back_and_listed_on_manage(self):
        h, fold = notes_page()
        before = lines(h, fold)
        status, body, _ = h.hand_review("sent", sk="form", sx="Workday application answers", su="https://example.test/apply/1")
        self.assertEqual(status, 200, body)
        self.assertIn("Sent: Application form / portal answers · Workday application answers", body)
        self.assertEqual(h.writes(), (0, 0))                                                            # review writes nothing
        status, body, _ = h.hand("sent", sk="form", sx="Workday application answers", su="https://example.test/apply/1")
        self.assertEqual(status, 200, body)
        after = lines(h, fold)
        self.assertEqual(after[:-1], before)
        got = M.sent_of(after)
        self.assertEqual([(s["kind"], s["text"], s["url"]) for s in got], [(M.FORM, "Workday application answers", "https://example.test/apply/1")])
        self.assertIn("Application form / portal answers: Workday application answers · recorded", h.hub(h.world.ws.target)[1])
        self.assertEqual(h.after, [])                                                                   # sent material never redraws the Hiring Pipeline

    def test_a_form_needs_what_it_was_and_a_link_is_optional_but_must_be_https(self):
        h, fold = notes_page()
        self.assertEqual(h.hand_review("sent", sk="form", sx="")[0], 400)
        self.assertEqual(h.hand_review("sent", sk="form", sx="Portal answers", su="javascript:alert(1)")[0], 400)
        self.assertEqual(h.hand_review("sent", sk="letter", sx="Cover letter")[0], 400)              # cover letters are out of scope: only form | file
        status, body, _ = h.hand("sent", sk="form", sx="Portal answers")
        self.assertEqual(status, 200, body)
        self.assertEqual(M.sent_of(lines(h, fold))[0]["url"], None)

    def test_a_drive_file_must_exist_and_not_be_trashed_and_is_named_from_drive(self):
        h, fold = notes_page()
        self.assertEqual(h.hand_review("sent", sk="file", su="https://example.test/not-drive")[0], 400)
        with mock.patch.object(manage_app, "_drive", lambda secrets: None):
            self.assertEqual(h.hand_review("sent", sk="file", su=DRIVE_URL)[0], 409)                 # Drive not connected on the Close service
        with mock.patch.object(manage_app, "_drive", lambda secrets: Drive(trashed=True)):
            status, body, _ = h.hand_review("sent", sk="file", su=DRIVE_URL)
            self.assertEqual(status, 409)
            self.assertIn("in the trash", body)
        drive = Drive()
        with mock.patch.object(manage_app, "_drive", lambda secrets: drive):
            status, body, _ = h.hand("sent", sk="file", su=DRIVE_URL)
        self.assertEqual(status, 200, body)
        self.assertEqual(drive.gets, 2)                                                                 # read at review and again at confirm, never changed
        got = M.sent_of(lines(h, fold))
        self.assertEqual([(s["kind"], s["text"], s["url"]) for s in got], [(M.FILE, "Launch plan for ExampleCo", drive.meta["webViewLink"])])

    def test_a_file_trashed_between_review_and_confirm_writes_nothing(self):
        h, fold = notes_page()
        drive = Drive()
        with mock.patch.object(manage_app, "_drive", lambda secrets: drive):
            review = h.fields(h.hand_review("sent", sk="file", su=DRIVE_URL)[1])
            drive.meta["trashed"] = True
            status, _, _ = h.call("POST", "/manage", form={**review, "step": "confirm"})
        self.assertEqual(status, 409)
        self.assertEqual(M.sent_of(lines(h, fold)), [])

    def test_on_the_ledger_sent_lines_are_kept_beside_the_latest_hold_and_next_action(self):
        h = M2([(TD.TARGET, COMPANY, ROLE)])
        ledger_ready(h.world.source)
        for act, values in (("hold", {"v": "paused"}), ("sent", {"sk": "form", "sx": "Portal answers"}), ("sent", {"sk": "form", "sx": "Assessment answers"}),
                            ("next", {"nx": "Call Pat"}), ("hold", {"v": "waiting"})):
            status, body, _ = h.hand(act, **values)
            self.assertEqual(status, 200, body)
        got = hand_text(h.world.source).splitlines()
        self.assertEqual([s["text"] for s in M.sent_of(got)], ["Portal answers", "Assessment answers"])
        self.assertEqual((M.hold_of(got)["state"], M.next_of(got)["text"], len(got)), (M.WAITING, "Call Pat", 4))
        self.assertEqual(sorted(f.get("act") for f in forms(h.hub(h.world.ws.target)[1]) if f.get("act")), ["drive", "hold", "next", "sent", "sent", "unsent", "unsent"])   # LIFE-172: one Remove per item; LIFE-173: Drive search; LIFE-174: a Drive-link form and a form form


class Remove(unittest.TestCase):
    """LIFE-172 (Jim, Oct 30 2026): a sent item is removed on Manage through its own review and confirm. The removal is one more append-only line naming the item's op,
    so the record keeps its history; Manage and the opportunity page then leave the item out."""
    def test_a_removed_item_leaves_manage_and_the_ledger_keeps_both_lines(self):
        h = M2([(TD.TARGET, COMPANY, ROLE)])
        ledger_ready(h.world.source)
        for text in ("Test", "Portal answers"):
            self.assertEqual(h.hand("sent", sk="form", sx=text)[0], 200)
        test = next(m for m in M.sent_of(hand_text(h.world.source).splitlines()) if m["text"] == "Test")
        remove = next(f for f in forms(h.hub(h.world.ws.target)[1]) if f.get("act") == "unsent" and f.get("v") == test["op"])
        before = len(h.world.source.patches())
        status, body, _ = h.call("POST", "/manage", form=remove)
        self.assertEqual(status, 200, body)
        self.assertIn("Remove sent material: Test", body)
        self.assertEqual(len(h.world.source.patches()), before)                                        # review writes nothing
        status, body, _ = h.call("POST", "/manage", form={**h.fields(body), "step": "confirm"})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(h.world.source.patches()), before + 1)
        got = hand_text(h.world.source).splitlines()
        self.assertEqual([m["text"] for m in M.sent_of(got)], ["Portal answers"])
        self.assertEqual(sum(1 for t in got if M.SENT_LINE.fullmatch(t)), 2)                           # the item's own line stays: history is kept
        self.assertEqual(M.UNSENT_LINE.fullmatch(got[-1]).group(3), test["op"])
        hub = h.hub(h.world.ws.target)[1]
        self.assertNotIn("Form / portal answers: Test", hub)
        self.assertEqual([f["v"] for f in forms(hub) if f.get("act") == "unsent"], [next(m["op"] for m in M.sent_of(got))])

    def test_a_forged_or_invalid_removal_writes_nothing(self):
        h = M2([(TD.TARGET, COMPANY, ROLE)])
        ledger_ready(h.world.source)
        self.assertEqual(h.hand("sent", sk="form", sx="Test")[0], 200)
        before = len(h.world.source.patches())
        remove = next(f for f in forms(h.hub(h.world.ws.target)[1]) if f.get("act") == "unsent")
        self.assertEqual(h.call("POST", "/manage", form={**remove, "v": "not-an-op"})[0], 400)
        review = h.fields(h.call("POST", "/manage", form=remove)[1])
        for patch in ({"v": "0" * 16}, {"sx": "Something else"}, {"c": "0" * 64}, {"act": "sent"}):
            self.assertEqual(h.call("POST", "/manage", form={**review, **patch, "step": "confirm"})[0], 403, patch)
        self.assertEqual(len(h.world.source.patches()), before)

    def test_on_a_notes_page_the_removal_is_appended_to_the_fold(self):
        h, fold = notes_page()
        self.assertEqual(h.hand("sent", sk="form", sx="Test")[0], 200)
        remove = next(f for f in forms(h.hub(h.world.ws.target)[1]) if f.get("act") == "unsent")
        status, body, _ = h.call("POST", "/manage", form={**h.fields(h.call("POST", "/manage", form=remove)[1]), "step": "confirm"})
        self.assertEqual(status, 200, body)
        self.assertEqual(M.sent_of(lines(h, fold)), [])
        self.assertTrue(M.UNSENT_LINE.fullmatch(lines(h, fold)[-1]))

    def test_the_page_leaves_a_removed_item_out_and_carries_the_removal(self):
        gone = M.unsent_line(OP, "2026-10-06T09:00:00-05:00", "e" * 16)
        page = Page()
        self.assertFalse([t for t in page.materials(opp(hand=(FORM,), hand_ledger=(gone,))) if t.startswith("Application form")])     # removed on the other record: still gone
        para = lambda t: {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": t}]}}         # noqa: E731
        self.assertEqual(create.hand_lines([para(FORM), para(gone)]), (FORM, gone))
        self.assertEqual(create._undone([{"text": notion.FOLD, "children": [{"text": gone}]}]), [{"text": notion.FOLD, "children": []}])
        self.assertEqual((view.UNSENT_LINE.pattern, D._op(gone)), (M.UNSENT_LINE.pattern, "e" * 16))


class Page(unittest.TestCase):
    def materials(self, page):
        shown = texts(template.opportunity_page(page, "2026-10-24"))
        start = shown.index("Materials Sent")
        return shown[start + 1:start + 1 + next(i for i, t in enumerate(shown[start + 1:]) if t.startswith("Forms, portal answers"))]

    def test_recorded_material_and_jims_own_emails_are_listed_after_the_resume(self):
        page = opp(hand=(FORM,), sent_mail=(("2026-10-07T01:30:00+00:00", "Thank you — ExampleCo"),), sent_mail_state="read")
        got = self.materials(page)
        self.assertTrue(got[0].startswith("Submitted résumé"))
        self.assertIn("Application form — Workday application answers · recorded Oct 5", got)
        self.assertIn("Email you sent — Thank you — ExampleCo · Oct 6", got)                          # 1:30 AM UTC Oct 7 is Oct 6 in Central Time
        self.assertIn("https://example.test/apply/1", str(template.opportunity_page(page, "2026-10-24")))

    def test_none_sent_unreadable_and_not_connected_are_never_the_same(self):
        self.assertIn("Follow-up / thank-you email — none sent on this job's request thread yet", self.materials(opp(sent_mail_state="read")))
        self.assertIn("Follow-up / thank-you email — DEGRADED · Gmail unreadable this run", self.materials(opp(sent_mail_state="unreadable")))
        self.assertFalse([t for t in self.materials(opp()) if "email" in t.lower()])
        health = [t for t in texts(template.opportunity_page(opp(hand=(FORM,), sent_mail_state="unreadable"), "2026-10-24")) if t.startswith("Submitted résumé and sent materials")]
        self.assertIn("1 form(s) / Drive file(s) recorded on Manage; DEGRADED · Gmail unreadable this run", health[0])

    def test_the_same_record_on_both_records_shows_once_and_sent_lines_are_carried_and_never_an_edit(self):
        self.assertEqual(len([t for t in self.materials(opp(hand=(FORM,), hand_ledger=(FORM,))) if t.startswith("Application form")]), 1)
        self.assertIn(FORM, texts(template.opportunity_page(opp(hand=(FORM,)), "2026-10-24")))         # carried in the fold as written
        para = lambda t: {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": t}]}}         # noqa: E731
        hold = M.hold_line(M.PAUSED, SET_AT, "f" * 16)
        self.assertEqual(create.hand_lines([para(FORM), para(notion.MARKERS["opportunity"]), para(hold)]), (FORM, hold))
        self.assertEqual(create._undone([{"text": notion.FOLD, "children": [{"text": FORM}]}]), [{"text": notion.FOLD, "children": []}])

    def test_the_line_format_is_one_contract(self):
        self.assertEqual((view.SENT_LINE.pattern, template.SENT_MAX), (M.SENT_LINE.pattern, M.SENT_MAX))
        self.assertEqual(D._op(FORM), OP)


class Mail(unittest.TestCase):
    THREAD = "18f2a3b4c5d6e7f8"

    def evidence(self, source=None):
        return [(ParentQuery("ExampleCo", "Director"), RoundQuery(True, source=source or f"https://mail.google.com/mail/u/0/#all/{self.THREAD}")),
                (ParentQuery("Initech", "Lead"), RoundQuery(True, source="https://www.linkedin.com/messaging/thread/x"))]

    def test_only_the_exact_gmail_thread_is_read_and_unreadable_is_never_none(self):
        class G:
            asked = []

            def thread_sent(self, thread):
                self.asked.append(thread)
                from datetime import datetime, timezone
                return [(datetime(2026, 10, 7, 1, 30, tzinfo=timezone.utc), "Thank you")]
        g = G()
        out, failed = interview_evidence.sent_mail({}, self.evidence(), g)
        self.assertFalse(failed)
        self.assertEqual(g.asked, [self.THREAD])
        self.assertEqual((out[0][1].sent_mail, out[0][1].sent_mail_state), ((("2026-10-07T01:30:00+00:00", "Thank you"),), "read"))
        self.assertEqual((out[1][1].sent_mail, out[1][1].sent_mail_state), ((), None))               # not a Gmail request: untouched

        class Down:
            def thread_sent(self, thread):
                raise GmailError("GMAIL_THREAD_INCOMPLETE")
        out, failed = interview_evidence.sent_mail({}, self.evidence(), Down())
        self.assertTrue(failed)
        self.assertEqual(out[0][1].sent_mail_state, "unreadable")
        out, failed = interview_evidence.sent_mail({}, self.evidence(), None)                           # no Gmail credential in this step: not connected
        self.assertEqual((out, failed), (self.evidence(), False))

    def test_the_reader_keeps_only_sent_messages_and_their_subject(self):
        g = Gmail("id", "secret", "refresh")
        reply = {"messages": [{"labelIds": ["INBOX"], "internalDate": "1791000000000", "payload": {"headers": [{"name": "Subject", "value": "Interview"}]}},
                              {"labelIds": ["SENT"], "internalDate": "1791100000000", "payload": {"headers": [{"name": "Subject", "value": "Re:  Interview"}]}}]}
        with mock.patch.object(g, "_request", return_value=reply) as call:
            got = g.thread_sent(self.THREAD)
        self.assertIn("format=metadata", call.call_args[0][1])
        self.assertEqual([s for _, s in got], ["Re: Interview"])
        with mock.patch.object(g, "_request", return_value={}):
            with self.assertRaises(GmailError):
                g.thread_sent(self.THREAD)

    def test_the_view_carries_the_mail_from_the_query(self):
        q = replace(RoundQuery(True), sent_mail=(("2026-10-07T01:30:00+00:00", "Thank you"),), sent_mail_state="read")
        with mock.patch.object(view, "ledger_facts", return_value=("not_linked", {})):
            got = view.opportunity(None, ParentQuery("ExampleCo", "Director"), q, "root", children=[])
        self.assertEqual((got.sent_mail, got.sent_mail_state), (q.sent_mail, "read"))


if __name__ == "__main__":
    unittest.main()


class Search(unittest.TestCase):
    """LIFE-173 (Jim, Oct 30 2026): "I don't want to have to hunt for Google Drive files". Manage searches Jim's Drive by name (read only); a hit opens the usual
    sent-material review for that file, so nothing is recorded until he confirms there."""
    class Found(Drive):
        def __init__(self, files=None):
            super().__init__()
            self.queries, self.files = [], files

        def find(self, query, order="createdTime"):
            self.queries.append((query, order))
            return [dict(self.meta)] if self.files is None else self.files

    def search(self, h, q, drive):
        form = next(f for f in forms(h.hub(h.world.ws.target)[1]) if f.get("act") == "drive")
        with mock.patch.object(manage_app, "_drive", lambda secrets: drive):
            return h.call("POST", "/manage", form={**form, "q": q}), form

    def test_a_hit_opens_the_review_for_that_file_and_confirm_records_it(self):
        h, fold = notes_page()
        drive = self.Found()
        (status, body, _), form = self.search(h, "Launch o'plan", drive)
        self.assertEqual(status, 200, body)
        self.assertEqual(drive.queries, [("name contains 'Launch o\\'plan' and mimeType != 'application/vnd.google-apps.folder'", "modifiedTime desc")])
        self.assertIn("Launch plan for ExampleCo", body)
        self.assertIn(f"/manage?o={form['p']}", body)                                                  # back to Manage, nothing recorded
        self.assertEqual(h.writes(), (0, 0))
        pick = next(f for f in forms(body) if f.get("act") == "sent")
        self.assertEqual((pick["sk"], pick["su"]), (M.FILE, drive.meta["webViewLink"]))
        with mock.patch.object(manage_app, "_drive", lambda secrets: drive):
            status, body, _ = h.call("POST", "/manage", form=pick)
            self.assertEqual(status, 200, body)
            self.assertIn("Launch plan for ExampleCo", body)
            self.assertEqual(h.writes(), (0, 0))                                                        # review writes nothing
            status, body, _ = h.call("POST", "/manage", form={**h.fields(body), "step": "confirm"})
        self.assertEqual(status, 200, body)
        self.assertEqual([(m["kind"], m["text"]) for m in M.sent_of(lines(h, fold))], [(M.FILE, "Launch plan for ExampleCo")])

    def test_no_match_an_empty_query_a_missing_credential_or_a_forged_form_shows_nothing_and_writes_nothing(self):
        h, fold = notes_page()
        (status, body, _), form = self.search(h, "nothing", self.Found(files=[]))
        self.assertEqual(status, 200)
        self.assertIn("No Drive file matches", body)
        self.assertEqual(self.search(h, "   ", self.Found())[0][0], 400)
        self.assertEqual(self.search(h, "x" * 101, self.Found())[0][0], 400)
        self.assertEqual(self.search(h, "plan", None)[0][0], 409)
        drive = self.Found()
        with mock.patch.object(manage_app, "_drive", lambda secrets: drive):
            for patch in ({"t": "0" * 64}, {"act": "drive", "o": "e" * 32}, {"exp": "1"}):
                self.assertEqual(h.call("POST", "/manage", form={**form, "q": "plan", **patch})[0], 403, patch)
        self.assertEqual(drive.queries, [])
        self.assertEqual(h.writes(), (0, 0))

    def test_folders_and_files_without_a_drive_link_are_never_offered(self):
        h, _ = notes_page()
        files = [{"name": "Odd", "webViewLink": "https://example.test/x"}, {"name": None, "webViewLink": DRIVE_URL}, {"name": "Good plan", "webViewLink": DRIVE_URL}]
        (status, body, _), _ = self.search(h, "plan", self.Found(files=files))
        self.assertEqual([f["su"] for f in forms(body) if f.get("act") == "sent"], [DRIVE_URL])


class Layout(unittest.TestCase):
    """LIFE-174 (Jim, Oct 31 2026): Manage's actions are one card each, their fields and button together, styled by /manage.css (the gate's CSP allows no inline styles)."""
    def test_the_hub_links_its_stylesheet_and_every_field_sits_in_a_card(self):
        h, _ = notes_page()
        body = h.hub(h.world.ws.target)[1]
        self.assertIn('<link rel="stylesheet" href="/close.css"><link rel="stylesheet" href="/manage.css">', body)
        self.assertNotIn("<style", body)
        for heading in ("Status", "Set a next action", "Materials sent", "Add a Google Drive file you sent", "Add an application form or portal answers"):
            self.assertIn(f'<div class="mcard"><h4>{heading}</h4>', body)
        self.assertEqual(sorted(f.get("sk") for f in forms(body) if f.get("act") == "sent"), [M.FILE, M.FORM])
        self.assertEqual(h.call("GET", "/manage.css")[:2][0], 200)
        self.assertEqual(manage_app.handle_css({}, {}, {})[:2], (200, "text/css; charset=utf-8"))
        self.assertIn(".mcard{", manage_app.CSS)
