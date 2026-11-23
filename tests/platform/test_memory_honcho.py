"""LIFE-868: governed shared memory (store, recall, correct, revoke) with a fake Honcho; synthetic facts only."""
import json
import unittest

from lifeos.platform import memory_honcho as mh


class FakeHoncho:
    """The few calls memory uses, over an in-memory list of conclusion rows; `down` makes every call fail the way an unreachable service does."""
    def __init__(self):
        self.rows, self.down, self.peers = [], False, 0

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


def state(h=None):
    return {"client": h, "now": None}


def run(op, args, h, client="hermes-librarian"):
    return mh.run(op, {**args, "_client": client}, state(h))


class Memory(unittest.TestCase):
    def setUp(self):
        self.h = FakeHoncho()
        self.fact = run("memory_store", {"workspace": "Personal", "text": "The synthetic launch code is amber falcon.", "source": "uat-note-1"}, self.h)

    def test_store_returns_provenance_and_labels_memory(self):
        f = self.fact
        self.assertEqual((f["label"], f["source"], f["client"], f["workspace"]), ("memory", "uat-note-1", "hermes-librarian", "Personal"))
        self.assertTrue(f["id"] and f["stored_at_ct"].endswith(("-06:00", "-05:00")))                       # a stable id and a Central-time stamp
        self.assertIn("canonical source wins", f["notice"])

    def test_recall_by_another_client_shows_provenance_and_the_canonical_rule(self):
        out = run("memory_recall", {"workspace": "Personal", "query": "launch code", "canonical_ref": "jira:LIFE-868"}, self.h, "openclaw")
        item = out["items"][0]
        self.assertEqual((item["id"], item["text"], item["client"], item["source"], item["label"], item["status"]), (self.fact["id"], "The synthetic launch code is amber falcon.", "hermes-librarian", "uat-note-1", "memory", "active"))
        self.assertEqual(out["label"], "memory")
        self.assertIn("canonical wins", out["canonical"]["rule"])
        self.assertEqual(out["canonical"]["ref"], "jira:LIFE-868")

    def test_correct_replaces_the_item_and_keeps_the_old_one_marked_superseded(self):
        new = run("memory_correct", {"workspace": "Personal", "id": self.fact["id"], "text": "The synthetic launch code is blue heron.", "source": "uat-note-2"}, self.h, "openclaw")
        self.assertEqual(new["supersedes"], self.fact["id"])
        out = run("memory_recall", {"workspace": "Personal", "query": "launch code"}, self.h, "openclaw")
        self.assertEqual([i["text"] for i in out["items"]], ["The synthetic launch code is blue heron."])             # only the correction is current
        hist = run("memory_recall", {"workspace": "Personal", "query": "launch code", "include_history": "true"}, self.h)
        by = {i["id"]: i for i in hist["items"]}
        self.assertEqual((by[self.fact["id"]]["status"], by[self.fact["id"]]["superseded_by"], by[new["id"]]["supersedes"]), ("superseded", new["id"], self.fact["id"]))

    def test_revoke_hides_the_item_at_once_and_keeps_the_record(self):
        out = run("memory_revoke", {"workspace": "Personal", "id": self.fact["id"]}, self.h)
        self.assertEqual(out["status"], "revoked")
        self.assertEqual(run("memory_recall", {"workspace": "Personal", "query": "launch code"}, self.h)["items"], [])
        hist = run("memory_recall", {"workspace": "Personal", "query": "launch code", "include_history": "true"}, self.h)
        self.assertEqual([i["status"] for i in hist["items"]], ["revoked"])
        self.assertEqual(len(self.h.rows), 2)                                                                    # nothing was edited or deleted: the fact and one marker

    def test_a_revoked_or_superseded_item_cannot_be_changed_again_and_an_unknown_id_is_not_found(self):
        run("memory_revoke", {"workspace": "Personal", "id": self.fact["id"]}, self.h)
        for op, extra in (("memory_revoke", {}), ("memory_correct", {"text": "x", "source": "s"})):
            with self.assertRaises(mh.MemoryError_) as raised:
                run(op, {"workspace": "Personal", "id": self.fact["id"], **extra}, self.h)
            self.assertEqual(raised.exception.reason, "memory_not_found")
        with self.assertRaises(mh.MemoryError_) as raised:
            run("memory_revoke", {"workspace": "Personal", "id": "nope"}, self.h)
        self.assertEqual(raised.exception.reason, "memory_not_found")

    def test_a_marker_or_a_non_memory_row_is_never_a_fact(self):
        self.h.rows.append({"id": "x1", "content": "plain text, not an envelope launch code"})
        marker = [r for r in self.h.rows if "tombstone" in r["content"]]
        self.assertEqual(marker, [])
        out = run("memory_recall", {"workspace": "Personal", "query": "launch code"}, self.h)
        self.assertEqual([i["id"] for i in out["items"]], [self.fact["id"]])
        with self.assertRaises(mh.MemoryError_):
            run("memory_revoke", {"workspace": "Personal", "id": "x1"}, self.h)                                  # not a stored memory

    def test_the_service_down_fails_safely_with_one_fixed_code_and_never_a_guess(self):
        self.h.down = True
        for op, args in (("memory_store", {"text": "t", "source": "s"}), ("memory_recall", {"query": "launch"}), ("memory_revoke", {"id": "c1"})):
            with self.assertRaises(mh.MemoryError_) as raised:
                run(op, {"workspace": "Personal", **args}, self.h)
            self.assertEqual(raised.exception.reason, "memory_unavailable")

    def test_no_client_workspace_or_bad_text_is_refused_with_a_fixed_code(self):
        for args, h, code in (({"workspace": "Personal", "query": "x"}, None, "memory_not_configured"), ({"workspace": "Bytalos", "text": "t", "source": "s"}, FakeHoncho(), "unknown_workspace"),
                              ({"workspace": "TGP", "text": "t", "source": "s"}, FakeHoncho(), "unknown_workspace"), ({"workspace": "Personal", "text": "", "source": "s"}, FakeHoncho(), "invalid_argument"),
                              ({"workspace": "Personal", "text": "x" * 2001, "source": "s"}, FakeHoncho(), "invalid_argument")):
            op = "memory_recall" if "query" in args else "memory_store"
            with self.assertRaises(mh.MemoryError_) as raised:
                run(op, args, h)
            self.assertEqual(raised.exception.reason, code, args)

    def test_the_client_comes_from_the_dispatch_never_from_a_caller_value(self):
        out = mh.run("memory_store", {"workspace": "Personal", "text": "t", "source": "s", "_client": "claimed-by-dispatch"}, state(FakeHoncho()))
        self.assertEqual(out["client"], "claimed-by-dispatch")                                                    # the dispatch injects it after the argument check; the MCP schema has no such argument
        self.assertNotIn("_client", {k for _, params in (v for v in mh.OPS.values()) for k in params} | set())

    def test_honcho_client_maps_http_and_network_failures_to_fixed_codes(self):
        import io
        import urllib.error

        def opener_404(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "x", {}, io.BytesIO(b""))

        def opener_down(req, timeout=None):
            raise urllib.error.URLError("refused")
        for opener, code in ((opener_404, "memory_not_found"), (opener_down, "memory_unavailable")):
            with self.assertRaises(mh.MemoryError_) as raised:
                mh.Honcho("http://127.0.0.1:1", "tok", opener).call("/v3/x", {})
            self.assertEqual(raised.exception.reason, code)

    def test_client_from_env_needs_the_token(self):
        self.assertIsNone(mh.client_from_env({}))
        self.assertIsNone(mh.client_from_env({"HONCHO_MEMORY_TOKEN": "  "}))
        self.assertEqual(mh.client_from_env({"HONCHO_MEMORY_TOKEN": "t"}).base, "http://127.0.0.1:8000")


if __name__ == "__main__":
    unittest.main()
