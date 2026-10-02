import hashlib
import json
from copy import deepcopy
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import patch

from lifeos.interview import advisor_store as store
from lifeos.interview.advisor import AdvisorDraft, AdvisorInputBundle, AdvisorManifest, AdvisorSource, CandidateEvidence, EvidenceRef, GroundedAdvice, SourceKind
from lifeos.interview.models import PrepEvidence
from lifeos.platform.notion_client import Client, NotionError


ROOT_ENV = {"INTERVIEW_ADVISOR_ROOT_PAGE_ID": "root"}


def canon(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rt(text):
    return [{"plain_text": text, "text": {"content": text}}]


def block(kind, text, title=None):
    obj = {"id": "block-" + str(abs(hash((kind, text, title)))), "type": kind, "has_children": False,
           kind: {"rich_text": rt(text)}}
    if kind == "child_page":
        obj[kind] = {"title": title}
    return obj


def page(title, parent, blocks, pid=None):
    return {"id": pid or title.lower().replace(" ", "-"), "parent": {"page_id": parent},
            "archived": False, "in_trash": False,
            "properties": {"Name": {"type": "title", "title": rt(title)}}, "blocks": blocks}


class FakeNotion:
    def __init__(self, pages):
        self.pages = {p["id"]: p for p in pages}
        self.methods = []
        self.paths = []
        self.posts = 0

    def call_once(self, method, path, body=None):
        return self.call(method, path, body)

    def call(self, method, path, body=None):
        self.methods.append(method)
        self.paths.append(path)
        if method == "GET" and path.startswith("/pages/"):
            return self.pages[path.split("/")[-1]]
        if method == "GET" and path.startswith("/blocks/"):
            pid = path.split("/")[2]
            return {"results": self.pages[pid]["blocks"], "has_more": False, "next_cursor": None}
        if method == "POST" and path == "/pages":
            self.posts += 1
            pid = "00000000-0000-0000-0000-000000000001"
            title = "".join(x["text"]["content"] for x in body["properties"]["title"]["title"])
            page_obj = page(title, body["parent"]["page_id"], body["children"], pid)
            self.pages[pid] = page_obj
            self.pages[body["parent"]["page_id"]]["blocks"].append(block("child_page", "", title))
            return page_obj
        raise AssertionError((method, path))


def marked(kind, rest=()):
    return [block("paragraph", f"v7-interview-advisor:1;kind={kind}")] + list(rest)


def source_body(source_id, version, kind):
    return marked(kind, [block("paragraph", "v7-interview-advisor-source:1;" + canon({"source_id": source_id, "version": version})),
                         block("paragraph", "Synthetic canonical source."), block("heading_2", "Context"),
                         block("bulleted_list_item", "Synthetic fact")])


def build_tree():
    sl_text = "Synthetic canonical source.\n## Context\n- Synthetic fact"
    gt_text = sl_text
    profile_text = sl_text
    evidence_record = {"evidence_id": "E-SYN-001", "version": 1, "canonical_text": "Synthetic candidate result",
                       "source_ref": "PROFILE-SYN-001@1", "tags": ["delivery"], "active": True}
    old_record = {"evidence_id": "E-SYN-002", "version": 1, "canonical_text": "Historical fact",
                  "source_ref": "PROFILE-SYN-001@1", "tags": [], "active": False}
    norm = [["E-SYN-001@1", "Synthetic candidate result", "PROFILE-SYN-001@1", ["delivery"], True],
            ["E-SYN-002@1", "Historical fact", "PROFILE-SYN-001@1", [], False]]
    manifest = {"straight_line_version": "1", "straight_line_hash": hashlib.sha256(sl_text.encode()).hexdigest(),
                "game_theory_version": "1", "game_theory_hash": hashlib.sha256(gt_text.encode()).hexdigest(),
                "candidate_profile_version": "1", "candidate_profile_hash": hashlib.sha256(profile_text.encode()).hexdigest(),
                "evidence_bank_version": "1", "evidence_bank_hash": hashlib.sha256(canon(norm).encode()).hexdigest(),
                "advisor_prompt_version": "prompt-v1"}
    pages = [page("LIFE OS — Interview Advisor", "workspace", [block("paragraph", "Root instructions")], "root")]
    names = ("Corpus Manifest", "Straight Line Doctrine", "Game Theory Doctrine", "Candidate Evidence Bank",
             "Candidate Profile", "Advisor Inputs", "Advisor Queue", "Advisor Previews")
    pages[0]["blocks"] += [block("child_page", "", n) for n in names]
    contents = {
        "Corpus Manifest": marked("manifest", [block("paragraph", canon(manifest))]),
        "Straight Line Doctrine": source_body("SL-SYN-001", 1, "straight_line_doctrine"),
        "Game Theory Doctrine": source_body("GT-SYN-001", 1, "game_theory_doctrine"),
        "Candidate Evidence Bank": marked("evidence_bank", [block("paragraph", "v7-interview-advisor-bank:1;" + canon({"version": 1})),
            block("paragraph", canon(evidence_record)), block("paragraph", canon(old_record))]),
        "Candidate Profile": source_body("PROFILE-SYN-001", 1, "candidate_profile"),
        "Advisor Inputs": marked("inputs", [block("child_page", "", "Guidance"), block("child_page", "", "Accepted Signals")]),
        "Advisor Queue": marked("queue"),
        "Advisor Previews": marked("previews")}
    for n in names:
        pages.append(page(n, "root", contents[n]))
    for b in pages[0]["blocks"]:
        if b["type"] == "child_page":
            b["id"] = b["child_page"]["title"].lower().replace(" ", "-")
    for b in pages[6]["blocks"]:
        if b["type"] == "child_page":
            b["id"] = b["child_page"]["title"].lower().replace(" ", "-")
    pages += [page("Guidance", "advisor-inputs", marked("guidance", [block("paragraph", canon({"source_id": "G-SYN-001", "version": 1,
        "text": "Be concise", "active": True})), block("paragraph", canon({"source_id": "G-OLD-001", "version": 1,
        "text": "Old guidance", "active": False}))])),
        page("Accepted Signals", "advisor-inputs", marked("accepted_signals", [block("paragraph", canon({"source_id": "S-SYN-001", "version": 1,
        "source_round": "ROUND-SYN-001", "text": "Values tradeoffs", "active": True})), block("paragraph", canon({"source_id": "S-OLD-001", "version": 1,
        "source_round": "ROUND-SYN-001", "text": "Historical signal", "active": False}))]))]
    return pages


def draft():
    return AdvisorDraft(
        straight_line=(GroundedAdvice("Clarify the decision threshold.", ("SL-SYN-001@1", "PROFILE-SYN-001@1")),),
        game_theory=(GroundedAdvice("Map the decision maker.", ("GT-SYN-001@1", "JD-SYN-001@1")),),
        decision_criteria=(), objections=(), close_strategy=(), focus=(GroundedAdvice("Confirm success.", ("PROFILE-SYN-001@1",)),),
        strongest_evidence_refs=("E-SYN-001@1",), pressure_points=(), questions=(GroundedAdvice("What changes your view?", ("SL-SYN-001@1",)),))


def bundle():
    # Preview contract tests use the already validated Advisor 1 value object fixture shape.
    sources = (AdvisorSource("JD-SYN-001", SourceKind.JOB_DESCRIPTION, "Synthetic role."),
               AdvisorSource("PROFILE-SYN-001", SourceKind.CANDIDATE_PROFILE, "Synthetic profile."),
               AdvisorSource("SL-SYN-001", SourceKind.STRAIGHT_LINE_DOCTRINE, "Synthetic doctrine."),
               AdvisorSource("GT-SYN-001", SourceKind.GAME_THEORY_DOCTRINE, "Synthetic strategy."))
    m = AdvisorManifest("1", "a" * 64, "1", "b" * 64, "1", "c" * 64, "1", "d" * 64, "prompt-v1")
    evidence = (CandidateEvidence(EvidenceRef("E-SYN-001", 1), "Synthetic candidate result", "PROFILE-SYN-001@1"),)
    return AdvisorInputBundle("Synthetic Co", "Synthetic Role", "2026-10-03", "Interviewer", 1,
                              "JOB-SYN-001", m, sources, evidence)


class StoreTests(unittest.TestCase):
    def test_runtime_config_uses_only_interview_token_and_explicit_root(self):
        with patch.object(store, "Client") as client_factory:
            store.make_client({"NOTION_INTERVIEW_TOKEN": "synthetic-token", "NOTION_API_TOKEN": "jobs-token"})
            environ = client_factory.call_args.args[0]
            self.assertEqual(environ["NOTION_API_TOKEN"], "synthetic-token")
            self.assertNotEqual(environ["NOTION_API_TOKEN"], "jobs-token")
        with self.assertRaises(store.AdvisorStoreError):
            store.make_client({"NOTION_API_TOKEN": "jobs-token"})
        with self.assertRaises(store.AdvisorStoreError):
            store.read_store(FakeNotion(build_tree()), {})

    def test_exact_root_shape_markers_and_historical_filtering(self):
        client = FakeNotion(build_tree())
        snapshot = store.read_store(client, ROOT_ENV)
        self.assertEqual(snapshot.straight_line.kind, SourceKind.STRAIGHT_LINE_DOCTRINE)
        self.assertEqual(tuple(x.ref.canonical() for x in snapshot.evidence), ("E-SYN-001@1",))
        self.assertEqual(tuple(x.source_id for x in snapshot.guidance), ("G-SYN-001",))
        self.assertEqual(tuple(x.source_id for x in snapshot.accepted_signals), ("S-SYN-001",))
        self.assertEqual(snapshot.previews_page_id, "advisor-previews")
        self.assertEqual(snapshot.queue_page_id, "advisor-queue")
        self.assertTrue(all(method == "GET" for method in client.methods))
        self.assertIn("/blocks/advisor-queue/children?page_size=1", client.paths)
        self.assertNotIn("/blocks/advisor-queue/children?page_size=100", client.paths)

    def test_missing_config_root_title_archive_trash_and_shape_fail_closed(self):
        for pages, root, env in ((build_tree(), "", {}), (build_tree(), "missing", {})):
            with self.assertRaises(store.AdvisorStoreError):
                store.read_store(FakeNotion(pages), env)
        for mutate in (lambda p: p[0]["properties"]["Name"]["title"].__setitem__(0, {"plain_text": "Wrong"}),
                       lambda p: p[0].__setitem__("archived", True), lambda p: p[0].__setitem__("in_trash", True),
                       lambda p: p[0]["blocks"].pop(), lambda p: p[0]["blocks"].append(block("child_page", "", "Extra"))):
            pages = build_tree(); mutate(pages)
            with self.assertRaises(store.AdvisorStoreError):
                store.read_store(FakeNotion(pages), ROOT_ENV)

    def test_duplicate_child_missing_nested_child_and_bad_marker_fail(self):
        cases = []
        p = build_tree(); p[0]["blocks"].append(block("child_page", "", "Corpus Manifest")); cases.append(p)
        p = build_tree(); p[6]["blocks"] = marked("inputs", [block("child_page", "", "Guidance")]); cases.append(p)
        p = build_tree(); p[6]["blocks"].append(block("child_page", "", "Other")); cases.append(p)
        p = build_tree(); p[1]["blocks"][0] = block("paragraph", "v7-interview-advisor:1;kind=oops"); cases.append(p)
        p = build_tree(); p[0]["blocks"] = [b for b in p[0]["blocks"] if not (b.get("type") == "child_page" and b["child_page"]["title"] == "Advisor Queue")]; cases.append(p)
        p = build_tree(); p[7]["blocks"][0] = block("paragraph", "v7-interview-advisor:1;kind=not_queue"); cases.append(p)
        for pages in cases:
            with self.assertRaises(store.AdvisorStoreError):
                store.read_store(FakeNotion(pages), ROOT_ENV)

    def test_manifest_and_source_hash_mismatch_fail(self):
        pages = build_tree(); pages[3]["blocks"][1] = block("paragraph", canon({"source_id": "GT-SYN-001", "version": 2}))
        with self.assertRaises(store.AdvisorStoreError):
            store.read_store(FakeNotion(pages), ROOT_ENV)
        for index in (2, 3, 5, 4):
            pages = build_tree()
            pages[index]["blocks"][-1] = block("paragraph", "Changed content")
            with self.assertRaises(store.AdvisorStoreError):
                store.read_store(FakeNotion(pages), ROOT_ENV)

    def test_source_metadata_body_blocks_and_manifest_metadata_fail_closed(self):
        mutations = []
        p = build_tree(); p[2]["blocks"][1] = block("paragraph", "v7-interview-advisor-source:1;" + canon({"source_id": "SL-SYN-001", "version": 1, "extra": 1})); mutations.append(p)
        p = build_tree(); p[2]["blocks"][1] = block("paragraph", "v7-interview-advisor-source:1;" + canon({"source_id": "SL-SYN-001", "version": 0})); mutations.append(p)
        p = build_tree(); p[2]["blocks"][2] = block("toggle", "unsupported"); mutations.append(p)
        p = build_tree(); p[2]["blocks"] = p[2]["blocks"][:2] + [block("paragraph", " ")]; mutations.append(p)
        p = build_tree(); p[2]["blocks"].append(block("paragraph", "v7-interview-advisor-source:1;" + canon({"source_id": "ALT", "version": 1}))); mutations.append(p)
        p = build_tree(); p[1]["blocks"].append(block("paragraph", canon({"extra": "metadata"}))); mutations.append(p)
        for pages in mutations:
            with self.subTest(page=len(pages)):
                with self.assertRaises(store.AdvisorStoreError):
                    store.read_store(FakeNotion(pages), ROOT_ENV)

    def test_bank_duplicates_schema_version_hash_and_signal_cap_fail_closed(self):
        mutations = []
        p = build_tree(); p[4]["blocks"].append(p[4]["blocks"][-1].copy()); mutations.append(p)
        p = build_tree(); p[4]["blocks"][2] = block("paragraph", canon({"evidence_id": "E-SYN-003", "version": 1})); mutations.append(p)
        p = build_tree(); p[4]["blocks"][1] = block("paragraph", "v7-interview-advisor-bank:1;" + canon({"version": 2})); mutations.append(p)
        p = build_tree();
        for i in range(5):
            p[-1]["blocks"].append(block("paragraph", canon({"source_id": f"S-{i}", "version": 1,
                "source_round": "ROUND-SYN-001", "text": "signal", "active": True})))
        mutations.append(p)
        for pages in mutations:
            with self.assertRaises(store.AdvisorStoreError):
                store.read_store(FakeNotion(pages), ROOT_ENV)

    def test_single_attempt_transport_does_not_retry_rate_limited_post(self):
        error = urllib.error.HTTPError("https://api.notion.com", 429, "rate", {}, None)
        with patch("urllib.request.urlopen", side_effect=error) as opened, patch("time.sleep"):
            client = Client({"NOTION_API_TOKEN": "synthetic", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"},
                            clock=lambda: 1.0, sleep=lambda _seconds: None)
            with self.assertRaises(NotionError):
                client.call_once("POST", "/pages", {})
            self.assertEqual(opened.call_count, 1)

    def test_bad_evidence_guidance_and_signal_fail_closed(self):
        pages = build_tree(); pages[4]["blocks"][-1] = block("paragraph", "not-json")
        with self.assertRaises(store.AdvisorStoreError):
            store.read_store(FakeNotion(pages), ROOT_ENV)
        pages = build_tree()
        for i in range(3):
            pages[-2]["blocks"].append(block("paragraph", canon({"source_id": f"G-{i}", "version": 1, "text": "more", "active": True})))
        with self.assertRaises(store.AdvisorStoreError):
            store.read_store(FakeNotion(pages), ROOT_ENV)

    def test_source_render_and_evidence_hash_cover_order_and_inactive_records(self):
        pages = build_tree(); client = FakeNotion(pages)
        snapshot = store.read_store(client, ROOT_ENV)
        self.assertEqual(snapshot.straight_line.text, "Synthetic canonical source.\n## Context\n- Synthetic fact")
        pages = build_tree(); pages[4]["blocks"].pop()
        with self.assertRaises(store.AdvisorStoreError):
            store.read_store(FakeNotion(pages), ROOT_ENV)

    def test_preview_render_hashes_complete_body_and_title_is_identity_safe(self):
        b, d = bundle(), draft()
        title, bh, body_hash, body, blocks, prep = store.render_preview("0123456789abcdef0123456789abcdef", b, d)
        self.assertEqual(title, "Advisor Preview 0123456789abcdef0123456789abcdef")
        self.assertNotIn("Synthetic Co", title); self.assertNotIn("Synthetic Role", title); self.assertNotIn("Interviewer", title)
        self.assertTrue(blocks[0]["paragraph"]["rich_text"][0]["text"]["content"].endswith(body_hash))
        self.assertIn("refs: SL-SYN-001@1,PROFILE-SYN-001@1", body)
        self.assertEqual(body_hash, hashlib.sha256(body.encode()).hexdigest())
        self.assertNotIn("reasoning", body.lower())
        self.assertNotEqual(body_hash, store.render_preview("0123456789abcdef0123456789abcdef", b,
            __import__("dataclasses").replace(d, focus=(GroundedAdvice("Different", ("PROFILE-SYN-001@1",)),)))[2])
        refs_changed = __import__("dataclasses").replace(d, straight_line=(GroundedAdvice(
            "Clarify the decision threshold.", ("PROFILE-SYN-001@1", "SL-SYN-001@1")),))
        self.assertNotEqual(body_hash, store.render_preview("0123456789abcdef0123456789abcdef", b, refs_changed)[2])
        prep_changed = __import__("dataclasses").replace(d, focus=(GroundedAdvice("Changed prep.", ("PROFILE-SYN-001@1",)),))
        self.assertNotEqual(body_hash, store.render_preview("0123456789abcdef0123456789abcdef", b, prep_changed)[2])
        self.assertNotEqual(blocks[0], store.render_preview("0123456789abcdef0123456789abcdef",
            __import__("dataclasses").replace(b, role="Other Role"), d)[4][0])
        self.assertEqual(prep, store.render_preview("0123456789abcdef0123456789abcdef", b, d)[5])

    def test_preview_parser_never_carries_pending_refs_across_private_section_headings(self):
        _, _, _, _, blocks, _ = store.render_preview("0123456789abcdef0123456789abcdef", bundle(), draft())
        body = blocks[1:]
        headings = [i for i, b in enumerate(body) if b["type"] == "heading_2"]
        transitions = []
        for left, right in zip(headings, headings[1:]):
            prior = body[left]["heading_2"]["rich_text"][0]["text"]["content"]
            following = body[right]["heading_2"]["rich_text"][0]["text"]["content"]
            if (prior, following) in (("Decision Criteria", "Objections"),
                                      ("Objections", "Close Strategy"),
                                      ("Close Strategy", "Compiled PrepEvidence"),
                                      ("Straight Line", "Game Theory"),
                                      ("Game Theory", "Decision Criteria")):
                transitions.append((left, right, prior, following))
        self.assertEqual(len(transitions), 5)
        bullet = block("bulleted_list_item", "Unreferenced synthetic finding")
        refs = block("paragraph", "refs: JD-SYN-001@1")
        for left, right, prior, following in transitions:
            malformed = body[:right] + [bullet, body[right], refs] + body[right + 1:]
            with self.subTest(section=prior, following=following), self.assertRaises(store.AdvisorStoreError):
                store._parse_preview_body(malformed)

    def test_preview_parser_accepts_optional_findings_and_empty_optional_sections(self):
        _, _, _, _, blocks, prep = store.render_preview("0123456789abcdef0123456789abcdef", bundle(), draft())
        body = blocks[1:]
        transition = next(i for i, item in enumerate(body) if item["type"] == "heading_2"
                          and item["heading_2"]["rich_text"][0]["text"]["content"] == "Objections")
        valid = body[:transition] + [block("bulleted_list_item", "Optional synthetic finding"),
            block("paragraph", "refs: JD-SYN-001@1"), body[transition]] + body[transition + 1:]
        _, parsed = store._parse_preview_body(valid)
        self.assertEqual(parsed, prep)
        # The untouched generated draft also proves every optional private section may be empty.
        _, empty_parsed = store._parse_preview_body(body)
        self.assertEqual(empty_parsed, prep)

    def test_preview_create_readback_post_only_conflict_and_no_retry(self):
        b, d = bundle(), draft(); client = FakeNotion(build_tree())
        verified = store.create_preview(client, "advisor-previews", "0123456789abcdef0123456789abcdef", b, d)
        self.assertIsInstance(verified.prep, PrepEvidence)
        self.assertEqual(client.posts, 1); self.assertNotIn("PATCH", client.methods)
        same = store.FakeNotion if False else client
        with self.assertRaises(store.AdvisorStoreError) as error:
            store.create_preview(same, "advisor-previews", "0123456789abcdef0123456789abcdef", b, d)
        self.assertEqual(error.exception.code, "advisor_preview_conflict")
        self.assertEqual(client.posts, 1)

    def test_preview_uncertain_post_and_readback_failure_are_not_retried(self):
        class Uncertain(FakeNotion):
            def call(self, method, path, body=None):
                if method == "POST":
                    self.posts += 1
                    raise OSError("synthetic private content")
                return super().call(method, path, body)
        client = Uncertain(build_tree())
        with self.assertRaises(store.AdvisorStoreError) as error:
            store.create_preview(client, "advisor-previews", "0123456789abcdef0123456789abcdef", bundle(), draft())
        self.assertEqual(error.exception.code, "advisor_preview_readback_failed"); self.assertEqual(client.posts, 1)

    def test_preview_read_rejects_parent_marker_hash_and_structure_mutations(self):
        client = FakeNotion(build_tree())
        verified = store.create_preview(client, "advisor-previews", "0123456789abcdef0123456789abcdef", bundle(), draft())
        self.assertEqual(store.read_preview(client, "00000000-0000-0000-0000-000000000001", "advisor-previews").prep, verified.prep)
        for parent, gen, digest in (("root", None, None), ("advisor-previews", "f" * 32, None),
                                    ("advisor-previews", None, "f" * 64)):
            with self.assertRaises(store.AdvisorStoreError):
                store.read_preview(client, "00000000-0000-0000-0000-000000000001", parent, gen, digest)
        page_obj = client.pages["00000000-0000-0000-0000-000000000001"]
        page_obj["blocks"].pop(2)
        with self.assertRaises(store.AdvisorStoreError):
            store.read_preview(client, "00000000-0000-0000-0000-000000000001", "advisor-previews")

    def test_preview_read_fails_closed_for_title_marker_state_and_body_tampering(self):
        original = FakeNotion(build_tree())
        store.create_preview(original, "advisor-previews", "0123456789abcdef0123456789abcdef", bundle(), draft())
        pid = "00000000-0000-0000-0000-000000000001"
        mutations = (
            lambda p: p["properties"]["Name"]["title"].__setitem__(0, {"plain_text": "wrong"}),
            lambda p: p["blocks"][0]["paragraph"]["rich_text"][0]["text"].__setitem__("content", "bad marker"),
            lambda p: p.__setitem__("archived", True), lambda p: p.__setitem__("in_trash", True),
            lambda p: p["blocks"].__setitem__(2, block("heading_2", "Game Theory")),
            lambda p: p["blocks"].insert(1, block("heading_2", "Straight Line")),
            lambda p: p["blocks"].append(block("child_page", "", "Unexpected")),
        )
        for mutate in mutations:
            client = deepcopy(original)
            mutate(client.pages[pid])
            with self.assertRaises(store.AdvisorStoreError):
                store.read_preview(client, pid, "advisor-previews")

    def test_post_success_readback_failure_reports_maybe_created_and_never_retries(self):
        class BrokenReadback(FakeNotion):
            def call(self, method, path, body=None):
                if method == "GET" and path.startswith("/pages/00000000-"):
                    raise OSError("private synthetic readback failure")
                return super().call(method, path, body)
        client = BrokenReadback(build_tree())
        with self.assertRaises(store.AdvisorStoreError) as error:
            store.create_preview(client, "advisor-previews", "0123456789abcdef0123456789abcdef", bundle(), draft())
        self.assertEqual(error.exception.code, "advisor_preview_readback_failed")
        self.assertEqual(client.posts, 1)

    def _page(self, pages, title):
        return next(p for p in pages if p["properties"]["Name"]["title"][0]["plain_text"] == title)

    def test_root_comes_only_from_the_environment_variable(self):
        with self.assertRaises(TypeError):
            store.read_store(FakeNotion(build_tree()), "root", ROOT_ENV)
        with self.assertRaises(store.AdvisorStoreError):
            store.read_store(FakeNotion(build_tree()), {"INTERVIEW_ADVISOR_ROOT_PAGE_ID": "  "})
        self.assertIsNotNone(store.read_store(FakeNotion(build_tree()), ROOT_ENV))

    def test_any_marker_like_text_outside_the_exact_marker_and_metadata_fails_closed(self):
        variants = ("v7-interview-advisor:2;kind=queue", "V7-Interview-Advisor:1;kind=x", "  v7-interview-advisor-source:2;{}",
                    "v7-interview-advisor-bank:1;{}", "v7-interview-advisor-preview:1;x", "v7-interview-advisor")
        for title in ("Straight Line Doctrine", "Game Theory Doctrine", "Candidate Profile", "Candidate Evidence Bank",
                      "Guidance", "Accepted Signals", "Corpus Manifest", "Advisor Inputs", "Advisor Previews"):
            for kind in ("paragraph", "heading_2", "bulleted_list_item"):
                for text in variants:
                    pages = build_tree()
                    self._page(pages, title)["blocks"].append(block(kind, text))
                    with self.subTest(title=title, kind=kind, text=text), self.assertRaises(store.AdvisorStoreError):
                        store.read_store(FakeNotion(pages), ROOT_ENV)
        # a second copy of the one allowed metadata line is also rejected
        pages = build_tree()
        sl = self._page(pages, "Straight Line Doctrine")
        sl["blocks"].insert(2, sl["blocks"][1])
        with self.assertRaises(store.AdvisorStoreError):
            store.read_store(FakeNotion(pages), ROOT_ENV)

    def test_unexpected_child_database_under_a_controlled_parent_fails_closed(self):
        for title in ("LIFE OS — Interview Advisor", "Advisor Inputs"):
            pages = build_tree()
            database = {"id": "db-1", "type": "child_database", "has_children": False, "child_database": {"title": "Advisor Queue"}}
            self._page(pages, title)["blocks"].append(database)
            with self.subTest(title=title), self.assertRaises(store.AdvisorStoreError):
                store.read_store(FakeNotion(pages), ROOT_ENV)

    def test_preview_create_requires_the_one_attempt_transport_and_touches_nothing_without_it(self):
        class NoOneAttempt(FakeNotion):
            call_once = None
        client = NoOneAttempt(build_tree())
        with self.assertRaises(store.AdvisorStoreError) as error:
            store.create_preview(client, "advisor-previews", "0123456789abcdef0123456789abcdef", bundle(), draft())
        self.assertEqual(error.exception.code, "advisor_preview_invalid")
        self.assertEqual((client.methods, client.posts), ([], 0))

    def test_only_a_definite_rejection_is_a_write_failure_everything_else_is_uncertain(self):
        cases = (("NOTION_HTTP_400", "advisor_preview_write_failed"), ("NOTION_HTTP_404", "advisor_preview_write_failed"),
                 ("NOTION_HTTP_429", "advisor_preview_write_failed"), ("NOTION_HTTP_500", "advisor_preview_readback_failed"),
                 ("NOTION_HTTP_502", "advisor_preview_readback_failed"), ("NOTION_NETWORK", "advisor_preview_readback_failed"))
        for notion_code, expected in cases:
            class Rejected(FakeNotion):
                def call_once(self, method, path, body=None, _code=notion_code):
                    self.posts += 1
                    raise NotionError(_code)
            client = Rejected(build_tree())
            with self.subTest(code=notion_code), self.assertRaises(store.AdvisorStoreError) as error:
                store.create_preview(client, "advisor-previews", "0123456789abcdef0123456789abcdef", bundle(), draft())
            self.assertEqual((error.exception.code, client.posts), (expected, 1))
            self.assertNotIn("PATCH", client.methods)

    def test_fixed_private_errors_and_no_provider_or_logging(self):
        secret = "Synthetic Secret Candidate Detail"
        with self.assertRaises(store.AdvisorStoreError) as error:
            store.read_store(FakeNotion(build_tree()), {"INTERVIEW_ADVISOR_ROOT_PAGE_ID": "private-root-id"})
        self.assertNotIn(secret, str(error.exception)); self.assertNotIn("private-root-id", str(error.exception))
        source = Path(store.__file__).read_text()
        self.assertNotIn("logging.", source); self.assertNotIn("openai", source.lower())
        self.assertNotIn("PATCH", source)
        with patch("lifeos.interview.prep.apply", side_effect=AssertionError("B3")):
            store.render_preview("0123456789abcdef0123456789abcdef", bundle(), draft())


if __name__ == "__main__":
    unittest.main()
