"""LIFE-903 (comment 13848): the two-process Ledger-creation race on a real MariaDB with the real `v7_jobs` schema (the two claim columns and the UNIQUE `dedupe_key`), not sqlite.

Runs only when LIFEOS_RACE_DB_NAME is set, which only .github/workflows/mariadb-race.yml does, against a throwaway MariaDB service container on the runner's own loopback. It refuses any host that is not
127.0.0.1 or localhost and any database not named `race_...`, so it can never touch the production store. The schema is built by the production `store.ensure_schema`. Four real OS processes (publish, Interview lead,
opportunity, publish) race one key: exactly one WON and one row in the stand-in Ledger; the same race without the claim makes two rows, which proves the test bites."""
import glob
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

from lifeos.jobs import store
from lifeos.platform import ledger_claim as lc

ROOT = pathlib.Path(__file__).resolve().parents[2]
KEY = "b" * 64
NAME = os.environ.get("LIFEOS_RACE_DB_NAME", "")
HOST = os.environ.get("LIFEOS_RACE_DB_HOST", "")


def connect():
    import pymysql
    env = os.environ
    return pymysql.connect(host=HOST, port=int(env.get("LIFEOS_RACE_DB_PORT") or 3306), user=env["LIFEOS_RACE_DB_USER"], password=env.get("LIFEOS_RACE_DB_PASSWORD", ""),
                           database=NAME, charset="utf8mb4", autocommit=True, connect_timeout=10)


@unittest.skipUnless(NAME, "LIFEOS_RACE_DB_NAME is set only by the mariadb-race workflow")
class MariaDbRace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if HOST not in ("127.0.0.1", "localhost") or not NAME.startswith("race_"):
            raise RuntimeError("the MariaDB race runs only against a throwaway local database named race_...")
        with connect() as connection:
            store.ensure_schema(connection)

    def setUp(self):
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM v7_jobs WHERE dedupe_key=%s", (KEY,))

    def test_the_real_schema_has_the_unique_key_and_both_claim_columns(self):
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='v7_jobs' AND column_name IN ('ledger_claim_by','ledger_claim_at') AND is_nullable='YES'")
            columns = cursor.fetchone()[0]
            cursor.execute("SELECT non_unique FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='v7_jobs' AND column_name='dedupe_key' AND index_name='uq_v7_jobs_dedupe'")
            unique = cursor.fetchone()
        self.assertEqual((columns, unique), (2, (0,)))

    def race(self, mode, roles):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file, go_file = os.path.join(tmp, "ledger.txt"), os.path.join(tmp, "go")
            open(ledger_file, "w").close()
            procs = [subprocess.Popen([sys.executable, "-B", "-m", "tests.jobs.claim_race_worker", "mariadb", ledger_file, KEY, role, go_file, mode, str(len(roles))], cwd=ROOT, stdout=subprocess.PIPE, text=True)
                     for role in roles]
            deadline = time.monotonic() + 60                                     # LIFE-966: every worker is ready (no fixed sleep), then they are released together
            while len(glob.glob(go_file + ".ready.*")) < len(roles) and time.monotonic() < deadline:
                time.sleep(0.005)
            open(go_file, "w").close()
            outs = [p.communicate(timeout=120)[0].strip() for p in procs]
            return outs, pathlib.Path(ledger_file).read_text().split()

    def test_without_the_claim_two_processes_make_two_rows(self):
        outs, rows = self.race("naive", ("publish", "lead"))
        self.assertEqual(len(rows), 2)

    def test_with_the_claim_four_processes_make_exactly_one_row_and_one_winner(self):
        outs, rows = self.race("claim", ("publish", "lead", "opportunity", "publish"))
        self.assertEqual(rows, [KEY])
        self.assertEqual(sorted(outs), [lc.HELD, lc.HELD, lc.HELD, lc.WON])
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM v7_jobs WHERE dedupe_key=%s", (KEY,))
            self.assertEqual(cursor.fetchone()[0], 1)                       # one store row for the key, however many tried


if __name__ == "__main__":
    unittest.main()
