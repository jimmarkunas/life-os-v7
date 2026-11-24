"""LIFE-903: one OS process of the two-process Ledger-creation race (tests/jobs/test_ledger_claim.py). Not a test module.

    python -m tests.jobs.claim_race_worker <store file | mariadb> <ledger file> <key> <role> <go file> <claim|naive> [workers]

`mariadb` as the store opens the throwaway MariaDB named by LIFEOS_RACE_DB_HOST/PORT/USER/PASSWORD/NAME (tests/jobs/test_ledger_claim_mariadb.py), with the same settings as the production connection (autocommit, utf8mb4).

Every process opens the SAME on-disk store file with its own connection and, once the go file exists, runs the creator protocol against a one-line-per-row `ledger file` standing in for Notion (which has no unique
constraint): `claim` = lifeos.platform.ledger_claim.claim, then look the key up, then create (appended), then release; `naive` = look up, then create, with no claim (the race the ticket forbids). The pause between the
lookup and the create is the window a lookup-then-create race needs. Prints the claim state (or `naive`)."""
import glob
import os
import sys
import time

from lifeos.platform import ledger_claim
from tests.jobs.fakes_jobsdb import JobsDB


def _mariadb():
    import pymysql
    env = os.environ
    return pymysql.connect(host=env["LIFEOS_RACE_DB_HOST"], port=int(env.get("LIFEOS_RACE_DB_PORT") or 3306), user=env["LIFEOS_RACE_DB_USER"], password=env.get("LIFEOS_RACE_DB_PASSWORD", ""),
                           database=env["LIFEOS_RACE_DB_NAME"], charset="utf8mb4", autocommit=True, connect_timeout=10)


def main(argv):
    store_file, ledger_file, key, role, go_file, mode = argv[:6]
    total = int(argv[6]) if len(argv) > 6 else 0                       # LIFE-966: how many workers there are; 0 = the old fixed sleeps
    connect = _mariadb if store_file == "mariadb" else (lambda db=JobsDB(store_file): db)
    open(f"{go_file}.ready.{os.getpid()}", "w").close()                # imports and the store are open: the test writes the go file once every worker has said so, not after a fixed sleep
    while not os.path.exists(go_file):
        time.sleep(0.001)
    claimant = ledger_claim.who(role)
    state = "naive"
    if mode == "claim":
        state, _ = ledger_claim.claim(key, claimant, connect)
        if state != ledger_claim.WON:
            if total:
                open(f"{go_file}.done.{os.getpid()}", "w").close()     # a refused worker still counts as having made its attempt
            print(state)
            return 0
    rows = open(ledger_file).read().split()
    if total:                                                          # LIFE-966: the slow-Notion window lasts until every other worker has made its own claim or lookup, however loaded the machine is
        open(f"{go_file}.done.{os.getpid()}", "w").close()
        deadline = time.monotonic() + 60
        while len(glob.glob(f"{go_file}.done.*")) < total and time.monotonic() < deadline:
            time.sleep(0.005)
    else:
        time.sleep(0.3)                                                # slow Notion: the window between the lookup and the create
    if key not in rows:
        with open(ledger_file, "a") as handle:
            handle.write(key + "\n")
    if mode == "claim":
        ledger_claim.release(key, claimant, connect)
    print(state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
