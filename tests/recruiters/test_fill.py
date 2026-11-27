"""synthetic only: Company, Role and Client come only from the message behind a row (signature, display name, sender
domain)
"""
import contextlib
import io
import unittest

from lifeos.recruiters import backfill, fill, notion as store, stage
from tests.recruiters.fakes import FakeRecruiters, ReadOnlyGmail, mail, page

AT = "@"
BODY_SIG = "Hi Jim,\n\nI have an opening you may like.\n\nThanks,\nPat Example\nSenior Recruiter at Northwind Staffing\n555-0100"


def derived(**kw):
    return fill.derive(mail(**kw))


class Company(unittest.TestCase):
    def test_signature_first(self):
        out = derived(sender_name="Pat Example | Other Agency", address="pat" + AT + "gmail.com", body=BODY_SIG)
        self.assertEqual(out["company"], ("Northwind Staffing", "signature"))

    def test_display_name_when_the_signature_has_none(self):
        for name in ("Pat Example | Contoso Search", "Pat Example - Contoso Search", "Pat Example (Contoso Search)", "Pat Example @ Contoso Search"):
            out = derived(sender_name=name, address="pat" + AT + "gmail.com", body="Hello, are you open to a new role?\n\nPat")
            self.assertEqual(out["company"], ("Contoso Search", "display name"), name)

    def test_domain_when_it_is_not_free_mail(self):
        out = derived(sender_name="Pat Example", address="pat" + AT + "fabrikamtalent.com", body="Hello, are you open to a new role?\n\nPat")
        self.assertEqual(out["company"][1], "domain")
        self.assertIn("fabrikamtalent", out["company"][0].casefold())
        for free in ("gmail.com", "outlook.com", "yahoo.com", "hotmail.com", "icloud.com", "proton.me"):
            self.assertEqual(derived(sender_name="Pat Example", address="pat" + AT + free, body="Hello, a role?\n\nPat")["company"], ("Not stated", "not stated"), free)

    def test_nothing_is_invented_when_the_message_does_not_say(self):
        self.assertEqual(derived(sender_name="Pat Example", address="pat" + AT + "gmail.com", body="Are you open to a new role?")["company"], ("Not stated", "not stated"))


class Role(unittest.TestCase):
    def test_the_subject_title_comes_first(self):
        out = derived(subject="Re: Senior Data Engineer - Remote - Contract", body="We also have a Program Manager opening.")
        self.assertEqual(out["role"], ("Senior Data Engineer", "subject"))

    def test_then_the_first_title_in_the_body(self):
        out = derived(subject="Opportunity", body="I am hiring a Staff Platform Engineer for a client.\nRegards")
        self.assertEqual(out["role"][1], "body")
        self.assertIn("Platform Engineer", out["role"][0])

    def test_a_labelled_role_line_and_the_sentence_pattern_count_as_body(self):
        self.assertEqual(derived(subject="Hello", body="Role: Solutions Architect\nLocation: Remote")["role"], ("Solutions Architect", "body"))

    def test_not_stated(self):
        self.assertEqual(derived(subject="Hello", body="Are you available for a quick call this week?")["role"], ("Not stated", "not stated"))


class Client(unittest.TestCase):
    def test_the_end_client_when_the_message_names_it(self):
        out = derived(body="I am recruiting on behalf of Wingtip Toys, a global retailer.")
        self.assertEqual(out["client"], ("Wingtip Toys", "body"))

    def test_not_stated(self):
        self.assertEqual(derived(body="Quick question about your availability.")["client"], ("Not stated", "not stated"))


class Row(unittest.TestCase):
    def test_only_blank_fields_are_filled_and_a_set_value_is_never_replaced(self):
        row = store.read(page("Pat Example", company="Already Set", role="", client="Not stated"))
        props, sources = fill.plan(row, mail(subject="Senior Data Engineer", body=BODY_SIG))
        self.assertEqual(list(props), ["Role"])
        self.assertEqual(sources, {"role": "subject"})

    def test_apply_writes_once_reads_back_and_logs_the_source_not_the_value(self):
        notion = FakeRecruiters([page("Pat Example", pid="r1", action="Needs Action", url="https://mail.example.com/m/1", follow_up="2026-11-30")])
        record = mail(subject="Senior Data Engineer", body=BODY_SIG, address="pat" + AT + "gmail.com")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            counts = {}
            stage._fill(notion, True, counts, [record], None, [])
        row = store.read(notion.pages[0])
        self.assertEqual((row["company"], row["role"], row["client"]), ("Northwind Staffing", "Senior Data Engineer", "Not stated"))
        self.assertEqual((counts["filled"], len(notion.writes)), (1, 1))
        self.assertIn("recruiters-fill: r1", out.getvalue())
        for secret in ("Northwind", "Senior Data", "Pat"):
            self.assertNotIn(secret, out.getvalue())

    def test_a_checked_row_is_not_checked_again(self):
        notion = FakeRecruiters([page("Pat Example", pid="r1", action="Needs Action", company="Not stated", role="Not stated", client="Not stated", url="https://mail.example.com/m/1")])
        counts = {}
        stage._fill(notion, True, counts, [mail()], None, [])
        self.assertEqual((counts["would_fill"], notion.writes), (0, []))

    def test_a_dry_run_reports_and_writes_nothing(self):
        notion = FakeRecruiters([page("Pat Example", pid="r1", action="Needs Action", url="https://mail.example.com/m/1")])
        counts = {}
        with contextlib.redirect_stdout(io.StringIO()):
            stage._fill(notion, False, counts, [mail(subject="Senior Data Engineer")], None, [])
        self.assertEqual((counts["would_fill"], counts["filled"], notion.writes), (1, 0, []))

    def test_a_row_whose_message_cannot_be_read_stays_as_it_was(self):
        notion = FakeRecruiters([page("Pat Example", pid="r1", action="Needs Action", url="https://example.com/not-a-mail-link")])
        counts = {}
        stage._fill(notion, True, counts, [], None, [])
        self.assertEqual((counts["fill_unread"], notion.writes), (1, []))

    def test_older_rows_are_read_again_a_few_at_a_time(self):
        rows = [page(f"Pat {i}", pid=f"r{i}", action="Needs Action", url=f"https://mail.google.com/mail/u/0/#all/abcdefgh{i:02d}") for i in range(backfill.MAX_PER_RUN + 3)]
        counts, asked = {}, []

        class Gm:
            def _request(self, method, url, *a, **k):
                asked.append(url)
                return {"messages": []}
        stage._fill(FakeRecruiters(rows), True, counts, [], Gm(), [])
        self.assertEqual(len(asked), backfill.MAX_PER_RUN)

    def test_the_thread_behind_a_gmail_link_gives_the_first_message_jim_did_not_send(self):
        gm = ReadOnlyGmail([mail(ident="m2", subject="Senior Data Engineer", body=BODY_SIG)])

        def request(method, url, *a, **k):
            return {"messages": [{"id": "m1", "labelIds": ["SENT"]}, {"id": "m2", "labelIds": ["INBOX"]}]}
        gm._request = request
        record = backfill.message_for({"source_url": "https://mail.google.com/mail/u/0/#all/abcdefgh12"}, {}, gm, [])
        self.assertEqual(record["id"], "m2")


if __name__ == "__main__":
    unittest.main()
