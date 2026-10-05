"""Parsing and measuring a connections export. All people and companies here are invented; example.com addresses only."""
import json
import unittest

from lifeos.network import parse
from lifeos.network.errors import NetworkError

NOTES = '''Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing.
You will only see email addresses for connections who have chosen to allow their contacts to download or export their email addresses."

'''
HEAD = "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
SAMPLE = NOTES + HEAD + "\n".join([
    'Ada,Example,https://www.linkedin.com/in/ada-example/,,"Acme Technologies, Inc.",Director of Product,05 Sep 2026',
    'Ada,Example,HTTPS://WWW.LINKEDIN.COM/in/Ada-Example?trk=x,,Acme,VP Product,01 Jan 2020',
    '"Pat, MBA",Sample,https://www.linkedin.com/in/pat-sample,pat@example.com,Widget Co,Engineer,04 Sep 2026',
    'Sam,Sample,https://www.linkedin.com/in/sam-sample,,Sam Sample,Founder,03 Sep 2026',
    'Lee,Example,https://uk.linkedin.com/in/lee-example,,Freelance Consultant,Consultant,02 Sep 2026',
    'Kim,Example,https://www.linkedin.com/in/kim-example,,Independent Bank,Analyst,not a date',
    'Ron,Example,,,Globex,Manager,01 Sep 2026',
    'Ron,Example,,,Globex Group,Manager,31 Aug 2026',
    'Zed,Example,https://example.com/profile/zed,,Initech,Lead,30 Aug 2026',
    'Bad,Row,only,three',
    '',
]) + "\n"


class KeyTests(unittest.TestCase):
    def test_profile_keys_ignore_scheme_case_country_query_and_trailing_slash(self):
        for url in ("https://www.linkedin.com/in/ada-example/", "HTTP://linkedin.com/in/Ada-Example", "https://uk.linkedin.com/in/ada-example?trk=1#top"):
            self.assertEqual(parse.url_key(url), "ada-example")

    def test_blank_and_non_profile_addresses_have_no_key(self):
        for url in ("", "   ", "https://example.com/in/ada", "https://www.linkedin.com/company/acme", "https://www.linkedin.com/in/"):
            self.assertIsNone(parse.url_key(url))

    def test_company_key_drops_legal_and_generic_words(self):
        self.assertEqual(parse.company_key("Acme Technologies, Inc."), "acme")
        self.assertEqual(parse.company_key("Acme"), "acme")
        self.assertEqual(parse.company_key(""), "")

    def test_no_employer_is_the_persons_own_name_or_freelance_wording_only(self):
        self.assertTrue(parse.not_an_employer("Sam Sample", "Sam", "Sample"))
        self.assertTrue(parse.not_an_employer("Freelance Consultant", "Lee", "Example"))
        self.assertTrue(parse.not_an_employer("Self-Employed", "A", "B"))
        self.assertFalse(parse.not_an_employer("Independent Bank", "Kim", "Example"))
        self.assertFalse(parse.not_an_employer("Acme", "Ada", "Example"))
        self.assertFalse(parse.not_an_employer("", "Ada", "Example"))

    def test_dates(self):
        self.assertEqual(str(parse.parse_date("05 Sep 2026")), "2026-09-05")
        self.assertIsNone(parse.parse_date("2026-09-05"))
        self.assertIsNone(parse.parse_date(""))


class ReadAndAnalyseTests(unittest.TestCase):
    def test_header_is_found_after_the_notes_and_headings_not_positions_decide_columns(self):
        rows, malformed = parse.read_rows(SAMPLE)
        self.assertEqual(len(rows), 9)
        self.assertEqual(malformed, 1)
        self.assertEqual(rows[2]["first"], "Pat, MBA")                 # a quoted comma stays inside the field
        reordered = "Position,Connected On,Company,URL,Last Name,First Name,Email Address\nCEO,01 Jan 2026,Acme,https://www.linkedin.com/in/a-b,B,A,\n"
        rows2, _ = parse.read_rows(reordered)
        self.assertEqual((rows2[0]["first"], rows2[0]["company"], rows2[0]["title"]), ("A", "Acme", "CEO"))

    def test_a_file_without_the_headings_fails_closed(self):
        for text in ("", "a,b,c\n1,2,3\n", "First Name,Last Name,URL\nA,B,C\n"):
            with self.assertRaises(NetworkError) as ctx:
                parse.read_rows(text)
            self.assertEqual(str(ctx.exception), "NETWORK_NO_HEADER")

    def test_a_byte_order_mark_does_not_hide_the_header(self):
        rows, _ = parse.read_rows("﻿" + HEAD + "A,B,https://www.linkedin.com/in/ab,,Acme,CEO,01 Jan 2026\n")
        self.assertEqual(len(rows), 1)

    def test_counts_for_the_sample(self):
        rows, malformed = parse.read_rows(SAMPLE)
        counts = parse.analyse(rows, malformed)
        self.assertEqual(counts["rows"], 9)
        self.assertEqual(counts["malformed_rows"], 1)
        self.assertEqual(counts["blank_url"], 2)
        self.assertEqual(counts["invalid_url"], 1)
        self.assertEqual(counts["duplicate_url_keys"], 1)                # the two Ada rows share one key
        self.assertEqual(counts["no_employer_company"], 2)               # own name, freelance
        self.assertEqual(counts["email_present"], 1)
        self.assertEqual(counts["bad_connected_on"], 1)
        self.assertEqual(counts["blank_company"], 0)
        self.assertEqual(counts["ambiguous_no_url_rows"], 2)             # the two Ron rows: same name, same employer key, no profile key
        self.assertEqual(counts["newest_connected_on"], "2026-09-05")
        self.assertEqual(counts["oldest_connected_on"], "2020-01-01")
        self.assertEqual(counts["distinct_company_keys"], 5)             # acme, widget, independent, globex, initech (own-name and freelance rows excluded)
        self.assertGreater(counts["matchable_rows"], 0)

    def test_output_holds_counts_and_dates_only_never_row_content(self):
        rows, malformed = parse.read_rows(SAMPLE)
        blob = json.dumps(parse.analyse(rows, malformed)).lower()
        for secret in ("ada", "example", "acme", "widget", "sam", "pat", "globex", "linkedin", "@"):
            self.assertNotIn(secret, blob)
        for value in parse.analyse(rows, malformed).values():
            self.assertIsInstance(value, (int, str))


if __name__ == "__main__":
    unittest.main()
