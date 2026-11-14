"""LIFE-697 (synthetic): the durable ledger makes replay, concurrency, timeouts and recovery safe across workers and restarts. Workers are threads with their own connection to one sqlite file."""
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import timedelta

from lifeos.inbox import actions, inbox, ledger as L, policy
from lifeos.inbox.actions import ActionService
from tests.inbox import fixtures as F


class Counting:
    """A fake source: counts every call across threads, and can sit in the call so racers overlap."""

    def __init__(self, raises=None, delay=0):
        self.lock, self.calls, self.raises, self.delay = threading.Lock(), [], raises, delay

    def __call__(self, record, action):
        with self.lock:
            self.calls.append((record.record_id, action))
        time.sleep(self.delay)
        if self.raises:
            raise self.raises
        return {"record_id": record.record_id, "revision": "r2", "source_status": sorted(policy.EXPECTED_STATUS[action])[0]}


class Durable(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "ledger.db")
        F.file_ledger(self.path)
        self.who = F.principal()
        self.result = inbox.build_inbox(F.all_sources(), self.who, F.NOW)
        self.record = F.record_for(self.result, "jira", "personal")

    def worker(self, executor=None, synthetic=True):
        """A new worker or a restarted process: fresh memory, the same durable state."""
        return ActionService(F.KEY, executor or Counting(), F.file_ledger(self.path, create=False), synthetic=synthetic)

    def auth(self, nonce="n1", action="accept_uat", record=None):
        return policy.mint(F.KEY, self.who, record or self.record, action, nonce, F.NOW)

    def rows(self):
        with F.FileDb(self.path).raw as raw:
            return {t: raw.execute(f"SELECT * FROM {t}").fetchall() for t in ("v7_inbox_events", "v7_inbox_intents", "v7_inbox_actions")}


class Replay(Durable):
    def test_an_authorization_cannot_be_replayed_after_a_restart(self):                                        # check 1
        executor = Counting()
        first = self.worker(executor).request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW)
        self.assertEqual(first.status, "completed")
        restarted = self.worker(executor)                                                                       # nothing is remembered in memory
        again = restarted.request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW)
        self.assertEqual((again.status, again.replayed), ("completed", True))                                   # the same request: the stored answer
        self.assertEqual(restarted.request(self.who, self.record, "accept_uat", self.auth(), "k2", F.NOW).reason, "authorization_used")      # a new key, the spent nonce
        self.assertEqual(self.worker(executor).request(self.who, self.record, "accept_uat", self.auth("n2"), "k3", F.NOW).reason, "already_applied")   # a new nonce, the same revision
        self.assertEqual(restarted.request(self.who, self.record, "reject_uat", self.auth("n3", "reject_uat"), "k1", F.NOW).reason, "idempotency_key_reused")
        self.assertEqual(len(executor.calls), 1)

    def test_the_two_workspaces_never_block_or_replay_each_other(self):                                        # check 9
        service = self.worker()
        personal, bytalos = F.record_for(self.result, "jira", "personal"), F.record_for(self.result, "jira", "bytalos")
        self.assertEqual(service.request(self.who, personal, "accept_uat", self.auth("n1", record=personal), "kp", F.NOW).status, "completed")
        self.assertEqual(service.request(self.who, bytalos, "accept_uat", self.auth("n2", record=bytalos), "kb", F.NOW).status, "completed")
        personal_only = F.principal(workspaces=("personal",))
        self.assertEqual(service.request(personal_only, bytalos, "accept_uat", self.auth("n3", record=bytalos), "kx", F.NOW).reason, "no_grant")
        self.assertEqual(self.worker().request(self.who, replace(bytalos, workspace="tgp"), "accept_uat", None, "kt", F.NOW).reason, "workspace_not_released")     # check 10
        self.assertEqual(len(self.rows()["v7_inbox_actions"]), 2)                                                # the refusals left no claim behind

    def test_calendar_and_asana_mutations_leave_no_claim(self):                                                # check 11
        service = self.worker()
        for source in ("calendar", "asana"):
            record = F.record_for(self.result, source, "personal")
            for action in ("edit", "complete_task", "approve", "accept_uat"):
                self.assertEqual(service.request(self.who, record, action, None, f"{source}-{action}", F.NOW).reason, "action_not_permitted")
        self.assertEqual(self.rows()["v7_inbox_actions"], [])

    def test_live_execution_is_off_and_a_live_claim_could_not_complete_anyway(self):
        executor = Counting()
        live = self.worker(executor, synthetic=False)
        self.assertEqual(live.request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW).reason, "live_execution_disabled")
        self.assertEqual(live.request(self.who, self.record, "open", None, "k2", F.NOW).status, "completed")     # reading is unaffected
        self.assertEqual(executor.calls, [])
        good = {"record_id": self.record.record_id, "revision": "r2", "source_status": "Done"}
        self.assertTrue(policy.completion_proven(self.record, "accept_uat", good, True))
        self.assertFalse(policy.completion_proven(self.record, "accept_uat", good, False))                       # no source is in VERIFIED_COMPLETION


class Concurrent(Durable):
    def race(self, jobs):
        """Run every job at once, each on its own worker; -> the outcomes."""
        barrier, out = threading.Barrier(len(jobs)), [None] * len(jobs)

        def run(i, job):
            service = job["service"]
            barrier.wait(timeout=5)
            out[i] = service.request(self.who, self.record, "accept_uat", job["auth"], job["key"], F.NOW)
        threads = [threading.Thread(target=run, args=(i, j)) for i, j in enumerate(jobs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        return out

    def test_two_workers_cannot_execute_the_same_claimed_operation_twice(self):                              # check 2
        executor = Counting(delay=0.1)                                                                          # the first claimer sits in the source call while the others arrive
        same = self.race([{"service": self.worker(executor), "auth": self.auth(), "key": "k1"} for _ in range(6)])                                  # one delivery, six workers
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(sum(o.status == "completed" and not o.replayed for o in same), 1)
        self.assertTrue(all(o.replayed or o.status == "completed" for o in same), same)                         # the rest got the stored or in-flight answer, never a second run
        self.setUp()
        executor = Counting(delay=0.1)
        mixed = self.race([{"service": self.worker(executor), "auth": self.auth(f"n{i}"), "key": f"k{i}"} for i in range(6)])                         # six requests, six authorizations, one revision
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(sum(o.status == "completed" for o in mixed), 1)
        self.assertTrue({o.reason for o in mixed if o.status == "refused"} <= {"already_applied", "authorization_used"})
        shared = self.race([{"service": self.worker(executor), "auth": self.auth("same"), "key": f"j{i}"} for i in range(6)])                        # six keys, one authorization, already applied
        self.assertEqual(len(executor.calls), 1)
        self.assertTrue(all(o.status == "refused" for o in shared), shared)


class Uncertain(Durable):
    def test_a_timeout_is_recoverable_and_never_retried_blindly(self):                                       # check 3
        executor = Counting(raises=TimeoutError("source did not answer"))
        service = self.worker(executor)
        out = service.request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW)
        self.assertEqual((out.status, out.reason), ("uncertain", "executor_outcome_unknown"))
        retry = self.worker(executor).request(self.who, self.record, "accept_uat", self.auth("n2"), "k2", F.NOW)
        self.assertEqual((retry.status, retry.reason), ("refused", "outcome_uncertain"))                       # a fresh authorization does not repeat a possibly-successful action
        same = self.worker(executor).request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW)
        self.assertEqual((same.status, same.replayed), ("uncertain", True))
        self.assertEqual(len(executor.calls), 1)

    def test_a_source_confirmed_result_reconciles_an_uncertain_action(self):                                  # check 4
        executor = Counting(raises=TimeoutError("x"))
        self.worker(executor).request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW)
        applied = lambda rid: {"record_id": rid, "revision": "r2", "source_status": "Done"}                    # noqa: E731
        out = self.worker(executor).reconcile("k1", applied, F.NOW)
        self.assertEqual((out.status, out.reason), ("completed", "read_back_verified"))
        self.assertEqual(self.worker(executor).request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW).status, "completed")
        self.assertEqual(self.worker(executor).reconcile("k1", applied, F.NOW).reason, "not_reconcilable")        # settled once
        self.assertEqual(len(executor.calls), 1)

    def test_a_source_that_shows_nothing_applied_frees_the_record_for_a_fresh_authorization(self):
        executor = Counting(raises=TimeoutError("x"))
        self.worker(executor).request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW)
        unchanged = lambda rid: {"record_id": rid, "revision": self.record.revision, "source_status": self.record.source_status}    # noqa: E731
        out = self.worker(executor).reconcile("k1", unchanged, F.NOW)
        self.assertEqual((out.status, out.reason), ("not_applied", "source_unchanged"))
        healthy = Counting()
        self.assertEqual(self.worker(healthy).request(self.who, self.record, "accept_uat", self.auth("n2"), "k2", F.NOW).status, "completed")
        self.assertEqual(self.worker(healthy).request(self.who, self.record, "accept_uat", self.auth(), "k3", F.NOW).reason, "authorization_used")   # the old nonce stays spent

    def test_a_source_result_that_cannot_be_verified_never_completes_and_waits_for_a_person(self):             # check 5
        executor = Counting(raises=TimeoutError("x"))
        self.worker(executor).request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW)
        def broken(rid):                                                                                          # noqa: E306
            raise ConnectionError(F.ERROR_CANARY)
        for read in (broken, lambda rid: None, lambda rid: {"record_id": "other", "revision": "r2", "source_status": "Done"},
                     lambda rid: {"record_id": rid, "revision": "r2", "source_status": "In Review"}, lambda rid: {"record_id": rid, "revision": self.record.revision, "source_status": "Done"}):
            out = self.worker(executor).reconcile("k1", read, F.NOW)
            self.assertEqual((out.status, out.reason), ("uncertain", "needs_human"))
        row = self.rows()["v7_inbox_actions"][0]
        self.assertEqual((row[L.ACTION_COLUMNS.index("state")], row[L.ACTION_COLUMNS.index("needs_human")]), ("uncertain", 1))
        self.assertEqual(len(executor.calls), 1)

    def test_a_dead_worker_is_recovered_without_claiming_success_or_repeating_the_action(self):
        ledger = F.file_ledger(self.path, create=False)
        for key, nonce, record in (("dead-claim", "a", self.record), ("dead-run", "b", F.record_for(self.result, "jira", "bytalos"))):
            fields = {"request_id": L.digest(key), "principal_id": "jim", "source": record.source, "workspace": record.workspace, "account": record.account, "record_id": record.record_id,
                      "action": "accept_uat", "revision": record.revision, "status_before": record.source_status, "nonce": nonce}
            self.assertEqual(ledger.claim_action(fields, F.NOW)[0], "claimed")
        self.assertTrue(ledger.move(L.digest("dead-run"), "claimed", "in_progress", "executing", F.NOW))
        service = self.worker()
        self.assertEqual(service.recover_stale(F.NOW + actions.LEASE - timedelta(seconds=1)), (0, 0))              # not old enough
        self.assertEqual(service.recover_stale(F.NOW + actions.LEASE + timedelta(seconds=1)), (1, 1))
        self.assertEqual(ledger.action(L.digest("dead-claim"))["state"], "expired")                              # it never started: nothing ran
        self.assertEqual(ledger.action(L.digest("dead-run"))["state"], "uncertain")                              # it may have run: reconcile decides
        executor, later = Counting(), F.NOW + actions.LEASE + timedelta(seconds=2)
        fresh = policy.mint(F.KEY, self.who, self.record, "accept_uat", "fresh", later)                          # the expired claim freed the revision: a fresh authorization may run it
        self.assertEqual(self.worker(executor).request(self.who, self.record, "accept_uat", fresh, "k-new", later).status, "completed")
        self.assertEqual(len(executor.calls), 1)


class Hygiene(Durable):
    def test_audit_logs_and_stored_rows_exclude_contents_credentials_and_exception_payloads(self):             # check 12
        failing = Counting(raises=RuntimeError(F.ERROR_CANARY))
        with self.assertLogs("lifeos.inbox", level="INFO") as logs:
            service = self.worker(failing)
            service.request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW)
            service.reconcile("k1", lambda rid: (_ for _ in ()).throw(RuntimeError(F.ERROR_CANARY)), F.NOW)
        stored = repr(self.rows())
        text = "\n".join(logs.output) + repr(service.audit) + stored
        for needle in (F.CANARY, F.ERROR_CANARY, "cred:", "example.test", F.KEY.decode(), F.SAME_TITLE, "k1"):
            self.assertNotIn(needle, text, needle)

    def test_a_database_failure_surfaces_only_a_fixed_code(self):
        def down():
            raise RuntimeError(F.ERROR_CANARY)
        with self.assertRaises(L.LedgerError) as raised:
            L.Ledger(down).claim_event("e", "request", F.NOW)
        self.assertEqual(str(raised.exception), "INBOX_LEDGER_UNAVAILABLE")

    def test_the_same_scenario_twice_gives_the_same_outcomes(self):
        def scenario():
            self.setUp()
            service = self.worker(Counting())
            outs = [service.request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW), service.request(self.who, self.record, "accept_uat", self.auth(), "k1", F.NOW),
                    service.request(self.who, self.record, "accept_uat", self.auth("n2"), "k2", F.NOW)]
            return [(o.status, o.reason, o.replayed) for o in outs], service.audit
        self.assertEqual(scenario(), scenario())


if __name__ == "__main__":
    unittest.main()
