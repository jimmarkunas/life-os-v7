"""(synthetic): the `followup` stage and the Inbox's read of its payload."""
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

from lifeos.inbox import followup, followup_stage as S, inbox, sources
from lifeos.inbox.followup_store import FollowupStore, FollowupStoreError
from lifeos.inbox.model import InboxError
from lifeos.platform import followup_readers as R
from tests.inbox import fixtures as F
from tests.kit.mysqlite import MySQLite

UTC = timezone.utc
T0 = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
ACCOUNT = "jim@example.com"
BASE = "https://example.test"


def make_store():
    shared = MySQLite()

    @contextmanager
    def connect():
        yield shared
    store = FollowupStore(connect)
    store.ensure_schema()
    return store, shared


def mail(row_id, at, subject="quote"):
    return {"id": row_id, "link": f"{BASE}/mail/{row_id}", "at": at, "subject": subject}


def promise(item):
    if item["source"] != "gmail":
        return None
    return {"kind": "promise", "outcome_key": "Send the signed quote", "owner": "jim", "who": "vendor", "title": item["title"], "checkpoint": "2026-11-20T15:00:00Z",
            "checkpoint_source": "source"}


class Rig:
    """One wiring over one store, with fixtures for every part and a record of what was published and audited."""

    def __init__(self, scopes=None, sources_=("gmail", "jira"), rows=None, classify=promise, captures=None, store=None):
        self.store, self.db = store or make_store()
        self.rows = rows if rows is not None else {"gmail": [mail("m1", "2026-11-16T18:00:00+00:00")], "jira": []}
        self.calls, self.published, self.receipts = [], [], []
        self.now = T0
        scopes = scopes or (S.Scope("personal", ACCOUNT, sources_),)
        grants = tuple(R.Grant(s, sc.workspace, sc.account, f"cred-{s}", T0 + timedelta(days=30)) for sc in scopes for s in sc.sources)
        self.wiring = S.Wiring(self.store, scopes, grants, {s: self._fetch(s) for s in ("gmail", "jira", "calendar", "todoist", "asana")}, self._audit, self.published.append,
                               clock=lambda: self.now, classify=classify, captures=captures or (lambda w, a: []), jira_base=BASE)

    def _fetch(self, source):
        def fetch(credential_ref, since):
            self.calls.append(source)
            rows = self.rows.get(source)
            if isinstance(rows, Exception):
                raise rows
            return list(rows or [])
        return fetch

    def _audit(self, receipt):
        self.receipts.append(receipt)

    def run(self, live=True, at=None):
        if at:
            self.now = at
        return S.run(0, live, self.wiring)


class Stage(unittest.TestCase):
    def test_without_production_wiring_the_stage_is_a_no_op(self):
        off = {"FOLLOWUP_WIRING": "off"}
        self.assertIsNone(S.production_wiring(off))
        with mock.patch.dict("os.environ", off):
            counts = S.run(0, True)
        self.assertEqual((counts["unwired"], counts["scopes"], counts["published"], counts["written"]), (1, 0, 0, 0))

    def test_the_source_log_lines_are_fixed_words_only(self):
        lines = S.source_lines("personal", [{"source": "gmail", "status": "degraded", "reason": "DENY_EXPIRED"}, {"source": "jira", "status": "ok", "reason": "jim@example.com at 10 Main St"}, {"source": "todoist"}])
        self.assertEqual(lines[0], "followup-source personal gmail degraded DENY_EXPIRED")
        self.assertEqual(lines[2], "followup-source personal todoist - -")
        for line in lines:
            self.assertRegex(line, r"^followup-source [A-Za-z0-9_]+ [A-Za-z0-9_-]+ [A-Za-z0-9_-]+ [A-Za-z0-9_-]+$")

    def test_a_dry_run_computes_everything_and_writes_nothing(self):
        rig = Rig()
        counts = rig.run(live=False)
        self.assertEqual((counts["threads"], counts["written"], counts["published"]), (1, 0, 0))
        self.assertEqual((rig.published, rig.db.count("v7_followup_threads"), rig.store.cursor_for("personal", ACCOUNT, "gmail")), ([], 0, None))

    def test_a_live_run_stores_the_thread_publishes_one_payload_and_advances_the_cursor_after_the_write(self):
        rig = Rig()
        counts = rig.run()
        self.assertEqual((counts["written"], counts["published"], counts["sources_ok"]), (1, 1, 2))
        (payload,) = rig.published
        self.assertEqual((payload["component"], payload["state"], payload["workspace"], payload["account"]), ("followup", "PASS", "personal", ACCOUNT))
        self.assertEqual(payload["counts"], {"needs_action_today": 0, "waiting_aging": 0, "upcoming": 1})
        self.assertEqual([(t["title"], t["state"]) for t in payload["threads"]], [("Send the signed quote", "upcoming")])
        self.assertEqual(payload["threads"][0]["id"], rig.store.threads("personal", ACCOUNT)[0]["thread_id"])  # a safe store id, never the label
        self.assertEqual(payload["review"], [])
        self.assertEqual(rig.store.cursor_for("personal", ACCOUNT, "gmail")["cursor"], "2026-11-16T18:00:00+00:00")
        (thread,) = rig.store.threads("personal", ACCOUNT)
        self.assertEqual((thread["klass"], thread["owner"], thread["state"], thread["checkpoint_basis"]), ("owed", "jim", "upcoming", "source"))

    def test_a_second_run_creates_no_duplicate_thread(self):
        rig = Rig()
        rig.run()
        before = rig.store.threads("personal", ACCOUNT)
        counts = rig.run(at=T0 + timedelta(hours=1))
        self.assertEqual((counts["written"], counts["unchanged"]), (0, 1))
        self.assertEqual(rig.store.threads("personal", ACCOUNT), before)  # not even a timestamp or version moved
        self.assertEqual(rig.db.count("v7_followup_threads"), 1)

    def test_a_stored_thread_becomes_due_by_time_alone(self):
        rig = Rig()
        rig.run()
        counts = rig.run(at=datetime(2026, 11, 21, 12, 0, tzinfo=UTC))
        (thread,) = rig.store.threads("personal", ACCOUNT)
        self.assertEqual((counts["written"], thread["state"], thread["version"]), (1, "needs_action", 2))
        self.assertEqual(rig.published[-1]["counts"]["needs_action_today"], 1)

    def test_a_failed_source_keeps_the_last_accepted_state_and_never_advances(self):
        rig = Rig()
        rig.run()
        cursor = rig.store.cursor_for("personal", ACCOUNT, "gmail")
        rig.rows["gmail"] = RuntimeError("canary-secret-text")
        counts = rig.run(at=T0 + timedelta(hours=1))
        payload = rig.published[-1]
        self.assertEqual((payload["state"], counts["sources_degraded"]), ("DEGRADED", 1))
        gmail = next(s for s in payload["sources"] if s["source"] == "gmail")
        self.assertEqual((gmail["status"], gmail["reason"]), ("degraded", "fetch_failed"))
        self.assertEqual([t["title"] for t in payload["threads"]], ["Send the signed quote"])
        self.assertEqual(rig.store.cursor_for("personal", ACCOUNT, "gmail"), cursor)  # the failed source's cursor did not move
        self.assertNotIn("canary", repr(payload))  # an exception text never leaves

    def test_every_source_failing_still_publishes_a_degraded_payload_and_keeps_the_threads(self):
        rig = Rig()
        rig.run()
        rig.rows = {"gmail": RuntimeError("x"), "jira": RuntimeError("y")}
        rig.run(at=T0 + timedelta(hours=1))
        payload = rig.published[-1]
        self.assertEqual((payload["state"], len(payload["threads"])), ("DEGRADED", 1))
        self.assertEqual(rig.db.count("v7_followup_threads"), 1)

    def test_tgp_is_never_read_and_asana_never_degrades_or_reads(self):
        rig = Rig(scopes=(S.Scope("personal", ACCOUNT, ("gmail", "asana")), S.Scope("tgp", "tgp-acct", ("gmail",))))
        counts = rig.run()
        self.assertEqual((counts["refused"], counts["scopes"], counts["published"]), (1, 1, 1))
        self.assertEqual(rig.calls, ["gmail"])  # one Gmail read for Personal
        self.assertEqual(rig.published[0]["state"], "PASS")
        self.assertEqual({r["tenant"] for r in rig.receipts}, {"personal"})

    def test_personal_and_bytalos_stay_separate_and_one_failing_scope_does_not_stop_the_other(self):
        rig = Rig(scopes=(S.Scope("personal", ACCOUNT, ("gmail",)), S.Scope("bytalos", "bytalos@example.com", ("gmail",))))

        def audit(receipt):
            if receipt["tenant"] == "bytalos":
                raise OSError("audit log unavailable")
            rig.receipts.append(receipt)
        rig.wiring = S.Wiring(**{**rig.wiring.__dict__, "audit": audit})
        counts = rig.run()
        self.assertEqual((counts["scopes"], counts["published"], counts.get("item_failed")), (1, 1, 1))
        self.assertEqual([p["workspace"] for p in rig.published], ["personal"])
        self.assertEqual(rig.store.threads("bytalos", "bytalos@example.com"), [])

    def test_proof_the_outcome_ended_closes_the_thread_and_the_same_evidence_never_reopens_it(self):
        rig = Rig()
        rig.run()
        rig.rows["gmail"] = [mail("m2", "2026-11-17T09:00:00+00:00", "done")]

        def ended(item):
            return {"kind": "outcome_ended", "outcome_key": "Send the signed quote", "owner": "jim", "who": "vendor", "title": "done"}
        rig.wiring = S.Wiring(**{**rig.wiring.__dict__, "classify": ended})
        counts = rig.run(at=T0 + timedelta(days=1))
        self.assertEqual(counts["closed"], 1)
        self.assertEqual([t["resolution"] for t in rig.store.threads("personal", ACCOUNT)], ["closed"])
        self.assertEqual(rig.published[-1]["threads"], [])
        rig.wiring = S.Wiring(**{**rig.wiring.__dict__, "classify": promise})
        rig.run(at=T0 + timedelta(days=2))
        self.assertEqual([t["resolution"] for t in rig.store.threads("personal", ACCOUNT)], ["closed"])

    def test_deltas_alone_never_create_a_loop(self):
        rig = Rig(classify=S.observe)
        counts = rig.run()
        self.assertEqual((counts["threads"], counts["written"]), (0, 0))
        self.assertEqual(rig.published[0]["threads"], [])

    def test_a_store_that_cannot_write_gives_a_degraded_payload_with_a_fixed_code(self):
        rig = Rig()

        class Broken:
            def __init__(self, inner):
                self.inner = inner

            def accept(self, *args, **kwargs):
                raise FollowupStoreError("FOLLOWUP_STORE_UNAVAILABLE")

            def __getattr__(self, name):
                return getattr(self.inner, name)
        rig.wiring = S.Wiring(**{**rig.wiring.__dict__, "store": Broken(rig.store)})
        rig.run()
        (payload,) = rig.published
        self.assertEqual((payload["state"], payload["provenance"]["write_error"]), ("DEGRADED", "FOLLOWUP_STORE_UNAVAILABLE"))

    def test_the_stage_is_registered_and_nothing_here_can_write_to_a_source(self):
        from lifeos import run
        self.assertEqual(run.STAGES["followup"].target, ("lifeos.inbox.followup_stage", "run"))
        with open(S.__file__) as one, open(followup.__file__) as two:
            text = one.read() + two.read()
        for word in ("requests", "urllib", "socket", "subprocess", "os.environ"):
            self.assertNotIn(f"import {word}", text)


def payload_for(**kw):
    base = {"component": "followup", "state": "PASS", "workspace": "personal", "account": ACCOUNT, "generated_at": T0.isoformat(), "last_accepted": T0.isoformat(),
            "counts": {}, "threads": [], "review": [], "sources": [], "provenance": {}}
    base.update(kw)
    return base


def thread(key="Send the signed quote", **kw):
    base = {"id": key, "url": f"{BASE}/mail/m1", "title": key, "notes": "checkpoint source-backed; evidence 1", "owner": "vendor", "status": "open", "next_checkpoint": "2026-11-20T15:00:00Z",
            "tz": "America/Chicago", "updated": "2026-11-16T18:00:00+00:00", "correlation_id": None, "state": "upcoming"}
    base.update(kw)
    return base


def read(payload, now=T0, workspace="personal", account=ACCOUNT):
    with mock.patch.object(sources, "LIVE_READS_ENABLED", True):
        return followup.FollowupSource(workspace, account, "jim", open_reader=followup.payload_reader(lambda ref: payload), now=now).fetch("cred")


class PayloadReader(unittest.TestCase):
    def test_the_reader_returns_the_active_threads_without_the_state_key(self):
        out = read(payload_for(threads=[thread()]))
        self.assertEqual(len(out), 1)
        self.assertNotIn("state", out[0])

    def test_a_missing_stale_future_mismatched_or_malformed_payload_is_unavailable(self):
        cases = [(None, "followup_projection_missing"), (payload_for(generated_at=(T0 - timedelta(hours=3)).isoformat()), "evidence_stale"),
                 (payload_for(generated_at=(T0 + timedelta(hours=1)).isoformat()), "evidence_stale"), (payload_for(generated_at=None), "evidence_stale"),
                 (payload_for(workspace="bytalos"), "payload_scope_mismatch"), (payload_for(account="other@example.com"), "payload_scope_mismatch"),
                 (payload_for(state="WEIRD"), "listing_incomplete"), ({"component": "followup"}, "listing_incomplete")]
        for payload, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(InboxError) as caught:
                read(payload)
            self.assertEqual(caught.exception.reason, reason)

    def test_a_degraded_payload_is_never_a_trustworthy_zero(self):
        with self.assertRaises(InboxError) as caught:
            read(payload_for(state="DEGRADED"))
        self.assertEqual(caught.exception.reason, "evidence_degraded")
        out = read(payload_for(state="DEGRADED", threads=[thread()]))
        self.assertIn("DEGRADED, last accepted", out[0]["notes"])  # the last accepted thread stays, visibly degraded

    def test_live_reads_stay_refused_while_the_switch_is_off(self):
        for opener in (followup.refuse_followup, followup.payload_reader(lambda ref: payload_for())):
            with self.assertRaises(InboxError) as caught:
                followup.FollowupSource("personal", ACCOUNT, "jim", open_reader=opener).fetch("cred")
            self.assertEqual(caught.exception.reason, "live_reads_not_authorized")

    def test_an_unrelated_component_keeps_rendering_when_followup_is_degraded(self):
        def source(payload):
            return followup.FollowupSource("personal", "jim-personal", "jim", open_reader=lambda ref: (lambda: payload), now=F.NOW)
        degraded = payload_for(state="DEGRADED", account="jim-personal", generated_at=F.NOW.isoformat())
        result = inbox.build_inbox([source(degraded), F.FakeSource("jira", "personal")], F.principal(sources=("followup", "jira")), F.NOW)
        self.assertEqual({h.source: (h.state, h.reason) for h in result.health}, {"followup": ("unavailable", "evidence_degraded"), "jira": ("ok", "ok")})
        self.assertEqual(len(result.records), 1)

    def test_the_inbox_shows_a_stage_payload_end_to_end(self):
        rig = Rig(scopes=(S.Scope("personal", "jim-personal", ("gmail",)),))
        rig.rows = {"gmail": [mail("m1", "2026-11-14T13:00:00+00:00")]}
        rig.now = F.NOW.astimezone(UTC)
        rig.run()
        payload = rig.published[-1]
        src = followup.FollowupSource("personal", "jim-personal", "jim", open_reader=lambda ref: (lambda: payload), now=F.NOW)
        result = inbox.build_inbox([src], F.principal(sources=("followup",)), F.NOW)
        self.assertEqual([(h.state, h.reason) for h in result.health], [("ok", "ok")])
        self.assertEqual(len(result.records), 1)


class Projection(unittest.TestCase):
    def test_the_projection_reports_each_threads_state_for_the_payload(self):
        evidence = [{"kind": "promise", "outcome_key": "a", "workspace": "personal", "owner": "jim", "who": "v", "title": "A", "link": f"{BASE}/a", "checkpoint": "2026-11-20T15:00:00Z",
                     "checkpoint_source": "source", "observed_at": "2026-11-16T12:00:00+00:00"},
                    {"kind": "waiting_on", "outcome_key": "b", "workspace": "personal", "owner": "external", "who": "v", "title": "B", "link": f"{BASE}/b", "checkpoint": "2026-11-20T15:00:00Z",
                     "checkpoint_source": "source", "observed_at": "2026-11-16T12:00:00+00:00"}]
        result = followup.project(evidence, T0)
        self.assertEqual(result.states, {"a": "upcoming", "b": "waiting_aging"})


if __name__ == "__main__":
    unittest.main()
