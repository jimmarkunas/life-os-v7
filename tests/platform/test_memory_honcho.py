"""LIFE-868, LIFE-949: governed shared memory (store, recall, correct, revoke, ask) with a fake Honcho; synthetic facts only, no network, no model."""
import json
import unittest
from datetime import datetime, timedelta

from lifeos.platform import memory_honcho as mh

CLIENTS = ("claude-chat", "chatgpt-chat", "muse-chat", "mac-claude-code", "oracle-claude-code", "hermes-librarian", "openclaw", "duet-chat")
OLD = "The synthetic launch code is amber falcon."
NEW = "The synthetic launch code is blue heron."


class FakeHoncho:
    """The few calls memory uses, over an in-memory list of conclusion rows. `chat` answers from the stored rows directly, as Honcho's ask does, so a row that was only hidden (not deleted) would still be repeated."""
    def __init__(self):
        self.rows, self.down, self.peers, self.chats, self.fail_delete = [], False, 0, [], False

    def _up(self):
        if self.down:
            raise mh.MemoryError_("memory_unavailable")

    def ensure_peer(self, ws):
        self._up()
        self.peers += 1

    def create(self, ws, envelope):
        self._up()
        row = {"id": f"c{len(self.rows) + 1}", "content": json.dumps(envelope, sort_keys=True)}
        self.rows.append(row)
        return row["id"]

    def query(self, ws, text, top_k):
        self._up()
        words = text.lower().split()
        return [r for r in self.rows if any(w in r["content"].lower() for w in words)][:top_k]

    def listing(self, ws):
        self._up()
        return list(self.rows)

    def get(self, ws, cid):
        self._up()
        for r in self.rows:
            if r["id"] == cid:
                return r
        raise mh.MemoryError_("memory_not_found")

    def delete(self, ws, cid):
        self._up()
        if self.fail_delete:
            raise mh.MemoryError_("memory_unavailable")
        self.get(ws, cid)
        self.rows = [r for r in self.rows if r["id"] != cid]

    def chat(self, ws, question, level):
        self._up()
        self.chats.append((question, level))
        facts = [json.loads(r["content"])["text"] for r in self.rows if json.loads(r["content"]).get("kind") == "fact"]
        return "Jim's memory says: " + " ".join(facts) if facts else "Nothing is stored about that."


def ok_look(text):
    return None


def state(h=None, look=ok_look, now=None, receipts=None):
    return {"client": h, "now": now, "look": look, "receipt": (receipts.append if receipts is not None else (lambda record: None))}


def run(op, args, h, client="hermes-librarian", **kw):
    return mh.run(op, {**args, "_client": client}, state(h, **kw))


def refused(test, op, args, h, client="hermes-librarian", **kw):
    with test.assertRaises(mh.MemoryError_) as raised:
        run(op, args, h, client, **kw)
    return raised.exception


class Memory(unittest.TestCase):
    def setUp(self):
        self.h = FakeHoncho()
        self.fact = run("memory_store", {"workspace": "Personal", "text": OLD, "source": "uat-note-1"}, self.h)

    def test_store_returns_provenance_and_labels_memory(self):
        f = self.fact
        self.assertEqual((f["label"], f["source"], f["client"], f["workspace"]), ("memory", "uat-note-1", "hermes-librarian", "Personal"))
        self.assertTrue(f["id"] and f["stored_at_ct"].endswith(("-06:00", "-05:00")))                       # a stable id and a Central-time stamp
        self.assertIn("canonical source wins", f["notice"])

    def test_every_granted_client_stores_and_another_client_recalls_it_with_the_writers_label(self):
        for writer, reader in zip(CLIENTS, CLIENTS[1:] + CLIENTS[:1]):
            stored = run("memory_store", {"workspace": "Personal", "text": f"Synthetic fact from {writer}: zebra{writer.replace('-', '')}.", "source": "uat"}, self.h, writer)
            seen = run("memory_recall", {"workspace": "Personal", "query": f"zebra{writer.replace('-', '')}"}, self.h, reader)
            self.assertEqual([(i["id"], i["client"]) for i in seen["items"]], [(stored["id"], writer)], (writer, reader))

    def test_recall_by_another_client_shows_provenance_and_the_canonical_rule(self):
        out = run("memory_recall", {"workspace": "Personal", "query": "launch code", "canonical_ref": "jira:LIFE-868"}, self.h, "openclaw")
        item = out["items"][0]
        self.assertEqual((item["id"], item["text"], item["client"], item["source"], item["label"], item["status"]), (self.fact["id"], OLD, "hermes-librarian", "uat-note-1", "memory", "active"))
        self.assertEqual(out["label"], "memory")
        self.assertIn("canonical wins", out["canonical"]["rule"])
        self.assertEqual(out["canonical"]["ref"], "jira:LIFE-868")

    def test_correct_deletes_the_old_text_keeps_a_metadata_marker_and_only_the_correction_is_current(self):
        new = run("memory_correct", {"workspace": "Personal", "id": self.fact["id"], "text": NEW, "source": "uat-note-2"}, self.h, "openclaw")
        self.assertEqual((new["supersedes"], new["old_text_deleted"]), (self.fact["id"], True))
        out = run("memory_recall", {"workspace": "Personal", "query": "launch code"}, self.h, "openclaw")
        self.assertEqual([i["text"] for i in out["items"]], [NEW])
        self.assertNotIn("amber falcon", json.dumps(self.h.rows))                                                # the old text is gone from the store, not only hidden
        hist = run("memory_recall", {"workspace": "Personal", "query": "launch code", "include_history": "true"}, self.h)
        by = {i["id"]: i for i in hist["items"]}
        self.assertEqual((by[self.fact["id"]]["status"], by[self.fact["id"]]["superseded_by"], by[self.fact["id"]]["text"], by[self.fact["id"]]["client"]), ("superseded", new["id"], None, "openclaw"))
        self.assertEqual(by[new["id"]]["supersedes"], self.fact["id"])

    def test_revoke_deletes_the_text_hides_the_item_and_keeps_only_a_metadata_record(self):
        out = run("memory_revoke", {"workspace": "Personal", "id": self.fact["id"]}, self.h, "duet-chat")
        self.assertEqual((out["status"], out["text_deleted"]), ("revoked", True))
        self.assertEqual(run("memory_recall", {"workspace": "Personal", "query": "launch code"}, self.h)["items"], [])
        hist = run("memory_recall", {"workspace": "Personal", "query": "launch code", "include_history": "true"}, self.h)
        self.assertEqual([(i["status"], i["text"], i["client"]) for i in hist["items"]], [("revoked", None, "duet-chat")])
        self.assertEqual(len(self.h.rows), 1)                                                                    # the fact is gone; one tombstone with an id, client, CT time and reason remains
        marker = json.loads(self.h.rows[0]["content"])
        self.assertEqual((marker["kind"], marker["of"], marker["why"], marker["client"]), ("tombstone", self.fact["id"], "revoked", "duet-chat"))
        self.assertNotIn("text", marker)
        self.assertNotIn("amber falcon", json.dumps(self.h.rows))

    def test_an_ask_after_a_revoke_or_a_correction_does_not_contain_the_old_text(self):
        before = run("memory_ask", {"workspace": "Personal", "question": "What is the launch code?"}, self.h, "claude-chat")
        self.assertIn("amber falcon", before["answer"])                                                          # while it is stored, the ask knows it
        new = run("memory_correct", {"workspace": "Personal", "id": self.fact["id"], "text": NEW, "source": "uat-note-2"}, self.h)
        after_correct = run("memory_ask", {"workspace": "Personal", "question": "What is the launch code?"}, self.h, "claude-chat")
        self.assertEqual(("amber falcon" in after_correct["answer"], "blue heron" in after_correct["answer"]), (False, True))
        run("memory_revoke", {"workspace": "Personal", "id": new["id"]}, self.h)
        after_revoke = run("memory_ask", {"workspace": "Personal", "question": "What is the launch code?"}, self.h, "claude-chat")
        self.assertNotIn("blue heron", after_revoke["answer"])
        self.assertNotIn("amber falcon", after_revoke["answer"])
        self.assertEqual(after_revoke["considered"], [])

    def test_a_revoked_or_superseded_item_cannot_be_changed_again_and_an_unknown_id_is_not_found(self):
        run("memory_revoke", {"workspace": "Personal", "id": self.fact["id"]}, self.h)
        for op, extra in (("memory_revoke", {}), ("memory_correct", {"text": "x", "source": "s"})):
            self.assertEqual(refused(self, op, {"workspace": "Personal", "id": self.fact["id"], **extra}, self.h).reason, "memory_not_found")
        self.assertEqual(refused(self, "memory_revoke", {"workspace": "Personal", "id": "nope"}, self.h).reason, "memory_not_found")

    def test_a_revoke_whose_delete_failed_hid_the_item_and_a_second_revoke_finishes_the_deletion(self):
        self.h.fail_delete = True
        self.assertEqual(refused(self, "memory_revoke", {"workspace": "Personal", "id": self.fact["id"]}, self.h).reason, "memory_unavailable")
        self.assertEqual(run("memory_recall", {"workspace": "Personal", "query": "launch code"}, self.h)["items"], [])      # hidden at once, even though the text is still there
        self.h.fail_delete = False
        self.assertEqual(run("memory_revoke", {"workspace": "Personal", "id": self.fact["id"]}, self.h)["status"], "revoked")
        self.assertNotIn("amber falcon", json.dumps(self.h.rows))
        self.assertEqual(len([r for r in self.h.rows if "tombstone" in r["content"]]), 1)                         # one marker, not two

    def test_a_marker_or_a_non_memory_row_is_never_a_fact(self):
        self.h.rows.append({"id": "x1", "content": "plain text, not an envelope launch code"})
        out = run("memory_recall", {"workspace": "Personal", "query": "launch code"}, self.h)
        self.assertEqual([i["id"] for i in out["items"]], [self.fact["id"]])
        self.assertEqual(refused(self, "memory_revoke", {"workspace": "Personal", "id": "x1"}, self.h).reason, "memory_not_found")      # not a stored memory

    def test_the_service_down_fails_safely_with_one_fixed_code_and_never_a_guess(self):
        self.h.down = True
        for op, args in (("memory_store", {"text": "t", "source": "s"}), ("memory_recall", {"query": "launch"}), ("memory_revoke", {"id": "c1"}), ("memory_ask", {"question": "q"})):
            self.assertEqual(refused(self, op, {"workspace": "Personal", **args}, self.h).reason, "memory_unavailable", op)

    def test_no_client_workspace_or_bad_text_is_refused_with_a_fixed_code(self):
        for args, h, code in (({"workspace": "Personal", "query": "x"}, None, "memory_not_configured"), ({"workspace": "Bytalos", "text": "t", "source": "s"}, FakeHoncho(), "unknown_workspace"),
                              ({"workspace": "TGP", "text": "t", "source": "s"}, FakeHoncho(), "unknown_workspace"), ({"workspace": "Personal", "text": "", "source": "s"}, FakeHoncho(), "invalid_argument"),
                              ({"workspace": "Personal", "text": "x" * 2001, "source": "s"}, FakeHoncho(), "invalid_argument"), ({"workspace": "Bytalos", "question": "q"}, FakeHoncho(), "unknown_workspace")):
            op = "memory_recall" if "query" in args else "memory_ask" if "question" in args else "memory_store"
            self.assertEqual(refused(self, op, args, h).reason, code, args)

    def test_the_client_comes_from_the_dispatch_never_from_a_caller_value(self):
        out = mh.run("memory_store", {"workspace": "Personal", "text": "t", "source": "s", "_client": "claimed-by-dispatch"}, state(FakeHoncho()))
        self.assertEqual(out["client"], "claimed-by-dispatch")                                                    # the dispatch injects it after the argument check; the MCP schema has no such argument
        self.assertNotIn("_client", {k for _, params in (v for v in mh.OPS.values()) for k in params} | set())

    def test_honcho_client_maps_http_and_network_failures_to_fixed_codes_and_chat_needs_an_answer(self):
        import io
        import urllib.error

        def opener_404(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "x", {}, io.BytesIO(b""))

        def opener_down(req, timeout=None):
            raise urllib.error.URLError("refused")

        def opener_refused_by_the_meter(req, timeout=None):                                                      # Honcho's own model call hit the meter's cap: Honcho answers 5xx
            raise urllib.error.HTTPError(req.full_url, 500, "x", {}, io.BytesIO(b""))
        for opener, code in ((opener_404, "memory_not_found"), (opener_down, "memory_unavailable"), (opener_refused_by_the_meter, "memory_unavailable")):
            with self.assertRaises(mh.MemoryError_) as raised:
                mh.Honcho("http://127.0.0.1:1", "tok", opener).call("/v3/x", {})
            self.assertEqual(raised.exception.reason, code)

        class Reply:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self.body
        seen = []

        def opener_chat(req, timeout=None):
            seen.append((req.get_method(), req.full_url.split(":1")[1], json.loads(req.data)))
            return Reply(b'{"content": "an answer"}')
        self.assertEqual(mh.Honcho("http://127.0.0.1:1", "tok", opener_chat).chat("personal", "q?", "low"), "an answer")
        self.assertEqual(seen, [("POST", "/v3/workspaces/personal/peers/lifeos-memory/chat", {"query": "q?", "reasoning_level": "low", "stream": False})])
        with self.assertRaises(mh.MemoryError_):
            mh.Honcho("http://127.0.0.1:1", "tok", lambda req, timeout=None: Reply(b"{}")).chat("personal", "q?", "minimal")
        deleted = []
        mh.Honcho("http://127.0.0.1:1", "tok", lambda req, timeout=None: deleted.append((req.get_method(), req.full_url.split(":1")[1])) or Reply(b"")).delete("personal", "abc")
        self.assertEqual(deleted, [("DELETE", "/v3/workspaces/personal/conclusions/abc")])

    def test_client_from_env_needs_the_token(self):
        self.assertIsNone(mh.client_from_env({}))
        self.assertIsNone(mh.client_from_env({"HONCHO_MEMORY_TOKEN": "  "}))
        self.assertEqual(mh.client_from_env({"HONCHO_MEMORY_TOKEN": "t"}).base, "http://127.0.0.1:8000")


class SensitiveRefusal(unittest.TestCase):
    """LIFE-949: refused before any write, text never stored, echoed or logged; the receipt holds client, category and CT time only."""
    def setUp(self):
        self.h = FakeHoncho()
        self.receipts = []

    def store(self, text, source="uat", look=ok_look, client="claude-chat"):
        return run("memory_store", {"workspace": "Personal", "text": text, "source": source}, self.h, client, look=look, receipts=self.receipts)

    def test_explicit_sensitive_text_is_refused_with_the_fixed_shape_and_nothing_is_stored_echoed_or_logged(self):
        for text, category in (("My SSN is 123-45-6789 for the form.", "identity"), ("The api_key: abcd1234efgh5678 is in the doc.", "credentials"), ("Pay with card 4111 1111 1111 1111.", "finances"),
                               ("His salary is $140,000 a year.", "finances"), ("She was diagnosed with diabetes last year.", "health"), ("Call him on 312-555-0142.", "others")):
            error = None
            try:
                self.store(text)
            except mh.MemoryError_ as raised:
                error = raised
            self.assertEqual((error.reason, error.detail["category"], error.detail["stored"]), ("sensitive_refused", category, False), text)
            self.assertIn("Nothing was stored", error.detail["hint"])
        self.assertEqual(self.h.rows, [])                                                                        # no write of any kind
        self.assertEqual(self.h.peers, 0)
        self.assertEqual([sorted(r) for r in self.receipts], [["category", "client", "ct", "event"]] * 6)
        self.assertEqual({r["client"] for r in self.receipts}, {"claude-chat"})
        log = json.dumps(self.receipts)
        for secret in ("123-45-6789", "abcd1234efgh5678", "4111", "140,000", "diabetes", "555-0142"):
            self.assertNotIn(secret, log)

    def test_a_paraphrase_the_patterns_miss_is_caught_by_the_second_look_and_a_clean_line_is_stored(self):
        for text, category in (("My back has been killing me for weeks.", "health"), ("Money has been really tight lately.", "finances")):
            with self.assertRaises(mh.MemoryError_) as raised:
                self.store(text, look=lambda t, c=category: c)
            self.assertEqual((raised.exception.reason, raised.exception.detail["category"]), ("sensitive_refused", category))
        looked = []
        stored = self.store("Jim wants Notion pages full width, always.", look=lambda t: looked.append(t))
        self.assertEqual((stored["label"], len(self.h.rows), looked), ("memory", 1, ["Jim wants Notion pages full width, always."]))

    def test_the_second_look_is_skipped_when_the_patterns_already_refused(self):
        looked = []
        with self.assertRaises(mh.MemoryError_):
            self.store("Her passport number is X1234567.", look=lambda t: looked.append(t))
        self.assertEqual(looked, [])

    def test_the_filter_fails_closed_when_the_second_look_is_unavailable(self):
        def down(text):
            raise mh.memory_filter.FilterUnavailable()
        with self.assertRaises(mh.MemoryError_) as raised:
            self.store("Jim wants Notion pages full width, always.", look=down)
        self.assertEqual(raised.exception.reason, "memory_unavailable")
        self.assertEqual((self.h.rows, self.h.peers, self.receipts), ([], 0, []))

    def test_a_correction_is_filtered_too_and_a_refused_correction_leaves_the_old_memory_alone(self):
        fact = self.store("Jim wants Notion pages full width, always.")
        with self.assertRaises(mh.MemoryError_) as raised:
            run("memory_correct", {"workspace": "Personal", "id": fact["id"], "text": "A paraphrase of a condition.", "source": "uat"}, self.h, "openclaw", look=lambda t: "health", receipts=self.receipts)
        self.assertEqual(raised.exception.reason, "sensitive_refused")
        with self.assertRaises(mh.MemoryError_) as raised:
            run("memory_correct", {"workspace": "Personal", "id": fact["id"], "text": "SSN 123-45-6789", "source": "uat"}, self.h, "openclaw", receipts=self.receipts)
        self.assertEqual(raised.exception.detail["category"], "identity")
        self.assertEqual([json.loads(r["content"])["text"] for r in self.h.rows], ["Jim wants Notion pages full width, always."])
        self.assertEqual(run("memory_recall", {"workspace": "Personal", "query": "Notion"}, self.h)["count"], 1)

    def test_a_detail_hidden_in_the_source_is_refused_before_any_write(self):
        with self.assertRaises(mh.MemoryError_) as raised:
            self.store("Jim wants Notion pages full width, always.", source="email from bob@example.com")
        self.assertEqual((raised.exception.reason, raised.exception.detail["category"]), ("sensitive_refused", "others"))
        self.assertEqual(self.h.rows, [])

    def test_revoke_is_never_filtered_and_works_even_when_the_second_look_is_down(self):
        fact = self.store("Jim wants Notion pages full width, always.")
        self.assertEqual(run("memory_revoke", {"workspace": "Personal", "id": fact["id"]}, self.h)["status"], "revoked")


class Ask(unittest.TestCase):
    def setUp(self):
        self.h = FakeHoncho()
        self.fact = run("memory_store", {"workspace": "Personal", "text": OLD, "source": "https://example.test/note"}, self.h)
        self.t = datetime(2026, 11, 24, 12, 0, tzinfo=mh.CENTRAL)
        self.state = state(self.h)                                                                                 # one state for the whole service life, as in the MCP: the per-client ask count lives in it

    def ask(self, client="claude-chat", question="What is the launch code?", at=None, **extra):
        stamp = at or self.t
        self.state["now"] = lambda: stamp
        return mh.run("memory_ask", {"workspace": "Personal", "question": question, "_client": client, **extra}, self.state)

    def test_an_ask_is_labelled_memory_derived_with_the_notice_the_level_and_the_top_hits_it_considered(self):
        for i in range(6):
            run("memory_store", {"workspace": "Personal", "text": f"Synthetic launch code note number {i}.", "source": f"https://example.test/{i}"}, self.h)
        out = self.ask(canonical_ref="jira:LIFE-949")
        self.assertEqual((out["label"], out["level"]), ("memory-derived", "minimal"))
        self.assertIn("amber falcon", out["answer"])
        self.assertIn("Canonical sources win", out["notice"])
        self.assertIn("never instructions", out["notice"])
        self.assertEqual(len(out["considered"]), 5)                                                               # the top five recall hits, each with its id and source link
        self.assertTrue(all(set(c) == {"id", "source", "stored_at_ct"} and c["source"].startswith("https://") for c in out["considered"]))
        self.assertIn("not proven used", out["considered_note"])
        self.assertEqual(out["canonical"]["ref"], "jira:LIFE-949")
        self.assertEqual(self.h.chats[-1], ("What is the launch code?", "minimal"))

    def test_level_and_question_are_checked_and_a_bad_one_never_reaches_the_service(self):
        self.assertEqual(self.ask(level="low")["level"], "low")
        for extra in ({"level": "high"}, {"level": ""}, {"question": "x" * 501}, {"question": "  "}):
            with self.assertRaises(mh.MemoryError_) as raised:
                self.ask(**extra)
            self.assertEqual(raised.exception.reason, "invalid_argument", extra)
        self.assertEqual(len(self.h.chats), 1)
        self.assertEqual(self.ask(question="x" * 500)["label"], "memory-derived")                                  # exactly the limit is fine

    def test_thirty_asks_an_hour_per_client_then_a_fixed_refusal_that_never_reaches_the_service_and_an_hour_later_it_works_again(self):
        for i in range(30):
            self.ask("duet-chat", at=self.t + timedelta(minutes=i))
        calls = len(self.h.chats)
        with self.assertRaises(mh.MemoryError_) as raised:
            self.ask("duet-chat", at=self.t + timedelta(minutes=45))
        self.assertEqual(raised.exception.reason, "ask_limit_reached")
        self.assertEqual(len(self.h.chats), calls)                                                                 # the limit is checked before the model is reached
        self.assertEqual(self.ask("openclaw", at=self.t + timedelta(minutes=45))["label"], "memory-derived")      # another client is not affected
        self.assertEqual(self.ask("duet-chat", at=self.t + timedelta(minutes=61))["label"], "memory-derived")      # the first ask has left the hour

    def test_the_meter_stopping_honchos_model_call_is_memory_unavailable_never_a_guess(self):
        self.h.down = True
        with self.assertRaises(mh.MemoryError_) as raised:
            self.ask()
        self.assertEqual(raised.exception.reason, "memory_unavailable")


class Wiring(unittest.TestCase):
    def test_the_five_operations_are_registered_and_ask_is_a_read_in_the_workspaces_tenant(self):
        self.assertEqual(sorted(mh.OPS), ["memory_ask", "memory_correct", "memory_recall", "memory_revoke", "memory_store"])
        self.assertEqual([mh.route(op, {"workspace": "Personal"}, "personal")[1] for op in ("memory_store", "memory_correct", "memory_revoke", "memory_recall", "memory_ask")], ["write", "write", "write", "read", "read"])
        self.assertEqual(mh.route("memory_ask", {"workspace": "Bytalos"}, "personal")[0], "bytalos")                 # the gate decides: only Personal is granted
        self.assertEqual(mh.route("memory_ask", {"workspace": "TGP"}, "personal")[0], "unknown")


if __name__ == "__main__":
    unittest.main()
