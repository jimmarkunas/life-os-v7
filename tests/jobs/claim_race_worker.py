"""LIFE-903: one OS process of the two-process Ledger-creation race (tests/jobs/test_ledger_claim.py). Not a test module.

    python -m tests.jobs.claim_race_worker <store file> <ledger file> <key> <role> <go file> <claim|naive>

Every process opens the SAME on-disk store file with its own connection and, once the go file exists, runs the creator protocol against a one-line-per-row `ledger file` standing in for Notion (which has no unique
constraint): `claim` = lifeos.platform.ledger_claim.claim, then look the key up, then create (appended), then release; `naive` = look up, then create, with no claim (the race the ticket forbids). The pause between the
lookup and the create is the window a lookup-then-create race needs. Prints the claim state (or `naive`)."""
import os
import sys
import time

from lifeos.platform import ledger_claim
from tests.jobs.fakes_jobsdb import JobsDB


def main(argv):
    store_file, ledger_file, key, role, go_file, mode = argv
    db = JobsDB(store_file)
    while not os.path.exists(go_file):
        time.sleep(0.001)
    claimant = ledger_claim.who(role)
    state = "naive"
    if mode == "claim":
        state, _ = ledger_claim.claim(key, claimant, lambda: db)
        if state != ledger_claim.WON:
            print(state)
            return 0
    rows = open(ledger_file).read().split()
    time.sleep(0.3)                                                    # slow Notion: the window between the lookup and the create
    if key not in rows:
        with open(ledger_file, "a") as handle:
            handle.write(key + "\n")
    if mode == "claim":
        ledger_claim.release(key, claimant, lambda: db)
    print(state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
