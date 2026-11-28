"""The Todoist guard (client allow-list, approvals, alert-only monitoring, ledger, weekly receipt)."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from lifeos.platform import mcp_access, todoist_governed as T, todoist_guard as G

NOON = datetime(2026, 11, 28, 18, 0, tzinfo=timezone.utc).timestamp()  # noon CT
DAY = 86400


class Approvals:
    """A double: states by request id; records asks, resets and consumes."""

    def __init__(self):
        self.states, self.asked, self.reset_rows, self.consumed = {}, [], [], []

    def state(self, rid, now):
        return self.states.get(rid, ("none", None))

    def ask(self, rid, client, op, summary, now):
        self.asked.append((rid, client, op, summary))
        self.states[rid] = ("pending", {"id": "row-" + rid})

    def reset(self, row, now):
        self.reset_rows.append(row["id"])

    def consume(self, row):
        self.consumed.append(row["id"])


class Clock:
    def __init__(self, now=NOON):
        self.now = now

    def __call__(self):
        return self.now


def make(state_dir=None, clock=None, approvals=None):
    sent = []
    clock = clock or Clock()
    guard = G.Guard(
        sent.append,
        approvals or Approvals(),
        state_dir,
        clock,
        dispatch=lambda send, item, report: send(item),
        report=lambda r: None,
    )
    return guard, sent, clock


def run(guard, op, args, client="claude-chat", effect=None):
    with mock.patch.object(T, "_run", side_effect=effect or (lambda o, a, s: {"task_id": "t1", "verified": True})) as run:
        out = T.run(op, {**args, "_client": client}, {"guard": guard})
        return out, run


class Denied(unittest.TestCase):
    def test_every_todoist_operation_is_denied_by_the_gate_for_a_client_without_a_grant(self):
        access = mcp_access.load()
        outsiders = [c for c in access.clients if c["id"] not in G.CLIENTS]
        self.assertTrue(outsiders)
        for client in outsiders:
            for op in T.OPS:
                tenant, action, account, service = T.route(op, {}, "personal")
                receipts = []
                out = mcp_access.call(access, client, tenant, op, action, account, service, lambda: self.fail("ran"), receipts.append)
                self.assertFalse(out.executed, (client["id"], op))
                self.assertEqual(receipts[-1]["outcome"], "deny")

    def test_every_granted_client_is_allowed_exactly_the_nine_operations_in_personal_only(self):
        access = mcp_access.load()
        for client in access.clients:
            if client["id"] not in G.CLIENTS:
                continue
            for op in T.OPS:
                tenant, action, account, service = T.route(op, {}, "bytalos")
                out = mcp_access.call(access, client, tenant, op, action, account, service, lambda: self.fail("ran"), lambda r: None)
                self.assertFalse(out.executed, (client["id"], op, "bytalos"))


class WithoutTheGuard(unittest.TestCase):
    def test_a_write_runs_unchanged_and_the_client_and_source_never_reach_it(self):
        with mock.patch.object(T, "_run", return_value={"ok": 1}) as run:
            out = T.run("todoist_task_complete", {"task_id": "t1", "_client": "claude-chat", "source": "own"}, {})
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(run.call_args.args[1], {"task_id": "t1"})


class AllowList(unittest.TestCase):
    def test_every_todoist_client_is_in_the_allow_list_and_nobody_else(self):
        import tests.platform.test_mcp_access as access_tests

        self.assertEqual(set(G.CLIENTS), set(access_tests.TODOIST_CLIENTS))
        self.assertEqual(set(G.WRITES), {op for op, (_, params) in T.OPS.items() if op not in T.READS})

    def test_an_unknown_client_is_refused_before_any_write_and_raises_one_alert(self):
        guard, sent, _ = make()
        with self.assertRaises(T.GovernedError) as caught:
            run(guard, "todoist_task_create", {"content": "x", "project_id": "p1"}, client="stranger")
        self.assertEqual(caught.exception.reason, "verb_not_allowed")
        self.assertEqual(len(sent), 1)
        self.assertIn("stranger", sent[0]["subject"])
        with self.assertRaises(T.GovernedError):
            run(guard, "todoist_task_create", {"content": "x", "project_id": "p1"}, client="stranger")
        self.assertEqual(len(sent), 1)  # one alert per client, verb and hour

    def test_a_routine_write_by_a_known_client_runs_with_no_approval_and_is_recorded(self):
        guard, sent, _ = make()
        out, inner = run(guard, "todoist_task_complete", {"task_id": "t1"})
        self.assertEqual(out["verified"], True)
        self.assertEqual(inner.call_args.args[1], {"task_id": "t1"})
        self.assertEqual([(r["c"], r["op"], r["r"]) for r in guard.ledger], [("claude-chat", "todoist_task_complete", "ok")])
        self.assertEqual(sent, [])

    def test_a_read_is_not_guarded_or_recorded_as_a_write(self):
        guard, sent, _ = make()
        run(guard, "todoist_projects", {}, client="stranger")
        self.assertEqual(sent, [])


class Approval(unittest.TestCase):
    def args(self):
        return {"task_id": "t1", "project_id": "p2"}

    def rid(self, op="todoist_task_move", client="claude-chat", args=None):
        return G.request_id(client, op, args or self.args())

    def test_a_move_without_an_approval_asks_once_and_writes_nothing(self):
        approvals = Approvals()
        guard, sent, _ = make(approvals=approvals)
        with self.assertRaises(T.GovernedError) as caught:
            run(guard, "todoist_task_move", self.args())
        self.assertEqual((caught.exception.reason, caught.exception.detail["approval_id"]), ("approval_required", self.rid()))
        self.assertEqual(len(approvals.asked), 1)
        with self.assertRaises(T.GovernedError):
            run(guard, "todoist_task_move", self.args())
        self.assertEqual(len(approvals.asked), 1)  # a repeat while pending does not ask again
        self.assertEqual(guard.ledger, [])

    def test_a_move_with_a_fresh_human_approval_runs_once_and_consumes_it(self):
        approvals = Approvals()
        approvals.states[self.rid()] = ("approved", {"id": "row-a"})
        guard, _, _ = make(approvals=approvals)
        out, inner = run(guard, "todoist_task_move", self.args())
        self.assertEqual(out["verified"], True)
        self.assertEqual(approvals.consumed, ["row-a"])
        approvals.states[self.rid()] = ("used", {"id": "row-a"})
        with self.assertRaises(T.GovernedError):
            run(guard, "todoist_task_move", self.args())
        self.assertEqual(approvals.reset_rows, ["row-a"])  # a used approval goes back to New and asks again
        self.assertEqual(inner.call_count, 1)

    def test_a_stale_approval_is_reset_and_asked_again(self):
        approvals = Approvals()
        approvals.states[self.rid()] = ("stale", {"id": "row-s"})
        guard, _, _ = make(approvals=approvals)
        with self.assertRaises(T.GovernedError) as caught:
            run(guard, "todoist_task_move", self.args())
        self.assertEqual(caught.exception.reason, "approval_required")
        self.assertEqual((approvals.reset_rows, len(approvals.asked)), (["row-s"], 1))

    def test_content_declared_from_outside_needs_approval_even_for_a_routine_verb(self):
        approvals = Approvals()
        guard, _, _ = make(approvals=approvals)
        args = {"content": "from an email", "project_id": "p1", "source": "outside"}
        with self.assertRaises(T.GovernedError) as caught:
            run(guard, "todoist_task_create", args)
        self.assertEqual(caught.exception.reason, "approval_required")
        self.assertIn("from outside", approvals.asked[0][3])
        own, inner = run(guard, "todoist_task_create", {"content": "mine", "project_id": "p1", "source": "own"})
        self.assertEqual(inner.call_args.args[1], {"content": "mine", "project_id": "p1"})

    def test_an_unknown_source_value_is_refused(self):
        guard, _, _ = make()
        with self.assertRaises(T.GovernedError) as caught:
            run(guard, "todoist_task_create", {"content": "x", "project_id": "p1", "source": "trusted"})
        self.assertEqual(caught.exception.reason, "bad_source")

    def test_an_unavailable_approval_path_means_no_write(self):
        approvals = Approvals()
        approvals.state = mock.Mock(side_effect=OSError("down"))
        guard, _, _ = make(approvals=approvals)
        with self.assertRaises(T.GovernedError) as caught:
            run(guard, "todoist_task_move", self.args())
        self.assertEqual(caught.exception.reason, "approval_unavailable")

    def test_the_approval_id_changes_with_the_client_and_the_arguments(self):
        self.assertNotEqual(self.rid(), self.rid(client="chatgpt-chat"))
        self.assertNotEqual(self.rid(), self.rid(args={"task_id": "t1", "project_id": "p3"}))
        self.assertEqual(self.rid(args={**self.args(), "source": "own"}), self.rid())


class Rows(unittest.TestCase):
    """InboxApprovals against a fake Notion client: only a person's recent Done counts."""

    BOT = "bot-1"

    def client(self, status, editor, edited):
        row = {
            "id": "page-1",
            "properties": {"Status": {"select": {"name": status}}},
            "last_edited_by": {"id": editor},
            "last_edited_time": edited,
        }
        notion = mock.Mock()
        notion.query_data_source.return_value = {"results": [row]}
        notion.call_once.side_effect = lambda m, p, b=None, **k: {"id": self.BOT} if p == "/users/me" else {}
        return notion

    def state(self, status="Done", editor="jim", age=60):
        edited = datetime.fromtimestamp(NOON - age, timezone.utc).isoformat().replace("+00:00", "Z")
        return G.InboxApprovals(self.client(status, editor, edited), "src", lambda i: None).state("abc", NOON)[0]

    def test_a_person_marking_done_within_five_minutes_approves(self):
        self.assertEqual(self.state(), "approved")

    def test_the_service_marking_its_own_row_done_never_approves(self):
        self.assertEqual(self.state(editor=self.BOT), "pending")

    def test_an_old_done_is_stale_and_a_new_row_is_pending(self):
        self.assertEqual(self.state(age=900), "stale")
        self.assertEqual(self.state(status="New"), "pending")

    def test_a_dismissed_row_is_used(self):
        self.assertEqual(self.state(status="Dismissed"), "used")

    def test_no_row_is_none(self):
        notion = mock.Mock()
        notion.query_data_source.return_value = {"results": []}
        self.assertEqual(G.InboxApprovals(notion, "src", lambda i: None).state("abc", NOON)[0], "none")

    def test_asking_sends_one_urgent_item_keyed_by_the_request(self):
        sent = []
        G.InboxApprovals(mock.Mock(), "src", sent.append).ask("abc123", "claude-chat", "todoist_task_move", "move", NOON)
        self.assertEqual((sent[0]["key"], sent[0]["urgency"]), ("todoist-approval-abc123", "Now"))
        self.assertIn("within 5 minutes", sent[0]["details"])


class Alerts(unittest.TestCase):
    def test_a_first_write_after_a_quiet_day_alerts_but_never_blocks(self):
        guard, sent, clock = make()
        run(guard, "todoist_task_complete", {"task_id": "t1"})
        self.assertEqual(sent, [])
        clock.now += 25 * 3600
        out, _ = run(guard, "todoist_task_complete", {"task_id": "t2"})
        self.assertEqual(out["verified"], True)
        self.assertEqual(len(sent), 1)
        self.assertIn("quiet", sent[0]["subject"])

    def test_a_write_outside_the_usual_hours_alerts_once_a_day(self):
        guard, sent, clock = make(clock=Clock(datetime(2026, 11, 28, 9, 30, tzinfo=timezone.utc).timestamp()))  # 3:30 AM CT
        run(guard, "todoist_task_complete", {"task_id": "t1"})
        run(guard, "todoist_task_complete", {"task_id": "t2"})
        self.assertEqual([("outside" in s["subject"]) for s in sent], [True])

    def test_more_than_thirty_writes_in_ten_minutes_alerts_once_an_hour_and_still_writes(self):
        guard, sent, clock = make()
        for n in range(G.RATE_N + 5):
            clock.now += 1
            run(guard, "todoist_task_complete", {"task_id": f"t{n}"})
        self.assertEqual(len([s for s in sent if "in 10 minutes" in s["subject"]]), 1)
        self.assertEqual(len(guard.ledger), G.RATE_N + 5)


class LedgerAndReceipt(unittest.TestCase):
    def test_the_ledger_and_state_persist_in_the_state_directory_with_private_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            guard, _, clock = make(folder)
            run(guard, "todoist_task_complete", {"task_id": "t1"})
            again, _, _ = make(folder, clock)
            self.assertEqual([r["op"] for r in again.ledger], ["todoist_task_complete"])
            self.assertEqual(again.last_write, {"claude-chat": NOON})
            for name in ("todoist-writes.jsonl", "todoist-guard.json"):
                self.assertEqual(os.stat(os.path.join(folder, name)).st_mode & 0o777, 0o600)
            with open(os.path.join(folder, "todoist-writes.jsonl")) as handle:
                self.assertNotIn("t1", handle.read())  # no task text or id

    def test_the_weekly_receipt_is_sent_once_by_the_first_call_after_seven_days(self):
        guard, sent, clock = make()
        guard.tick()  # the first call only sets the baseline
        run(guard, "todoist_task_complete", {"task_id": "t1"})
        run(guard, "todoist_task_complete", {"task_id": "t2"}, client="codex")
        clock.now += 6 * DAY
        guard.tick()
        self.assertEqual(sent, [])
        clock.now += 2 * DAY
        guard.tick()
        guard.tick()
        receipts = [s for s in sent if s["key"].startswith("todoist-receipt-")]
        self.assertEqual(len(receipts), 1)
        self.assertIn("2 total", receipts[0]["subject"])
        self.assertIn("claude-chat: task_complete ok 1", receipts[0]["details"])
        self.assertIn("codex: task_complete ok 1", receipts[0]["details"])


class Wiring(unittest.TestCase):
    def test_the_guard_is_off_unless_the_switch_is_exactly_on_and_the_inbox_is_configured(self):
        for environ, inbox in (({}, {"client": object()}), ({"TODOIST_GUARD": "ON"}, {"client": object()}), ({"TODOIST_GUARD": "on"}, {"client": None})):
            state = {}
            G.wire(environ, state, mock.Mock(), inbox)
            self.assertIsNone(state["guard"], (environ, inbox))

    def test_with_the_switch_on_and_the_inbox_the_guard_is_attached(self):
        state = {}
        with mock.patch("lifeos.platform.agent_inbox.locate", return_value="src"):
            G.wire({"TODOIST_GUARD": "on", "STATE_DIRECTORY": "/tmp/x:/y"}, state, mock.Mock(), {"client": object()})
        self.assertIsInstance(state["guard"], G.Guard)
        self.assertEqual(state["guard"].state_dir, "/tmp/x")

    def test_a_missing_inbox_leaves_the_guard_off_and_says_why(self):
        state = {}
        with mock.patch("lifeos.platform.agent_inbox.locate", side_effect=RuntimeError("nope")):
            G.wire({"TODOIST_GUARD": "on"}, state, mock.Mock(), {"client": object()})
        self.assertEqual((state["guard"], state["guard_error"]), (None, "inbox_not_found"))


if __name__ == "__main__":
    unittest.main()
