"""NET-1b importer: invented rows only. The store's real SQL runs on an in-memory stand-in (tests/kit/mysqlite.py)."""
import csv
import io
import unittest
from datetime import date
from unittest.mock import patch

from lifeos import run
from lifeos.network import identity, importer, store
from lifeos.network.errors import NetworkError
from tests.kit.mysqlite import MySQLite
from tests.network.test_source_and_inspect import FakeClient, Response, file_block, page

PAGE = "3d73c5a0-5926-806f-866d-c92960349fe9"
ENV = {"NETWORK_HANDOFF_PAGE_ID": PAGE, "NOTION_API_TOKEN": "t"}
HEAD = ["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"]
URL = "https://www.linkedin.com/in/"


def make_csv(rows):
    out = io.StringIO()
    out.write("Notes:\nexample export\n\n")
    writer = csv.writer(out)
    writer.writerow(HEAD)
    writer.writerows(rows)
    return out.getvalue()


def row(i, company="Acme Corp", title="Director", url=True, first="Pat", last=None):
    return [first, last or f"Person{i:05d}", f"{URL}person-{i:05d}" if url else "", f"pat{i}@example.com", company, title, "05 Sep 2026"]


def seed_rows():
    """The real seed's shape with invented values: 3,346 people (3,244 with an employer: some blank titles), 102 without a usable employer, 107 with no profile address."""
    rows = [row(i, title="" if i % 7 == 0 else "Engineer", company=f"Company {i % 2485}") for i in range(3244)]
    kinds = ["", "Freelance", "Self-employed", "Retired", "Student", "Independent"]
    rows += [row(3244 + i, company=kinds[i % len(kinds)]) for i in range(101)]
    rows.append(row(3345, company="Pat Person03345", first="Pat", last="Person03345"))          # own name as company
    rows += [row(9000 + i, url=False) for i in range(107)]
    return rows


def run_import(rows, live=True, conn=None, name="LI_Connections_20260907.csv", chunk=500, environ=ENV):
    text = make_csv(rows).encode()
    client = FakeClient([page([file_block(name)])])
    return importer.run(0, live, environ=environ, client=client, opener=lambda *a, **k: Response(text), connection=conn, chunk=chunk)


class Classify(unittest.TestCase):
    def test_seed_accounting_3453_3346_3244_107(self):
        plan = identity.classify([dict(zip(("first", "last", "url", "email", "company", "title", "connected"), r)) for r in seed_rows()])
        self.assertEqual((plan["source_rows"], len(plan["people"]), plan["positions"], plan["ambiguous_held"], plan["rejected"]), (3453, 3346, 3244, 107, 0))

    def test_negative_controls(self):
        def rec(url, company="Acme", first="Ann", last="Lee", title="X"):
            return {"first": first, "last": last, "url": url, "email": "", "company": company, "title": title, "connected": "05 Sep 2026"}
        rows = [rec(URL + "a-1"), rec(URL + "a-1/", company="Other"),             # same profile twice: held, not merged
                rec("https://example.com/notprofile"),                          # not a profile address: rejected
                rec("", company="Acme"),                                        # no address: held
                rec(URL + "b-2"), rec(URL + "c-3")]                             # same name, different people: two people
        plan = identity.classify(rows)
        self.assertEqual((len(plan["people"]), plan["ambiguous_held"], plan["rejected"]), (2, 3, 1))
        self.assertEqual(plan["source_rows"], 6)

    def test_no_employer_and_blank_title(self):
        def rec(company, title):
            return {"first": "Ann", "last": "Lee", "url": URL + "q", "email": "", "company": company, "title": title, "connected": ""}
        for company in ("", "Freelance", "self-employed", "Retired", "Ann Lee"):
            p = identity.classify([rec(company, "Boss")])["people"][0]
            self.assertIsNone(p["company_key"])
        p = identity.classify([rec("Acme Inc", "")])["people"][0]
        self.assertEqual((p["company_key"], p["title"], p["connected_on"]), ("acme", None, None))


class Import(unittest.TestCase):
    def test_dry_run_is_counts_only_and_touches_no_database(self):
        conn = MySQLite()
        with patch("lifeos.network.importer.db.connect", side_effect=AssertionError("dry run must not connect")):
            counts = run_import(seed_rows(), live=False, conn=conn)
        self.assertEqual((counts["source_rows"], counts["people_accepted"], counts["positions_accepted"], counts["ambiguous_held"]), (3453, 3346, 3244, 107))
        self.assertEqual(conn.raw.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0)

    def test_live_seed_replay_and_no_email_stored(self):
        conn = MySQLite()
        first = run_import(seed_rows(), conn=conn)
        self.assertEqual((first["people_total"], first["positions_total"], first["batch_status"], first["replay"]), (3346, 3244, "COMPLETE", False))
        self.assertEqual((first["people_new"], first["positions_new"]), (3346, 3244))
        again = run_import(seed_rows(), conn=conn)
        self.assertEqual((again["replay"], again["people_new"], again["positions_new"]), (True, 0, 0))
        self.assertEqual(conn.count("v7_network_batches"), 1)
        for table in ("v7_network_people", "v7_network_positions", "v7_network_batches"):
            dump = repr(conn.raw.execute(f"SELECT * FROM {table}").fetchall())
            self.assertNotIn("@", dump)
        self.assertEqual(conn.raw.execute("SELECT COUNT(*) FROM v7_network_positions WHERE title IS NULL").fetchone()[0] > 0, True)
        self.assertEqual(conn.raw.execute("SELECT COUNT(*) FROM v7_network_positions WHERE company_key IN ('freelance','self employed','')").fetchone()[0], 0)

    def test_failed_chunk_rolls_back_and_the_next_run_resumes(self):
        conn = MySQLite()
        conn.fail_on = ("INSERT IGNORE INTO v7_network_people", 2)                   # the second chunk fails
        with self.assertRaises(NetworkError) as ctx:
            run_import(seed_rows(), conn=conn, chunk=1000)
        self.assertEqual(str(ctx.exception), "NETWORK_STORE_FAILED")
        self.assertEqual((conn.count("v7_network_people"), conn.raw.execute("SELECT status, cursor_row FROM v7_network_batches").fetchone()), (1000, ("APPLYING", 1000)))
        done = run_import(seed_rows(), conn=conn, chunk=1000)
        self.assertEqual((done["people_total"], done["positions_total"], done["batch_status"]), (3346, 3244, "COMPLETE"))
        self.assertEqual(run_import(seed_rows(), conn=conn)["people_new"], 0)

    def test_scale_5000_rows(self):
        conn = MySQLite()
        counts = run_import([row(i) for i in range(5000)], conn=conn)
        self.assertEqual((counts["people_total"], counts["positions_total"]), (5000, 5000))

    def test_schema_mismatch_fails_closed(self):
        conn = MySQLite()
        conn.raw.execute("CREATE TABLE v7_network_people (id INTEGER, other TEXT)")
        with self.assertRaises(NetworkError) as ctx:
            run_import([row(1)], conn=conn)
        self.assertEqual(str(ctx.exception), "NETWORK_SCHEMA_MISMATCH")

    def test_later_observations(self):
        conn = MySQLite()
        run_import([row(1, "Acme Corp", "Director"), row(2, "Beta Inc", "Lead")], conn=conn, name="LI_Connections_20260907.csv")
        later = [row(1, "", ""), row(2, "Gamma LLC", "Lead")]                                     # 1: blank employer and title, 2: moved
        run_import(later, conn=conn, name="LI_Connections_20261007.csv")
        positions = conn.raw.execute("SELECT p.url_key, s.company_key, s.title, s.position_state FROM v7_network_positions s JOIN v7_network_people p ON p.id = s.person_id ORDER BY 1, 4").fetchall()
        self.assertIn(("person-00001", "acme", "Director", "CURRENT"), positions)                 # blank employer/title never supersedes or erases
        self.assertIn(("person-00002", "beta", "Lead", "SUPERSEDED"), positions)
        self.assertIn(("person-00002", "gamma", "Lead", "CURRENT"), positions)
        self.assertEqual(conn.count("v7_network_batches"), 2)
        run_import([row(2, "Beta Inc", "Lead")], conn=conn, name="LI_Connections_20260801.csv")  # an older observation never replaces a newer one
        current = conn.raw.execute("SELECT company_key FROM v7_network_positions WHERE position_state='CURRENT' AND person_id=2").fetchall()
        self.assertEqual(current, [("gamma",)])

    def test_same_title_blank_new_title_confirms(self):
        conn = MySQLite()
        run_import([row(1, "Acme Corp", "Director")], conn=conn)
        run_import([row(1, "Acme Corp", "")], conn=conn, name="LI_Connections_20261007.csv")
        self.assertEqual(conn.raw.execute("SELECT title, position_state, last_verified FROM v7_network_positions").fetchall(), [("Director", "CURRENT", "2026-10-07")])


class EntryPoint(unittest.TestCase):
    def test_registered_stage_and_fixed_failure_code(self):
        self.assertEqual(run.STAGES["network-import"].target, ("lifeos.network.importer", "run"))
        with patch.dict(run.STAGES, {"network-import": lambda limit, live: (_ for _ in ()).throw(NetworkError("NETWORK_STORE_FAILED"))}):
            self.assertEqual(run.main(["network-import"]), 1)

    def test_missing_config_fails_with_a_fixed_code(self):
        with self.assertRaises(NetworkError) as ctx:
            importer.run(0, False, environ={})
        self.assertEqual(str(ctx.exception), "NETWORK_CONFIG_MISSING")


if __name__ == "__main__":
    unittest.main()
