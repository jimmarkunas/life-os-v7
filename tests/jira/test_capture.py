"""Jira decisions into the shared memory."""
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone

from lifeos.jira import capture as C

NOW = datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)


def adf(*paragraphs):
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs]}


class FakeJira:
    def __init__(self, comments=None, issues=("LIFE-1",)):
        self.comments, self.issues, self.calls = comments or {}, issues, []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "/rest/api/3/search/jql":
            return {"issues": [{"key": k} for k in self.issues], "isLast": True}
        key = path.split("/")[5]
        return {"comments": self.comments.get(key, [])}


class FakeMemory:
    def __init__(self, refuse=()):
        self.log, self.refuse, self.n = [], set(refuse), 0

    def store(self, text, source):
        self.log.append(("store", text, source))
        if any(word in text for word in self.refuse):
            return {"ok": False, "error": "sensitive_refused"}
        self.n += 1
        return {"ok": True, "data": {"id": f"m{self.n}"}}

    def correct(self, memory_id, text, source):
        self.log.append(("correct", memory_id, text))
        self.n += 1
        return {"ok": True, "data": {"id": f"m{self.n}"}}

    def revoke(self, memory_id):
        self.log.append(("revoke", memory_id))
        return {"ok": True}


class FakeState:
    def __init__(self):
        self.c, self.i = {}, {}

    def comment(self, cid):
        return self.c.get(int(cid))

    def put_comment(self, cid, payload, now):
        self.c[int(cid)] = payload

    def issue(self, key):
        return self.i.get(key, [])

    def put_issue(self, key, ids, now):
        self.i[key] = sorted(ids)


def comment(cid, *paragraphs):
    return {"id": str(cid), "body": adf(*paragraphs)}


class CaptureTests(unittest.TestCase):
    def run_once(self, jira, memory, state, live=True):
        total = {"issues": 0, "decisions": 0, "stored": 0, "corrected": 0, "revoked": 0, "unchanged": 0, "filtered": 0, "failed": 0, "deferred": 0, "would_write": 0, "writes": 0, "why": {}}
        C.capture_issue(jira, "LIFE-1", memory, state, live, NOW, total)
        return total

    def test_what_counts_as_a_decision(self):
        yes = ["[approvals] Jim chat Nov 22 8:09 PM CT: 867 nightly backup timer (B1)", "Jim's decision, Nov 21: keep the retained Honcho.", "Jim, Nov 25 2026, 5:45 PM CT: \"Yes to all three.\" More words."]
        for text in yes:
            self.assertTrue(C.decision_text(adf(text)), text)
        for text in ("Claimed by MC02. Working the targets in this order.", "Merged PR 1565. Tests green.", "Everything else is done and live. Jim's decision is not here."):
            self.assertEqual(C.decision_text(adf(text)), "", text)
        self.assertLessEqual(len(C.decision_text(adf("[approvals] " + "x" * 900))), C.TEXT_MAX)
        self.assertEqual(C.decision_text(adf("Intro", "[approvals] the line", "more")), "[approvals] the line")

    def test_a_decision_is_stored_once_with_its_ticket_and_comment_and_a_repeat_does_nothing(self):
        jira, memory, state = FakeJira({"LIFE-1": [comment(11, "[approvals] Jim chat: 988 wireframe ok")]}), FakeMemory(), FakeState()
        total = self.run_once(jira, memory, state)
        self.assertEqual((total["decisions"], total["stored"]), (1, 1))
        kind, text, source = memory.log[0]
        self.assertIn("LIFE-1, comment 11", text)
        self.assertEqual(source, "jira-decision:LIFE-1#11")
        again = self.run_once(jira, memory, state)
        self.assertEqual((again["stored"], again["unchanged"], len(memory.log)), (0, 1, 1))

    def test_an_edited_comment_corrects_its_item_and_a_deleted_one_revokes_it(self):
        memory, state = FakeMemory(), FakeState()
        self.run_once(FakeJira({"LIFE-1": [comment(11, "[approvals] original")]}), memory, state)
        edited = self.run_once(FakeJira({"LIFE-1": [comment(11, "[approvals] edited")]}), memory, state)
        self.assertEqual((edited["corrected"], memory.log[-1][0], memory.log[-1][1]), (1, "correct", "m1"))
        gone = self.run_once(FakeJira({"LIFE-1": []}), memory, state)
        self.assertEqual((gone["revoked"], memory.log[-1]), (1, ("revoke", "m2")))
        self.assertEqual(state.comment(11)["state"], "revoked")
        self.assertEqual(self.run_once(FakeJira({"LIFE-1": []}), memory, state)["revoked"], 0)  # not revoked twice

    def test_a_sensitive_decision_is_counted_never_stored_and_not_retried_every_hour(self):
        memory, state = FakeMemory(refuse=("SECRETWORD",)), FakeState()
        jira = FakeJira({"LIFE-1": [comment(11, "[approvals] the card is SECRETWORD")]})
        total = self.run_once(jira, memory, state)
        self.assertEqual((total["filtered"], total["stored"], state.comment(11)["state"], state.comment(11)["memory_id"]), (1, 0, "filtered", ""))
        self.assertNotIn("SECRETWORD", repr(total))
        again = self.run_once(jira, memory, state)
        self.assertEqual((again["unchanged"], again["filtered"], len(memory.log)), (1, 0, 1))  # not tried again every hour
        jira.comments["LIFE-1"] = [comment(11, "[approvals] the card was removed")]
        fixed = self.run_once(jira, memory, state)
        self.assertEqual((fixed["stored"], len(memory.log)), (1, 2))  # an edited comment is judged again

    def test_comments_that_are_not_decisions_are_ignored_and_the_write_cap_defers_the_rest(self):
        notes = [comment(n, f"Claimed by MC02 number {n}") for n in range(20, 25)]
        decisions = [comment(100 + n, f"[approvals] Jim chat: item {n}") for n in range(C.WRITE_CAP + 5)]
        memory, state = FakeMemory(), FakeState()
        total = self.run_once(FakeJira({"LIFE-1": notes + decisions}), memory, state)
        self.assertEqual((total["decisions"], total["stored"], total["deferred"], len(memory.log)), (C.WRITE_CAP + 5, C.WRITE_CAP, 5, C.WRITE_CAP))
        second = self.run_once(FakeJira({"LIFE-1": notes + decisions}), memory, state)
        self.assertEqual((second["stored"], second["unchanged"]), (5, C.WRITE_CAP))  # the deferred five land on the next hour

    def test_a_dry_run_writes_nothing_anywhere(self):
        memory = FakeMemory()
        total = self.run_once(FakeJira({"LIFE-1": [comment(11, "[approvals] Jim chat: x")]}), memory, None, live=False)
        self.assertEqual((total["would_write"], total["stored"], memory.log), (1, 0, []))

    def test_without_the_credential_a_live_run_is_a_fixed_word_and_reads_nothing(self):
        jira = FakeJira({"LIFE-1": [comment(11, "[approvals] x")]})
        out = C.run(5, True, environ={}, jira=jira)
        self.assertEqual((out["why"], out["stored"], jira.calls), ({"capture_not_configured": 1}, 0, []))

    def test_a_repeated_state_write_loss_is_a_fixed_warning_and_stops_this_run(self):
        from unittest.mock import patch

        class FailedState(FakeState):
            def put_comment(self, cid, payload, now):
                return False

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        jira = FakeJira({"LIFE-1": [comment(11, "[approvals] first"), comment(12, "[approvals] second")]})
        memory = FakeMemory()
        with patch.object(C, "State", lambda connection: FailedState()):
            out = C.run(5, True, environ={"LIFEOS_MCP_JIRA_CAPTURE": "configured"}, jira=jira,
                        memory=memory, connect=Connection)
        self.assertEqual((out["warnings"], out["failed"], out["why"]), (1, 0, {"capture_state_unavailable": 1}))
        self.assertEqual((out["stored"], len(memory.log)), (1, 1))

    def test_only_the_life_project_and_the_last_two_hours_are_read(self):
        jira = FakeJira()
        C.run(5, False, environ={}, jira=jira, memory=FakeMemory(), connect=None)
        jql = jira.calls[0][1]["jql"]
        self.assertEqual(jql, "project = LIFE AND updated >= -2h ORDER BY updated DESC")


class MemoryClient(unittest.TestCase):
    def test_the_client_sends_the_governed_operations_as_the_bearer_and_maps_failures_to_a_fixed_word(self):
        import json
        sent = []

        class Reply:
            def __init__(self, doc):
                self.doc = doc

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({"result": {"content": [{"text": json.dumps(self.doc)}]}}).encode()

        def opener(request, timeout=0):
            sent.append((request.full_url, request.headers["Authorization"], json.loads(request.data)["params"]["arguments"]))
            return Reply({"ok": True, "data": {"id": "m1"}})

        memory = C.Memory("https://example.invalid/mcp", "lm1.jira-capture.TESTSECRET", opener)
        self.assertTrue(memory.store("a decision", "jira-decision:LIFE-1#1")["ok"])
        self.assertEqual(sent[0][2], {"operation": "memory_store", "arguments": {"workspace": "Personal", "text": "a decision", "source": "jira-decision:LIFE-1#1"}})
        self.assertEqual(sent[0][1], "Bearer lm1.jira-capture.TESTSECRET")
        memory.revoke("m1")
        self.assertEqual(sent[1][2]["operation"], "memory_revoke")

        def broken(request, timeout=0):
            raise OSError("down")
        self.assertEqual(C.Memory("https://example.invalid/mcp", "t", broken).store("x", "s"), {"ok": False, "error": "capture_unreachable"})

        calls = []

        def counting(request, timeout=0):
            calls.append(1)
            raise OSError("down")
        down = C.Memory("https://example.invalid/mcp", "t", counting)
        down.store("x", "s"), down.store("y", "s")
        self.assertEqual((len(calls), down.down), (1, "OSError"))  # the second call fails at once

    @staticmethod
    def _reply(doc, wrapped):
        import json

        class R:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({"result": {"content": [{"text": json.dumps(doc)}]}} if wrapped else doc).encode()
        return R()

    def test_a_timeout_retries_once_through_the_public_address(self):
        import unittest.mock as mock

        def timing_out(request, timeout=0):
            raise urllib.error.URLError(TimeoutError("timed out"))
        seen = []
        with mock.patch.object(urllib.request, "urlopen", timing_out), \
                mock.patch.object(C, "public_address", lambda host: "203.0.113.9"), \
                mock.patch.object(C, "pinned_open", lambda address: (seen.append(address), lambda request, timeout=0: self._reply({"ok": True, "data": {"id": "m9"}}, True))[1]):
            memory = C.Memory("https://example.invalid/mcp", "t", urllib.request.urlopen)
            self.assertTrue(memory.store("x", "s")["ok"])
            self.assertTrue(memory.store("y", "s")["ok"])
        self.assertEqual(seen, ["203.0.113.9"])

    def test_public_address_reads_the_first_a_record(self):
        reply = self._reply({"Answer": [{"type": 5, "data": "alias."}, {"type": 1, "data": "198.51.100.7"}]}, False)
        self.assertEqual(C.public_address("h.example", lambda request, timeout=0: reply), "198.51.100.7")

    def test_a_state_write_that_lost_its_session_reconnects_and_runs_once_more(self):
        class OperationalError(Exception):
            pass

        class Conn:
            reconnects = 0

            def _reconnect(self, error):
                self.reconnects += 1

        class Store:
            def __init__(self, failures):
                self.failures, self.saved = failures, []

            def save(self, connection, key, payload):
                if self.failures:
                    self.failures -= 1
                    raise OperationalError(2013, "Lost connection to MySQL server during query")
                self.saved.append(key)

        state = C.State.__new__(C.State)
        state.connection = Conn()
        store = Store(1)
        self.assertTrue(state._write(store, 7, {"state": "stored"}, datetime(2026, 11, 26, tzinfo=timezone.utc)))
        self.assertEqual((store.saved, state.connection.reconnects), ([7], 1))
        self.assertFalse(state._write(Store(2), 7, {}, datetime(2026, 11, 26, tzinfo=timezone.utc)))


if __name__ == "__main__":
    unittest.main()
