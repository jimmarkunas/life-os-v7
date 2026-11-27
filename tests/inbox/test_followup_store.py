"""(synthetic): the Followup state store keeps replay, suppression, cursor and failure behavior safe."""

import unittest
import unittest.mock
from contextlib import contextmanager
from datetime import timedelta

from lifeos.inbox import followup_store as S
from lifeos.inbox.ledger import digest
from tests.inbox import fixtures as F
from tests.kit.mysqlite import MySQLite

NOW = F.NOW
ACCOUNT = "jim@example.com"


def make():
    shared = MySQLite()

    @contextmanager
    def connect():
        yield shared

    store = S.FollowupStore(connect)
    store.ensure_schema()
    return store, shared


def item(key="loop-1", **kw):
    base = {
        "identity_key": key,
        "outcome": "Vendor owes the signed quote",
        "klass": "waiting",
        "owner": "external",
        "state": "waiting",
        "checkpoint_at": "2026-11-20T15:00:00Z",
        "checkpoint_basis": "source",
        "confidence": "high",
        "source_refs": ["https://example.test/mail/1"],
        "evidence_fp": digest("evidence-1"),
    }
    return {**base, **kw}


def accept(store, items, cursor="c1", workspace="personal", source="gmail", now=NOW, complete=True, account=ACCOUNT):
    return store.accept(workspace, account, source, items, cursor, now, complete)


class Replay(unittest.TestCase):
    def test_a_replay_of_the_same_batch_changes_nothing(self):
        store, db = make()
        first = accept(store, [item(), item("loop-2")])
        before = store.threads("personal", ACCOUNT)
        again = accept(store, [item(), item("loop-2")], now=NOW + timedelta(hours=1))
        self.assertEqual((first["written"], first["unchanged"]), (2, 0))
        self.assertEqual((again["written"], again["unchanged"]), (0, 2))
        self.assertEqual(store.threads("personal", ACCOUNT), before)  # not even a timestamp or version moved
        self.assertEqual(db.count("v7_followup_threads"), 2)

    def test_changed_content_updates_one_row_and_bumps_the_version(self):
        store, db = make()
        accept(store, [item()])
        accept(store, [item(state="needs_action", owner="jim")], now=NOW + timedelta(hours=1))
        (thread,) = store.threads("personal", ACCOUNT)
        self.assertEqual((thread["state"], thread["owner"], thread["version"]), ("needs_action", "jim", 2))
        self.assertEqual(db.count("v7_followup_threads"), 1)


class Suppression(unittest.TestCase):
    def test_a_dismissed_thread_is_not_resurrected_by_the_same_evidence(self):
        store, _ = make()
        accept(store, [item()])
        self.assertTrue(store.resolve("personal", ACCOUNT, "loop-1", "dismissed", digest("evidence-1"), NOW))
        again = accept(store, [item()], now=NOW + timedelta(hours=1))
        self.assertEqual(again["suppressed"], 1)
        self.assertEqual(store.threads("personal", ACCOUNT)[0]["resolution"], "dismissed")

    def test_new_authoritative_evidence_reopens_a_closed_thread(self):
        store, _ = make()
        accept(store, [item()])
        store.resolve("personal", ACCOUNT, "loop-1", "closed", digest("evidence-1"), NOW)
        reopened = accept(
            store,
            [item(evidence_fp=digest("evidence-2"), state="needs_action", owner="jim")],
            now=NOW + timedelta(hours=1),
        )
        self.assertEqual(reopened["written"], 1)
        (thread,) = store.threads("personal", ACCOUNT)
        self.assertEqual((thread["resolution"], thread["state"]), ("open", "needs_action"))


class Cursor(unittest.TestCase):
    def test_the_cursor_advances_only_after_an_accepted_write(self):
        store, _ = make()
        self.assertIsNone(store.cursor_for("personal", ACCOUNT, "gmail"))
        out = accept(store, [item()], cursor="c1")
        self.assertTrue(out["cursor_advanced"])
        self.assertEqual(store.cursor_for("personal", ACCOUNT, "gmail")["cursor"], "c1")
        partial = accept(store, [item("loop-9")], cursor="c2", complete=False)  # a failed, denied or truncated read
        self.assertFalse(partial["cursor_advanced"])
        self.assertEqual(store.cursor_for("personal", ACCOUNT, "gmail")["cursor"], "c1")

    def test_a_bad_item_rejects_the_whole_batch_before_any_write(self):
        store, db = make()
        with self.assertRaises(S.FollowupRejected) as bad:
            accept(store, [item(), item("loop-2", klass="made_up")], cursor="c9")
        self.assertEqual(str(bad.exception), "class_unknown")
        self.assertEqual((db.count("v7_followup_threads"), db.count("v7_followup_checkpoints")), (0, 0))

    def test_a_failed_write_keeps_the_known_good_state_and_cursor(self):
        store, db = make()
        accept(store, [item()], cursor="c1")
        good = store.threads("personal", ACCOUNT)
        db.fail_on = ("UPDATE v7_followup_threads", 1)
        with self.assertRaises(S.FollowupStoreError) as failed:
            accept(store, [item(state="needs_action", owner="jim"), item("loop-2")], cursor="c2")
        self.assertEqual(str(failed.exception), "FOLLOWUP_STORE_UNAVAILABLE")
        self.assertEqual(store.threads("personal", ACCOUNT), good)  # the new row was rolled back too
        self.assertEqual(store.cursor_for("personal", ACCOUNT, "gmail")["cursor"], "c1")

    def test_a_failed_cursor_write_rolls_the_threads_back(self):
        store, db = make()
        db.fail_on = ("INSERT INTO v7_followup_checkpoints", 1)
        with self.assertRaises(S.FollowupStoreError):
            accept(store, [item()], cursor="c1")
        self.assertEqual((db.count("v7_followup_threads"), db.count("v7_followup_checkpoints")), (0, 0))

    def test_a_readback_mismatch_rolls_back_and_does_not_advance(self):
        store, db = make()
        accept(store, [item()], cursor="c1")
        original = S.FollowupStore._upsert

        def lying(self, cursor, row, stamped, out):
            wanted = original(self, cursor, row, stamped, out)
            return {**wanted, "owner": "ambiguous"} if wanted else wanted

        S.FollowupStore._upsert = lying
        self.addCleanup(setattr, S.FollowupStore, "_upsert", original)
        with self.assertRaises(S.FollowupStoreError) as bad:
            accept(store, [item(state="needs_action")], cursor="c2")
        self.assertEqual(str(bad.exception), "FOLLOWUP_READBACK_MISMATCH")
        self.assertEqual(store.threads("personal", ACCOUNT)[0]["state"], "waiting")
        self.assertEqual(store.cursor_for("personal", ACCOUNT, "gmail")["cursor"], "c1")
        self.assertEqual(db.count("v7_followup_threads"), 1)


class Isolation(unittest.TestCase):
    def test_personal_and_bytalos_never_see_or_share_a_thread(self):
        store, db = make()
        accept(store, [item()], workspace="personal")
        accept(store, [item(source_refs=["https://example.test/mail/2"])], workspace="bytalos")
        self.assertEqual(db.count("v7_followup_threads"), 2)  # the same identity key is two threads
        self.assertNotEqual(S.thread_id("personal", ACCOUNT, "loop-1"), S.thread_id("bytalos", ACCOUNT, "loop-1"))
        self.assertEqual(
            [t["source_refs"] for t in store.threads("personal", ACCOUNT)], [["https://example.test/mail/1"]]
        )
        self.assertEqual(
            [t["source_refs"] for t in store.threads("bytalos", ACCOUNT)], [["https://example.test/mail/2"]]
        )

    def test_a_resolve_or_override_in_one_workspace_cannot_touch_the_other(self):
        store, _ = make()
        accept(store, [item()], workspace="personal")
        accept(store, [item()], workspace="bytalos")
        self.assertTrue(store.resolve("bytalos", ACCOUNT, "loop-1", "closed", digest("x"), NOW))
        self.assertEqual(store.threads("personal", ACCOUNT)[0]["resolution"], "open")
        self.assertFalse(
            store.set_override("personal", "other@example.com", "loop-1", "2026-12-01T15:00:00Z", "jim", NOW)
        )  # a different account owns no such thread
        self.assertEqual(store.cursor_for("bytalos", ACCOUNT, "gmail"), store.cursor_for("bytalos", ACCOUNT, "gmail"))

    def test_tgp_and_unknown_workspaces_are_refused_everywhere(self):
        store, db = make()
        for call in (
            lambda w: accept(store, [item()], workspace=w),
            lambda w: store.threads(w, ACCOUNT),
            lambda w: store.resolve(w, ACCOUNT, "k", "closed", digest("x"), NOW),
            lambda w: store.set_override(w, ACCOUNT, "k", "2026-12-01T15:00:00Z", "jim", NOW),
            lambda w: store.cursor_for(w, ACCOUNT, "gmail"),
        ):
            for workspace, reason in (("tgp", "workspace_not_released"), ("acme", "workspace_unknown")):
                with self.assertRaises(S.FollowupRejected) as refused:
                    call(workspace)
                self.assertEqual(str(refused.exception), reason)
        self.assertEqual((db.count("v7_followup_threads"), db.count("v7_followup_checkpoints")), (0, 0))

    def test_asana_and_unknown_sources_are_not_in_the_first_slice(self):
        store, _ = make()
        for source in ("asana", "slack"):
            with self.assertRaises(S.FollowupRejected) as refused:
                accept(store, [item()], source=source)
            self.assertEqual(str(refused.exception), "source_not_approved")


class Override(unittest.TestCase):
    def test_a_jim_override_wins_and_is_visibly_an_override(self):
        store, db = make()
        accept(store, [item()])
        self.assertTrue(store.set_override("personal", ACCOUNT, "loop-1", "2026-12-05T15:00:00Z", "jim", NOW))
        (thread,) = store.threads("personal", ACCOUNT)
        self.assertEqual(
            (thread["effective_checkpoint"], thread["checkpoint_basis_effective"]), ("2026-12-05T15:00:00Z", "override")
        )
        self.assertEqual(thread["checkpoint_at"], "2026-11-20T15:00:00Z")  # the source's own date is untouched
        accept(store, [item(checkpoint_at="2026-11-22T15:00:00Z")], now=NOW + timedelta(hours=1))
        self.assertEqual(
            store.threads("personal", ACCOUNT)[0]["effective_checkpoint"], "2026-12-05T15:00:00Z"
        )  # a new source date does not undo it
        self.assertEqual(db.count("v7_followup_overrides"), 1)

    def test_a_suggested_checkpoint_stays_visibly_suggested_and_needs_a_basis(self):
        store, _ = make()
        accept(store, [item(checkpoint_basis="suggested")])
        self.assertEqual(store.threads("personal", ACCOUNT)[0]["checkpoint_basis_effective"], "suggested")
        with self.assertRaises(S.FollowupRejected) as bad:
            accept(store, [item("loop-2", checkpoint_basis="")])
        self.assertEqual(str(bad.exception), "checkpoint_basis_mismatch")


class Storage(unittest.TestCase):
    def test_only_bounded_fields_and_links_are_stored_never_a_payload(self):
        store, _ = make()
        for bad, reason in (
            ({"source_refs": ["http://insecure.example.test/x"]}, "source_ref_invalid"),
            ({"outcome": "x" * 121}, "outcome_invalid"),
            ({"source_refs": []}, "source_refs_invalid"),
            ({"evidence_fp": "short"}, "evidence_fp_invalid"),
            ({"checkpoint_at": "2026-11-20 15:00"}, "checkpoint_not_utc"),
        ):
            with self.assertRaises(S.FollowupRejected) as refused:
                accept(store, [item(**bad)])
            self.assertEqual(str(refused.exception), reason)
        columns = {
            c for table in S.SCHEMA[:4] for c in table.split("(", 1)[1].lower().split()
        }  # the four state tables never hold a source payload
        for forbidden in ("body", "snippet", "payload", "token", "password", "secret"):
            self.assertNotIn(forbidden, columns)
        payload_table = S.SCHEMA[4].split("(", 1)[1].lower()
        for forbidden in ("body", "snippet", "token", "password", "secret"):
            self.assertNotIn(forbidden, payload_table)
        self.assertEqual(
            [c.split()[0] for c in payload_table.split("primary key")[0].split(",") if c.strip()],
            ["scope_key", "payload", "generated_at", "state"],
        )

    def test_the_five_tables_are_defined_and_the_schema_is_never_run_by_import(self):
        self.assertEqual(len(S.SCHEMA), 5)
        self.assertEqual(
            S.TABLES,
            (
                "v7_followup_threads",
                "v7_followup_checkpoints",
                "v7_followup_overrides",
                "v7_followup_suppressions",
                "v7_followup_payload",
            ),
        )
        for statement, table in zip(S.SCHEMA, S.TABLES, strict=True):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table} ", statement)

    def test_a_store_that_cannot_connect_gives_the_fixed_code_without_the_driver_message(self):
        @contextmanager
        def broken():
            raise RuntimeError("CANARY-ERROR-5521 host db.internal")
            yield

        with self.assertRaises(S.FollowupStoreError) as failed:
            S.FollowupStore(broken).threads("personal", ACCOUNT)
        self.assertEqual(str(failed.exception), "FOLLOWUP_STORE_UNAVAILABLE")
        self.assertNotIn("CANARY", str(failed.exception))
        self.assertIsNone(failed.exception.__cause__)


def payload(workspace="personal", account=ACCOUNT, state="PASS", **kw):
    base = {
        "component": "followup",
        "state": state,
        "workspace": workspace,
        "account": account,
        "generated_at": "2026-11-16T20:00:00+00:00",
        "counts": {"needs_me": 1},
        "threads": [],
        "review": [],
    }
    return {**base, **kw}


class Payload(unittest.TestCase):
    def test_a_payload_is_saved_replaced_and_read_back(self):
        store, db = make()
        self.assertIsNone(store.load_payload("personal", ACCOUNT))
        self.assertTrue(store.save_payload("personal", ACCOUNT, payload()))
        self.assertEqual(store.load_payload("personal", ACCOUNT), payload())
        newer = payload(state="DEGRADED", generated_at="2026-11-16T21:00:00+00:00", counts={"needs_me": 2})
        self.assertTrue(store.save_payload("personal", ACCOUNT, newer))
        self.assertEqual(store.load_payload("personal", ACCOUNT), newer)
        self.assertEqual(db.count("v7_followup_payload"), 1)

    def test_the_write_is_one_statement(self):
        store, db = make()
        seen = []
        original = db.cursor

        def spy():
            cursor = original()
            run = cursor.execute

            def execute(sql, args=()):
                seen.append(sql.split()[0])
                return run(sql, args)

            cursor.execute = execute
            return cursor

        db.cursor = spy
        store.save_payload("personal", ACCOUNT, payload())
        self.assertEqual(seen, ["INSERT", "SELECT"])  # one write statement, then the read-back

    def test_scopes_never_share_a_row_and_one_scope_never_reads_another(self):
        store, db = make()
        store.save_payload("personal", ACCOUNT, payload())
        store.save_payload("bytalos", ACCOUNT, payload(workspace="bytalos", state="DEGRADED"))
        store.save_payload(
            "personal", "other@example.com", payload(account="other@example.com", counts={"needs_me": 9})
        )
        self.assertEqual(db.count("v7_followup_payload"), 3)
        self.assertEqual(store.load_payload("personal", ACCOUNT)["state"], "PASS")
        self.assertEqual(store.load_payload("bytalos", ACCOUNT)["state"], "DEGRADED")
        self.assertEqual(store.load_payload("personal", "other@example.com")["counts"], {"needs_me": 9})
        self.assertNotEqual(S.scope_key("personal", ACCOUNT), S.scope_key("bytalos", ACCOUNT))
        self.assertLessEqual(len(S.scope_key("bytalos", "a" * 80)), 190)  # fits the VARCHAR(190) key

    def test_tgp_unknown_workspaces_and_bad_payloads_are_refused_before_a_write(self):
        store, db = make()
        for workspace in ("tgp", "unknown", "nobody"):
            with self.assertRaises(S.FollowupRejected):
                store.save_payload(workspace, ACCOUNT, payload(workspace=workspace))
            with self.assertRaises(S.FollowupRejected):
                store.load_payload(workspace, ACCOUNT)
        for bad, reason in (
            (payload(workspace="bytalos"), "payload_scope_mismatch"),
            (payload(account="x@example.com"), "payload_scope_mismatch"),
            (payload(state="OK"), "payload_state_invalid"),
            (payload(generated_at=""), "payload_time_invalid"),
            (payload(generated_at="x" * 33), "payload_time_invalid"),
            (payload(generated_at=5), "payload_time_invalid"),
            ("not a dict", "payload_invalid"),
            (payload(threads=[object()]), "payload_invalid"),
            (payload(blob="x" * 1_000_001), "payload_too_large"),
        ):
            with self.assertRaises(S.FollowupRejected) as refused:
                store.save_payload("personal", ACCOUNT, bad)
            self.assertEqual(str(refused.exception), reason)
        self.assertEqual(db.count("v7_followup_payload"), 0)  # nothing was written by any refusal

    def test_a_row_that_disagrees_with_its_own_columns_is_a_fixed_mismatch(self):
        store, db = make()
        store.save_payload("personal", ACCOUNT, payload())
        cursor = db.cursor()
        cursor.execute(
            "UPDATE v7_followup_payload SET state=%s WHERE scope_key=%s", ("DEGRADED", S.scope_key("personal", ACCOUNT))
        )
        with self.assertRaises(S.FollowupStoreError) as failed:
            store.load_payload("personal", ACCOUNT)
        self.assertEqual(str(failed.exception), "FOLLOWUP_READBACK_MISMATCH")
        cursor.execute(
            "UPDATE v7_followup_payload SET payload=%s WHERE scope_key=%s",
            ("not json", S.scope_key("personal", ACCOUNT)),
        )
        with self.assertRaises(S.FollowupStoreError) as failed:
            store.load_payload("personal", ACCOUNT)
        self.assertEqual(str(failed.exception), "FOLLOWUP_READBACK_MISMATCH")

    def test_a_save_whose_read_back_differs_is_a_fixed_mismatch_and_a_dead_store_a_fixed_code(self):
        store, _ = make()
        with unittest.mock.patch.object(S.FollowupStore, "load_payload", return_value=payload(counts={"needs_me": 99})):
            with self.assertRaises(S.FollowupStoreError) as failed:
                store.save_payload("personal", ACCOUNT, payload())
        self.assertEqual(str(failed.exception), "FOLLOWUP_READBACK_MISMATCH")

        @contextmanager
        def broken():
            raise RuntimeError("CANARY-ERROR-7733 host db.internal")
            yield

        with self.assertRaises(S.FollowupStoreError) as failed:
            S.FollowupStore(broken).save_payload("personal", ACCOUNT, payload())
        self.assertEqual(str(failed.exception), "FOLLOWUP_STORE_UNAVAILABLE")
        self.assertNotIn("CANARY", str(failed.exception))


if __name__ == "__main__":
    unittest.main()
