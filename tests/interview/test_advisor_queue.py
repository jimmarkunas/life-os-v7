import hashlib
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from lifeos.interview import advisor, advisor_queue as queue
from lifeos.interview.advisor_store import VerifiedPreview
from lifeos.interview.models import PrepEvidence
from lifeos.platform.notion_client import NotionError


def rt(value):
    return [{"plain_text": value, "text": {"content": value}}]


def block(kind, value="", title=None, children=False):
    data = {"id": f"id-{kind}-{title or value[:8]}", "type": kind, "has_children": children}
    if kind == "child_page":
        data[kind] = {"title": title}
    else:
        data[kind] = {"rich_text": rt(value)}
    return data


def page(title, parent, blocks, pid):
    return {"id": pid, "parent": {"page_id": parent}, "archived": False, "in_trash": False,
            "properties": {"Name": {"type": "title", "title": rt(title)}}, "blocks": blocks}


def bundle():
    manifest = advisor.AdvisorManifest("sl1", "a" * 64, "gt1", "b" * 64, "cp1", "c" * 64,
                                      "eb1", "d" * 64, "prompt1")
    sources = (advisor.AdvisorSource("JD-SYN", advisor.SourceKind.JOB_DESCRIPTION, "Synthetic role."),
               advisor.AdvisorSource("CP-SYN", advisor.SourceKind.CANDIDATE_PROFILE, "Synthetic candidate."),
               advisor.AdvisorSource("SL-SYN", advisor.SourceKind.STRAIGHT_LINE_DOCTRINE, "Synthetic doctrine."),
               advisor.AdvisorSource("GT-SYN", advisor.SourceKind.GAME_THEORY_DOCTRINE, "Synthetic strategy."))
    evidence = (advisor.CandidateEvidence(advisor.EvidenceRef("E-SYN", 1), "Synthetic evidence", "CP-SYN@1"),)
    return advisor.AdvisorInputBundle("Synthetic Co", "Synthetic Role", "2026-10-03", "Interviewer", 1,
                                      "JOB-SYN", manifest, sources, evidence)


def draft(**changes):
    value = advisor.AdvisorDraft(
        straight_line=(advisor.GroundedAdvice("Clarify threshold.", ("SL-SYN@1", "JD-SYN@1")),),
        game_theory=(advisor.GroundedAdvice("Map decision maker.", ("GT-SYN@1", "JD-SYN@1")),),
        decision_criteria=(), objections=(), close_strategy=(),
        focus=(advisor.GroundedAdvice("Confirm success.", ("JD-SYN@1",)),),
        strongest_evidence_refs=("E-SYN@1",), pressure_points=(),
        questions=(advisor.GroundedAdvice("What matters?", ("JD-SYN@1",)),))
    return replace(value, **changes)


class FakeNotion:
    def __init__(self):
        self.pages = {"root": page("LIFE OS — Interview Advisor", "workspace", [], "root"),
                      "queue": page("Advisor Queue", "root", [block("paragraph", "v7-interview-advisor:1;kind=queue")], "queue")}
        self.posts, self.methods, self.paths = 0, [], []
        self.post_error = None
        self.pagination_incomplete = False

    def call_once(self, method, path, body=None):
        return self.call(method, path, body)

    def call(self, method, path, body=None):
        self.methods.append(method); self.paths.append(path)
        if method == "GET" and path.startswith("/pages/"):
            return self.pages[path.rsplit("/", 1)[-1]]
        if method == "GET" and path.startswith("/blocks/"):
            page_id = path.split("/")[2]
            blocks = self.pages[page_id]["blocks"]
            if path.endswith("page_size=1"):
                return {"results": blocks[:1], "has_more": len(blocks) > 1, "next_cursor": "next" if len(blocks) > 1 else None}
            if "start_cursor" in path:
                return {"results": [], "has_more": False, "next_cursor": None}
            if self.pagination_incomplete:
                return {"results": blocks[:1], "has_more": True, "next_cursor": None}
            return {"results": blocks, "has_more": False, "next_cursor": None}
        if method == "POST":
            self.posts += 1
            if self.post_error:
                raise self.post_error
            pid = f"00000000-0000-0000-0000-{self.posts:012d}"
            title = "".join(x["text"]["content"] for x in body["properties"]["title"]["title"])
            item = page(title, "queue", body["children"], pid)
            self.pages[pid] = item
            self.pages["queue"]["blocks"].append(block("child_page", title=title))
            self.pages["queue"]["blocks"][-1]["id"] = pid
            return item
        raise AssertionError((method, path))


def response_page(request, response_draft=None):
    response_draft = response_draft or draft()
    payload = json.dumps(queue._draft_value(response_draft), ensure_ascii=False)
    marker = f"v7-interview-advisor-response:1;{request.request_id};{request.bundle_hash}"
    return page(f"Advisor Response {request.request_id}", request.page_id,
        [block("paragraph", marker), block("paragraph", payload)], "response")


class AdvisorQueueTests(unittest.TestCase):
    def test_bundle_serialization_is_canonical_round_trips_and_commits_digest(self):
        original = bundle()
        payload = queue.serialize_bundle(original)
        self.assertEqual(payload, queue.serialize_bundle(original))
        restored = queue.parse_bundle(payload)
        self.assertEqual(restored, original)
        self.assertEqual(advisor.bundle_digest(restored), advisor.bundle_digest(original))

    def test_bundle_parser_rejects_schema_and_noncanonical_json_at_each_level(self):
        payload = json.loads(queue.serialize_bundle(bundle()))
        for mutation in (lambda v: v.pop("role"), lambda v: v.__setitem__("extra", 1),
                         lambda v: v["manifest"].__setitem__("extra", 1),
                         lambda v: v["sources"][0].__setitem__("extra", 1),
                         lambda v: v["evidence"][0].__setitem__("extra", 1),
                         lambda v: v["manifest"].__setitem__("straight_line_hash", "bad"),
                         lambda v: v["sources"][0].__setitem__("kind", "unknown"),
                         lambda v: v["evidence"][0].__setitem__("version", 0)):
            value = json.loads(json.dumps(payload)); mutation(value)
            with self.assertRaises(queue.AdvisorQueueError):
                queue.parse_bundle(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        with self.assertRaises(queue.AdvisorQueueError):
            queue.parse_bundle(json.dumps(payload, indent=2))

    def test_bundle_size_bound_fails_with_fixed_code(self):
        original = bundle()
        extra = tuple(advisor.AdvisorSource(f"COMPANY-{i}", advisor.SourceKind.COMPANY, "x" * 20000) for i in range(9))
        oversized = replace(original, sources=original.sources + extra)
        with self.assertRaises(queue.AdvisorQueueError) as error:
            queue.serialize_bundle(oversized)
        self.assertEqual(error.exception.code, "advisor_queue_too_large")

    def test_request_identity_title_marker_and_changed_bundle(self):
        client = FakeNotion(); result = queue.ensure_request(client, "queue", bundle())
        self.assertEqual(result.request_id, advisor.bundle_digest(bundle())[:32])
        self.assertEqual(client.pages[result.page_id]["properties"]["Name"]["title"][0]["plain_text"],
                         "Advisor Request " + result.request_id)
        self.assertEqual(client.pages[result.page_id]["blocks"][0]["paragraph"]["rich_text"][0]["text"]["content"],
                         f"v7-interview-advisor-request:1;{result.request_id};{result.bundle_hash}")
        other = replace(bundle(), role="Another Synthetic Role")
        self.assertNotEqual(advisor.bundle_digest(other)[:32], result.request_id)
        self.assertNotIn("Synthetic", "Advisor Request " + result.request_id)

    def test_call_once_is_required_before_any_queue_io(self):
        class NoOnce:
            def __init__(self): self.calls = 0
            def call(self, *args): self.calls += 1
        client = NoOnce()
        with self.assertRaises(queue.AdvisorQueueError):
            queue.ensure_request(client, "queue", bundle())
        self.assertEqual(client.calls, 0)

    def test_request_creation_is_one_post_and_exact_replay_has_no_post(self):
        client = FakeNotion(); first = queue.ensure_request(client, "queue", bundle())
        replay = queue.ensure_request(client, "queue", bundle())
        self.assertEqual(first, replay); self.assertEqual(client.posts, 1)
        self.assertEqual(client.methods.count("POST"), 1); self.assertNotIn("PATCH", client.methods)

    def test_existing_duplicate_or_unexpected_queue_children_fail_closed(self):
        client = FakeNotion(); req = queue.ensure_request(client, "queue", bundle())
        client.pages["queue"]["blocks"].append(block("child_page", title=f"Advisor Request {req.request_id}"))
        with self.assertRaises(queue.AdvisorQueueError) as error:
            queue.ensure_request(client, "queue", bundle())
        self.assertEqual(error.exception.code, "advisor_queue_conflict")
        for unexpected in (block("paragraph", "body"), block("child_database"), block("child_page", title="Other")):
            bad = FakeNotion(); bad.pages["queue"]["blocks"].append(unexpected)
            with self.assertRaises(queue.AdvisorQueueError):
                queue.ensure_request(bad, "queue", bundle())

    def test_queue_idempotency_conflicts_on_other_full_hash_for_same_id(self):
        client = FakeNotion(); original = bundle(); stored = queue.ensure_request(client, "queue", original)
        collision = replace(original, role="Collision role")
        wanted = advisor.bundle_digest(collision)
        with patch.object(queue.advisor, "bundle_digest", return_value=stored.request_id + wanted[32:]):
            with self.assertRaises(queue.AdvisorQueueError) as error:
                queue.ensure_request(client, "queue", collision)
        self.assertEqual(error.exception.code, "advisor_queue_conflict")
        self.assertEqual(client.posts, 1)

    def test_queue_parent_and_marker_are_validated(self):
        client = FakeNotion(); client.pages["queue"]["parent"]["page_id"] = "elsewhere"
        with self.assertRaises(queue.AdvisorQueueError):
            queue.ensure_request(client, "queue", bundle())
        client = FakeNotion(); client.pages["queue"]["properties"]["Name"]["title"] = rt("Wrong")
        with self.assertRaises(queue.AdvisorQueueError):
            queue.ensure_request(client, "queue", bundle())
        client = FakeNotion(); client.pages["queue"]["blocks"][0] = block("paragraph", "bad marker")
        with self.assertRaises(queue.AdvisorQueueError):
            queue.ensure_request(client, "queue", bundle())

    def test_post_failure_statuses_and_uncertain_outcomes_are_fixed_and_never_retried(self):
        for status, expected in ((400, "advisor_queue_write_failed"), (409, "advisor_queue_write_failed"),
                                 (429, "advisor_queue_write_failed"), (500, "advisor_queue_readback_failed")):
            client = FakeNotion(); client.post_error = NotionError(f"NOTION_HTTP_{status}")
            with self.subTest(status=status), self.assertRaises(queue.AdvisorQueueError) as error:
                queue.ensure_request(client, "queue", bundle())
            self.assertEqual(error.exception.code, expected); self.assertEqual(client.posts, 1)
        client = FakeNotion(); client.post_error = OSError("private synthetic data")
        with self.assertRaises(queue.AdvisorQueueError) as error:
            queue.ensure_request(client, "queue", bundle())
        self.assertEqual(error.exception.code, "advisor_queue_readback_failed"); self.assertEqual(client.posts, 1)

    def test_post_returned_but_authoritative_readback_fails_without_retry(self):
        class ReadbackFails(FakeNotion):
            def __init__(self): super().__init__(); self.posted = False
            def call_once(self, method, path, body=None):
                result = super().call_once(method, path, body)
                self.posted = True
                return result
            def call(self, method, path, body=None):
                if self.posted and method == "GET" and path.startswith("/pages/00000000-"):
                    raise OSError("synthetic readback uncertainty")
                return super().call(method, path, body)
        client = ReadbackFails()
        with self.assertRaises(queue.AdvisorQueueError) as error:
            queue.ensure_request(client, "queue", bundle())
        self.assertEqual(error.exception.code, "advisor_queue_readback_failed")
        self.assertEqual(client.posts, 1)

    def test_request_page_read_validates_marker_parent_title_and_hash(self):
        client = FakeNotion(); req = queue.ensure_request(client, "queue", bundle())
        read, trailing = queue._read_request(client, [40], req.page_id, "queue")
        self.assertEqual(read, req); self.assertEqual(trailing, [])
        page = client.pages[req.page_id]; page["blocks"][0] = block("paragraph", "bad")
        with self.assertRaises(queue.AdvisorQueueError):
            queue._read_request(client, [40], req.page_id, "queue")

    def test_request_read_rejects_wrong_title_parent_state_marker_hash_payload_and_extra_block(self):
        mutations = (lambda p: p["properties"]["Name"].__setitem__("title", rt("Wrong")),
                       lambda p: p["parent"].__setitem__("page_id", "other"),
                       lambda p: p.__setitem__("archived", True), lambda p: p.__setitem__("in_trash", True),
                       lambda p: p["blocks"][0]["paragraph"]["rich_text"][0]["text"].__setitem__("content", "bad"),
                       lambda p: p["blocks"][0]["paragraph"]["rich_text"][0]["text"].__setitem__(
                           "content", "v7-interview-advisor-request:1;" + "0" * 32 + ";" + "f" * 64),
                       lambda p: p["blocks"][1]["paragraph"]["rich_text"][0]["text"].__setitem__("content", "{}"),
                       lambda p: p["blocks"].append(block("paragraph", "extra")))
        for index, mutate in enumerate(mutations):
            client = FakeNotion(); req = queue.ensure_request(client, "queue", bundle())
            mutate(client.pages[req.page_id])
            with self.subTest(request_mutation=index), self.assertRaises(queue.AdvisorQueueError):
                queue.ensure_request(client, "queue", bundle())

    def test_response_accepts_noncanonical_json_but_uses_existing_compiler(self):
        client = FakeNotion(); req = queue.ensure_request(client, "queue", bundle())
        page_obj = response_page(req)
        # Whitespace and key order are free for the external handoff.
        page_obj["blocks"][1] = block("paragraph", json.dumps(queue._draft_value(draft()), indent=2))
        client.pages["response"] = page_obj
        response = queue.read_response(client, req, "response")
        self.assertEqual(response.prep.focus, ("Confirm success.",))
        self.assertEqual(response.request_id, req.request_id)

    def test_response_schema_grounding_marker_parent_and_extra_blocks_fail(self):
        client = FakeNotion(); req = queue.ensure_request(client, "queue", bundle())
        invalid_drafts = (replace(draft(), straight_line=(advisor.GroundedAdvice("No doctrine", ("JD-SYN@1",)),)),
                          replace(draft(), game_theory=(advisor.GroundedAdvice("No doctrine", ("JD-SYN@1",)),)))
        for bad_draft in invalid_drafts:
            client.pages["response"] = response_page(req, bad_draft)
            with self.assertRaises(queue.AdvisorQueueError): queue.read_response(client, req, "response")
        response = response_page(req)
        response["blocks"][1] = block("paragraph", json.dumps({"bad": 1}))
        client.pages["response"] = response
        with self.assertRaises(queue.AdvisorQueueError): queue.read_response(client, req, "response")
        response = response_page(req); response["blocks"].append(block("paragraph", "extra")); client.pages["response"] = response
        with self.assertRaises(queue.AdvisorQueueError): queue.read_response(client, req, "response")
        response = response_page(req); response["parent"]["page_id"] = "other"; client.pages["response"] = response
        with self.assertRaises(queue.AdvisorQueueError): queue.read_response(client, req, "response")

    def test_response_malformed_draft_fields_and_refs_fail_closed(self):
        client = FakeNotion(); req = queue.ensure_request(client, "queue", bundle())
        valid = queue._draft_value(draft())
        mutations = (lambda v: v.pop("focus"), lambda v: v.__setitem__("extra", []),
                     lambda v: v["focus"][0].__setitem__("extra", "no"),
                     lambda v: v["focus"][0].pop("source_refs"),
                     lambda v: v["straight_line"][0].__setitem__("source_refs", ["NO-SUCH@1"]),
                     lambda v: v.__setitem__("strongest_evidence_refs", ["E-MISSING@1"]))
        for mutation in mutations:
            response = response_page(req)
            value = json.loads(queue._plain(response["blocks"][1])); mutation(value)
            response["blocks"][1] = block("paragraph", json.dumps(value))
            client.pages["response"] = response
            with self.assertRaises(queue.AdvisorQueueError): queue.read_response(client, req, "response")

    def test_response_wrong_title_markers_archive_and_trash_fail(self):
        def set_text(p, value):
            item = p["blocks"][0]["paragraph"]["rich_text"][0]
            item["plain_text"] = value
            item["text"]["content"] = value
        changes = (lambda p: p["properties"]["Name"]["title"].__setitem__(0, {"plain_text": "Wrong"}),
                   lambda p: set_text(p, "bad"),
                   lambda p: set_text(p, f"v7-interview-advisor-response:1;{'0' * 32};{'f' * 64}"),
                   lambda p: p.__setitem__("archived", True), lambda p: p.__setitem__("in_trash", True),
                   lambda p: p["blocks"][1].__setitem__("has_children", True))
        for index, mutate in enumerate(changes):
            client = FakeNotion(); req = queue.ensure_request(client, "queue", bundle())
            response = response_page(req); mutate(response); client.pages["response"] = response
            with self.subTest(response_mutation=index), self.assertRaises(queue.AdvisorQueueError):
                queue.read_response(client, req, "response")

    def test_scan_queue_derives_ready_responded_and_ambiguous_in_order(self):
        client = FakeNotion(); one = queue.ensure_request(client, "queue", bundle())
        changed = replace(bundle(), role="Second Synthetic Role")
        second = queue.ensure_request(client, "queue", changed)
        items = queue.scan_queue(client, "queue")
        self.assertEqual([item.request.page_id for item in items], [one.page_id, second.page_id])
        self.assertEqual([item.state for item in items], [queue.QueueState.READY, queue.QueueState.READY])
        response = response_page(one); client.pages["response"] = response
        client.pages[one.page_id]["blocks"].append(block("child_page", title=response["properties"]["Name"]["title"][0]["plain_text"]))
        client.pages[one.page_id]["blocks"][-1]["id"] = "response"
        items = queue.scan_queue(client, "queue")
        self.assertEqual(items[0].state, queue.QueueState.RESPONDED)
        self.assertEqual(items[1].state, queue.QueueState.READY)
        client.pages[one.page_id]["blocks"].append(block("child_page", title=f"Advisor Response {one.request_id}"))
        items = queue.scan_queue(client, "queue")
        self.assertEqual(items[0].state, queue.QueueState.AMBIGUOUS)

    def test_malformed_or_unexpected_response_makes_item_ambiguous(self):
        for title in ("Unexpected", "expected"):
            client = FakeNotion(); req = queue.ensure_request(client, "queue", bundle())
            if title == "expected":
                response = response_page(req); response["blocks"][0] = block("paragraph", "bad")
                client.pages["response"] = response
                child_title = f"Advisor Response {req.request_id}"
            else:
                child_title = title
            child = block("child_page", title=child_title); child["id"] = "response"
            client.pages[req.page_id]["blocks"].append(child)
            self.assertEqual(queue.scan_queue(client, "queue")[0].state, queue.QueueState.AMBIGUOUS)

    def test_malformed_request_and_incomplete_pagination_fail_closed(self):
        client = FakeNotion(); req = queue.ensure_request(client, "queue", bundle())
        client.pages[req.page_id]["blocks"][0] = block("paragraph", "bad marker")
        with self.assertRaises(queue.AdvisorQueueError): queue.scan_queue(client, "queue")
        client = FakeNotion(); client.pagination_incomplete = True
        with self.assertRaises(queue.AdvisorQueueError): queue.scan_queue(client, "queue")

    def test_previewed_requires_exact_response_preview_identity_hash_and_prep(self):
        client = FakeNotion(); req = queue.ensure_request(client, "queue", bundle())
        response = queue.QueueResponse(req.request_id, req.bundle_hash, draft(), advisor.compile_prep(req.bundle, draft()), "response")
        item = queue.QueueItem(req, response, queue.QueueState.RESPONDED)
        exact = VerifiedPreview(req.request_id, req.bundle_hash, "f" * 64, response.prep)
        self.assertEqual(queue.with_verified_preview(item, exact).state, queue.QueueState.PREVIEWED)
        for preview in (replace(exact, generation_id="0" * 32), replace(exact, bundle_hash="e" * 64),
                        replace(exact, prep=PrepEvidence(focus=("different",)))):
            with self.assertRaises(queue.AdvisorQueueError): queue.with_verified_preview(item, preview)
        with self.assertRaises(queue.AdvisorQueueError):
            queue.with_verified_preview(queue.QueueItem(req, None, queue.QueueState.READY), exact)

    def test_fixed_errors_private_repr_and_no_provider_or_response_writer(self):
        client = FakeNotion(); req = queue.ensure_request(client, "queue", bundle())
        self.assertNotIn("Synthetic Co", repr(req)); self.assertNotIn(req.page_id, repr(req))
        self.assertNotIn("Synthetic Co", repr(queue.QueueResponse(req.request_id, req.bundle_hash, draft(),
                          advisor.compile_prep(req.bundle, draft()), "private-page")))
        source = Path(queue.__file__).read_text()
        self.assertNotIn("openai", source.lower()); self.assertNotIn("anthropic", source.lower())
        self.assertNotIn('"POST", "/pages"', source[source.index("def read_response"):])
        self.assertNotIn('"PATCH"', source)
        error = queue.AdvisorQueueError("private synthetic value")
        self.assertEqual(str(error), "advisor_queue_invalid")


if __name__ == "__main__":
    unittest.main()
