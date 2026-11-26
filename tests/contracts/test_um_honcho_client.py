"""LIFE-1003: the Honcho v3 client against a scripted opener: no network, the token never appears in a repr or an error, spending is armed per message, tenants are answered by their own peer."""
import importlib.util
import io
import json
import pathlib
import sys
import unittest
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


c = load("um_honcho_client")
guard = c.guard
TOKEN = "tok-secret-value-1234567890"


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    def __init__(self, replies=None, error=None):
        self.log, self.replies, self.error = [], list(replies or []), error

    def __call__(self, req, timeout=None):
        self.log.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None, req.get_header("Authorization")))
        if self.error:
            raise self.error
        return Answer(json.dumps(self.replies.pop(0) if self.replies else {}).encode())


def make(replies=None, error=None):
    op = Opener(replies, error)
    return c.Honcho(c.Http("http://127.0.0.1:8000", TOKEN, op), "um-t2-fixture-v1"), op


class Safety(unittest.TestCase):
    def test_the_token_is_never_in_a_repr_or_an_error(self):
        h, _ = make(error=urllib.error.HTTPError("u", 401, "no", {}, None))
        self.assertNotIn(TOKEN, repr(h.http))
        with self.assertRaises(guard.Unauthorized) as caught:
            h.queue()
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_401_403_are_unauthorized_other_failures_are_honcho_errors(self):
        for code, kind in ((401, guard.Unauthorized), (403, guard.Unauthorized), (500, guard.HonchoError)):
            h, _ = make(error=urllib.error.HTTPError("u", code, "x", {}, None))
            with self.assertRaises(kind):
                h.queue()
        h, _ = make(error=urllib.error.URLError("down"))
        with self.assertRaises(guard.HonchoError):
            h.queue()

    def test_the_other_workspace_probe_is_true_only_when_refused(self):
        h, _ = make(error=urllib.error.HTTPError("u", 403, "x", {}, None))
        self.assertTrue(h.probe_other_workspace("personal"))
        h, _ = make(replies=[{"pending_work_units": 0}])
        self.assertFalse(h.probe_other_workspace("personal"))


class Calls(unittest.TestCase):
    def test_the_workspace_is_created_with_every_spending_switch_off(self):
        h, op = make()
        h.create_workspace()
        method, url, body, auth = op.log[0]
        self.assertEqual((method, url.endswith("/v3/workspaces"), body["id"]), ("POST", True, "um-t2-fixture-v1"))
        self.assertEqual(body["configuration"], {"reasoning": {"enabled": False}, "dream": {"enabled": False}, "summary": {"enabled": False}})
        self.assertEqual(auth, "Bearer " + TOKEN)

    def test_only_armed_messages_carry_the_reasoning_switch_and_all_carry_the_fixture_id(self):
        h, op = make()
        batch = [{"peer_id": "t-alpha", "content": "FAKE-REQ-001: x", "source_ref": "FAKE-REQ-001"}]
        h.post_messages(batch, armed=False)
        h.post_messages(batch, armed=True)
        off, on = op.log[0][2]["messages"][0], op.log[1][2]["messages"][0]
        self.assertNotIn("configuration", off)
        self.assertEqual(on["configuration"], {"reasoning": {"enabled": True}})
        self.assertEqual((off["metadata"], on["metadata"]), ({"source_ref": "FAKE-REQ-001"},) * 2)

    def test_queue_idle_needs_nothing_pending_or_running(self):
        for q, idle in (({"pending_work_units": 0, "in_progress_work_units": 0}, True), ({"pending_work_units": 1, "in_progress_work_units": 0}, False), ({"pending_work_units": 0, "in_progress_work_units": 1}, False)):
            h, _ = make(replies=[q])
            self.assertEqual(h.queue_idle(), idle)

    def test_flags_are_set_for_reasoning_and_dream_and_summaries_stay_off(self):
        h, op = make()
        h.set_flags(True, True)
        self.assertEqual(op.log[0][2]["configuration"], {"reasoning": {"enabled": True}, "dream": {"enabled": True}, "summary": {"enabled": False}})
        self.assertEqual(op.log[0][0], "PUT")

    def test_recall_maps_through_the_source_message_then_the_quoted_id_and_a_tenant_uses_its_own_peer(self):
        h, op = make(replies=[[{"content": "about a thing", "source_message_ids": ["m1"]}, {"content": "see FAKE-DEC-002 for it"}, {"content": "no id here"}], {"metadata": {"source_ref": "FAKE-REQ-001"}}])
        items = h.recall("what", "beta", 5)
        self.assertEqual([i["source_ref"] for i in items], ["FAKE-REQ-001", "FAKE-DEC-002", None])
        self.assertEqual(op.log[0][2]["filters"], {"observed_id": "t-beta"})

    def test_a_dream_is_finished_only_when_a_new_run_exists_and_the_queue_is_idle(self):
        h, op = make(replies=[{"items": []}, {}])
        h.schedule_dream()
        self.assertEqual(op.log[1][2], {"observer": "t-alpha", "observed": "t-alpha", "dream_type": "omni", "session_id": "s1"})
        h2, _ = make(replies=[{"items": [{"id": "r1", "status": "success"}]}, {"pending_work_units": 0, "in_progress_work_units": 0}])
        h2.dream_scheduled_at_runs = 0
        self.assertTrue(h2.dream_finished())
        h3, _ = make(replies=[{"items": []}, {"pending_work_units": 0, "in_progress_work_units": 0}])
        self.assertFalse(h3.dream_finished())


if __name__ == "__main__":
    unittest.main()
