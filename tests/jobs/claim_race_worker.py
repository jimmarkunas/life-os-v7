"""one OS process of the two-process Ledger-creation race (tests/jobs/test_ledger_claim.py)."""

import glob
import os
import sys
import time

from lifeos.platform import ledger_claim
from tests.jobs.fakes_jobsdb import JobsDB


def _mariadb():
    import pymysql

    env = os.environ
    return pymysql.connect(
        host=env["LIFEOS_RACE_DB_HOST"],
        port=int(env.get("LIFEOS_RACE_DB_PORT") or 3306),
        user=env["LIFEOS_RACE_DB_USER"],
        password=env.get("LIFEOS_RACE_DB_PASSWORD", ""),
        database=env["LIFEOS_RACE_DB_NAME"],
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=10,
    )


def main(argv):
    store_file, ledger_file, key, role, go_file, mode = argv[:6]
    total = int(argv[6]) if len(argv) > 6 else 0  # how many workers there are
    connect = _mariadb if store_file == "mariadb" else (lambda db=JobsDB(store_file): db)
    open(f"{go_file}.ready.{os.getpid()}", "w").close()
    while not os.path.exists(go_file):
        time.sleep(0.001)
    claimant = ledger_claim.who(role)
    state = "naive"
    if mode == "claim":
        state, _ = ledger_claim.claim(key, claimant, connect)
        if state != ledger_claim.WON:
            if total:
                open(f"{go_file}.done.{os.getpid()}", "w").close()
            print(state)
            return 0
    rows = open(ledger_file).read().split()
    if total:
        open(f"{go_file}.done.{os.getpid()}", "w").close()
        deadline = time.monotonic() + 60
        while len(glob.glob(f"{go_file}.done.*")) < total and time.monotonic() < deadline:
            time.sleep(0.005)
    else:
        time.sleep(0.3)
    if key not in rows:
        with open(ledger_file, "a") as handle:
            handle.write(key + "\n")
    if mode == "claim":
        ledger_claim.release(key, claimant, connect)
    print(state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
