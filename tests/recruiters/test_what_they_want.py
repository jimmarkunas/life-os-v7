"""What they want is never empty (extracted ask, else the fallback sentence with Needs review ticked), the backfill fills
open rows only, and both the.
"""

import contextlib
import io
import unittest

from lifeos.recruiters import fill, notion as store, stage
from tests.recruiters.fakes import FakeRecruiters, ReadOnlyGmail, mail, page
from tests.recruiters.test_recruiters import NOW, SIGNED, run, signed

ASK = SIGNED + "\nCould you send an updated resume today?\n"


class Fallback(unittest.TestCase):
    def test_the_fallback_is_built_from_the_role_then_the_subject_then_nothing_and_is_never_blank(self):
        self.assertEqual(
            store.what_or_fallback({"what": "Send an updated resume."}), ("Send an updated resume.", False)
        )
        self.assertEqual(
            store.what_or_fallback({"what": "", "role": "Data Engineer"}),
            ("Reply to their message about Data Engineer.", True),
        )
        self.assertEqual(
            store.what_or_fallback({"what": "  ", "role": "", "subject": "Opportunity at Acme"}),
            ("Reply to their message about Opportunity at Acme.", True),
        )
        self.assertEqual(store.what_or_fallback({}), ("Reply to their message.", True))
        for blank in ({}, {"what": None}, {"what": " \n "}, {"role": "  ", "subject": ""}):
            self.assertTrue(store.what_or_fallback(blank)[0].strip())

    def test_a_created_row_has_the_extracted_ask_and_no_review_tick_or_the_fallback_with_it(self):
        with_ask, without = FakeRecruiters(), FakeRecruiters()
        run([mail(body=ASK)], with_ask, live=True)
        run([signed()], without, live=True)
        a, b = store.read(with_ask.pages[0]), store.read(without.pages[0])
        self.assertTrue(
            a["what"].startswith("Send an updated resume") and not a["needs_review"]
        )  # the extracted ask is kept, no review tick
        self.assertTrue(b["what"].startswith("Reply to their message") and b["needs_review"])
        self.assertTrue(all(store.read(p)["what"].strip() for p in with_ask.pages + without.pages))

    def test_an_update_that_fills_a_blank_ask_uses_the_fallback_and_ticks_needs_review(self):
        notion = FakeRecruiters(
            [
                page(
                    "Pat Example",
                    "Acme Staffing",
                    role="Data Engineer",
                    last="2026-10-06T15:00:00+00:00",
                    what="",
                    review=False,
                )
            ]
        )
        run([signed(received="2026-10-08T09:00:00+00:00")], notion, live=True)
        row = store.read(notion.pages[0])
        self.assertTrue(row["what"].startswith("Reply to their message") and row["needs_review"])


class Names(unittest.TestCase):
    def test_rows_are_read_and_written_under_recruiter_and_company(self):
        notion = FakeRecruiters()
        run([signed()], notion, live=True)
        keys = set(notion.writes[0][1])
        self.assertTrue({"Recruiter", "Company"} <= keys and not ({"Recruiter / Person", "Recruiter Company"} & keys))
        row = store.read(notion.pages[0])
        self.assertEqual((row["person"], row["company"]), ("Pat Example", "Acme Staffing"))
        again = run([signed()], notion, live=True)  # a replay finds the same row
        self.assertEqual((again["created"], len(notion.pages)), (0, 1))

    def test_the_old_names_alone_fail_closed_with_the_fixed_code(self):
        notion = FakeRecruiters(schema={})
        notion.schema = {
            ("Recruiter / Person" if n == "Recruiter" else "Recruiter Company" if n == "Company" else n): {"type": t}
            for n, t in store.SCHEMA.items()
        }
        with self.assertRaises(store.RecruitersError) as caught:
            store.check_schema(notion)
        self.assertEqual(str(caught.exception), "RECRUITERS_NOTION_SCHEMA_MISMATCH")
        self.assertEqual(notion.writes, [])

    def test_either_new_name_missing_is_a_fixed_code(self):
        for missing in ("Recruiter", "Company"):
            notion = FakeRecruiters()
            del notion.schema[missing]
            with self.assertRaises(store.RecruitersError) as caught:
                store.check_schema(notion)
            self.assertEqual(str(caught.exception), "RECRUITERS_NOTION_SCHEMA_MISMATCH")

    def test_company_fill_writes_company(self):
        notion = FakeRecruiters([page("Pat Example", "", role="", url="https://mail.google.com/m/1")])
        store.check_schema(notion)
        props, _ = fill.plan(store.read(notion.pages[0]), mail(body=SIGNED))
        self.assertIn("Company", props)
        self.assertNotIn("Recruiter Company", props)


class AskBackfill(unittest.TestCase):
    URL = "https://mail.example.com/m/1"

    def go(self, pages, records, write=True):
        notion = FakeRecruiters(pages)
        counts = {}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            stage._asks(notion, write, counts, records, None, [])
        return notion, counts, out.getvalue()

    def test_open_rows_with_a_blank_ask_are_filled_and_rows_with_text_or_closed_are_skipped(self):
        pages = [
            page(
                "Pat Example", "Acme", role="Data Engineer", url=self.URL, action="Needs Action", pid="abcd1234abcd1234"
            ),
            page(
                "Sam Example",
                "Beta",
                role="Admin",
                url="https://mail.example.com/m/2",
                action="Waiting",
                what="Already has text",
            ),
            page("Kim Example", "Gamma", role="QA", url="https://mail.example.com/m/3", action="Completed"),
        ]
        notion, counts, out = self.go(pages, [mail(body=SIGNED, url=self.URL)])
        rows = {store.read(p)["person"]: store.read(p) for p in notion.pages}
        self.assertTrue(rows["Pat Example"]["what"].strip() and rows["Pat Example"]["needs_review"])
        self.assertEqual(rows["Sam Example"]["what"], "Already has text")
        self.assertEqual(rows["Kim Example"]["what"], "")  # a closed row keeps its blank
        self.assertEqual((counts["asks_filled"], counts["asks_fallback"]), (1, 1))
        self.assertIn("recruiters-ask:", out)
        self.assertNotIn("Pat", out)  # ids and a kind only, never a name

    def test_an_extracted_ask_is_kept_without_the_review_tick_and_a_dry_run_writes_nothing(self):
        pages = [page("Pat Example", "Acme", role="Data Engineer", url=self.URL, action="Replied")]
        dry, counts, _ = self.go(pages, [mail(body=ASK, url=self.URL)], write=False)
        self.assertEqual((counts["asks_would_fill"], counts["asks_filled"], dry.writes), (1, 0, []))
        notion, counts, _ = self.go(
            [page("Pat Example", "Acme", role="Data Engineer", url=self.URL, action="Replied")],
            [mail(body=ASK, url=self.URL)],
        )
        row = store.read(notion.pages[0])
        self.assertTrue(row["what"].strip())
        self.assertTrue(
            row["what"].startswith("Send an updated resume")
            and not row["needs_review"]
            and counts["asks_fallback"] == 0
        )

    def test_a_row_whose_message_cannot_be_read_is_left_and_counted(self):
        notion, counts, _ = self.go(
            [page("Pat Example", "Acme", url="https://elsewhere.example/x", action="Needs Action")], []
        )
        self.assertEqual((counts["asks_unread"], counts["asks_filled"], notion.writes), (1, 0, []))


if __name__ == "__main__":
    unittest.main()
